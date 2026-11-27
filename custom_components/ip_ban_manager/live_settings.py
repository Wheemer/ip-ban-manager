"""Durable source of truth for IP Ban Manager settings."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError

from .const import CONF_LEGACY_ENTRY_ID, DOMAIN
from .file_store import atomic_write_text
from .storage_keys import KEY_CONFIG_ENTRY

REINSTALLED_FROM_DISK = "reinstalled_from_disk"
_LAST_SAVED_SETTINGS = "ip_ban_manager_last_saved_settings"
_LOGGER = logging.getLogger(__name__)


def settings_path(hass: HomeAssistant) -> Path:
    """Return the private live settings path, not the manual backup path."""
    return Path(hass.config.path(DOMAIN, "settings.json"))


async def async_load_settings(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Hydrate the runtime entry from disk, or use existing settings on upgrade."""
    hass.data.pop(_LAST_SAVED_SETTINGS, None)
    reinstall = entry.data.get(REINSTALLED_FROM_DISK) is True
    try:
        content = await hass.async_add_executor_job(
            settings_path(hass).read_text, "utf-8"
        )
    except FileNotFoundError as err:
        if not reinstall:
            return False
        raise ConfigEntryError(
            "IP Ban Manager settings file was removed during setup."
        ) from err
    except (OSError, ValueError) as err:
        raise ConfigEntryError(
            "IP Ban Manager could not read its settings file."
        ) from err
    try:
        payload = json.loads(content)
        if (
            not isinstance(payload, dict)
            or payload.get("domain") != DOMAIN
            or payload.get("format_version") != 1
            or not isinstance(payload.get("data"), dict)
            or not isinstance(payload.get("options"), dict)
        ):
            raise ValueError("Invalid live settings format.")
    except ValueError as err:
        raise ConfigEntryError(
            "IP Ban Manager settings file is invalid and was not changed."
        ) from err
    hass.config_entries.async_update_entry(
        entry,
        data={
            **payload["data"],
            **({REINSTALLED_FROM_DISK: True} if reinstall else {}),
        },
        options=payload["options"],
    )
    return reinstall


async def async_save_settings(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Atomically persist settled runtime settings to the live file."""
    lock = hass.data.setdefault("ip_ban_manager_configuration_lock", asyncio.Lock())
    async with lock:
        if hass.http.app.get(KEY_CONFIG_ENTRY) is not entry:
            return
        payload = {
            "domain": DOMAIN,
            "format_version": 1,
            "data": {
                key: value
                for key, value in entry.data.items()
                if key not in {REINSTALLED_FROM_DISK, CONF_LEGACY_ENTRY_ID}
            },
            "options": dict(entry.options),
        }
        try:
            content = json.dumps(payload, indent=2, sort_keys=True)
            if hass.data.get(_LAST_SAVED_SETTINGS) == (entry.entry_id, content):
                return
            await hass.async_add_executor_job(
                atomic_write_text, str(settings_path(hass)), content
            )
            hass.data[_LAST_SAVED_SETTINGS] = (entry.entry_id, content)
        except (OSError, TypeError, ValueError):
            _LOGGER.exception("Could not write IP Ban Manager live settings")


async def async_flush_settings(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Persist pending edits before unload without replacing unchanged settings."""
    if _LAST_SAVED_SETTINGS in hass.data:
        await async_save_settings(hass, entry)


def notify_restored_restrictions(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Notify only when a reinstall has activated saved restrictions."""
    from .entry_helpers import (
        entry_allowlisted_logins_can_ban,
        entry_blocked_networks,
        entry_default_deny_enabled,
    )
    from .nginx_proxy_manager import entry_npm_config
    from .region_rules import entry_public_region_settings

    if (
        entry_public_region_settings(hass, entry)["public_region_enabled"]
        or entry_default_deny_enabled(entry)
        or entry_blocked_networks(entry)
        or entry_allowlisted_logins_can_ban(entry)
        or entry_npm_config(entry).get("enabled")
    ):
        persistent_notification.async_create(
            hass,
            "IP Ban Manager loaded your saved settings, including enabled access "
            "restrictions. Review Blocking and Settings if your access requirements changed.",
            title="IP Ban Manager restored access restrictions",
            notification_id="ip_ban_manager_recovery",
        )
