"""Bundled panel registration and payload helpers."""

from __future__ import annotations

import importlib
from ipaddress import ip_address
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    ALLOWED_REGION_ANYWHERE,
    ATTR_BACKUP,
    ATTR_LAST_EXPORT,
    CONF_ALLOWED_REGION_COUNTRY,
    CONF_ALLOWED_REGION_MODE,
    CONF_ALLOWED_REGION_SUBDIVISION,
    CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED,
    CONF_ALLOWLISTED_LOGINS_CAN_BAN,
    CONF_AUTO_BAN_ENABLED,
    CONF_BAN_NOTIFICATIONS_ENABLED,
    CONF_BLOCKED_NETWORKS,
    CONF_DEFAULT_DENY_ENABLED,
    CONF_GEOIP_ENABLED,
    CONF_IP_ADDRESSES,
    CONF_LOGIN_ATTEMPTS_THRESHOLD,
    CONF_REGIONAL_LOGIN_THRESHOLDS,
    CONF_SIDEBAR_PANEL_ENABLED,
    CONF_SILENCED_ALLOWLISTED_LOGIN_IPS,
    DOMAIN,
)
from .entry_helpers import (
    coerce_allowed_region_options,
    entry_allowed_region_country,
    entry_allowed_region_mode,
    entry_allowed_region_subdivision,
    entry_allowlisted_login_notifications_enabled,
    entry_allowlisted_logins_can_ban,
    entry_auto_ban_enabled,
    entry_ban_notifications_enabled,
    entry_blocked_networks,
    entry_default_deny_enabled,
    entry_geoip_enabled,
    entry_ip_addresses,
    entry_login_threshold,
    entry_regional_login_thresholds,
    entry_sidebar_panel_enabled,
    normalize_login_attempts_threshold,
    normalize_regional_login_thresholds,
    update_entry_options,
)
from .entry_meta import (
    entry_allowlist_meta,
    entry_blocked_network_meta,
    format_network_entries,
)
from .file_store import (
    config_export_path,
    file_updated,
    geoip_database_path,
    ha_config_relative_path,
    path_is_file,
)
from .geoip import (
    async_local_geoip_region,
    async_prepare_geoip_reader,
    close_geoip_reader,
    geoip_status,
)
from .i18n import async_load_panel_translations, async_normalize_language
from .legacy_migration import ENTRY_TITLE
from .network_policy import (
    apply_ban_settings,
    apply_blocked_networks,
    async_validate_panel_network_safety,
    current_allowlist_strings,
    current_blocked_network_strings,
)
from .nginx_proxy_manager import npm_panel_status, schedule_npm_sync
from .notifications import (
    NOTIFICATION_ICON_URL,
    entry_silenced_allowlisted_login_ip_strings,
    silence_allowlisted_login_notifications,
    unsilence_allowlisted_login_notifications,
)
from .panel_assets import (
    PANEL_WEB_COMPONENT,
    async_integration_version,
    async_panel_js_url,
)
from .runtime_options import (
    CONF_CALLBACK_ROUTE_PROTECTION_ENABLED,
    entry_callback_route_protection_enabled,
)
from .status import async_current_status
from .storage_keys import (
    KEY_CONFIG_ENTRY,
    KEY_PANEL_MODULE_URL,
    KEY_PANEL_REGISTERED,
    KEY_PANEL_SIDEBAR_ENABLED,
    KEY_STATIC_PATH_REGISTERED,
)


