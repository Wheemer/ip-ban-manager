"""Read-only status payloads for IP Ban Manager."""

from __future__ import annotations

from homeassistant.components.http.ban import (
    ATTR_BANNED_AT,
    KEY_BAN_MANAGER,
    KEY_FAILED_LOGIN_ATTEMPTS,
    IpBan,
)
from homeassistant.core import HomeAssistant

from .ban_ops import chronological_ip_bans
from .const import (
    ALLOWED_REGION_ANYWHERE,
    ATTR_ALLOWED_REGION_COUNTRY,
    ATTR_ALLOWED_REGION_MODE,
    ATTR_ALLOWED_REGION_SUBDIVISION,
    ATTR_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED,
    ATTR_ALLOWLISTED_LOGINS_CAN_BAN,
    ATTR_AUTO_BAN_ENABLED,
    ATTR_BAN_NOTIFICATIONS_ENABLED,
    ATTR_BANNED_IPS,
    ATTR_BLOCKED_NETWORKS,
    ATTR_DEFAULT_DENY_ENABLED,
    ATTR_FAILED_LOGIN_ATTEMPTS,
    ATTR_GEOIP_DATABASE_PRESENT,
    ATTR_GEOIP_ENABLED,
    ATTR_HEALTH,
    ATTR_IP_ADDRESS,
    ATTR_LOGIN_ATTEMPTS_THRESHOLD,
    ATTR_METRICS,
    ATTR_NATIVE_IP_BAN_ENABLED,
    ATTR_NETWORKS,
)
from .entry_helpers import (
    current_login_threshold,
    entry_allowed_region_country,
    entry_allowed_region_mode,
    entry_allowed_region_subdivision,
    entry_allowlisted_login_notifications_enabled,
    entry_allowlisted_logins_can_ban,
    entry_auto_ban_enabled,
    entry_ban_notifications_enabled,
    entry_default_deny_enabled,
    entry_geoip_enabled,
    entry_login_threshold,
    native_ip_banning_enabled,
)
from .file_store import geoip_database_path, path_is_file
from .geoip import geoip_location_for_ip
from .health import async_health_status, health_status
from .metrics import metrics
from .runtime_options import (
    CONF_CALLBACK_ROUTE_PROTECTION_ENABLED as ATTR_CALLBACK_ROUTE_PROTECTION_ENABLED,
    entry_callback_route_protection_enabled,
)
from .storage_keys import (
    KEY_ALLOWLIST,
    KEY_BLOCKED_NETWORKS,
    KEY_CONFIG_ENTRY,
    KEY_HEALTH,
)


def format_ip_ban(hass: HomeAssistant, ip_ban: IpBan) -> dict[str, str]:
    """Return a stable UI/API representation of a ban entry."""
    formatted = {
        ATTR_IP_ADDRESS: str(ip_ban.ip_address),
        ATTR_BANNED_AT: ip_ban.banned_at.isoformat(),
    }
    if location := geoip_location_for_ip(hass, ip_ban.ip_address):
        formatted["location"] = location
    return formatted


def current_status(
    hass: HomeAssistant,
    *,
    geoip_database_present: bool | None = None,
    health: dict[str, object] | None = None,
) -> dict[str, object]:
    """Return the live ban and allowlist status for UI surfaces."""
    ban_manager = hass.http.app.get(KEY_BAN_MANAGER)
    failed_attempts = hass.http.app.get(KEY_FAILED_LOGIN_ATTEMPTS, {})
    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    health = (
        health
        or hass.data.get(KEY_HEALTH)
        or health_status(hass, geoip_database_present=geoip_database_present)
    )
    if geoip_database_present is None:
        geoip_database_present = geoip_database_path(hass).is_file()
    return {
        ATTR_NATIVE_IP_BAN_ENABLED: native_ip_banning_enabled(hass),
        ATTR_AUTO_BAN_ENABLED: entry_auto_ban_enabled(entry) if entry else False,
        ATTR_BAN_NOTIFICATIONS_ENABLED: (
            entry_ban_notifications_enabled(entry) if entry else True
        ),
        ATTR_CALLBACK_ROUTE_PROTECTION_ENABLED: (
            entry_callback_route_protection_enabled(entry) if entry else True
        ),
        ATTR_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED: (
            entry_allowlisted_login_notifications_enabled(entry) if entry else True
        ),
        ATTR_ALLOWLISTED_LOGINS_CAN_BAN: (
            entry_allowlisted_logins_can_ban(entry) if entry else False
        ),
        ATTR_LOGIN_ATTEMPTS_THRESHOLD: (
            entry_login_threshold(entry, hass)
            if entry
            else current_login_threshold(hass)
        ),
        ATTR_NETWORKS: [
            str(network) for network in hass.http.app.get(KEY_ALLOWLIST, ())
        ],
        ATTR_BLOCKED_NETWORKS: [
            str(network) for network in hass.http.app.get(KEY_BLOCKED_NETWORKS, ())
        ],
        ATTR_DEFAULT_DENY_ENABLED: (
            entry_default_deny_enabled(entry) if entry else False
        ),
        ATTR_ALLOWED_REGION_MODE: (
            entry_allowed_region_mode(entry) if entry else ALLOWED_REGION_ANYWHERE
        ),
        ATTR_ALLOWED_REGION_COUNTRY: (
            entry_allowed_region_country(entry) if entry else ""
        ),
        ATTR_ALLOWED_REGION_SUBDIVISION: (
            entry_allowed_region_subdivision(entry) if entry else ""
        ),
        ATTR_GEOIP_ENABLED: entry_geoip_enabled(entry) if entry else False,
        ATTR_GEOIP_DATABASE_PRESENT: geoip_database_present,
        ATTR_BANNED_IPS: [
            format_ip_ban(hass, ip_ban)
            for ip_ban in (chronological_ip_bans(ban_manager) if ban_manager else ())
        ],
        ATTR_FAILED_LOGIN_ATTEMPTS: {
            str(ip): count
            for ip, count in sorted(
                failed_attempts.items(),
                key=lambda item: (item[0].version, item[0].packed),
            )
            if count
        },
        ATTR_HEALTH: health,
        ATTR_METRICS: dict(metrics(hass)),
    }


async def async_current_status(hass: HomeAssistant) -> dict[str, object]:
    """Return the live status without blocking the event loop on file checks."""
    geoip_database_present = await hass.async_add_executor_job(
        path_is_file, geoip_database_path(hass)
    )
    health = hass.data.get(KEY_HEALTH) or await async_health_status(hass)
    return current_status(
        hass,
        geoip_database_present=geoip_database_present,
        health=health,
    )
