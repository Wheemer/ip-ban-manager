"""Compatibility loader for old ban_allowlist config entries."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.ip_ban_manager.const import (
    CONF_ALLOWED_IPS,
    CONF_IP_ADDRESSES,
    DOMAIN as TARGET_DOMAIN,
)

DOMAIN = "ban_allowlist"

_LOGGER = logging.getLogger(__name__)


def _legacy_ip_addresses(entry: ConfigEntry) -> list[str]:
    """Return old allowlist entries from legacy data or options."""
    raw_addresses: Any = entry.options.get(
        CONF_ALLOWED_IPS,
        entry.options.get(
            CONF_IP_ADDRESSES,
            entry.data.get(CONF_ALLOWED_IPS, entry.data.get(CONF_IP_ADDRESSES, [])),
        ),
    )
    if isinstance(raw_addresses, str):
        return [raw_addresses]
    return list(raw_addresses or [])


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Import a legacy ban_allowlist entry into IP Ban Manager."""
    if not hass.config_entries.async_entries(TARGET_DOMAIN):
        _LOGGER.info("Migrating legacy ban_allowlist entry to IP Ban Manager")
        await hass.config_entries.flow.async_init(
            TARGET_DOMAIN,
            context={"source": SOURCE_IMPORT},
            data={CONF_IP_ADDRESSES: _legacy_ip_addresses(entry)},
        )

    async def _remove_legacy_entry() -> None:
        await hass.config_entries.async_remove(entry.entry_id)

    hass.async_create_task(_remove_legacy_entry())
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the compatibility entry."""
    return True