async def async_panel_payload(
    hass: HomeAssistant, entry: ConfigEntry, *, language: str | None = None
) -> dict[str, object]:
    """Return the complete JSON payload used by the bundled panel."""
    resolved_language = await async_normalize_language(hass, language)
    translations = await async_load_panel_translations(hass, resolved_language)
    backup_status = await hass.async_add_executor_job(_backup_status, hass)
    geoip = await hass.async_add_executor_job(geoip_status, hass, entry)
    geoip["local_region"] = await async_local_geoip_region(hass)
    version = await async_integration_version(hass)
    return {
        "ok": True,
        "version": version,
        "language": resolved_language,
        "translations": translations,
        "status": await async_current_status(hass),
        "settings": {
            CONF_IP_ADDRESSES: entry_ip_addresses(entry),
            CONF_BLOCKED_NETWORKS: entry_blocked_networks(entry),
            "allowlist_entries": format_network_entries(
                entry_ip_addresses(entry), entry_allowlist_meta(entry)
            ),
            "blocked_network_entries": format_network_entries(
                entry_blocked_networks(entry), entry_blocked_network_meta(entry)
            ),
            CONF_AUTO_BAN_ENABLED: entry_auto_ban_enabled(entry),
            CONF_BAN_NOTIFICATIONS_ENABLED: entry_ban_notifications_enabled(entry),
            CONF_CALLBACK_ROUTE_PROTECTION_ENABLED: (
                entry_callback_route_protection_enabled(entry)
            ),
            CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED: (
                entry_allowlisted_login_notifications_enabled(entry)
            ),
            CONF_ALLOWLISTED_LOGINS_CAN_BAN: entry_allowlisted_logins_can_ban(entry),
            CONF_DEFAULT_DENY_ENABLED: entry_default_deny_enabled(entry),
            CONF_ALLOWED_REGION_MODE: entry_allowed_region_mode(entry),
            CONF_ALLOWED_REGION_COUNTRY: entry_allowed_region_country(entry),
            CONF_ALLOWED_REGION_SUBDIVISION: entry_allowed_region_subdivision(entry),
            CONF_LOGIN_ATTEMPTS_THRESHOLD: entry_login_threshold(entry, hass),
            CONF_REGIONAL_LOGIN_THRESHOLDS: entry_regional_login_thresholds(entry),
            CONF_SIDEBAR_PANEL_ENABLED: entry_sidebar_panel_enabled(entry),
            CONF_GEOIP_ENABLED: entry_geoip_enabled(entry),
            CONF_SILENCED_ALLOWLISTED_LOGIN_IPS: (
                entry_silenced_allowlisted_login_ip_strings(entry)
            ),
        },
        "geoip": geoip,
        "nginx_proxy_manager": npm_panel_status(hass, entry),
        ATTR_BACKUP: backup_status,
    }


def _backup_status(hass: HomeAssistant) -> dict[str, object]:
    """Return on-disk export file status for the bundled panel."""
    export_path = config_export_path(hass)
    return {
        "path": ha_config_relative_path(export_path),
        "exists": export_path.is_file(),
        ATTR_LAST_EXPORT: file_updated(export_path),
    }


def coerce_panel_boolean(value: object) -> bool:
    """Parse booleans sent by the bundled panel."""
    return cv.boolean(value)


def _config_entry(hass: HomeAssistant) -> ConfigEntry:
    """Return the active config entry, tolerating a prior AppKey hot reload."""
    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    if isinstance(entry, ConfigEntry):
        return entry

    for key, value in hass.http.app.items():
        if "ip_ban_manager_config_entry" in repr(key) and isinstance(
            value, ConfigEntry
        ):
            hass.http.app[KEY_CONFIG_ENTRY] = value
            return value

    raise KeyError(KEY_CONFIG_ENTRY)


