"""Dependency-light runtime option helpers for hot-reloaded modules."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry

CONF_CALLBACK_ROUTE_PROTECTION_ENABLED = "callback_route_protection_enabled"


def entry_callback_route_protection_enabled(entry: ConfigEntry) -> bool:
    """Return whether managed rules should protect HA callback routes."""
    return bool(
        entry.options.get(
            CONF_CALLBACK_ROUTE_PROTECTION_ENABLED,
            entry.data.get(CONF_CALLBACK_ROUTE_PROTECTION_ENABLED, True),
        )
    )
