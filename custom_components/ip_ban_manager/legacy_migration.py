"""Legacy domain and folder cleanup for IP Ban Manager."""

from __future__ import annotations

import logging
import shutil
from asyncio import CancelledError, Task
from contextlib import suppress
from pathlib import Path

from homeassistant.config_entries import ConfigEntry, UnknownEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BANNED_IPS,
    CONF_LEGACY_ENTRY_ID,
    DOMAIN,
    LEGACY_DOMAIN,
)
from .health import (
    async_update_health_issue,
    async_update_legacy_folder_cleanup_issue,
)
from .storage_keys import (
    KEY_CONFIG_ENTRY,
    KEY_LEGACY_CLEANUP_SCHEDULED,
    KEY_LEGACY_FOLDER_CLEANED,
    KEY_LEGACY_FOLDER_CLEANUP_TASK,
)

_LOGGER = logging.getLogger(__name__)

ENTRY_TITLE = "IP Ban Manager"
LEGACY_ENTRY_TITLES = {"IP Ban Allowlist", "ban_allowlist"}
LEGACY_BACKUP_DIR = "ip_ban_manager_legacy_backup"
LEGACY_CLEANUP_DIR = ".cleanup"


def async_cleanup_entry_metadata(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean stale options without changing live ban state."""
    data = dict(entry.data)
    legacy_entry_id = data.pop(CONF_LEGACY_ENTRY_ID, None)
    if legacy_entry_id is not None:
        hass.config_entries.async_update_entry(entry, data=data)

    if entry.title in LEGACY_ENTRY_TITLES:
        hass.config_entries.async_update_entry(entry, title=ENTRY_TITLE)

    if CONF_BANNED_IPS in entry.options:
        options = dict(entry.options)
        options.pop(CONF_BANNED_IPS, None)
        hass.config_entries.async_update_entry(entry, options=options)

    if isinstance(legacy_entry_id, str):
        legacy_entry = hass.config_entries.async_get_entry(legacy_entry_id)
        if legacy_entry is not None and legacy_entry.domain == LEGACY_DOMAIN:
            _LOGGER.info("Removing migrated legacy ban_allowlist config entry")

            async def _remove_migrated_legacy_entry() -> None:
                if hass.config_entries.async_get_entry(legacy_entry_id) is None:
                    return
                with suppress(UnknownEntry):
                    await hass.config_entries.async_remove(legacy_entry_id)

            hass.async_create_task(_remove_migrated_legacy_entry())


@callback
def async_remove_legacy_entries(hass: HomeAssistant) -> None:
    """Remove stale old-domain entries once IP Ban Manager exists."""
    if not hass.config_entries.async_entries(DOMAIN):
        return

    legacy_entries = [
        entry
        for entry in hass.config_entries.async_entries()
        if entry.domain == LEGACY_DOMAIN
    ]
    for entry in legacy_entries:
        _LOGGER.info("Removing legacy ban_allowlist config entry after migration")

        async def _remove_legacy_entry(entry_id: str = entry.entry_id) -> None:
            if hass.config_entries.async_get_entry(entry_id) is None:
                return
            with suppress(UnknownEntry):
                await hass.config_entries.async_remove(entry_id)

        hass.async_create_task(_remove_legacy_entry())


def cleanup_destination(cleanup_root: Path, name: str, timestamp: str) -> Path:
    """Return a non-existing cleanup destination path."""
    destination = cleanup_root / f"{name}-{timestamp}"
    suffix = 2
    while destination.exists():
        destination = cleanup_root / f"{name}-{timestamp}-{suffix}"
        suffix += 1
    return destination


def move_to_cleanup(
    cleanup_root: Path, source: Path, name: str, timestamp: str
) -> str | None:
    """Move a stale path into cleanup storage and return a failed source path."""
    try:
        cleanup_root.mkdir(parents=True, exist_ok=True)
        destination = cleanup_destination(cleanup_root, name, timestamp)
        shutil.move(str(source), str(destination))
    except (OSError, shutil.Error):
        _LOGGER.warning("Could not move stale cleanup path %s", source, exc_info=True)
        return str(source)

    _LOGGER.info("Moved stale cleanup path %s to %s", source, destination)
    return None


def move_legacy_component_folder(hass: HomeAssistant) -> list[str]:
    """Move a stale legacy custom component folder out of Home Assistant's loader path."""
    integration_path = Path(hass.config.path("custom_components", DOMAIN))
    cleanup_root = integration_path / LEGACY_CLEANUP_DIR
    legacy_path = Path(hass.config.path("custom_components", LEGACY_DOMAIN))
    nested_custom_components_path = integration_path / "custom_components"
    timestamp = dt_util.utcnow().strftime("%Y%m%d-%H%M%S")
    failures: list[str] = []

    if nested_custom_components_path.is_dir():
        try:
            shutil.rmtree(nested_custom_components_path)
        except OSError:
            _LOGGER.warning(
                "Could not remove nested custom_components path %s",
                nested_custom_components_path,
                exc_info=True,
            )
            failures.append(str(nested_custom_components_path))
        else:
            _LOGGER.info(
                "Removed nested custom_components path %s",
                nested_custom_components_path,
            )

    if legacy_path.is_dir():
        manifest_path = legacy_path / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = manifest_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                _LOGGER.warning(
                    "Could not inspect stale legacy folder %s",
                    legacy_path,
                    exc_info=True,
                )
                failures.append(str(legacy_path))
            else:
                if f'"domain": "{LEGACY_DOMAIN}"' in manifest:
                    failure = move_to_cleanup(
                        cleanup_root,
                        legacy_path,
                        LEGACY_DOMAIN,
                        timestamp,
                    )
                    if failure is not None:
                        failures.append(failure)

    old_backup_root = Path(hass.config.path(LEGACY_BACKUP_DIR))
    if old_backup_root.is_dir():
        failure = move_to_cleanup(
            cleanup_root,
            old_backup_root,
            LEGACY_BACKUP_DIR,
            timestamp,
        )
        if failure is not None:
            failures.append(failure)

    return failures


async def async_cleanup_legacy_component_folder(hass: HomeAssistant) -> None:
    """Move stale legacy files once the new integration is running."""
    if hass.data.get(KEY_LEGACY_FOLDER_CLEANED):
        return
    hass.data[KEY_LEGACY_FOLDER_CLEANED] = True
    failures = await hass.async_add_executor_job(move_legacy_component_folder, hass)
    async_update_legacy_folder_cleanup_issue(hass, failures)


def async_schedule_legacy_folder_cleanup(hass: HomeAssistant) -> None:
    """Move stale legacy files in the background after startup-critical setup."""
    existing_task = hass.data.get(KEY_LEGACY_FOLDER_CLEANUP_TASK)
    if existing_task is not None and not existing_task.done():
        return

    task = hass.async_create_task(async_cleanup_legacy_component_folder(hass))
    hass.data[KEY_LEGACY_FOLDER_CLEANUP_TASK] = task

    def _legacy_folder_cleanup_done(done_task: Task[None]) -> None:
        hass.data.pop(KEY_LEGACY_FOLDER_CLEANUP_TASK, None)
        try:
            done_task.result()
        except CancelledError:
            return
        except Exception:
            _LOGGER.warning("Legacy folder cleanup failed", exc_info=True)
        if KEY_CONFIG_ENTRY in hass.http.app:
            hass.async_create_task(async_update_health_issue(hass))

    task.add_done_callback(_legacy_folder_cleanup_done)


def async_schedule_legacy_cleanup(hass: HomeAssistant) -> None:
    """Remove old-domain entries now and once Home Assistant has started."""
    async_remove_legacy_entries(hass)

    if hass.data.get(KEY_LEGACY_CLEANUP_SCHEDULED):
        return

    hass.data[KEY_LEGACY_CLEANUP_SCHEDULED] = True
    async_at_started(hass, async_remove_legacy_entries)