async def async_panel_set_options(hass: HomeAssistant, options: object) -> None:
    """Persist and apply panel-managed booleans and threshold."""
    if not isinstance(options, dict):
        raise HomeAssistantError("Options must be a JSON object.")

    npm_enabled = options.get("npm_edge_protection_enabled")
    entry = _config_entry(hass)
    current_options = {
        CONF_AUTO_BAN_ENABLED: entry_auto_ban_enabled(entry),
        CONF_BAN_NOTIFICATIONS_ENABLED: entry_ban_notifications_enabled(entry),
        CONF_CALLBACK_ROUTE_PROTECTION_ENABLED: (
            entry_callback_route_protection_enabled(entry)
        ),
        CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED: (
            entry_allowlisted_login_notifications_enabled(entry)
        ),
        CONF_ALLOWLISTED_LOGINS_CAN_BAN: entry_allowlisted_logins_can_ban(entry),
        CONF_DEFAULT_DENY_ENABLED: entry_default_deny_enabled(entry),
        CONF_ALLOWED_REGION_MODE: entry_allowed_region_mode(entry),
        CONF_ALLOWED_REGION_COUNTRY: entry_allowed_region_country(entry),
        CONF_ALLOWED_REGION_SUBDIVISION: entry_allowed_region_subdivision(entry),
        CONF_GEOIP_ENABLED: entry_geoip_enabled(entry),
        CONF_LOGIN_ATTEMPTS_THRESHOLD: entry_login_threshold(entry, hass),
        CONF_REGIONAL_LOGIN_THRESHOLDS: entry_regional_login_thresholds(entry),
        CONF_SIDEBAR_PANEL_ENABLED: entry_sidebar_panel_enabled(entry),
    }
    for key in (
        CONF_AUTO_BAN_ENABLED,
        CONF_BAN_NOTIFICATIONS_ENABLED,
        CONF_CALLBACK_ROUTE_PROTECTION_ENABLED,
        CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED,
        CONF_ALLOWLISTED_LOGINS_CAN_BAN,
        CONF_DEFAULT_DENY_ENABLED,
        CONF_GEOIP_ENABLED,
        CONF_SIDEBAR_PANEL_ENABLED,
    ):
        if key in options:
            current_options[key] = coerce_panel_boolean(options[key])

    if CONF_LOGIN_ATTEMPTS_THRESHOLD in options:
        current_options[CONF_LOGIN_ATTEMPTS_THRESHOLD] = (
            normalize_login_attempts_threshold(options[CONF_LOGIN_ATTEMPTS_THRESHOLD])
        )
    if CONF_REGIONAL_LOGIN_THRESHOLDS in options:
        current_options[CONF_REGIONAL_LOGIN_THRESHOLDS] = (
            normalize_regional_login_thresholds(options[CONF_REGIONAL_LOGIN_THRESHOLDS])
        )
    current_options.update(
        coerce_allowed_region_options(
            {
                CONF_ALLOWED_REGION_MODE: current_options[CONF_ALLOWED_REGION_MODE],
                CONF_ALLOWED_REGION_COUNTRY: current_options[
                    CONF_ALLOWED_REGION_COUNTRY
                ],
                CONF_ALLOWED_REGION_SUBDIVISION: current_options[
                    CONF_ALLOWED_REGION_SUBDIVISION
                ],
                **{
                    key: options[key]
                    for key in (
                        CONF_ALLOWED_REGION_MODE,
                        CONF_ALLOWED_REGION_COUNTRY,
                        CONF_ALLOWED_REGION_SUBDIVISION,
                    )
                    if key in options
                },
            }
        )
    )
    if (
        current_options[CONF_ALLOWED_REGION_MODE] != ALLOWED_REGION_ANYWHERE
        or current_options[CONF_REGIONAL_LOGIN_THRESHOLDS]
    ):
        current_options[CONF_GEOIP_ENABLED] = True

    await async_validate_panel_network_safety(
        hass,
        current_allowlist_strings(hass),
        current_blocked_network_strings(hass),
        bool(current_options[CONF_DEFAULT_DENY_ENABLED]),
    )
    if current_options[CONF_GEOIP_ENABLED]:
        geoip_path = geoip_database_path(hass)
        if await hass.async_add_executor_job(path_is_file, geoip_path):
            await async_prepare_geoip_reader(hass)
    else:
        close_geoip_reader(hass)
    entry = update_entry_options(hass, **current_options)
    apply_ban_settings(hass, entry)
    apply_blocked_networks(hass, entry)
    if npm_enabled is None:
        schedule_npm_sync(hass)
    else:
        npm_manager = importlib.import_module(
            "custom_components.ip_ban_manager.nginx_proxy_manager"
        )
        if coerce_panel_boolean(npm_enabled):
            await npm_manager.async_enable_npm(hass)
        else:
            await npm_manager.async_disable_npm(hass)
    await async_register_panel(
        hass, sidebar_enabled=bool(current_options[CONF_SIDEBAR_PANEL_ENABLED])
    )


