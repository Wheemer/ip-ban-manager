"""Compatibility loader that migrates older installs to IP Ban Manager."""

from __future__ import annotations

import asyncio
import logging

import voluptuous as vol
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from custom_components.ip_ban_manager.const import (
    CONF_IP_ADDRESSES,
)
from custom_components.ip_ban_manager.const import DOMAIN as NEW_DOMAIN

_LOGGER = logging.getLogger(__name__)

DOMAIN = "ban_allowlist"
ENTRY_TITLE = "IP Ban Manager"

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_IP_ADDRESSES): vol.All(cv.ensure_list, [cv.string]),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


def _new_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """Return the migrated config entry, if it already exists."""
    entries = hass.config_entries.async_entries(NEW_DOMAIN)
    return entries[0] if entries else None


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Import legacy YAML into the new integration domain."""
    if DOMAIN in config and _new_entry(hass) is None:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                NEW_DOMAIN,
                context={"source": SOURCE_IMPORT},
                data=dict(config[DOMAIN]),
            )
        )
    return True


async def _async_remove_legacy_entry(hass: HomeAssistant, entry_id: str) -> None:
    """Remove the legacy entry after its setup lifecycle has yielded."""
    await asyncio.sleep(0)
    await hass.config_entries.async_remove(entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Move a legacy config entry to the new integration domain."""
    migrated = _new_entry(hass)
    if migrated is None:
        import_data = {
            CONF_IP_ADDRESSES: entry.options.get(
                CONF_IP_ADDRESSES, entry.data.get(CONF_IP_ADDRESSES, [])
            )
        }
        await hass.config_entries.flow.async_init(
            NEW_DOMAIN,
            context={"source": SOURCE_IMPORT},
            data=import_data,
        )
        migrated = _new_entry(hass)
        _LOGGER.info("Migrated IP Ban Manager config entry to %s", NEW_DOMAIN)

    if migrated is not None:
        hass.config_entries.async_update_entry(
            migrated,
            title=ENTRY_TITLE,
            options={**migrated.options, **entry.options},
        )

    hass.async_create_task(_async_remove_legacy_entry(hass, entry.entry_id))
    return True
