"""Shared Home Assistant app/data keys for IP Ban Manager."""

from __future__ import annotations

from asyncio import Lock, Task
from collections.abc import Awaitable, Callable
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network
from typing import Any

from aiohttp.web import AppKey
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry

IPAddress = IPv4Address | IPv6Address
IPNetwork = IPv4Network | IPv6Network
AddBanCallable = Callable[[IPAddress], Awaitable[None]]
LoadBansCallable = Callable[[], Awaitable[None]]

KEY_ALLOWLIST = AppKey[tuple[IPNetwork, ...]]("ip_ban_manager_networks")
KEY_BLOCKED_NETWORKS = AppKey[tuple[IPNetwork, ...]]("ip_ban_manager_blocked_networks")
KEY_CONFIG_ENTRY = AppKey[ConfigEntry]("ip_ban_manager_config_entry")
KEY_DEFAULT_DENY = AppKey[bool]("ip_ban_manager_default_deny")
KEY_INTERNAL_BYPASS_NETWORKS = AppKey[tuple[IPNetwork, ...]](
    "ip_ban_manager_internal_bypass_networks"
)
KEY_ORIGINAL_ADD_BAN = AppKey[AddBanCallable]("ip_ban_manager_original_add_ban")
KEY_ORIGINAL_LOAD_BANS = AppKey[LoadBansCallable]("ip_ban_manager_original_load_bans")
KEY_STATIC_PATH_REGISTERED = AppKey[bool]("ip_ban_manager_static_path_registered")
KEY_PANEL_REGISTERED = AppKey[bool]("ip_ban_manager_panel_registered")
KEY_PANEL_SIDEBAR_ENABLED = AppKey[bool]("ip_ban_manager_panel_sidebar_enabled")
KEY_PANEL_MODULE_URL = AppKey[str]("ip_ban_manager_panel_module_url")
KEY_EMERGENCY_DISABLED = AppKey[bool]("ip_ban_manager_emergency_disabled")
KEY_LEGACY_CLEANUP_SCHEDULED = AppKey[bool]("ip_ban_manager_legacy_cleanup_scheduled")
KEY_LEGACY_FOLDER_CLEANED = AppKey[bool]("ip_ban_manager_legacy_folder_cleaned")
KEY_LEGACY_FOLDER_CLEANUP_TASK = AppKey[Task[None]](
    "ip_ban_manager_legacy_folder_cleanup_task"
)
KEY_GEOIP_READER = AppKey[object]("ip_ban_manager_geoip_reader")
KEY_GEOIP_READER_MTIME = AppKey[float]("ip_ban_manager_geoip_reader_mtime")
KEY_GEOIP_READER_PREPARE_TASK = AppKey[Task[None]](
    "ip_ban_manager_geoip_reader_prepare_task"
)
KEY_LOCAL_GEOIP_REGION_CACHE = AppKey[dict[str, Any]](
    "ip_ban_manager_local_geoip_region_cache"
)
KEY_REVERSE_DNS_CACHE = AppKey[dict[IPAddress, Any]]("ip_ban_manager_reverse_dns_cache")
KEY_HEALTH = AppKey[dict[str, object]]("ip_ban_manager_health")
KEY_METRICS = AppKey[dict[str, object]]("ip_ban_manager_metrics")
KEY_BAN_FILE_WRITE_LOCK = AppKey[Lock]("ip_ban_manager_ban_file_write_lock")
KEY_HTTP_VIEWS = AppKey[tuple[HomeAssistantView, ...]]("ip_ban_manager_http_views")
KEY_HTTP_VIEW_HANDLERS = "ip_ban_manager_http_view_handlers"
KEY_NPM_RUNTIME = "ip_ban_manager_npm_runtime"
KEY_NPM_SYNC_TASK = "ip_ban_manager_npm_sync_task"
KEY_NPM_UNSUBSCRIBERS = "ip_ban_manager_npm_unsubscribers"
