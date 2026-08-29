"""The IP Ban Manager integration."""

# flake8: noqa: F401

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from types import ModuleType
from typing import Any

from homeassistant.components.http.ban import KEY_BAN_MANAGER, IpBanManager
from homeassistant.config_entries import SOURCE_IMPORT as HA_SOURCE_IMPORT
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .backup import (
    CONFIG_EXPORT_FORMAT_VERSION,
)
from .backup import (
    async_apply_config_backup_payload as _async_apply_config_backup_payload,
)
from .backup import async_export_config as _async_export_config
from .backup import async_import_config as _async_import_config
from .backup import async_import_config_from_yaml as _async_import_config_from_yaml
from .backup import async_restore_exact_bans as _async_restore_exact_bans
from .backup import config_download_payload as _config_download_payload
from .backup import config_export_payload as _config_export_payload
from .ban_lookup import NetworkAwareBanLookup, _supervisor_internal_networks
from .ban_ops import (
    async_remove_allowlisted_ip_bans as _async_remove_allowlisted_ip_bans,
)
from .ban_ops import async_replace_ip_bans as _async_replace_ip_bans
from .ban_ops import ban_manager as _ban_manager
from .ban_ops import ip_ban_file_payload as _ip_ban_file_payload
from .const import (
    CONF_IP_ADDRESSES,
    DOMAIN,
    LEGACY_DOMAIN,
    SERVICE_ADD_ALLOWLIST_NETWORK,
    SERVICE_ADD_BLOCKED_NETWORK,
    SERVICE_ADD_IP_BAN,
    SERVICE_EXPORT_CONFIG,
    SERVICE_IMPORT_CONFIG,
    SERVICE_REMOVE_ALL_IP_BANS,
    SERVICE_REMOVE_ALLOWLIST_NETWORK,
    SERVICE_REMOVE_BLOCKED_NETWORK,
    SERVICE_REMOVE_IP_BAN,
    SERVICE_UPDATE_GEOIP,
)
from .entry_helpers import (
    entry_allowlisted_login_notifications_enabled as _entry_allowlisted_login_notifications_enabled,
)
from .entry_helpers import entry_geoip_enabled as _entry_geoip_enabled
from .entry_helpers import entry_ip_addresses as _entry_ip_addresses
from .entry_helpers import entry_sidebar_panel_enabled as _entry_sidebar_panel_enabled
from .entry_helpers import parse_allowlist as _parse_allowlist
from .geoip import close_geoip_reader as _close_geoip_reader
from .geoip_lifecycle import (
    async_schedule_geoip_reader_prepare as _async_schedule_geoip_reader_prepare,
)
from .health import (
    INTEGRATION_DISABLED_BY_YAML_ISSUE_ID,
    IP_BAN_DISABLED_ISSUE_ID,
    LEGACY_FOLDER_CLEANUP_FAILED_ISSUE_ID,
    LEGACY_YAML_PRESENT_ISSUE_ID,
)
from .health import (
    async_create_ip_ban_disabled_issue as _async_create_ip_ban_disabled_issue,
)
from .health import (
    async_delete_ip_ban_disabled_issue as _async_delete_ip_ban_disabled_issue,
)
from .health import (
    async_update_emergency_disabled_issue as _async_update_emergency_disabled_issue,
)
from .health import async_update_health_issue as _async_update_health_issue
from .health import async_update_legacy_yaml_issue as _async_update_legacy_yaml_issue
from .http_patches import (
    _ORIGINAL_PROCESS_WRONG_LOGIN,
    _allowlist_process_wrong_login,
    _async_handle_standard_wrong_login,
    _process_allowlisted_wrong_login,
    _request_remote_ip,
)
from .http_patches import install_add_ban_patch as _install_add_ban_patch
from .http_patches import install_load_bans_patch as _install_load_bans_patch
from .http_patches import install_wrong_login_patch as _install_wrong_login_patch
from .http_patches import uninstall_patches as _uninstall_patches
from .http_views import (
    IPBanManagerManageView,
    IPBanManagerPanelView,
    IPBanManagerStatusView,
    SilenceAllowlistedLoginNotificationsView,
)
from .http_views import register_http_views as _register_http_views
from .http_views import unregister_http_views as _unregister_http_views
from .legacy_migration import (
    LEGACY_BACKUP_DIR,
    LEGACY_CLEANUP_DIR,
)
from .legacy_migration import (
    async_cleanup_entry_metadata as _async_cleanup_entry_metadata,
)
from .legacy_migration import (
    async_cleanup_legacy_component_folder as _async_cleanup_legacy_component_folder,
)
from .legacy_migration import (
    async_remove_legacy_entries as _async_remove_legacy_entries,
)
from .legacy_migration import (
    async_schedule_legacy_cleanup as _async_schedule_legacy_cleanup,
)
from .legacy_migration import (
    async_schedule_legacy_folder_cleanup as _async_schedule_legacy_folder_cleanup,
)
from .legacy_migration import cleanup_destination as _cleanup_destination
from .metrics import metrics as _metrics
from .network_policy import apply_ban_settings as _apply_ban_settings
from .network_policy import apply_blocked_networks as _apply_blocked_networks
from .network_policy import (
    async_sync_detected_allowlist_defaults as _async_sync_detected_allowlist_defaults,
)
from .network_policy import (
    async_update_internal_bypass_networks as _async_update_internal_bypass_networks,
)
from .network_policy import update_allowlist_entry as _update_allowlist_entry
from .nginx_proxy_manager import setup_npm_sync as _setup_npm_sync
from .nginx_proxy_manager import unload_npm_sync as _unload_npm_sync
from .notifications import (
    ALLOWLISTED_LOGIN_ESCALATION_THRESHOLD,
    ALLOWLISTED_LOGIN_SILENCE_LABEL,
    ALLOWLISTED_LOGIN_SILENCE_URL,
    ATTR_NOTIFICATION_ID,
    INTEGRATION_CONFIG_URL,
    NOTIFICATION_ICON_DATA_URL,
    NOTIFICATION_ICON_URL,
    NOTIFICATION_TITLE,
    PANEL_ACTION_SILENCE_ALLOWLISTED_LOGIN,
    PANEL_ACTION_UNSILENCE_ALLOWLISTED_LOGIN,
)
from .notifications import (
    add_manager_links_to_http_notifications as _add_manager_links_to_http_notifications,
)
from .notifications import (
    allowlisted_login_silence_panel_url as _allowlisted_login_silence_panel_url,
)
from .notifications import (
    create_allowlisted_login_notification as _create_allowlisted_login_notification,
)
from .notifications import create_manager_notification as _create_manager_notification
from .notifications import handle_http_notifications as _handle_http_notifications
from .panel import async_panel_set_options as _async_panel_set_options
from .panel import async_register_panel as _async_register_panel
from .panel import async_register_static_assets as _async_register_static_assets
from .panel import async_remove_panel as _async_remove_panel
from .services import (
    IP_ADDRESS_SCHEMA,
    NETWORK_SCHEMA,
    REGISTERED_SERVICES,
    REMOVE_ALL_IP_BANS_SCHEMA,
)
from .services import register_services as _register_services
from .status import current_status
from .storage_keys import (
    KEY_ALLOWLIST,
    KEY_BAN_FILE_WRITE_LOCK,
    KEY_BLOCKED_NETWORKS,
    KEY_CONFIG_ENTRY,
    KEY_DEFAULT_DENY,
    KEY_EMERGENCY_DISABLED,
    KEY_GEOIP_READER_PREPARE_TASK,
    KEY_HEALTH,
    KEY_HTTP_VIEW_HANDLERS,
    KEY_HTTP_VIEWS,
    KEY_INTERNAL_BYPASS_NETWORKS,
    KEY_LEGACY_CLEANUP_SCHEDULED,
    KEY_LEGACY_FOLDER_CLEANUP_TASK,
    KEY_METRICS,
    KEY_ORIGINAL_ADD_BAN,
    KEY_ORIGINAL_LOAD_BANS,
    KEY_PANEL_REGISTERED,
    KEY_PANEL_SIDEBAR_ENABLED,
    KEY_REVERSE_DNS_CACHE,
    KEY_STATIC_PATH_REGISTERED,
)
from .yaml_config import CONFIG_SCHEMA  # noqa: F401
from .yaml_config import (
    async_emergency_disable_requested as _async_emergency_disable_requested,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]