def panel_silence_allowlisted_login_notification(
    hass: HomeAssistant,
    ip_address_value: str,
    notification_id: object,
) -> None:
    """Silence allowlisted login notifications from a panel action link."""
    entry = _config_entry(hass)
    try:
        remote_addr = ip_address(ip_address_value)
    except ValueError as err:
        raise HomeAssistantError("Invalid IP address.") from err

    silence_allowlisted_login_notifications(
        hass,
        entry,
        remote_addr,
        notification_id if isinstance(notification_id, str) else None,
    )


def panel_unsilence_allowlisted_login_notification(
    hass: HomeAssistant,
    ip_address_value: str,
) -> None:
    """Unsilence allowlisted login notifications from the admin panel API."""
    entry = _config_entry(hass)
    try:
        remote_addr = ip_address(ip_address_value)
    except ValueError as err:
        raise HomeAssistantError("Invalid IP address.") from err

    unsilence_allowlisted_login_notifications(hass, entry, remote_addr)


async def async_register_static_assets(hass: HomeAssistant) -> None:
    """Register stable local URLs for notification assets."""
    if hass.http.app.get(KEY_STATIC_PATH_REGISTERED):
        return
    if _http_route_registered(hass, NOTIFICATION_ICON_URL):
        hass.http.app[KEY_STATIC_PATH_REGISTERED] = True
        return

    hass.http.app[KEY_STATIC_PATH_REGISTERED] = True
    icon_path = str(Path(__file__).with_name("icon.png"))
    try:
        if hasattr(hass.http, "async_register_static_paths"):
            from homeassistant.components.http import StaticPathConfig

            await hass.http.async_register_static_paths(
                [
                    StaticPathConfig(
                        NOTIFICATION_ICON_URL,
                        icon_path,
                        cache_headers=True,
                    ),
                ]
            )
        else:
            register_static_path = getattr(hass.http, "register_static_path")
            register_static_path(
                NOTIFICATION_ICON_URL,
                icon_path,
                cache_headers=True,
            )
    except Exception:
        hass.http.app.pop(KEY_STATIC_PATH_REGISTERED, None)
        raise


async def async_register_panel(
    hass: HomeAssistant, *, sidebar_enabled: bool = True
) -> None:
    """Register the bundled IP Ban Manager panel."""
    module_url = await async_panel_js_url(hass)
    if (
        hass.data.get(KEY_PANEL_REGISTERED)
        and hass.data.get(KEY_PANEL_SIDEBAR_ENABLED) == sidebar_enabled
        and hass.data.get(KEY_PANEL_MODULE_URL) == module_url
    ):
        return

    if hass.data.get(KEY_PANEL_REGISTERED):
        async_remove_panel(hass)

    from homeassistant.components import frontend

    frontend.async_remove_panel(hass, DOMAIN, warn_if_unknown=False)

    from homeassistant.components import panel_custom

    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=DOMAIN,
        webcomponent_name=PANEL_WEB_COMPONENT,
        sidebar_title=ENTRY_TITLE if sidebar_enabled else None,
        sidebar_icon="mdi:shield-lock-outline" if sidebar_enabled else None,
        module_url=module_url,
        require_admin=True,
        config_panel_domain=DOMAIN,
    )
    hass.data[KEY_PANEL_REGISTERED] = True
    hass.data[KEY_PANEL_SIDEBAR_ENABLED] = sidebar_enabled
    hass.data[KEY_PANEL_MODULE_URL] = module_url


def async_remove_panel(hass: HomeAssistant) -> None:
    """Remove the bundled panel during unload."""
    if not hass.data.pop(KEY_PANEL_REGISTERED, False):
        return
    hass.data.pop(KEY_PANEL_SIDEBAR_ENABLED, None)
    hass.data.pop(KEY_PANEL_MODULE_URL, None)

    from homeassistant.components import frontend

    frontend.async_remove_panel(hass, DOMAIN, warn_if_unknown=False)


def _http_route_registered(hass: HomeAssistant, url: str) -> bool:
    """Return whether a URL path already has an HTTP route."""
    for route in hass.http.app.router.routes():
        resource = route.resource
        if resource is not None and resource.canonical == url:
            return True
    return False
