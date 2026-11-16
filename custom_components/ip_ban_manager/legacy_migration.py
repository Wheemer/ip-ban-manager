"""Legacy domain and folder cleanup for IP Ban Manager."""

from __future__ import annotations

import asyncio
import logging
import shutil
from asyncio import CancelledError, Task
from collections.abc import Callable, Coroutine
from contextlib import suppress
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry, UnknownEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import UNDEFINED
from homeassistant.util import dt as dt_util

from .const import CONF_BANNED_IPS, CONF_LEGACY_ENTRY_ID, DOMAIN, LEGACY_DOMAIN
from .health import async_update_health_issue, async_update_legacy_folder_cleanup_issue
from .storage_keys import (
    KEY_CONFIG_ENTRY,
    KEY_LEGACY_CLEANUP_SCHEDULED,
    KEY_LEGACY_FOLDER_CLEANED,
    KEY_LEGACY_FOLDER_CLEANUP_TASK,
)

_LOGGER = logging.getLogger(__name__)
_LEGACY_TASKS = "ip_ban_manager_legacy_tasks"
_LEGACY_START_HANDLES = "ip_ban_manager_legacy_start_handles"


def _legacy_task(hass: HomeAssistant, work: Coroutine[Any, Any, None]) -> None:
    """Track migration work so unload can drain it."""
    tasks = hass.data.setdefault(_LEGACY_TASKS, set())
    task = hass.async_create_task(work)
    tasks.add(task)
    task.add_done_callback(tasks.discard)


def _legacy_at_started(
    hass: HomeAssistant, action: Callable[[HomeAssistant], Any]
) -> None:
    """Retain startup subscriptions and any coroutine they start."""

    @callback
    def run(instance: HomeAssistant) -> None:
        result = action(instance)
        if asyncio.iscoroutine(result):
            _legacy_task(instance, result)

    hass.data.setdefault(_LEGACY_START_HANDLES, []).append(async_at_started(hass, run))


async def async_stop_legacy_work(hass: HomeAssistant) -> None:
    """Cancel only work owned by this integration's current runtime."""
    for remove in hass.data.pop(_LEGACY_START_HANDLES, []):
        remove()
    if not hass.is_running:
        hass.data.pop(KEY_LEGACY_CLEANUP_SCHEDULED, None)
    tasks: set[Task[None]] = hass.data.pop(_LEGACY_TASKS, set())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


ENTRY_TITLE = "IP Ban Manager"
LEGACY_ENTRY_TITLES = {"IP Ban Allowlist", "ban_allowlist"}
LEGACY_BACKUP_DIR = "ip_ban_manager_legacy_backup"
LEGACY_CLEANUP_DIR = ".cleanup"

SENSOR_ENTITY_IDS = {
    "active_bans": "sensor.ip_ban_manager_active_bans",
    "allowlisted_networks": "sensor.ip_ban_manager_allowlisted_networks",
    "blocked_networks": "sensor.ip_ban_manager_blocked_networks",
    "failed_login_sources": "sensor.ip_ban_manager_failed_login_sources",
}


async def _async_clear_legacy_statistics_unit(
    hass: HomeAssistant, statistic_id: str
) -> None:
    """Normalize the empty unit used by older count sensors."""
    try:
        from homeassistant.components.recorder.statistics import (
            async_update_statistics_metadata,
        )
    except ImportError:
        # Home Assistant 2024.6 predates the async convenience wrapper.
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import (
            update_statistics_metadata,
        )

        instance = get_instance(hass)
        await instance.async_add_executor_job(
            update_statistics_metadata,
            instance,
            statistic_id,
            UNDEFINED,
            None,
        )
        return

    async_update_statistics_metadata(
        hass,
        statistic_id,
        new_unit_class=None,
        new_unit_of_measurement=None,
    )


def _migrate_sensor_entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> set[str]:
    """Reclaim legacy sensor IDs and return every statistics ID to normalize."""
    registry = er.async_get(hass)
    statistic_ids = set(SENSOR_ENTITY_IDS.values())

    for key, desired_entity_id in SENSOR_ENTITY_IDS.items():
        unique_id = f"{entry.entry_id}_{key}"
        current_entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
        if current_entity_id is not None:
            statistic_ids.add(current_entity_id)

        if (
            current_entity_id is not None
            and current_entity_id != desired_entity_id
            and registry.async_get(desired_entity_id) is None
        ):
            registry.async_update_entity(
                current_entity_id, new_entity_id=desired_entity_id
            )
            _LOGGER.info(
                "Restored IP Ban Manager sensor entity ID %s to %s",
                current_entity_id,
                desired_entity_id,
            )

    return statistic_ids


async def _async_migrate_sensor_statistics(
    hass: HomeAssistant, statistic_ids: set[str]
) -> None:
    """Normalize statistics units left by older sensor versions."""
    if "recorder" in hass.config.components:
        for statistic_id in statistic_ids:
            # Updating a statistic ID that has no metadata is a harmless no-op.
            await _async_clear_legacy_statistics_unit(hass, statistic_id)
    else:
        _LOGGER.debug(
            "Skipping legacy statistics-unit migration because Recorder is not loaded"
        )

    issue_registry = ir.async_get(hass)
    for issue in list(issue_registry.issues.values()):
        if issue.domain == "sensor" and issue.issue_id.startswith(
            "units_changed_sensor.ip_ban_manager_"
        ):
            ir.async_delete_issue(hass, issue.domain, issue.issue_id)


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

    statistic_ids = _migrate_sensor_entity_ids(hass, entry)

    async def _migrate_sensor_statistics(_hass: HomeAssistant) -> None:
        await _async_migrate_sensor_statistics(_hass, statistic_ids)

    _legacy_at_started(hass, _migrate_sensor_statistics)

    if isinstance(legacy_entry_id, str):
        legacy_entry = hass.config_entries.async_get_entry(legacy_entry_id)
        if legacy_entry is not None and legacy_entry.domain == LEGACY_DOMAIN:
            _LOGGER.info("Removing migrated legacy ban_allowlist config entry")

            async def _remove_migrated_legacy_entry() -> None:
                if hass.config_entries.async_get_entry(legacy_entry_id) is None:
                    return
                with suppress(UnknownEntry):
                    await hass.config_entries.async_remove(legacy_entry_id)

            _legacy_task(hass, _remove_migrated_legacy_entry())


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

        _legacy_task(hass, _remove_legacy_entry())


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
    job = hass.async_add_executor_job(move_legacy_component_folder, hass)
    try:
        failures = await asyncio.shield(job)
    except CancelledError:
        await job
        raise
    hass.data[KEY_LEGACY_FOLDER_CLEANED] = not failures
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
    _legacy_at_started(hass, async_remove_legacy_entries)