_RELOADABLE_MODULES = (
    "custom_components.ip_ban_manager.const",
    "custom_components.ip_ban_manager.metrics",
    "custom_components.ip_ban_manager.entry_helpers",
    "custom_components.ip_ban_manager.ban_lookup",
    "custom_components.ip_ban_manager.ban_ops",
    "custom_components.ip_ban_manager.geoip",
    "custom_components.ip_ban_manager.geoip_lifecycle",
    "custom_components.ip_ban_manager.health",
    "custom_components.ip_ban_manager.legacy_migration",
    "custom_components.ip_ban_manager.network_policy",
    "custom_components.ip_ban_manager.nginx_proxy_manager",
    "custom_components.ip_ban_manager.notifications",
    "custom_components.ip_ban_manager.panel",
    "custom_components.ip_ban_manager.backup",
    "custom_components.ip_ban_manager.http_patches",
    "custom_components.ip_ban_manager.http_views",
    "custom_components.ip_ban_manager.services",
    "custom_components.ip_ban_manager.status",
    "custom_components.ip_ban_manager.yaml_config",
)

_RELOADABLE_BINDINGS: dict[str, tuple[str, str]] = {
    "CONF_IP_ADDRESSES": ("const", "CONF_IP_ADDRESSES"),
    "DOMAIN": ("const", "DOMAIN"),
    "LEGACY_DOMAIN": ("const", "LEGACY_DOMAIN"),
    "SERVICE_ADD_ALLOWLIST_NETWORK": ("const", "SERVICE_ADD_ALLOWLIST_NETWORK"),
    "SERVICE_ADD_BLOCKED_NETWORK": ("const", "SERVICE_ADD_BLOCKED_NETWORK"),
    "SERVICE_ADD_IP_BAN": ("const", "SERVICE_ADD_IP_BAN"),
    "SERVICE_EXPORT_CONFIG": ("const", "SERVICE_EXPORT_CONFIG"),
    "SERVICE_IMPORT_CONFIG": ("const", "SERVICE_IMPORT_CONFIG"),
    "SERVICE_REMOVE_ALL_IP_BANS": ("const", "SERVICE_REMOVE_ALL_IP_BANS"),
    "SERVICE_REMOVE_ALLOWLIST_NETWORK": (
        "const",
        "SERVICE_REMOVE_ALLOWLIST_NETWORK",
    ),
    "SERVICE_REMOVE_BLOCKED_NETWORK": ("const", "SERVICE_REMOVE_BLOCKED_NETWORK"),
    "SERVICE_REMOVE_IP_BAN": ("const", "SERVICE_REMOVE_IP_BAN"),
    "SERVICE_UPDATE_GEOIP": ("const", "SERVICE_UPDATE_GEOIP"),
    "CONFIG_EXPORT_FORMAT_VERSION": ("backup", "CONFIG_EXPORT_FORMAT_VERSION"),
    "_async_apply_config_backup_payload": (
        "backup",
        "async_apply_config_backup_payload",
    ),
    "_async_export_config": ("backup", "async_export_config"),
    "_async_import_config": ("backup", "async_import_config"),
    "_async_import_config_from_yaml": ("backup", "async_import_config_from_yaml"),
    "_async_restore_exact_bans": ("backup", "async_restore_exact_bans"),
    "_config_download_payload": ("backup", "config_download_payload"),
    "_config_export_payload": ("backup", "config_export_payload"),
    "NetworkAwareBanLookup": ("ban_lookup", "NetworkAwareBanLookup"),
    "_supervisor_internal_networks": ("ban_lookup", "_supervisor_internal_networks"),
    "_async_remove_allowlisted_ip_bans": (
        "ban_ops",
        "async_remove_allowlisted_ip_bans",
    ),
    "_async_replace_ip_bans": ("ban_ops", "async_replace_ip_bans"),
    "_ban_manager": ("ban_ops", "ban_manager"),
    "_ip_ban_file_payload": ("ban_ops", "ip_ban_file_payload"),
    "_entry_allowlisted_login_notifications_enabled": (
        "entry_helpers",
        "entry_allowlisted_login_notifications_enabled",
    ),
    "_entry_geoip_enabled": ("entry_helpers", "entry_geoip_enabled"),
    "_entry_ip_addresses": ("entry_helpers", "entry_ip_addresses"),
    "_entry_sidebar_panel_enabled": ("entry_helpers", "entry_sidebar_panel_enabled"),
    "_parse_allowlist": ("entry_helpers", "parse_allowlist"),
    "_close_geoip_reader": ("geoip", "close_geoip_reader"),
    "_async_schedule_geoip_reader_prepare": (
        "geoip_lifecycle",
        "async_schedule_geoip_reader_prepare",
    ),
    "INTEGRATION_DISABLED_BY_YAML_ISSUE_ID": (
        "health",
        "INTEGRATION_DISABLED_BY_YAML_ISSUE_ID",
    ),
    "IP_BAN_DISABLED_ISSUE_ID": ("health", "IP_BAN_DISABLED_ISSUE_ID"),
    "LEGACY_FOLDER_CLEANUP_FAILED_ISSUE_ID": (
        "health",
        "LEGACY_FOLDER_CLEANUP_FAILED_ISSUE_ID",
    ),
    "LEGACY_YAML_PRESENT_ISSUE_ID": ("health", "LEGACY_YAML_PRESENT_ISSUE_ID"),
    "_async_create_ip_ban_disabled_issue": (
        "health",
        "async_create_ip_ban_disabled_issue",
    ),
    "_async_delete_ip_ban_disabled_issue": (
        "health",
        "async_delete_ip_ban_disabled_issue",
    ),
    "_async_update_emergency_disabled_issue": (
        "health",
        "async_update_emergency_disabled_issue",
    ),
    "_async_update_health_issue": ("health", "async_update_health_issue"),
    "_async_update_legacy_yaml_issue": ("health", "async_update_legacy_yaml_issue"),
    "_ORIGINAL_PROCESS_WRONG_LOGIN": ("http_patches", "_ORIGINAL_PROCESS_WRONG_LOGIN"),
    "_allowlist_process_wrong_login": (
        "http_patches",
        "_allowlist_process_wrong_login",
    ),
    "_async_handle_standard_wrong_login": (
        "http_patches",
        "_async_handle_standard_wrong_login",
    ),
    "_process_allowlisted_wrong_login": (
        "http_patches",
        "_process_allowlisted_wrong_login",
    ),
    "_request_remote_ip": ("http_patches", "_request_remote_ip"),
    "_install_add_ban_patch": ("http_patches", "install_add_ban_patch"),
    "_install_load_bans_patch": ("http_patches", "install_load_bans_patch"),
    "_install_wrong_login_patch": ("http_patches", "install_wrong_login_patch"),
    "_uninstall_patches": ("http_patches", "uninstall_patches"),
    "IPBanManagerManageView": ("http_views", "IPBanManagerManageView"),
    "IPBanManagerPanelView": ("http_views", "IPBanManagerPanelView"),
    "IPBanManagerStatusView": ("http_views", "IPBanManagerStatusView"),
    "SilenceAllowlistedLoginNotificationsView": (
        "http_views",
        "SilenceAllowlistedLoginNotificationsView",
    ),
    "_register_http_views": ("http_views", "register_http_views"),
    "_unregister_http_views": ("http_views", "unregister_http_views"),
    "LEGACY_BACKUP_DIR": ("legacy_migration", "LEGACY_BACKUP_DIR"),
    "LEGACY_CLEANUP_DIR": ("legacy_migration", "LEGACY_CLEANUP_DIR"),
    "_async_cleanup_entry_metadata": (
        "legacy_migration",
        "async_cleanup_entry_metadata",
    ),
    "_async_cleanup_legacy_component_folder": (
        "legacy_migration",
        "async_cleanup_legacy_component_folder",
    ),
    "_async_remove_legacy_entries": ("legacy_migration", "async_remove_legacy_entries"),
    "_async_schedule_legacy_cleanup": (
        "legacy_migration",
        "async_schedule_legacy_cleanup",
    ),
    "_async_schedule_legacy_folder_cleanup": (
        "legacy_migration",
        "async_schedule_legacy_folder_cleanup",
    ),
    "_cleanup_destination": ("legacy_migration", "cleanup_destination"),
    "_metrics": ("metrics", "metrics"),
    "_apply_ban_settings": ("network_policy", "apply_ban_settings"),
    "_apply_blocked_networks": ("network_policy", "apply_blocked_networks"),
    "_async_sync_detected_allowlist_defaults": (
        "network_policy",
        "async_sync_detected_allowlist_defaults",
    ),
    "_async_update_internal_bypass_networks": (
        "network_policy",
        "async_update_internal_bypass_networks",
    ),
    "_update_allowlist_entry": ("network_policy", "update_allowlist_entry"),
    "_setup_npm_sync": ("nginx_proxy_manager", "setup_npm_sync"),
    "_unload_npm_sync": ("nginx_proxy_manager", "unload_npm_sync"),
    "ALLOWLISTED_LOGIN_ESCALATION_THRESHOLD": (
        "notifications",
        "ALLOWLISTED_LOGIN_ESCALATION_THRESHOLD",
    ),
    "ALLOWLISTED_LOGIN_SILENCE_LABEL": (
        "notifications",
        "ALLOWLISTED_LOGIN_SILENCE_LABEL",
    ),
    "ALLOWLISTED_LOGIN_SILENCE_URL": ("notifications", "ALLOWLISTED_LOGIN_SILENCE_URL"),
    "ATTR_NOTIFICATION_ID": ("notifications", "ATTR_NOTIFICATION_ID"),
    "INTEGRATION_CONFIG_URL": ("notifications", "INTEGRATION_CONFIG_URL"),
    "NOTIFICATION_ICON_DATA_URL": ("notifications", "NOTIFICATION_ICON_DATA_URL"),
    "NOTIFICATION_ICON_URL": ("notifications", "NOTIFICATION_ICON_URL"),
    "NOTIFICATION_TITLE": ("notifications", "NOTIFICATION_TITLE"),
    "PANEL_ACTION_SILENCE_ALLOWLISTED_LOGIN": (
        "notifications",
        "PANEL_ACTION_SILENCE_ALLOWLISTED_LOGIN",
    ),
    "PANEL_ACTION_UNSILENCE_ALLOWLISTED_LOGIN": (
        "notifications",
        "PANEL_ACTION_UNSILENCE_ALLOWLISTED_LOGIN",
    ),
    "_add_manager_links_to_http_notifications": (
        "notifications",
        "add_manager_links_to_http_notifications",
    ),
    "_allowlisted_login_silence_panel_url": (
        "notifications",
        "allowlisted_login_silence_panel_url",
    ),
    "_create_allowlisted_login_notification": (
        "notifications",
        "create_allowlisted_login_notification",
    ),
    "_create_manager_notification": ("notifications", "create_manager_notification"),
    "_handle_http_notifications": ("notifications", "handle_http_notifications"),
    "_async_panel_set_options": ("panel", "async_panel_set_options"),
    "_async_register_panel": ("panel", "async_register_panel"),
    "_async_register_static_assets": ("panel", "async_register_static_assets"),
    "_async_remove_panel": ("panel", "async_remove_panel"),
    "IP_ADDRESS_SCHEMA": ("services", "IP_ADDRESS_SCHEMA"),
    "NETWORK_SCHEMA": ("services", "NETWORK_SCHEMA"),
    "REGISTERED_SERVICES": ("services", "REGISTERED_SERVICES"),
    "REMOVE_ALL_IP_BANS_SCHEMA": ("services", "REMOVE_ALL_IP_BANS_SCHEMA"),
    "_register_services": ("services", "register_services"),
    "current_status": ("status", "current_status"),
    "CONFIG_SCHEMA": ("yaml_config", "CONFIG_SCHEMA"),
    "_async_emergency_disable_requested": (
        "yaml_config",
        "async_emergency_disable_requested",
    ),
}


