"""Config-entry option helpers for IP Ban Manager."""

from __future__ import annotations

from typing import Any

from homeassistant.components.http.ban import KEY_BAN_MANAGER, KEY_LOGIN_THRESHOLD
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ALLOWED_IPS,
    CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED,
    CONF_ALLOWLISTED_LOGINS_CAN_BAN,
    CONF_AUTO_BAN_ENABLED,
    CONF_BAN_NOTIFICATIONS_ENABLED,
    CONF_BLOCKED_NETWORKS,
    CONF_DEFAULT_DENY_ENABLED,
    CONF_GEOIP_ENABLED,
    CONF_IP_ADDRESSES,
    CONF_LOGIN_ATTEMPTS_THRESHOLD,
    CONF_SIDEBAR_PANEL_ENABLED,
    DEFAULT_LOGIN_ATTEMPTS_THRESHOLD,
    MAX_LOGIN_ATTEMPTS_THRESHOLD,
)
from .ip_utils import parse_allowlist_network
from .metrics import mark_config_write
from .storage_keys import KEY_CONFIG_ENTRY, IPNetwork

DEFAULT_SIDEBAR_PANEL_ENABLED = True


def parse_allowlist(ip_addresses: list[str]) -> tuple[IPNetwork, ...]:
    """Parse configured IP addresses and networks."""
    return tuple(parse_allowlist_network(ip) for ip in ip_addresses)


def parse_blocked_networks(networks: list[str]) -> tuple[IPNetwork, ...]:
    """Parse configured blocked networks."""
    return tuple(parse_allowlist_network(network) for network in networks)


def entry_ip_addresses(entry: ConfigEntry) -> list[str]:
    """Return the configured allowlist for a config entry."""
    return entry.options.get(
        CONF_IP_ADDRESSES,
        entry.options.get(CONF_ALLOWED_IPS, entry.data.get(CONF_IP_ADDRESSES, [])),
    )


def entry_blocked_networks(entry: ConfigEntry) -> list[str]:
    """Return configured blocked network strings for a config entry."""
    return entry.options.get(
        CONF_BLOCKED_NETWORKS,
        entry.data.get(CONF_BLOCKED_NETWORKS, []),
    )


def entry_default_deny_enabled(entry: ConfigEntry) -> bool:
    """Return whether addresses outside the allowlist should be blocked."""
    return bool(
        entry.options.get(
            CONF_DEFAULT_DENY_ENABLED,
            entry.data.get(CONF_DEFAULT_DENY_ENABLED, False),
        )
    )


def native_ip_banning_enabled(hass: HomeAssistant) -> bool:
    """Return whether Home Assistant loaded its native IP ban manager."""
    return hass.http is not None and KEY_BAN_MANAGER in hass.http.app


def entry_auto_ban_enabled(entry: ConfigEntry) -> bool:
    """Return whether automatic IP bans should be active when HA supports them."""
    return bool(
        entry.options.get(
            CONF_AUTO_BAN_ENABLED,
            entry.data.get(CONF_AUTO_BAN_ENABLED, True),
        )
    )


def entry_ban_notifications_enabled(entry: ConfigEntry) -> bool:
    """Return whether automatic IP ban/login notifications should remain."""
    return bool(
        entry.options.get(
            CONF_BAN_NOTIFICATIONS_ENABLED,
            entry.data.get(CONF_BAN_NOTIFICATIONS_ENABLED, True),
        )
    )


def entry_allowlisted_login_notifications_enabled(entry: ConfigEntry) -> bool:
    """Return whether allowlisted failed logins should notify immediately."""
    return bool(
        entry.options.get(
            CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED,
            entry.data.get(CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED, True),
        )
    )


def entry_allowlisted_logins_can_ban(entry: ConfigEntry) -> bool:
    """Return whether failed logins from allowlisted sources can become exact bans."""
    return bool(
        entry.options.get(
            CONF_ALLOWLISTED_LOGINS_CAN_BAN,
            entry.data.get(CONF_ALLOWLISTED_LOGINS_CAN_BAN, False),
        )
    )


def entry_sidebar_panel_enabled(entry: ConfigEntry) -> bool:
    """Return whether the IP Ban Manager sidebar panel should be registered."""
    return bool(
        entry.options.get(
            CONF_SIDEBAR_PANEL_ENABLED,
            entry.data.get(CONF_SIDEBAR_PANEL_ENABLED, DEFAULT_SIDEBAR_PANEL_ENABLED),
        )
    )


def entry_geoip_enabled(entry: ConfigEntry) -> bool:
    """Return whether local GeoIP labels should be shown when a database exists."""
    return bool(
        entry.options.get(
            CONF_GEOIP_ENABLED,
            entry.data.get(CONF_GEOIP_ENABLED, False),
        )
    )


def allowlisted_logins_can_ban(hass: HomeAssistant) -> bool:
    """Return whether the current entry allows exact bans inside the allowlist."""
    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    return entry_allowlisted_logins_can_ban(entry) if entry else False


def current_login_threshold(hass: HomeAssistant) -> int:
    """Return Home Assistant's current live login-attempt threshold."""
    if hass.http is None:
        return DEFAULT_LOGIN_ATTEMPTS_THRESHOLD
    return normalize_login_attempts_threshold(
        hass.http.app.get(KEY_LOGIN_THRESHOLD, DEFAULT_LOGIN_ATTEMPTS_THRESHOLD)
    )


def normalize_login_attempts_threshold(value: Any) -> int:
    """Return a login-attempt threshold inside the supported backend range."""
    return min(MAX_LOGIN_ATTEMPTS_THRESHOLD, max(0, int(value)))


def entry_login_threshold(entry: ConfigEntry, hass: HomeAssistant) -> int:
    """Return the configured login-attempt threshold for a config entry."""
    return normalize_login_attempts_threshold(
        entry.options.get(
            CONF_LOGIN_ATTEMPTS_THRESHOLD,
            entry.data.get(
                CONF_LOGIN_ATTEMPTS_THRESHOLD, current_login_threshold(hass)
            ),
        )
    )


def effective_login_threshold(entry: ConfigEntry, hass: HomeAssistant) -> int:
    """Return the live threshold to apply to Home Assistant."""
    if not entry_auto_ban_enabled(entry):
        return 0
    return entry_login_threshold(entry, hass)


def update_entry_options(hass: HomeAssistant, **updates: object) -> ConfigEntry:
    """Persist config-entry options without dropping unrelated settings."""
    entry = hass.http.app[KEY_CONFIG_ENTRY]
    options = {**entry.options, **updates}
    if options == entry.options:
        return entry

    hass.config_entries.async_update_entry(entry, options=options)
    mark_config_write(hass)
    return entry