def _reload_runtime_modules_sync() -> None:
    """Reload IP Ban Manager submodules so config-entry reloads pick up new code."""
    importlib.invalidate_caches()
    modules: dict[str, ModuleType] = {}
    for module_name in _RELOADABLE_MODULES:
        module = importlib.import_module(module_name)
        modules[module_name.rsplit(".", 1)[-1]] = importlib.reload(module)

    globals().update(
        {
            binding: getattr(modules[module_name], attribute)
            for binding, (module_name, attribute) in _RELOADABLE_BINDINGS.items()
        }
    )
    storage_keys = importlib.import_module(
        "custom_components.ip_ban_manager.storage_keys"
    )
    globals().update(
        {
            name: getattr(storage_keys, name)
            for name in list(globals())
            if name.startswith("KEY_") and hasattr(storage_keys, name)
        }
    )


async def _async_reload_runtime_modules(hass: HomeAssistant) -> None:
    """Reload IP Ban Manager code off the event loop before setting up an entry."""
    await hass.async_add_executor_job(_reload_runtime_modules_sync)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up IP Ban Manager and import YAML configuration."""
    emergency_disabled = await _async_emergency_disable_requested(hass, config)
    hass.data[KEY_EMERGENCY_DISABLED] = emergency_disabled
    _async_update_emergency_disabled_issue(hass, emergency_disabled)
    if emergency_disabled:
        _LOGGER.warning("IP Ban Manager is disabled by emergency override")
        return True

    _async_update_legacy_yaml_issue(hass, config)

    if hass.config_entries.async_entries(DOMAIN):
        _async_schedule_legacy_cleanup(hass)

    yaml_config = config.get(DOMAIN) or config.get(LEGACY_DOMAIN)
    if yaml_config is not None and CONF_IP_ADDRESSES in yaml_config:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": HA_SOURCE_IMPORT},
                data={CONF_IP_ADDRESSES: yaml_config[CONF_IP_ADDRESSES]},
            )
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up IP Ban Manager from a config entry."""
    if hass.data.get(KEY_EMERGENCY_DISABLED):
        _LOGGER.warning(
            "IP Ban Manager config entry setup skipped because ip_ban_manager is disabled"
        )
        _async_update_emergency_disabled_issue(hass, True)
        return True

    await _async_reload_runtime_modules(hass)
    _async_cleanup_entry_metadata(hass, entry)
    _async_schedule_legacy_cleanup(hass)
    _async_schedule_legacy_folder_cleanup(hass)
    hass.http.app[KEY_CONFIG_ENTRY] = entry
    hass.http.app[KEY_ALLOWLIST] = _parse_allowlist(_entry_ip_addresses(entry))

    try:
        ban_manager: IpBanManager = hass.http.app[KEY_BAN_MANAGER]
    except KeyError:
        _LOGGER.warning(
            "Can't find ban manager. ip_ban_manager requires http.ip_ban_enabled to be True, so disabling."
        )
        _async_create_ip_ban_disabled_issue(hass)
        return True
    _async_delete_ip_ban_disabled_issue(hass)
    await _async_register_static_assets(hass)
    await _async_register_panel(
        hass, sidebar_enabled=_entry_sidebar_panel_enabled(entry)
    )
    _register_http_views(hass)
    _LOGGER.debug("Ban manager %s", ban_manager)
    _install_load_bans_patch(hass, ban_manager)
    await _async_update_internal_bypass_networks(hass)
    await _async_sync_detected_allowlist_defaults(hass)
    _apply_ban_settings(hass, entry)
    _apply_blocked_networks(hass, entry)
    await _async_remove_allowlisted_ip_bans(hass)
    if _entry_geoip_enabled(entry):
        _async_schedule_geoip_reader_prepare(hass)
    allowlist = hass.http.app[KEY_ALLOWLIST]

    if len(allowlist) == 0:
        _LOGGER.info("Not setting allowlist, as no IPs set")
    else:
        _LOGGER.info("Setting allowlist with %s", [str(ip) for ip in allowlist])

    _install_wrong_login_patch()
    _install_add_ban_patch(hass, ban_manager)
    _handle_http_notifications(hass)

    _register_services(hass)
    _setup_npm_sync(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await _async_update_health_issue(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload IP Ban Manager."""
    _unregister_http_views(hass)
    _unload_npm_sync(hass)
    _async_remove_panel(hass)
    legacy_cleanup_task = hass.data.pop(KEY_LEGACY_FOLDER_CLEANUP_TASK, None)
    if legacy_cleanup_task is not None:
        legacy_cleanup_task.cancel()
    geoip_prepare_task = hass.http.app.pop(KEY_GEOIP_READER_PREPARE_TASK, None)
    if geoip_prepare_task is not None:
        geoip_prepare_task.cancel()
    _close_geoip_reader(hass)
    _uninstall_patches(hass)
    hass.http.app.pop(KEY_ALLOWLIST, None)
    hass.http.app.pop(KEY_BLOCKED_NETWORKS, None)
    hass.http.app.pop(KEY_CONFIG_ENTRY, None)
    hass.http.app.pop(KEY_DEFAULT_DENY, None)
    hass.http.app.pop(KEY_INTERNAL_BYPASS_NETWORKS, None)
    hass.http.app.pop(KEY_REVERSE_DNS_CACHE, None)
    # Static asset routes are process-lifetime in Home Assistant; leave
    # KEY_STATIC_PATH_REGISTERED set so unload/reload does not double-register.
    hass.data.pop(KEY_HEALTH, None)
    hass.data.pop(KEY_METRICS, None)
    hass.data.pop(KEY_BAN_FILE_WRITE_LOCK, None)
    for service in REGISTERED_SERVICES:
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
    await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return True
