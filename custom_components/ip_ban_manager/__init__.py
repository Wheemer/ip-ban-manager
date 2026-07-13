"""The IP Ban Manager integration."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import ssl
import sys
from asyncio import CancelledError, Lock, Task
from collections.abc import Awaitable, Callable, Collection, Iterable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from gzip import BadGzipFile, GzipFile
from http.client import HTTPResponse
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
    ip_interface,
    ip_network,
)
from pathlib import Path
from secrets import token_urlsafe
from socket import getaddrinfo, gethostbyaddr, herror
from tempfile import NamedTemporaryFile
from typing import Any, cast
from urllib.error import URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

import maxminddb
import voluptuous as vol
import yaml
from aiohttp.web import AppKey, Request, Response
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http import ban as http_ban
from homeassistant.components.http.ban import (
    ATTR_BANNED_AT,
    KEY_BAN_MANAGER,
    KEY_FAILED_LOGIN_ATTEMPTS,
    KEY_LOGIN_THRESHOLD,
    NOTIFICATION_ID_BAN,
    NOTIFICATION_ID_LOGIN,
    IpBan,
    IpBanManager,
)
from homeassistant.components.http.const import KEY_HASS
from homeassistant.components.network import async_get_adapters
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry, UnknownEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util
from voluptuous.schema_builder import Optional as vol_optional
from voluptuous.validators import Any as vol_any

from .const import (
    ATTR_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED,
    ATTR_ALLOWLISTED_LOGINS_CAN_BAN,
    ATTR_AUTO_BAN_ENABLED,
    ATTR_BACKUP,
    ATTR_BAN_NOTIFICATIONS_ENABLED,
    ATTR_BANNED_IPS,
    ATTR_BLOCKED_NETWORKS,
    ATTR_CONFIRM,
    ATTR_DEFAULT_DENY_ENABLED,
    ATTR_FAILED_LOGIN_ATTEMPTS,
    ATTR_GEOIP_ATTRIBUTION,
    ATTR_GEOIP_DATABASE_PRESENT,
    ATTR_GEOIP_DATABASE_SOURCE,
    ATTR_GEOIP_DATABASE_UPDATED,
    ATTR_GEOIP_ENABLED,
    ATTR_HEALTH,
    ATTR_HEALTH_ISSUES,
    ATTR_IP_ADDRESS,
    ATTR_LAST_CONFIG_WRITE,
    ATTR_LAST_EXPORT,
    ATTR_LOGIN_ATTEMPTS_THRESHOLD,
    ATTR_METRICS,
    ATTR_NATIVE_IP_BAN_ENABLED,
    ATTR_NETWORK,
    ATTR_NETWORKS,
    CONF_ALLOWED_IPS,
    CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED,
    CONF_ALLOWLISTED_LOGINS_CAN_BAN,
    CONF_AUTO_BAN_ENABLED,
    CONF_BAN_NOTIFICATIONS_ENABLED,
    CONF_BANNED_IPS,
    CONF_BLOCKED_NETWORKS,
    CONF_DEFAULT_DENY_ENABLED,
    CONF_DISABLE_BAN_MANAGER,
    CONF_DISABLED,
    CONF_GEOIP_ENABLED,
    CONF_IP_ADDRESSES,
    CONF_LEGACY_ENTRY_ID,
    CONF_LOGIN_ATTEMPTS_THRESHOLD,
    CONF_NOTIFICATION_ACTION_TOKEN,
    CONF_SIDEBAR_PANEL_ENABLED,
    CONF_SILENCED_ALLOWLISTED_LOGIN_IPS,
    DEFAULT_LOGIN_ATTEMPTS_THRESHOLD,
    DOMAIN,
    LEGACY_DOMAIN,
    MAX_LOGIN_ATTEMPTS_THRESHOLD,
    SERVICE_ADD_ALLOWLIST_NETWORK,
    SERVICE_ADD_IP_BAN,
    SERVICE_EXPORT_CONFIG,
    SERVICE_IMPORT_CONFIG,
    SERVICE_REMOVE_ALL_IP_BANS,
    SERVICE_REMOVE_ALLOWLIST_NETWORK,
    SERVICE_REMOVE_IP_BAN,
)
from .ip_utils import parse_allowlist_network

_LOGGER = logging.getLogger(__name__)

IPAddress = IPv4Address | IPv6Address
IPNetwork = IPv4Network | IPv6Network
AddBanCallable = Callable[[IPAddress], Awaitable[None]]
LoadBansCallable = Callable[[], Awaitable[None]]

IP_BAN_DISABLED_ISSUE_ID = "ip_ban_disabled"
INTEGRATION_DISABLED_BY_YAML_ISSUE_ID = "integration_disabled_by_yaml"
LEGACY_YAML_PRESENT_ISSUE_ID = "legacy_yaml_present"
LEGACY_FOLDER_CLEANUP_FAILED_ISSUE_ID = "legacy_folder_cleanup_failed"
HEALTH_CHECK_FAILED_ISSUE_ID = "health_check_failed"
ALLOWLISTED_LOGIN_ESCALATION_THRESHOLD = 10
DBIP_ATTRIBUTION = "IP geolocation by DB-IP.com"
DBIP_DOWNLOAD_MAX_BYTES = 250 * 1024 * 1024
DBIP_DOWNLOAD_TIMEOUT = 120
DBIP_DOWNLOAD_USER_AGENT = "IPBanManager/1.5"
DBIP_SOURCE_NAME = "DB-IP City Lite"
DNS_OVER_HTTPS_URL = (
    "https://cloudflare-dns.com/dns-query?name=download.db-ip.com&type=A"
)
GEOIP_DIR = "geoip"
GEOIP_FILENAME = "dbip-city-lite.mmdb"
CONFIG_EXPORT_FILENAME = "ip-ban-manager-backup.yaml"
CONFIG_EXPORT_FORMAT_VERSION = 1
SNAPSHOT_DIR = "snapshots"
SNAPSHOT_KEEP = 3
SUPERVISOR_DOCKER_PARENT_NETWORK = IPv4Network("172.30.0.0/16")
SUPERVISOR_INTERNAL_NETWORKS: tuple[IPNetwork, ...] = (
    SUPERVISOR_DOCKER_PARENT_NETWORK,
)
HTTP_IP_BAN_DOCS_URL = (
    "https://www.home-assistant.io/integrations/http/#ip-filtering-and-banning"
)
INTEGRATION_CONFIG_URL = f"/config/integrations/integration/{DOMAIN}"
CONFIG_ENTRY_URL_TEMPLATE = (
    f"/config/integrations/integration/{DOMAIN}?config_entry={{entry_id}}"
)
EMERGENCY_DISABLE_FILENAME = "ip_ban_manager.disabled"
NOTIFICATION_LINK_LABEL = "Open settings"
ALLOWLISTED_LOGIN_SILENCE_LABEL = "Don't show for this address again"
ALLOWLISTED_LOGIN_SILENCE_URL = f"/api/{DOMAIN}/silence_allowlisted_login_notifications"
PANEL_ACTION_SILENCE_ALLOWLISTED_LOGIN = "silence_allowlisted_login"
PANEL_ACTION_UNSILENCE_ALLOWLISTED_LOGIN = "unsilence_allowlisted_login"
ATTR_NOTIFICATION_ID = "notification_id"
ATTR_TOKEN = "token"
IPV4_IN_TEXT = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
IPV6_IN_TEXT = re.compile(
    r"(?<![0-9A-Fa-f:.])(?:[0-9A-Fa-f]{0,4}:){2,}[0-9A-Fa-f:.%]*(?![0-9A-Fa-f:.])"
)
ENTRY_TITLE = "IP Ban Manager"
LEGACY_ENTRY_TITLES = {"IP Ban Allowlist", "ban_allowlist"}
NOTIFICATION_TITLE = " "
NOTIFICATION_ICON_URL = f"/api/{DOMAIN}/icon.png"
PANEL_WEB_COMPONENT = "ip-ban-manager-panel-v19"
PANEL_JS_URL = f"/api/{DOMAIN}/panel-v19.js"
DEFAULT_SIDEBAR_PANEL_ENABLED = True
NOTIFICATION_ICON_DATA_URL = (
    "data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20"
    "viewBox='0%200%2064%2064'%3E%3Cpath%20fill='%231ea8d1'%20"
    "d='M32%204L56%2014v17c0%2015-10%2025-24%2029C18%2056%208%2046%208%2031V14z'/%3E"
    "%3Cpath%20fill='%233fb6dc'%20d='M32%204l24%2010v17c0%2015-10%2025-24%2029z'/%3E"
    "%3Cpath%20stroke='%23fff'%20stroke-width='7'%20stroke-linecap='round'%20"
    "d='M20%2032h24M32%2020v24'/%3E%3Cpath%20stroke='%230b4d78'%20"
    "stroke-width='7'%20stroke-linecap='round'%20d='M17%2050L49%2014'/%3E%3C/svg%3E"
)

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
KEY_REVERSE_DNS_CACHE = AppKey[dict[IPAddress, "ReverseDNSCacheEntry"]](
    "ip_ban_manager_reverse_dns_cache"
)
KEY_HEALTH = AppKey[dict[str, object]]("ip_ban_manager_health")
KEY_METRICS = AppKey[dict[str, object]]("ip_ban_manager_metrics")
KEY_BAN_FILE_WRITE_LOCK = AppKey[Lock]("ip_ban_manager_ban_file_write_lock")
LEGACY_BACKUP_DIR = "ip_ban_manager_legacy_backup"
LEGACY_CLEANUP_DIR = ".cleanup"
REVERSE_DNS_CACHE_TTL = timedelta(minutes=10)

PLATFORMS = ["sensor"]

_ORIGINAL_PROCESS_WRONG_LOGIN = http_ban.process_wrong_login


@dataclass(frozen=True)
class ReverseDNSCacheEntry:
    """Cached reverse-DNS lookup result for a remote address."""

    hostname: str | None
    expires_at: datetime


def _metrics(hass: HomeAssistant) -> dict[str, object]:
    """Return mutable in-memory integration metrics."""
    return cast(
        dict[str, object],
        hass.data.setdefault(
            KEY_METRICS,
            {
                "panel_api_calls": 0,
                "panel_api_errors": 0,
                "config_writes": 0,
                "snapshots_created": 0,
                "geoip_lookups": 0,
                "reverse_dns_lookups": 0,
                "reverse_dns_cache_hits": 0,
                ATTR_LAST_CONFIG_WRITE: None,
            },
        ),
    )


def _metric_int(metrics: dict[str, object], key: str) -> int:
    """Return an in-memory metric value as an integer."""
    value = metrics.get(key, 0)
    return value if isinstance(value, int) else 0


def _metric_increment(hass: HomeAssistant, key: str) -> None:
    """Increment a numeric integration metric."""
    metrics = _metrics(hass)
    metrics[key] = _metric_int(metrics, key) + 1


def _mark_config_write(hass: HomeAssistant) -> None:
    """Record that IP Ban Manager wrote managed configuration."""
    metrics = _metrics(hass)
    metrics["config_writes"] = _metric_int(metrics, "config_writes") + 1
    metrics[ATTR_LAST_CONFIG_WRITE] = dt_util.utcnow().isoformat()


CONFIG_SCHEMA = vol.Schema(
    {
        vol_optional(DOMAIN): vol_any(
            CONF_DISABLED,
            vol.Schema(
                {
                    vol_optional(CONF_DISABLE_BAN_MANAGER, default=False): cv.boolean,
                    vol_optional(CONF_IP_ADDRESSES): vol.All(
                        cv.ensure_list, [cv.string]
                    ),
                }
            ),
        ),
        vol_optional(LEGACY_DOMAIN): vol_any(
            CONF_DISABLED,
            vol.Schema(
                {
                    vol_optional(CONF_DISABLE_BAN_MANAGER, default=False): cv.boolean,
                    vol_optional(CONF_IP_ADDRESSES): vol.All(
                        cv.ensure_list, [cv.string]
                    ),
                }
            ),
        ),
    },
    extra=vol.ALLOW_EXTRA,
)

IP_ADDRESS_SCHEMA = vol.Schema({vol.Required(ATTR_IP_ADDRESS): cv.string})
NETWORK_SCHEMA = vol.Schema({vol.Required(ATTR_NETWORK): cv.string})
REMOVE_ALL_IP_BANS_SCHEMA = vol.Schema(
    {vol.Required(ATTR_CONFIRM, default=False): cv.boolean}
)


class NetworkAwareBanLookup(dict[IPAddress, IpBan]):
    """IP ban lookup that also blocks configured networks."""

    def __init__(
        self,
        values: dict[IPAddress, IpBan],
        blocked_networks: tuple[IPNetwork, ...],
        allowlist: tuple[IPNetwork, ...],
        default_deny_enabled: bool,
        internal_bypass_networks: tuple[IPNetwork, ...] | None = None,
    ) -> None:
        """Initialize the lookup from Home Assistant's exact IP bans."""
        super().__init__(values)
        self.blocked_networks = blocked_networks
        self.allowlist = allowlist
        self.default_deny_enabled = default_deny_enabled
        self.internal_bypass_networks = (
            internal_bypass_networks or _supervisor_internal_networks()
        )

    def __contains__(self, key: object) -> bool:
        """Return whether an IP is exactly banned or blocked by network."""
        if not isinstance(key, (IPv4Address, IPv6Address)):
            return False

        remote_addr = _normalize_remote_addr(key)
        if _is_allowed(remote_addr, self.internal_bypass_networks):
            return False

        if dict.__contains__(self, key):
            return True

        if remote_addr != key and dict.__contains__(self, remote_addr):
            return True

        if _is_allowed(remote_addr, self.allowlist):
            return False

        if _is_blocked(remote_addr, self.blocked_networks):
            return True

        return self.default_deny_enabled

    def __bool__(self) -> bool:
        """Keep Home Assistant's ban middleware active for network-only blocks."""
        return bool(
            dict.__len__(self) or self.blocked_networks or self.default_deny_enabled
        )


def _supervisor_internal_networks() -> tuple[IPNetwork, ...]:
    """Return narrow internal networks that should not be blocked by managed rules."""
    networks = list(SUPERVISOR_INTERNAL_NETWORKS)
    supervisor_host = _supervisor_host_from_env()
    if supervisor_host is None:
        return tuple(networks)

    with suppress(ValueError):
        supervisor_addr = ip_address(supervisor_host)
        if isinstance(supervisor_addr, IPv4Address):
            if supervisor_addr in SUPERVISOR_DOCKER_PARENT_NETWORK:
                networks.insert(0, SUPERVISOR_DOCKER_PARENT_NETWORK)
            else:
                networks.insert(0, IPv4Network(f"{supervisor_addr}/32"))
        else:
            networks.insert(0, IPv6Network(f"{supervisor_addr}/128"))

    return tuple(dict.fromkeys(networks))


async def _async_home_assistant_self_networks(
    hass: HomeAssistant,
) -> tuple[IPNetwork, ...]:
    """Return exact Home Assistant-owned addresses that managed rules must not block."""
    networks = list(_supervisor_internal_networks())

    for adapter in await async_get_adapters(hass):
        if not adapter["enabled"]:
            continue

        for address in (*adapter["ipv4"], *adapter["ipv6"]):
            interface = ip_interface(
                f"{address['address']}/{address['network_prefix']}"
            )
            if (
                isinstance(interface.network, IPv6Network)
                and interface.network.is_link_local
            ):
                networks.append(interface.network)
                continue
            host_prefix = 32 if isinstance(interface.ip, IPv4Address) else 128
            networks.append(ip_network(f"{interface.ip}/{host_prefix}"))

    return tuple(dict.fromkeys(networks))


async def _async_update_internal_bypass_networks(hass: HomeAssistant) -> None:
    """Refresh exact Home Assistant self-addresses protected from managed rules."""
    networks = await _async_home_assistant_self_networks(hass)
    hass.http.app[KEY_INTERNAL_BYPASS_NETWORKS] = networks

    try:
        lookup = hass.http.app[KEY_BAN_MANAGER].ip_bans_lookup
    except KeyError:
        return

    if isinstance(lookup, NetworkAwareBanLookup):
        lookup.internal_bypass_networks = networks


def _supervisor_host_from_env() -> str | None:
    """Return the Supervisor host from Home Assistant's Supervisor environment."""
    supervisor = os.environ.get("SUPERVISOR")
    if not supervisor:
        return None
    if "://" in supervisor:
        return urlsplit(supervisor).hostname
    if supervisor.count(":") == 1 and "." in supervisor:
        return supervisor.split(":", 1)[0]
    return supervisor


def _normalize_remote_addr(remote_addr: IPAddress) -> IPAddress:
    """Normalize runtime addresses into the family users configured."""
    if isinstance(remote_addr, IPv6Address) and remote_addr.ipv4_mapped is not None:
        return remote_addr.ipv4_mapped

    return remote_addr


def _is_allowed(remote_addr: IPAddress, allowlist: tuple[IPNetwork, ...]) -> bool:
    """Return whether a remote address is covered by the allowlist."""
    normalized_addr = _normalize_remote_addr(remote_addr)
    return any(normalized_addr in allowed_network for allowed_network in allowlist)


def _is_blocked(
    remote_addr: IPAddress, blocked_networks: tuple[IPNetwork, ...]
) -> bool:
    """Return whether a remote address is covered by blocked networks."""
    normalized_addr = _normalize_remote_addr(remote_addr)
    return any(
        normalized_addr in blocked_network for blocked_network in blocked_networks
    )


def _request_remote_ip(request: Request) -> IPAddress | None:
    """Parse the request's remote address, if Home Assistant provided one."""
    if request.remote is None:
        return None

    try:
        return _normalize_remote_addr(ip_address(request.remote))
    except ValueError:
        _LOGGER.debug(
            "Ignoring invalid remote address from request: %s", request.remote
        )
        return None


async def _async_reverse_dns_name(
    hass: HomeAssistant, remote_addr: IPAddress
) -> str | None:
    """Return a cached reverse-DNS name for a remote address."""
    now = dt_util.utcnow()
    cache = hass.http.app.setdefault(KEY_REVERSE_DNS_CACHE, {})
    cached = cache.get(remote_addr)
    if cached is not None and cached.expires_at > now:
        _metric_increment(hass, "reverse_dns_cache_hits")
        return cached.hostname

    hostname: str | None = None
    _metric_increment(hass, "reverse_dns_lookups")
    with suppress(herror, OSError):
        hostname, _, _ = await hass.async_add_executor_job(
            gethostbyaddr, str(remote_addr)
        )

    cache[remote_addr] = ReverseDNSCacheEntry(
        hostname=hostname,
        expires_at=now + REVERSE_DNS_CACHE_TTL,
    )
    return hostname


async def _allowlist_process_wrong_login(request: Request) -> None:
    """Process failed logins while preventing allowlisted addresses from bans."""
    allowlist = request.app.get(KEY_ALLOWLIST, ())
    remote_addr = _request_remote_ip(request)
    hass = request.app[KEY_HASS]

    if remote_addr is None or not _is_allowed(remote_addr, allowlist):
        await _ORIGINAL_PROCESS_WRONG_LOGIN(request)
        _handle_http_notifications(hass)
        return

    if _allowlisted_logins_can_ban(hass):
        await _ORIGINAL_PROCESS_WRONG_LOGIN(request)
        _handle_http_notifications(hass)
        return

    await _process_allowlisted_wrong_login(request, remote_addr)
    _LOGGER.info(
        "Allowlisted address %s failed authentication but was not banned",
        remote_addr,
    )


async def _process_allowlisted_wrong_login(
    request: Request, remote_addr: IPAddress
) -> None:
    """Record an allowlisted failed login without letting it become a ban."""
    hass = request.app[KEY_HASS]
    remote_host = await _async_reverse_dns_name(hass, remote_addr)

    remote_display = _format_remote_display(remote_host, remote_addr)
    base_msg = (
        "Login attempt or request with invalid authentication from"
        f" {remote_display}."
    )
    user_agent = request.headers.get("user-agent")
    log_msg = f"{base_msg} Requested URL: '{request.rel_url}'. ({user_agent})"
    notification_msg = f"{base_msg} See the log for details."

    logging.getLogger("homeassistant.components.http.ban").warning(log_msg)

    if KEY_BAN_MANAGER in request.app and request.app[KEY_LOGIN_THRESHOLD] >= 1:
        request.app[KEY_FAILED_LOGIN_ATTEMPTS][remote_addr] += 1

    _create_allowlisted_login_notification(hass, remote_addr, notification_msg)


def _format_remote_display(remote_host: str | None, remote_addr: IPAddress) -> str:
    """Return a readable remote identity without duplicating numeric addresses."""
    remote_ip = str(remote_addr)
    if remote_host is None or remote_host == remote_ip:
        return remote_ip
    return f"{remote_host} ({remote_ip})"


def _notifications_enabled(hass: HomeAssistant) -> bool:
    """Return whether Home Assistant HTTP ban/login notifications should remain."""
    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    if entry is None:
        return True
    return bool(
        entry.options.get(
            CONF_BAN_NOTIFICATIONS_ENABLED,
            entry.data.get(CONF_BAN_NOTIFICATIONS_ENABLED, True),
        )
    )


def _handle_http_notifications(hass: HomeAssistant) -> None:
    """Add manager links to, or suppress, Home Assistant HTTP notifications."""
    if _notifications_enabled(hass):
        _add_manager_links_to_http_notifications(hass)
        return

    _dismiss_http_notifications(hass)


def _dismiss_http_notifications(hass: HomeAssistant) -> None:
    """Dismiss Home Assistant HTTP ban/login notifications."""
    from homeassistant.components import persistent_notification

    persistent_notification.async_dismiss(hass, NOTIFICATION_ID_LOGIN)
    persistent_notification.async_dismiss(hass, NOTIFICATION_ID_BAN)


def _dismiss_ban_notification_for_ips(
    hass: HomeAssistant, removed_ips: Collection[IPAddress]
) -> None:
    """Dismiss Home Assistant's ban notification when it only describes these IPs."""
    from homeassistant.components import persistent_notification

    notifications = persistent_notification._async_get_or_create_notifications(
        hass
    )  # noqa: SLF001
    ban_notification = notifications.get(NOTIFICATION_ID_BAN)
    if ban_notification and any(
        str(removed_ip) in ban_notification["message"] for removed_ip in removed_ips
    ):
        persistent_notification.async_dismiss(hass, NOTIFICATION_ID_BAN)


def _manager_notification_link(hass: HomeAssistant) -> str:
    """Return the markdown link to IP Ban Manager settings."""
    return f"[{NOTIFICATION_LINK_LABEL}]({_manager_config_url(hass)})"


def _with_manager_link(hass: HomeAssistant, message: str) -> str:
    """Append the manager settings link once."""
    if NOTIFICATION_LINK_LABEL in message or INTEGRATION_CONFIG_URL in message:
        return message
    return f"{message}\n\n{_manager_notification_link(hass)}"


def _notification_action_token(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Return a persistent token for notification action links."""
    token = entry.data.get(CONF_NOTIFICATION_ACTION_TOKEN)
    if isinstance(token, str) and token:
        return token

    token = token_urlsafe(24)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_NOTIFICATION_ACTION_TOKEN: token}
    )
    return token


def _allowlisted_login_silence_url(
    hass: HomeAssistant,
    entry: ConfigEntry,
    remote_addr: IPAddress,
    notification_id: str = NOTIFICATION_ID_LOGIN,
) -> str:
    """Return the per-address allowlisted-login silence URL."""
    query = urlencode(
        {
            ATTR_IP_ADDRESS: str(remote_addr),
            ATTR_NOTIFICATION_ID: notification_id,
            ATTR_TOKEN: _notification_action_token(hass, entry),
        }
    )
    return f"{ALLOWLISTED_LOGIN_SILENCE_URL}?{query}"


def _allowlisted_login_silence_panel_url(
    remote_addr: IPAddress,
    notification_id: str = NOTIFICATION_ID_LOGIN,
) -> str:
    """Return the panel URL that silences one allowlisted-login address."""
    query = urlencode(
        {
            "action": PANEL_ACTION_SILENCE_ALLOWLISTED_LOGIN,
            ATTR_IP_ADDRESS: str(remote_addr),
            ATTR_NOTIFICATION_ID: notification_id,
        }
    )
    return f"/{DOMAIN}?{query}"


def _notification_action_response() -> Response:
    """Acknowledge notification-link actions without navigating the frontend."""
    return Response(status=204)


def _with_allowlisted_login_silence_link(
    hass: HomeAssistant,
    entry: ConfigEntry,
    message: str,
    remote_addr: IPAddress,
    notification_id: str = NOTIFICATION_ID_LOGIN,
) -> str:
    """Append the allowlisted-login silence link once."""
    message = _strip_notification_action_links(message)
    if ALLOWLISTED_LOGIN_SILENCE_LABEL in message:
        return message
    return (
        f"{message}\n\n"
        f"[{ALLOWLISTED_LOGIN_SILENCE_LABEL}]"
        f"({_allowlisted_login_silence_panel_url(remote_addr, notification_id)})"
    )


def _strip_notification_action_links(message: str) -> str:
    """Remove old action links before adding the current action."""
    action_labels = (
        NOTIFICATION_LINK_LABEL,
        "Open integrations",
        "Allowlisted login notifications",
        ALLOWLISTED_LOGIN_SILENCE_LABEL,
    )
    return "\n".join(
        line
        for line in message.splitlines()
        if not any(line.startswith(f"[{label}](") for label in action_labels)
    ).rstrip()


def _first_ip_address_in_text(message: str) -> IPAddress | None:
    """Return the first IP address in notification text."""
    for match in IPV4_IN_TEXT.findall(message):
        with suppress(ValueError):
            return ip_address(match)
    for match in IPV6_IN_TEXT.findall(message):
        with suppress(ValueError):
            return ip_address(match.strip("[]").split("%", 1)[0])
    return None


def _dismiss_allowlisted_login_notifications(
    hass: HomeAssistant, remote_addr: IPAddress | None = None
) -> None:
    """Dismiss allowlisted-login notifications, including rewritten variants."""
    from homeassistant.components import persistent_notification

    notifications = persistent_notification._async_get_or_create_notifications(hass)
    matching_ids = set()
    for notification_id, notification in notifications.items():
        message = notification["message"]
        message_lower = message.lower()
        if notification_id == NOTIFICATION_ID_LOGIN:
            matching_ids.add(notification_id)
            continue
        if remote_addr is None:
            if (
                ALLOWLISTED_LOGIN_SILENCE_URL in message
                or ALLOWLISTED_LOGIN_SILENCE_LABEL in message
                or "allowlisted login" in message_lower
            ):
                matching_ids.add(notification_id)
            continue

        remote_addr_text = str(remote_addr)
        encoded_remote_addr = quote(remote_addr_text, safe="")
        if (
            remote_addr_text in message
            or f"{ATTR_IP_ADDRESS}={encoded_remote_addr}" in message
        ) and (
            ALLOWLISTED_LOGIN_SILENCE_URL in message
            or ALLOWLISTED_LOGIN_SILENCE_LABEL in message
            or "allowlisted login" in message_lower
            or "is allowlisted" in message_lower
            or "will not be banned" in message_lower
        ):
            matching_ids.add(notification_id)

    for notification_id in matching_ids:
        persistent_notification.async_dismiss(hass, notification_id)


def _silence_allowlisted_login_notifications(
    hass: HomeAssistant,
    entry: ConfigEntry,
    remote_addr: IPAddress,
    notification_id: str | None = None,
) -> None:
    """Persist per-address silence and dismiss matching notifications."""
    silenced_ips = _entry_silenced_allowlisted_login_ip_strings(entry)
    if str(remote_addr) not in silenced_ips:
        silenced_ips.append(str(remote_addr))

    _update_entry_options(hass, **{CONF_SILENCED_ALLOWLISTED_LOGIN_IPS: silenced_ips})
    if notification_id:
        from homeassistant.components import persistent_notification

        persistent_notification.async_dismiss(hass, notification_id)
    _dismiss_allowlisted_login_notifications(hass, remote_addr)


def _unsilence_allowlisted_login_notifications(
    hass: HomeAssistant,
    entry: ConfigEntry,
    remote_addr: IPAddress,
) -> None:
    """Remove per-address silence for allowlisted login notifications."""
    silenced_ips = [
        ip_value
        for ip_value in _entry_silenced_allowlisted_login_ip_strings(entry)
        if ip_value != str(remote_addr)
    ]
    _update_entry_options(hass, **{CONF_SILENCED_ALLOWLISTED_LOGIN_IPS: silenced_ips})


def _notification_heading(notification_id: str, message: str) -> str:
    """Return the short message heading for a Home Assistant HTTP notification."""
    if notification_id == NOTIFICATION_ID_BAN:
        return "IP banned"
    message_lower = message.lower()
    if "allowlisted" in message_lower:
        if (
            "repeated allowlisted login failures" in message_lower
            or "trusted source should be reviewed" in message_lower
            or "threshold" in message_lower
        ):
            return "Repeated allowlisted login failures"
        return "Allowlisted login failed"
    return "Login attempt failed"


def _notification_brand_header() -> str:
    """Return the compact branded header used in persistent notifications."""
    return (
        f'## <img src="{NOTIFICATION_ICON_DATA_URL}" width="28" height="28" '
        'alt="">&nbsp;&nbsp;IP Ban Manager'
    )


def _strip_notification_brand_header(message: str) -> str:
    """Remove an existing IP Ban Manager markdown header before rebranding."""
    first_line, separator, rest = message.partition("\n")
    if separator and first_line.startswith("## ") and "IP Ban Manager" in first_line:
        return rest.lstrip("\n")
    return message


def _with_notification_heading(heading: str, message: str) -> str:
    """Prefix a notification body with the branded header and compact heading once."""
    brand_header = _notification_brand_header()
    heading_line = f"**{heading}**"
    message = _strip_notification_brand_header(message)
    if message.startswith(heading_line):
        return f"{brand_header}\n\n{message}"
    return f"{brand_header}\n\n{heading_line}\n\n{message}"


def _with_geoip_attribution_footer(message: str) -> str:
    """Append the DB-IP attribution as a quiet notification footer."""
    if DBIP_ATTRIBUTION in message:
        return message
    return f"{message}\n\n<small><sub>{DBIP_ATTRIBUTION}</sub></small>"


def _geoip_notification_detail(
    hass: HomeAssistant, remote_addr: IPAddress | None
) -> str | None:
    """Return a GeoIP notification detail line when local data is available."""
    if remote_addr is None:
        return None
    location = _geoip_location_for_ip(hass, remote_addr)
    if location is None:
        return None
    return f"Location: {location}"


def _create_manager_notification(
    hass: HomeAssistant, message: str, notification_id: str
) -> None:
    """Create a branded IP Ban Manager persistent notification."""
    from homeassistant.components import persistent_notification

    persistent_notification.async_create(
        hass,
        message,
        NOTIFICATION_TITLE,
        notification_id,
    )


def _entry_silenced_allowlisted_login_ip_strings(entry: ConfigEntry) -> list[str]:
    """Return normalized silenced allowlisted-login addresses in stored order."""
    values = entry.options.get(
        CONF_SILENCED_ALLOWLISTED_LOGIN_IPS,
        entry.data.get(CONF_SILENCED_ALLOWLISTED_LOGIN_IPS, []),
    )
    silenced: list[str] = []
    seen: set[IPAddress] = set()
    for value in values if isinstance(values, list) else []:
        with suppress(ValueError):
            address = ip_address(value)
            if address not in seen:
                silenced.append(str(address))
                seen.add(address)
    return silenced


def _entry_silenced_allowlisted_login_ips(entry: ConfigEntry) -> set[IPAddress]:
    """Return allowlisted addresses with login notices silenced."""
    return {
        ip_address(address)
        for address in _entry_silenced_allowlisted_login_ip_strings(entry)
    }


def _should_notify_allowlisted_login(
    hass: HomeAssistant, remote_addr: IPAddress, attempts: int
) -> bool:
    """Return whether an allowlisted failed login should notify the user."""
    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    if entry is None:
        return True

    if remote_addr in _entry_silenced_allowlisted_login_ips(entry):
        return False

    if attempts >= ALLOWLISTED_LOGIN_ESCALATION_THRESHOLD:
        return True

    return _entry_allowlisted_login_notifications_enabled(entry)


def _create_allowlisted_login_notification(
    hass: HomeAssistant, remote_addr: IPAddress, base_message: str
) -> None:
    """Create an IP Ban Manager failed-login notification for an allowlisted source."""
    failed_attempts = hass.http.app.get(KEY_FAILED_LOGIN_ATTEMPTS, {})
    attempts = int(failed_attempts.get(remote_addr, 0))
    threshold = int(hass.http.app.get(KEY_LOGIN_THRESHOLD, 0))
    if not _should_notify_allowlisted_login(hass, remote_addr, attempts):
        return

    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    details = [base_message]
    has_geoip_detail = False
    if geoip_detail := _geoip_notification_detail(hass, remote_addr):
        details.append(geoip_detail)
        has_geoip_detail = True
    if attempts >= ALLOWLISTED_LOGIN_ESCALATION_THRESHOLD:
        heading = "Repeated allowlisted login failures"
        details.append(
            f"This allowlisted source has failed authentication {attempts} times. "
            f"It was not banned because {remote_addr} is trusted, but repeated "
            "failures from a trusted source should be reviewed."
        )
    elif threshold >= 1 and attempts >= threshold:
        heading = "Repeated allowlisted login failures"
        details.append(
            f"This source has reached the automatic ban threshold "
            f"({attempts}/{threshold}), but {remote_addr} is allowlisted, "
            "so it was not banned."
        )
    else:
        heading = "Allowlisted login failed"
        if threshold >= 1:
            details.append(
                f"Current failed-login count: {attempts}/{threshold}. "
                f"{remote_addr} is allowlisted, so it will not be banned."
            )
        else:
            details.append(f"{remote_addr} is allowlisted, so it will not be banned.")

    message = _with_notification_heading(heading, "\n\n".join(details))
    if entry is not None:
        message = _with_allowlisted_login_silence_link(
            hass, entry, message, remote_addr
        )
    if has_geoip_detail:
        message = _with_geoip_attribution_footer(message)
    _create_manager_notification(hass, message, NOTIFICATION_ID_LOGIN)


def _add_manager_links_to_http_notifications(hass: HomeAssistant) -> None:
    """Rewrite Home Assistant HTTP notifications as IP Ban Manager notifications."""
    from homeassistant.components import persistent_notification

    notifications = persistent_notification._async_get_or_create_notifications(
        hass
    )  # noqa: SLF001
    for notification_id in (NOTIFICATION_ID_LOGIN, NOTIFICATION_ID_BAN):
        notification = notifications.get(notification_id)
        if notification is None:
            continue

        heading = _notification_heading(notification_id, notification["message"])
        message = _with_notification_heading(heading, notification["message"])
        remote_addr = _first_ip_address_in_text(message)
        has_geoip_detail = False
        if (
            remote_addr is not None
            and "Location:" not in message
            and (geoip_detail := _geoip_notification_detail(hass, remote_addr))
        ):
            message = f"{message}\n\n{geoip_detail}"
            has_geoip_detail = True
        if (
            notification_id == NOTIFICATION_ID_LOGIN
            and "allowlisted" in message.lower()
        ):
            if heading in (
                "Allowlisted login failed",
                "Repeated allowlisted login failures",
            ):
                if remote_addr is not None:
                    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
                    if entry is not None:
                        message = _with_allowlisted_login_silence_link(
                            hass, entry, message, remote_addr, notification_id
                        )
        else:
            message = _with_manager_link(hass, message)
        if has_geoip_detail:
            message = _with_geoip_attribution_footer(message)
        if (
            message == notification["message"]
            and notification["title"] == NOTIFICATION_TITLE
        ):
            continue

        _create_manager_notification(hass, message, notification_id)


class SilenceAllowlistedLoginNotificationsView(HomeAssistantView):
    """Silence allowlisted failed-login notifications from a notification link."""

    name = "api:ip_ban_manager:silence_allowlisted_login_notifications"
    url = ALLOWLISTED_LOGIN_SILENCE_URL
    requires_auth = False

    async def get(self, request: Request) -> Response:
        """Silence allowlisted failed-login notifications and dismiss the current notification."""
        hass = request.app[KEY_HASS]
        entry = hass.http.app.get(KEY_CONFIG_ENTRY)
        if entry is None:
            return Response(text="IP Ban Manager is not loaded.", status=404)

        user = request.get("hass_user")
        token = getattr(request, "query", {}).get(ATTR_TOKEN)
        token_is_valid = bool(
            token and token == entry.data.get(CONF_NOTIFICATION_ACTION_TOKEN)
        )
        if (user is None or not user.is_admin) and not token_is_valid:
            return self.json_message("Administrator access is required.", 403)

        ip_address_value = getattr(request, "query", {}).get(ATTR_IP_ADDRESS)
        if ip_address_value:
            try:
                remote_addr = ip_address(ip_address_value)
            except ValueError:
                return Response(text="Invalid IP address.", status=400)

            notification_id = getattr(request, "query", {}).get(ATTR_NOTIFICATION_ID)
            _silence_allowlisted_login_notifications(
                hass,
                entry,
                remote_addr,
                notification_id if isinstance(notification_id, str) else None,
            )
            return _notification_action_response()

        _update_entry_options(
            hass, **{CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED: False}
        )
        _dismiss_allowlisted_login_notifications(hass)
        return _notification_action_response()


def _panel_payload(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, object]:
    """Return the complete JSON payload used by the bundled panel."""
    return {
        "ok": True,
        "status": current_status(hass),
        "settings": {
            CONF_IP_ADDRESSES: _entry_ip_addresses(entry),
            CONF_BLOCKED_NETWORKS: _entry_blocked_networks(entry),
            CONF_AUTO_BAN_ENABLED: _entry_auto_ban_enabled(entry),
            CONF_BAN_NOTIFICATIONS_ENABLED: _entry_ban_notifications_enabled(entry),
            CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED: (
                _entry_allowlisted_login_notifications_enabled(entry)
            ),
            CONF_ALLOWLISTED_LOGINS_CAN_BAN: _entry_allowlisted_logins_can_ban(entry),
            CONF_DEFAULT_DENY_ENABLED: _entry_default_deny_enabled(entry),
            CONF_LOGIN_ATTEMPTS_THRESHOLD: _entry_login_threshold(entry, hass),
            CONF_SIDEBAR_PANEL_ENABLED: _entry_sidebar_panel_enabled(entry),
            CONF_GEOIP_ENABLED: _entry_geoip_enabled(entry),
            CONF_SILENCED_ALLOWLISTED_LOGIN_IPS: (
                _entry_silenced_allowlisted_login_ip_strings(entry)
            ),
        },
        "geoip": _geoip_status(hass, entry),
        ATTR_BACKUP: _backup_status(hass),
    }


def _backup_status(hass: HomeAssistant) -> dict[str, object]:
    """Return manual import/export file status for the bundled panel."""
    export_path = _config_export_path(hass)
    return {
        "path": _ha_config_relative_path(export_path),
        "exists": export_path.is_file(),
        ATTR_LAST_EXPORT: _geoip_database_updated(export_path),
    }


class IPBanManagerStatusView(HomeAssistantView):
    """Return live IP Ban Manager state for the bundled panel."""

    name = "api:ip_ban_manager:status"
    url = f"/api/{DOMAIN}/status"

    async def get(self, request: Request) -> Response:
        """Return live status and persisted editable values."""
        hass = request.app[KEY_HASS]
        _metric_increment(hass, "panel_api_calls")
        user = request.get("hass_user")
        if user is None or not user.is_admin:
            _metric_increment(hass, "panel_api_errors")
            return self.json(
                {"ok": False, "error": "Administrator access is required."},
                status_code=403,
            )

        entry = hass.http.app.get(KEY_CONFIG_ENTRY)
        if entry is None:
            _metric_increment(hass, "panel_api_errors")
            return self.json(
                {"ok": False, "error": "IP Ban Manager is not loaded."},
                status_code=404,
            )

        return self.json(_panel_payload(hass, entry))


class IPBanManagerManageView(HomeAssistantView):
    """Apply live IP Ban Manager changes from the bundled panel."""

    name = "api:ip_ban_manager:manage"
    url = f"/api/{DOMAIN}/manage"

    async def post(self, request: Request) -> Response:
        """Apply one validated panel action."""
        hass = request.app[KEY_HASS]
        _metric_increment(hass, "panel_api_calls")
        user = request.get("hass_user")
        if user is None or not user.is_admin:
            _metric_increment(hass, "panel_api_errors")
            return self.json(
                {"ok": False, "error": "Administrator access is required."},
                status_code=403,
            )

        try:
            data = await request.json()
        except ValueError:
            _metric_increment(hass, "panel_api_errors")
            return self.json(
                {"ok": False, "error": "Expected JSON request body."},
                status_code=400,
            )

        action = data.get("action")
        value = str(data.get("value", "")).strip()

        try:
            if action == "add_allowlist":
                await _async_panel_add_allowlist_network(hass, value)
            elif action == "remove_allowlist":
                await _async_panel_remove_allowlist_network(hass, value)
            elif action == "add_ban":
                await _async_add_ip_ban(hass, value)
            elif action == "remove_ban":
                await _async_remove_ip_ban(hass, value)
            elif action == "add_blocked_network":
                await _async_panel_add_blocked_network(hass, value)
            elif action == "remove_blocked_network":
                await _async_panel_remove_blocked_network(hass, value)
            elif action == "set_options":
                await _async_panel_set_options(hass, data.get("options", {}))
            elif action == "update_geoip":
                await _async_download_geoip_database(hass)
            elif action == "export_config":
                await _async_export_config(hass)
            elif action == "import_config":
                await _async_import_config(hass)
            elif action == PANEL_ACTION_SILENCE_ALLOWLISTED_LOGIN:
                _panel_silence_allowlisted_login_notification(
                    hass, value, data.get(ATTR_NOTIFICATION_ID)
                )
            elif action == PANEL_ACTION_UNSILENCE_ALLOWLISTED_LOGIN:
                _panel_unsilence_allowlisted_login_notification(hass, value)
            else:
                _metric_increment(hass, "panel_api_errors")
                return self.json(
                    {"ok": False, "error": "Unknown action."},
                    status_code=400,
                )
        except (HomeAssistantError, ValueError) as err:
            _metric_increment(hass, "panel_api_errors")
            return self.json({"ok": False, "error": str(err)}, status_code=400)

        _async_update_health_issue(hass)
        entry = hass.http.app.get(KEY_CONFIG_ENTRY)
        if entry is None:
            _metric_increment(hass, "panel_api_errors")
            return self.json(
                {"ok": False, "error": "IP Ban Manager is not loaded."},
                status_code=404,
            )
        return self.json(_panel_payload(hass, entry))


def _manager_config_url(hass: HomeAssistant) -> str:
    """Return the most direct stable frontend URL for this integration."""
    if hass.http is None or hass.http.app is None:
        return INTEGRATION_CONFIG_URL

    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    if entry is None:
        return INTEGRATION_CONFIG_URL
    return CONFIG_ENTRY_URL_TEMPLATE.format(entry_id=entry.entry_id)


def _install_wrong_login_patch() -> None:
    """Install the Home Assistant failed-login hook once."""
    if http_ban.process_wrong_login is not _allowlist_process_wrong_login:
        http_ban.process_wrong_login = _allowlist_process_wrong_login

    # Some Home Assistant auth modules import process_wrong_login directly during
    # startup. Patch those already-imported references too so their persistent
    # notifications go through the same branding and allowlist handling path.
    for module_name in (
        "homeassistant.components.auth.login_flow",
        "homeassistant.components.websocket_api.auth",
    ):
        module = sys.modules.get(module_name)
        if (
            module is not None
            and getattr(module, "process_wrong_login", None)
            is not _allowlist_process_wrong_login
        ):
            setattr(module, "process_wrong_login", _allowlist_process_wrong_login)


def _install_add_ban_patch(hass: HomeAssistant, ban_manager: IpBanManager) -> None:
    """Install the IP ban hook for this Home Assistant app once."""
    app = hass.http.app
    app.setdefault(KEY_ORIGINAL_ADD_BAN, ban_manager.async_add_ban)

    async def allowlist_async_add_ban(remote_addr: IPAddress) -> None:
        if _is_allowed(
            remote_addr,
            app.get(KEY_INTERNAL_BYPASS_NETWORKS, _supervisor_internal_networks()),
        ):
            _LOGGER.info(
                "Not adding %s to ban list, as it's a Home Assistant internal address",
                remote_addr,
            )
            return

        allowlist = app.get(KEY_ALLOWLIST, ())
        if _is_allowed(remote_addr, allowlist) and not _allowlisted_logins_can_ban(
            hass
        ):
            _LOGGER.info(
                "Not adding %s to ban list, as it's in the allowlist",
                remote_addr,
            )
            return

        _LOGGER.info("Banning IP %s", remote_addr)
        await app[KEY_ORIGINAL_ADD_BAN](remote_addr)

    ban_manager.async_add_ban = allowlist_async_add_ban  # type: ignore[method-assign]


def _install_load_bans_patch(hass: HomeAssistant, ban_manager: IpBanManager) -> None:
    """Keep managed network blocks applied after Home Assistant reloads bans."""
    app = hass.http.app
    app.setdefault(KEY_ORIGINAL_LOAD_BANS, ban_manager.async_load)

    async def network_aware_async_load() -> None:
        await app[KEY_ORIGINAL_LOAD_BANS]()
        entry = app.get(KEY_CONFIG_ENTRY)
        if entry is not None:
            _apply_blocked_networks(hass, entry)

    ban_manager.async_load = network_aware_async_load  # type: ignore[method-assign]


def _uninstall_patches(hass: HomeAssistant) -> None:
    """Restore Home Assistant internals patched by this integration."""
    app = hass.http.app

    if http_ban.process_wrong_login is _allowlist_process_wrong_login:
        http_ban.process_wrong_login = _ORIGINAL_PROCESS_WRONG_LOGIN

    for module_name in (
        "homeassistant.components.auth.login_flow",
        "homeassistant.components.websocket_api.auth",
    ):
        module = sys.modules.get(module_name)
        if (
            module is not None
            and getattr(module, "process_wrong_login", None)
            is _allowlist_process_wrong_login
        ):
            setattr(module, "process_wrong_login", _ORIGINAL_PROCESS_WRONG_LOGIN)

    original_add_ban = app.pop(KEY_ORIGINAL_ADD_BAN, None)
    original_load_bans = app.pop(KEY_ORIGINAL_LOAD_BANS, None)
    ban_manager = app.get(KEY_BAN_MANAGER)
    if original_add_ban is not None and ban_manager is not None:
        ban_manager.async_add_ban = original_add_ban
    if original_load_bans is not None and ban_manager is not None:
        ban_manager.async_load = original_load_bans
    if ban_manager is not None and isinstance(
        ban_manager.ip_bans_lookup, NetworkAwareBanLookup
    ):
        ban_manager.ip_bans_lookup = dict(ban_manager.ip_bans_lookup)


def _parse_allowlist(ip_addresses: list[str]) -> tuple[IPNetwork, ...]:
    """Parse configured IP addresses and networks."""
    return tuple(parse_allowlist_network(ip) for ip in ip_addresses)


def _parse_blocked_networks(networks: list[str]) -> tuple[IPNetwork, ...]:
    """Parse configured blocked networks."""
    return tuple(parse_allowlist_network(network) for network in networks)


def _entry_ip_addresses(entry: ConfigEntry) -> list[str]:
    """Return the configured allowlist for a config entry."""
    return entry.options.get(
        CONF_IP_ADDRESSES,
        entry.options.get(CONF_ALLOWED_IPS, entry.data.get(CONF_IP_ADDRESSES, [])),
    )


def _entry_blocked_networks(entry: ConfigEntry) -> list[str]:
    """Return configured blocked network strings for a config entry."""
    return entry.options.get(
        CONF_BLOCKED_NETWORKS,
        entry.data.get(CONF_BLOCKED_NETWORKS, []),
    )


def _entry_default_deny_enabled(entry: ConfigEntry) -> bool:
    """Return whether addresses outside the allowlist should be blocked."""
    return bool(
        entry.options.get(
            CONF_DEFAULT_DENY_ENABLED,
            entry.data.get(CONF_DEFAULT_DENY_ENABLED, False),
        )
    )


def _native_ip_banning_enabled(hass: HomeAssistant) -> bool:
    """Return whether Home Assistant loaded its native IP ban manager."""
    return hass.http is not None and KEY_BAN_MANAGER in hass.http.app


def _entry_auto_ban_enabled(entry: ConfigEntry) -> bool:
    """Return whether automatic IP bans should be active when HA supports them."""
    return bool(
        entry.options.get(
            CONF_AUTO_BAN_ENABLED,
            entry.data.get(CONF_AUTO_BAN_ENABLED, True),
        )
    )


def _entry_ban_notifications_enabled(entry: ConfigEntry) -> bool:
    """Return whether automatic IP ban/login notifications should remain."""
    return bool(
        entry.options.get(
            CONF_BAN_NOTIFICATIONS_ENABLED,
            entry.data.get(CONF_BAN_NOTIFICATIONS_ENABLED, True),
        )
    )


def _entry_allowlisted_login_notifications_enabled(entry: ConfigEntry) -> bool:
    """Return whether allowlisted failed logins should notify immediately."""
    return bool(
        entry.options.get(
            CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED,
            entry.data.get(CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED, True),
        )
    )


def _entry_allowlisted_logins_can_ban(entry: ConfigEntry) -> bool:
    """Return whether failed logins from allowlisted sources can become exact bans."""
    return bool(
        entry.options.get(
            CONF_ALLOWLISTED_LOGINS_CAN_BAN,
            entry.data.get(CONF_ALLOWLISTED_LOGINS_CAN_BAN, False),
        )
    )


def _entry_sidebar_panel_enabled(entry: ConfigEntry) -> bool:
    """Return whether the IP Ban Manager sidebar panel should be registered."""
    return bool(
        entry.options.get(
            CONF_SIDEBAR_PANEL_ENABLED,
            entry.data.get(CONF_SIDEBAR_PANEL_ENABLED, DEFAULT_SIDEBAR_PANEL_ENABLED),
        )
    )


def _entry_geoip_enabled(entry: ConfigEntry) -> bool:
    """Return whether local GeoIP labels should be shown when a database exists."""
    return bool(
        entry.options.get(
            CONF_GEOIP_ENABLED,
            entry.data.get(CONF_GEOIP_ENABLED, False),
        )
    )


def _allowlisted_logins_can_ban(hass: HomeAssistant) -> bool:
    """Return whether the current entry allows exact bans inside the allowlist."""
    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    return _entry_allowlisted_logins_can_ban(entry) if entry else False


def _current_login_threshold(hass: HomeAssistant) -> int:
    """Return Home Assistant's current live login-attempt threshold."""
    if hass.http is None:
        return DEFAULT_LOGIN_ATTEMPTS_THRESHOLD
    return _normalize_login_attempts_threshold(
        hass.http.app.get(KEY_LOGIN_THRESHOLD, DEFAULT_LOGIN_ATTEMPTS_THRESHOLD)
    )


def _normalize_login_attempts_threshold(value: Any) -> int:
    """Return a login-attempt threshold inside the supported backend range."""
    return min(MAX_LOGIN_ATTEMPTS_THRESHOLD, max(0, int(value)))


def _entry_login_threshold(entry: ConfigEntry, hass: HomeAssistant) -> int:
    """Return the configured login-attempt threshold for a config entry."""
    return _normalize_login_attempts_threshold(
        entry.options.get(
            CONF_LOGIN_ATTEMPTS_THRESHOLD,
            entry.data.get(
                CONF_LOGIN_ATTEMPTS_THRESHOLD, _current_login_threshold(hass)
            ),
        )
    )


def _effective_login_threshold(entry: ConfigEntry, hass: HomeAssistant) -> int:
    """Return the live threshold to apply to Home Assistant."""
    if not _entry_auto_ban_enabled(entry):
        return 0
    return _entry_login_threshold(entry, hass)


def _apply_ban_settings(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply integration-owned ban settings to Home Assistant's live app."""
    if _native_ip_banning_enabled(hass):
        hass.http.app[KEY_LOGIN_THRESHOLD] = _effective_login_threshold(entry, hass)
    if not _entry_ban_notifications_enabled(entry):
        _dismiss_http_notifications(hass)


def _apply_blocked_networks(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply blocked network settings to Home Assistant's live ban lookup."""
    blocked_networks = _parse_blocked_networks(_entry_blocked_networks(entry))
    default_deny_enabled = _entry_default_deny_enabled(entry)
    allowlist = hass.http.app.get(KEY_ALLOWLIST, ())
    hass.http.app[KEY_BLOCKED_NETWORKS] = blocked_networks
    hass.http.app[KEY_DEFAULT_DENY] = default_deny_enabled

    if not _native_ip_banning_enabled(hass):
        return

    ban_manager = hass.http.app[KEY_BAN_MANAGER]
    lookup = ban_manager.ip_bans_lookup
    if isinstance(lookup, NetworkAwareBanLookup):
        lookup.blocked_networks = blocked_networks
        lookup.allowlist = allowlist
        lookup.default_deny_enabled = default_deny_enabled
        lookup.internal_bypass_networks = hass.http.app.get(
            KEY_INTERNAL_BYPASS_NETWORKS, _supervisor_internal_networks()
        )
        return

    ban_manager.ip_bans_lookup = NetworkAwareBanLookup(
        dict(lookup),
        blocked_networks,
        allowlist,
        default_deny_enabled,
        hass.http.app.get(
            KEY_INTERNAL_BYPASS_NETWORKS, _supervisor_internal_networks()
        ),
    )


def _update_entry_options(hass: HomeAssistant, **updates: object) -> ConfigEntry:
    """Persist config-entry options without dropping unrelated settings."""
    entry = hass.http.app[KEY_CONFIG_ENTRY]
    options = {**entry.options, **updates}
    if options == entry.options:
        return entry

    hass.config_entries.async_update_entry(entry, options=options)
    _mark_config_write(hass)
    return entry


def _ban_manager(hass: HomeAssistant) -> IpBanManager:
    """Return Home Assistant's loaded IP ban manager."""
    try:
        return hass.http.app[KEY_BAN_MANAGER]
    except KeyError as err:
        raise HomeAssistantError(
            "Home Assistant IP banning is not enabled. Set http.ip_ban_enabled to true."
        ) from err


def _async_create_ip_ban_disabled_issue(hass: HomeAssistant) -> None:
    """Create a repair issue when Home Assistant IP banning is disabled."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        IP_BAN_DISABLED_ISSUE_ID,
        is_fixable=False,
        is_persistent=True,
        learn_more_url=HTTP_IP_BAN_DOCS_URL,
        severity=ir.IssueSeverity.WARNING,
        translation_key=IP_BAN_DISABLED_ISSUE_ID,
    )


def _async_delete_ip_ban_disabled_issue(hass: HomeAssistant) -> None:
    """Delete the disabled-IP-ban repair issue when setup is healthy."""
    ir.async_delete_issue(hass, DOMAIN, IP_BAN_DISABLED_ISSUE_ID)


def _yaml_disable_ban_manager(config: ConfigType) -> bool:
    """Return whether YAML requested the emergency integration kill switch."""
    for domain in (DOMAIN, LEGACY_DOMAIN):
        domain_config = config.get(domain)
        if domain_config == CONF_DISABLED:
            return True
        if not isinstance(domain_config, dict):
            continue
        if domain_config.get(CONF_DISABLE_BAN_MANAGER):
            return True

    return False


def _emergency_disable_file_exists(hass: HomeAssistant) -> bool:
    """Return whether the emergency disable file exists."""
    return Path(hass.config.path(EMERGENCY_DISABLE_FILENAME)).is_file()


def _emergency_disable_requested(hass: HomeAssistant, config: ConfigType) -> bool:
    """Return whether any supported emergency disable path is active."""
    return _yaml_disable_ban_manager(config) or _emergency_disable_file_exists(hass)


def _async_update_emergency_disabled_issue(
    hass: HomeAssistant, emergency_disabled: bool
) -> None:
    """Create or clear the Repair for the emergency kill switch."""
    if emergency_disabled:
        ir.async_create_issue(
            hass,
            DOMAIN,
            INTEGRATION_DISABLED_BY_YAML_ISSUE_ID,
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=INTEGRATION_DISABLED_BY_YAML_ISSUE_ID,
        )
        return

    ir.async_delete_issue(hass, DOMAIN, INTEGRATION_DISABLED_BY_YAML_ISSUE_ID)


def _async_update_legacy_yaml_issue(hass: HomeAssistant, config: ConfigType) -> None:
    """Create a repair when old YAML remains after migration."""
    if LEGACY_DOMAIN in config and hass.config_entries.async_entries(DOMAIN):
        ir.async_create_issue(
            hass,
            DOMAIN,
            LEGACY_YAML_PRESENT_ISSUE_ID,
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=LEGACY_YAML_PRESENT_ISSUE_ID,
        )
        return

    if LEGACY_DOMAIN not in config:
        ir.async_delete_issue(hass, DOMAIN, LEGACY_YAML_PRESENT_ISSUE_ID)


def _async_update_legacy_folder_cleanup_issue(
    hass: HomeAssistant, failures: list[str]
) -> None:
    """Create or clear the repair for failed legacy folder cleanup."""
    if failures:
        ir.async_create_issue(
            hass,
            DOMAIN,
            LEGACY_FOLDER_CLEANUP_FAILED_ISSUE_ID,
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=LEGACY_FOLDER_CLEANUP_FAILED_ISSUE_ID,
            translation_placeholders={
                "paths": "\n".join(f"- `{path}`" for path in failures)
            },
        )
        return

    ir.async_delete_issue(hass, DOMAIN, LEGACY_FOLDER_CLEANUP_FAILED_ISSUE_ID)


def _ban_file_access_issue(hass: HomeAssistant) -> str | None:
    """Return an ip_bans.yaml access issue, if one is visible without writing."""
    ban_manager = hass.http.app.get(KEY_BAN_MANAGER)
    if ban_manager is None:
        return "Home Assistant IP banning is not loaded."

    path = Path(ban_manager.path)
    if path.exists():
        if not path.is_file():
            return f"{path} is not a regular file."
        if not os.access(path, os.R_OK | os.W_OK):
            return f"{path} is not readable and writable."
        return None

    parent = path.parent
    if not parent.exists():
        return f"{parent} does not exist."
    if not os.access(parent, os.W_OK):
        return f"{parent} is not writable."
    return None


def _health_status(hass: HomeAssistant) -> dict[str, object]:
    """Return the latest lightweight integration health summary."""
    issues: list[str] = []

    if not _native_ip_banning_enabled(hass):
        issues.append("Native Home Assistant IP banning is disabled.")

    if ban_file_issue := _ban_file_access_issue(hass):
        issues.append(ban_file_issue)

    if not hass.data.get(KEY_PANEL_REGISTERED, False):
        issues.append("The IP Ban Manager panel is not registered.")

    if not hass.data.get(KEY_LEGACY_FOLDER_CLEANED, False):
        issues.append("Legacy custom component cleanup has not completed yet.")

    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    if (
        entry is not None
        and _entry_geoip_enabled(entry)
        and _geoip_database_path(hass).is_file()
        and _geoip_reader(hass) is None
    ):
        issues.append("GeoIP is enabled, but the local database reader is not ready.")

    return {
        "ok": not issues,
        ATTR_HEALTH_ISSUES: issues,
        "checked_at": dt_util.utcnow().isoformat(),
    }


def _async_update_health_issue(hass: HomeAssistant) -> None:
    """Refresh the lightweight health status and matching Repair issue."""
    health = _health_status(hass)
    hass.data[KEY_HEALTH] = health
    issues = cast(list[str], health[ATTR_HEALTH_ISSUES])
    actionable = [
        issue
        for issue in issues
        if issue
        not in (
            "Legacy custom component cleanup has not completed yet.",
            "GeoIP is enabled, but the local database reader is not ready.",
        )
    ]
    if actionable:
        ir.async_create_issue(
            hass,
            DOMAIN,
            HEALTH_CHECK_FAILED_ISSUE_ID,
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=HEALTH_CHECK_FAILED_ISSUE_ID,
            translation_placeholders={
                "issues": "\n".join(f"- {issue}" for issue in actionable)
            },
        )
        return

    ir.async_delete_issue(hass, DOMAIN, HEALTH_CHECK_FAILED_ISSUE_ID)


def _format_ip_ban(hass: HomeAssistant, ip_ban: IpBan) -> dict[str, str]:
    """Return a stable UI/API representation of a ban entry."""
    formatted = {
        ATTR_IP_ADDRESS: str(ip_ban.ip_address),
        ATTR_BANNED_AT: ip_ban.banned_at.isoformat(),
    }
    if location := _geoip_location_for_ip(hass, ip_ban.ip_address):
        formatted["location"] = location
    return formatted


def _atomic_write_text(path: str, content: str) -> None:
    """Write text to a file using an atomic same-directory replacement."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: str | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = temp_file.name
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, target)
    finally:
        if temp_path is not None and os.path.exists(temp_path):
            os.unlink(temp_path)


def _snapshot_dir(hass: HomeAssistant) -> Path:
    """Return the integration-owned snapshot directory."""
    return Path(hass.config.path(DOMAIN, SNAPSHOT_DIR))


def _snapshot_existing_file(path: Path, snapshots: Path) -> bool:
    """Keep a small local snapshot before replacing or deleting a managed file."""
    if not path.is_file():
        return False

    snapshots.mkdir(parents=True, exist_ok=True)
    timestamp = dt_util.utcnow().strftime("%Y%m%d%H%M%S%f")
    snapshot_path = snapshots / f"{path.name}.{timestamp}.bak"
    shutil.copy2(path, snapshot_path)

    existing_snapshots = sorted(
        snapshots.glob(f"{path.name}.*.bak"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for stale_snapshot in existing_snapshots[SNAPSHOT_KEEP:]:
        stale_snapshot.unlink(missing_ok=True)
    return True


def _geoip_database_path(hass: HomeAssistant) -> Path:
    """Return the local GeoIP database path owned by this integration."""
    return Path(hass.config.path(DOMAIN, GEOIP_DIR, GEOIP_FILENAME))


def _config_export_path(hass: HomeAssistant) -> Path:
    """Return the manual config export path owned by this integration."""
    return Path(hass.config.path(DOMAIN, CONFIG_EXPORT_FILENAME))


def _ha_config_relative_path(path: Path) -> str:
    """Return a Home Assistant-style display path for a file under /config."""
    return f"/config/{path.parent.name}/{path.name}"


def _path_is_file(path: Path) -> bool:
    """Return whether a path is a file."""
    return path.is_file()


def _geoip_database_updated(path: Path) -> str | None:
    """Return the database file modification time for status surfaces."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, dt_util.UTC).isoformat()
    except OSError:
        return None


def _geoip_download_months(now: datetime | None = None) -> list[str]:
    """Return current and previous DB-IP release month strings."""
    now = now or dt_util.utcnow()
    current = now.strftime("%Y-%m")
    previous_day = now.replace(day=1) - timedelta(days=1)
    previous = previous_day.strftime("%Y-%m")
    return [current] if current == previous else [current, previous]


def _geoip_download_urls(now: datetime | None = None) -> list[str]:
    """Return DB-IP Lite MMDB download URLs to try."""
    return [
        f"https://download.db-ip.com/free/dbip-city-lite-{month}.mmdb.gz"
        for month in _geoip_download_months(now)
    ]


def _geoip_download_host_is_blocked(host: str) -> bool:
    """Return whether DNS resolved the download host to unusable sinkhole addresses."""
    try:
        addresses = {item[4][0] for item in getaddrinfo(host, 443)}
    except OSError:
        return False
    return bool(addresses) and all(
        address in {"0.0.0.0", "::"} for address in addresses
    )


def _geoip_resolve_download_host_via_https() -> list[str]:
    """Resolve the DB-IP download host without using Home Assistant's local DNS."""
    request = UrlRequest(
        DNS_OVER_HTTPS_URL,
        headers={
            "Accept": "application/dns-json",
            "User-Agent": DBIP_DOWNLOAD_USER_AGENT,
        },
    )
    with urlopen(request, timeout=DBIP_DOWNLOAD_TIMEOUT) as response:
        payload = json.loads(response.read().decode())

    addresses = []
    for answer in payload.get("Answer", []):
        address = answer.get("data")
        if answer.get("type") == 1 and isinstance(address, str):
            addresses.append(address)
    if not addresses:
        raise HomeAssistantError("Could not resolve the GeoIP download host.")
    return addresses


@contextmanager
def _open_geoip_download_url(url: str) -> Iterator[HTTPResponse]:
    """Open a GeoIP download URL, bypassing local DNS only when it is sinkholed."""
    parsed = urlsplit(url)
    host = parsed.hostname or "download.db-ip.com"
    if not _geoip_download_host_is_blocked(host):
        request = UrlRequest(url, headers={"User-Agent": DBIP_DOWNLOAD_USER_AGENT})
        with urlopen(request, timeout=DBIP_DOWNLOAD_TIMEOUT) as response:
            yield response
        return

    _LOGGER.warning(
        "GeoIP database download host %s resolves to 0.0.0.0 or ::; "
        "resolving with DNS-over-HTTPS fallback",
        host,
    )
    last_error: Exception | None = None
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    context = ssl.create_default_context()
    for address in _geoip_resolve_download_host_via_https():
        fallback_response: HTTPResponse | None = None
        tls_socket = None
        try:
            raw_socket = socket.create_connection(
                (address, parsed.port or 443), timeout=DBIP_DOWNLOAD_TIMEOUT
            )
            tls_socket = context.wrap_socket(raw_socket, server_hostname=host)
            tls_socket.sendall(
                (
                    f"GET {target} HTTP/1.1\r\n"
                    f"Host: {host}\r\n"
                    f"User-Agent: {DBIP_DOWNLOAD_USER_AGENT}\r\n"
                    "Accept: application/octet-stream\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii")
            )
            fallback_response = HTTPResponse(tls_socket)
            fallback_response.begin()
            if fallback_response.status != 200:
                raise HomeAssistantError(
                    "GeoIP database download returned HTTP "
                    f"{fallback_response.status}."
                )
            yield fallback_response
            return
        except (OSError, HomeAssistantError) as err:
            last_error = err
            if fallback_response is not None:
                fallback_response.close()
            elif tls_socket is not None:
                tls_socket.close()

    raise HomeAssistantError(
        f"Could not connect to the GeoIP download host: {last_error}"
    ) from last_error


def _download_geoip_database_to_path(path: Path) -> None:
    """Download and install the DB-IP City Lite database atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None

    for url in _geoip_download_urls():
        temp_path: str | None = None
        try:
            with (
                _open_geoip_download_url(url) as response,
                GzipFile(fileobj=response) as gzip_file,
                NamedTemporaryFile(
                    "wb",
                    dir=path.parent,
                    prefix=f".{path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as temp_file,
            ):
                temp_path = temp_file.name
                total = 0
                while chunk := gzip_file.read(1024 * 1024):
                    total += len(chunk)
                    if total > DBIP_DOWNLOAD_MAX_BYTES:
                        raise HomeAssistantError(
                            "GeoIP database download is too large."
                        )
                    temp_file.write(chunk)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, path)
            return
        except (BadGzipFile, EOFError, OSError, URLError, HomeAssistantError) as err:
            last_error = err
            _LOGGER.warning("GeoIP database download failed from %s: %s", url, err)
            if temp_path is not None and os.path.exists(temp_path):
                os.unlink(temp_path)

    raise HomeAssistantError(
        f"Could not download the GeoIP database: {last_error}"
    ) from last_error


async def _async_download_geoip_database(hass: HomeAssistant) -> None:
    """Download the local GeoIP database without blocking the event loop."""
    path = _geoip_database_path(hass)
    await hass.async_add_executor_job(_download_geoip_database_to_path, path)
    await _async_prepare_geoip_reader(hass)


def _close_geoip_reader(hass: HomeAssistant) -> None:
    """Close any cached GeoIP database reader."""
    reader = hass.http.app.pop(KEY_GEOIP_READER, None)
    hass.http.app.pop(KEY_GEOIP_READER_MTIME, None)
    close = getattr(reader, "close", None)
    if callable(close):
        close()


def _open_geoip_reader(path: Path) -> tuple[object, float] | None:
    """Open the local MMDB reader from an executor thread."""
    if not path.is_file():
        return None

    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None

    try:
        reader = maxminddb.open_database(str(path))
    except (OSError, RuntimeError):
        _LOGGER.warning("Could not open GeoIP database %s", path, exc_info=True)
        return None
    return reader, mtime


async def _async_prepare_geoip_reader(hass: HomeAssistant) -> None:
    """Prepare the local MMDB reader without blocking the event loop."""
    result = await hass.async_add_executor_job(
        _open_geoip_reader, _geoip_database_path(hass)
    )
    _close_geoip_reader(hass)
    if result is None:
        return

    reader, mtime = result
    hass.http.app[KEY_GEOIP_READER] = reader
    hass.http.app[KEY_GEOIP_READER_MTIME] = mtime


def _async_schedule_geoip_reader_prepare(hass: HomeAssistant) -> None:
    """Warm the local GeoIP reader without holding Home Assistant startup."""
    existing_task = hass.http.app.get(KEY_GEOIP_READER_PREPARE_TASK)
    if existing_task is not None and not existing_task.done():
        return

    task = hass.async_create_task(_async_prepare_geoip_reader(hass))
    hass.http.app[KEY_GEOIP_READER_PREPARE_TASK] = task

    def _geoip_prepare_done(done_task: Task[None]) -> None:
        hass.http.app.pop(KEY_GEOIP_READER_PREPARE_TASK, None)
        try:
            done_task.result()
        except CancelledError:
            pass
        except Exception:
            _LOGGER.warning("GeoIP reader preparation failed", exc_info=True)
        _async_update_health_issue(hass)

    task.add_done_callback(_geoip_prepare_done)


def _geoip_reader(hass: HomeAssistant) -> object | None:
    """Return the prepared MMDB reader if it is available."""
    return hass.http.app.get(KEY_GEOIP_READER)


def _localized_geoip_name(value: object) -> str | None:
    """Return an English GeoIP display name from a DB-IP/MaxMind-style field."""
    if not isinstance(value, dict):
        return None
    names = value.get("names")
    if isinstance(names, dict):
        name = names.get("en") or next(
            (item for item in names.values() if isinstance(item, str) and item),
            None,
        )
        if isinstance(name, str) and name:
            return name
    name = value.get("name")
    return name if isinstance(name, str) and name else None


def _geoip_location_for_ip(hass: HomeAssistant, remote_addr: IPAddress) -> str | None:
    """Return a human-readable local GeoIP location for a public IP address."""
    normalized_addr = _normalize_remote_addr(remote_addr)
    if normalized_addr.is_private or normalized_addr.is_loopback:
        return None

    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    if entry is not None and not _entry_geoip_enabled(entry):
        return None

    reader = _geoip_reader(hass)
    if reader is None:
        return None

    _metric_increment(hass, "geoip_lookups")
    try:
        result = cast(Any, reader).get(str(normalized_addr))
    except (ValueError, OSError, RuntimeError):
        return None
    if not isinstance(result, dict):
        return None

    city = _localized_geoip_name(result.get("city"))
    country = _localized_geoip_name(result.get("country"))
    country_code = None
    country_data = result.get("country")
    if isinstance(country_data, dict) and isinstance(country_data.get("iso_code"), str):
        country_code = country_data["iso_code"]

    parts = [part for part in (city, country or country_code) if part]
    return ", ".join(parts) if parts else None


def _geoip_status(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, object]:
    """Return GeoIP state for the live panel."""
    database_path = _geoip_database_path(hass)
    return {
        ATTR_GEOIP_ENABLED: _entry_geoip_enabled(entry),
        ATTR_GEOIP_DATABASE_PRESENT: database_path.is_file(),
        ATTR_GEOIP_DATABASE_SOURCE: DBIP_SOURCE_NAME,
        ATTR_GEOIP_DATABASE_UPDATED: _geoip_database_updated(database_path),
        ATTR_GEOIP_ATTRIBUTION: DBIP_ATTRIBUTION,
    }


def current_status(hass: HomeAssistant) -> dict[str, object]:
    """Return the live ban and allowlist status for UI surfaces."""
    ban_manager = hass.http.app.get(KEY_BAN_MANAGER)
    failed_attempts = hass.http.app.get(KEY_FAILED_LOGIN_ATTEMPTS, {})
    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    health = hass.data.get(KEY_HEALTH) or _health_status(hass)
    return {
        ATTR_NATIVE_IP_BAN_ENABLED: _native_ip_banning_enabled(hass),
        ATTR_AUTO_BAN_ENABLED: _entry_auto_ban_enabled(entry) if entry else False,
        ATTR_BAN_NOTIFICATIONS_ENABLED: (
            _entry_ban_notifications_enabled(entry) if entry else True
        ),
        ATTR_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED: (
            _entry_allowlisted_login_notifications_enabled(entry) if entry else True
        ),
        ATTR_ALLOWLISTED_LOGINS_CAN_BAN: (
            _entry_allowlisted_logins_can_ban(entry) if entry else False
        ),
        ATTR_LOGIN_ATTEMPTS_THRESHOLD: (
            _entry_login_threshold(entry, hass)
            if entry
            else _current_login_threshold(hass)
        ),
        ATTR_NETWORKS: [
            str(network) for network in hass.http.app.get(KEY_ALLOWLIST, ())
        ],
        ATTR_BLOCKED_NETWORKS: [
            str(network) for network in hass.http.app.get(KEY_BLOCKED_NETWORKS, ())
        ],
        ATTR_DEFAULT_DENY_ENABLED: (
            _entry_default_deny_enabled(entry) if entry else False
        ),
        ATTR_GEOIP_ENABLED: _entry_geoip_enabled(entry) if entry else False,
        ATTR_GEOIP_DATABASE_PRESENT: _geoip_database_path(hass).is_file(),
        ATTR_BANNED_IPS: [
            _format_ip_ban(hass, ip_ban)
            for ip_ban in (_chronological_ip_bans(ban_manager) if ban_manager else ())
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
        ATTR_METRICS: dict(_metrics(hass)),
    }


def _ip_ban_file_payload(ban_manager: IpBanManager) -> dict[str, dict[str, str]]:
    """Return the serialized ban mapping for ip_bans.yaml."""
    return {
        str(ip_ban.ip_address): {
            ATTR_BANNED_AT: (
                ip_ban.banned_at.isoformat()
                if isinstance(ip_ban.banned_at, datetime)
                else ip_ban.banned_at
            )
        }
        for ip_ban in _chronological_ip_bans(ban_manager)
    }


def _config_export_payload(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, object]:
    """Return a stable manual export payload for IP Ban Manager."""
    ban_manager = hass.http.app.get(KEY_BAN_MANAGER)
    return {
        "domain": DOMAIN,
        "format_version": CONFIG_EXPORT_FORMAT_VERSION,
        "exported_at": dt_util.utcnow().isoformat(),
        "settings": {
            CONF_IP_ADDRESSES: _entry_ip_addresses(entry),
            CONF_BLOCKED_NETWORKS: _entry_blocked_networks(entry),
            CONF_AUTO_BAN_ENABLED: _entry_auto_ban_enabled(entry),
            CONF_BAN_NOTIFICATIONS_ENABLED: _entry_ban_notifications_enabled(entry),
            CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED: (
                _entry_allowlisted_login_notifications_enabled(entry)
            ),
            CONF_ALLOWLISTED_LOGINS_CAN_BAN: _entry_allowlisted_logins_can_ban(entry),
            CONF_DEFAULT_DENY_ENABLED: _entry_default_deny_enabled(entry),
            CONF_GEOIP_ENABLED: _entry_geoip_enabled(entry),
            CONF_LOGIN_ATTEMPTS_THRESHOLD: _entry_login_threshold(entry, hass),
            CONF_SIDEBAR_PANEL_ENABLED: _entry_sidebar_panel_enabled(entry),
            CONF_SILENCED_ALLOWLISTED_LOGIN_IPS: (
                _entry_silenced_allowlisted_login_ip_strings(entry)
            ),
        },
        ATTR_BANNED_IPS: _ip_ban_file_payload(ban_manager) if ban_manager else {},
    }


async def _async_export_config(hass: HomeAssistant) -> Path:
    """Export IP Ban Manager settings to a readable integration-owned file."""
    entry = hass.http.app[KEY_CONFIG_ENTRY]
    payload = _config_export_payload(hass, entry)
    export_path = _config_export_path(hass)
    content = yaml.safe_dump(payload, sort_keys=False)
    await hass.async_add_executor_job(_atomic_write_text, str(export_path), content)
    return export_path


def _bool_from_import(settings: dict[str, object], key: str, default: bool) -> bool:
    """Read a boolean import value without treating arbitrary strings as true."""
    if key not in settings:
        return default
    value = settings[key]
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lower_value = value.lower()
        if lower_value in {"true", "yes", "on", "1"}:
            return True
        if lower_value in {"false", "no", "off", "0"}:
            return False
    raise HomeAssistantError(f"Invalid boolean value for {key}.")


def _list_from_import(settings: dict[str, object], key: str) -> list[str] | None:
    """Read an optional string list from an import settings object."""
    if key not in settings:
        return None
    value = settings[key]
    if value is None:
        return []
    if isinstance(value, str):
        return [
            line.strip()
            for line in value.replace(",", "\n").splitlines()
            if line.strip()
        ]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise HomeAssistantError(f"Invalid list value for {key}.")


def _parse_import_banned_at(value: object) -> datetime:
    """Parse an exported ban timestamp."""
    if not isinstance(value, str):
        raise HomeAssistantError("Invalid banned_at value in backup file.")
    try:
        banned_at = datetime.fromisoformat(value)
    except ValueError as err:
        raise HomeAssistantError("Invalid banned_at value in backup file.") from err
    if banned_at.tzinfo is None:
        banned_at = dt_util.as_utc(banned_at)
    return banned_at


def _imported_bans_from_payload(payload: object) -> list[IpBan]:
    """Return timestamp-preserving exact bans from an import payload."""
    if payload in (None, {}):
        return []

    bans: list[IpBan] = []
    try:
        if isinstance(payload, dict):
            for raw_ip, raw_detail in payload.items():
                remote_addr = ip_address(str(raw_ip).strip())
                banned_at = dt_util.utcnow()
                if isinstance(raw_detail, dict) and ATTR_BANNED_AT in raw_detail:
                    banned_at = _parse_import_banned_at(raw_detail[ATTR_BANNED_AT])
                bans.append(IpBan(remote_addr, banned_at))
            return bans

        if isinstance(payload, list):
            for raw_ip in payload:
                bans.append(IpBan(ip_address(str(raw_ip).split(" - ", 1)[0].strip())))
            return bans
    except ValueError as err:
        raise HomeAssistantError("Invalid IP address in banned_ips section.") from err

    raise HomeAssistantError("Invalid banned_ips section in backup file.")


async def _async_restore_exact_bans(hass: HomeAssistant, bans: list[IpBan]) -> None:
    """Replace Home Assistant's exact ban list while preserving ban timestamps."""
    ban_manager = _ban_manager(hass)
    existing_bans = ban_manager.ip_bans_lookup
    removed_addrs = set(existing_bans) - {ban.ip_address for ban in bans}

    existing_bans.clear()
    existing_bans.update(
        {
            ip_ban.ip_address: ip_ban
            for ip_ban in sorted(bans, key=_ip_ban_chronological_key)
        }
    )

    failed_attempts = hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS]
    for remote_addr in removed_addrs | set(existing_bans):
        failed_attempts.pop(remote_addr, None)

    await _async_rewrite_ip_bans_file(hass, ban_manager)
    _dismiss_removed_ip_notifications(hass, removed_addrs)


async def _async_import_config(hass: HomeAssistant) -> Path:
    """Import IP Ban Manager settings from the manual backup file."""
    export_path = _config_export_path(hass)

    def _read_export_file() -> object:
        if not export_path.is_file():
            raise HomeAssistantError(
                f"No backup file found at {_ha_config_relative_path(export_path)}."
            )
        with export_path.open(encoding="utf8") as export_file:
            try:
                return yaml.safe_load(export_file) or {}
            except yaml.YAMLError as err:
                raise HomeAssistantError("Backup file is not valid YAML.") from err

    payload = await hass.async_add_executor_job(_read_export_file)
    if not isinstance(payload, dict):
        raise HomeAssistantError("Backup file must contain a YAML mapping.")
    if payload.get("domain") not in (None, DOMAIN):
        raise HomeAssistantError("Backup file is not for IP Ban Manager.")
    if payload.get("format_version", CONFIG_EXPORT_FORMAT_VERSION) != (
        CONFIG_EXPORT_FORMAT_VERSION
    ):
        raise HomeAssistantError("Unsupported IP Ban Manager backup format.")

    settings = payload.get("settings", {})
    if not isinstance(settings, dict):
        raise HomeAssistantError("Backup file settings must be a YAML mapping.")

    entry = hass.http.app[KEY_CONFIG_ENTRY]
    from .config_flow import (
        BannedAllowlistedIPError,
        UnsafeAllowlistError,
        UnsafeBlockedNetworkError,
        _validate_ban_safety,
        _validate_blocked_networks,
        _validate_ip_addresses,
    )

    imported_allowlist = _list_from_import(settings, CONF_IP_ADDRESSES)
    imported_blocked_networks = _list_from_import(settings, CONF_BLOCKED_NETWORKS)
    imported_silenced_ips = _list_from_import(
        settings, CONF_SILENCED_ALLOWLISTED_LOGIN_IPS
    )

    try:
        allowlist = (
            _validate_ip_addresses(imported_allowlist)
            if imported_allowlist is not None
            else _entry_ip_addresses(entry)
        )
        blocked_networks = (
            _validate_blocked_networks(imported_blocked_networks)
            if imported_blocked_networks is not None
            else _entry_blocked_networks(entry)
        )
    except UnsafeAllowlistError as err:
        raise HomeAssistantError(
            "Backup file allowlist cannot allow every address."
        ) from err
    except UnsafeBlockedNetworkError as err:
        raise HomeAssistantError(
            "Backup file blocked networks cannot block every address."
        ) from err
    except ValueError as err:
        raise HomeAssistantError(
            "Backup file contains an invalid allowlist or blocked network entry."
        ) from err
    try:
        silenced_ips = (
            [str(ip_address(value)) for value in imported_silenced_ips]
            if imported_silenced_ips is not None
            else _entry_silenced_allowlisted_login_ip_strings(entry)
        )
    except ValueError as err:
        raise HomeAssistantError(
            "Invalid IP address in silenced allowlisted login notifications."
        ) from err

    auto_ban_enabled = _bool_from_import(
        settings, CONF_AUTO_BAN_ENABLED, _entry_auto_ban_enabled(entry)
    )
    ban_notifications_enabled = _bool_from_import(
        settings,
        CONF_BAN_NOTIFICATIONS_ENABLED,
        _entry_ban_notifications_enabled(entry),
    )
    allowlisted_login_notifications_enabled = _bool_from_import(
        settings,
        CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED,
        _entry_allowlisted_login_notifications_enabled(entry),
    )
    allowlisted_logins_can_ban = _bool_from_import(
        settings,
        CONF_ALLOWLISTED_LOGINS_CAN_BAN,
        _entry_allowlisted_logins_can_ban(entry),
    )
    default_deny_enabled = _bool_from_import(
        settings, CONF_DEFAULT_DENY_ENABLED, _entry_default_deny_enabled(entry)
    )
    geoip_enabled = _bool_from_import(
        settings, CONF_GEOIP_ENABLED, _entry_geoip_enabled(entry)
    )
    sidebar_panel_enabled = _bool_from_import(
        settings, CONF_SIDEBAR_PANEL_ENABLED, _entry_sidebar_panel_enabled(entry)
    )
    try:
        login_attempts_threshold = _normalize_login_attempts_threshold(
            settings.get(
                CONF_LOGIN_ATTEMPTS_THRESHOLD,
                _entry_login_threshold(entry, hass),
            )
        )
    except (TypeError, ValueError) as err:
        raise HomeAssistantError(
            "Backup file login attempts threshold must be a number."
        ) from err

    imported_bans = (
        _imported_bans_from_payload(payload[ATTR_BANNED_IPS])
        if ATTR_BANNED_IPS in payload
        else None
    )
    if imported_bans is not None:
        try:
            _validate_ban_safety(
                allowlist, [str(ban.ip_address) for ban in imported_bans]
            )
        except BannedAllowlistedIPError as err:
            raise HomeAssistantError(
                "Backup file contains an IP that is both allowed and banned."
            ) from err
    await _async_validate_panel_network_safety(
        hass, allowlist, blocked_networks, default_deny_enabled
    )

    updated_entry = _update_entry_options(
        hass,
        **{
            CONF_IP_ADDRESSES: allowlist,
            CONF_BLOCKED_NETWORKS: blocked_networks,
            CONF_AUTO_BAN_ENABLED: auto_ban_enabled,
            CONF_BAN_NOTIFICATIONS_ENABLED: ban_notifications_enabled,
            CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED: (
                allowlisted_login_notifications_enabled
            ),
            CONF_ALLOWLISTED_LOGINS_CAN_BAN: allowlisted_logins_can_ban,
            CONF_DEFAULT_DENY_ENABLED: default_deny_enabled,
            CONF_GEOIP_ENABLED: geoip_enabled,
            CONF_LOGIN_ATTEMPTS_THRESHOLD: login_attempts_threshold,
            CONF_SIDEBAR_PANEL_ENABLED: sidebar_panel_enabled,
            CONF_SILENCED_ALLOWLISTED_LOGIN_IPS: silenced_ips,
        },
    )
    hass.http.app[KEY_ALLOWLIST] = _parse_allowlist(allowlist)
    _apply_ban_settings(hass, updated_entry)
    _apply_blocked_networks(hass, updated_entry)
    if geoip_enabled and _geoip_database_path(hass).is_file():
        await _async_prepare_geoip_reader(hass)
    elif not geoip_enabled:
        _close_geoip_reader(hass)
    await _async_register_panel(hass, sidebar_enabled=sidebar_panel_enabled)
    if imported_bans is not None:
        await _async_restore_exact_bans(hass, imported_bans)
    return export_path


async def _async_rewrite_ip_bans_file(
    hass: HomeAssistant, ban_manager: IpBanManager
) -> None:
    """Rewrite ip_bans.yaml from a stable snapshot of the live ban manager."""
    lock = hass.data.setdefault(KEY_BAN_FILE_WRITE_LOCK, Lock())
    async with lock:
        ban_path = ban_manager.path
        ip_bans = _ip_ban_file_payload(ban_manager)
        snapshots = _snapshot_dir(hass)

        def _write_bans() -> bool:
            path = Path(ban_path)
            snapshot_created = _snapshot_existing_file(path, snapshots)
            if not ip_bans:
                path.unlink(missing_ok=True)
                return snapshot_created

            _atomic_write_text(
                ban_path,
                yaml.safe_dump(ip_bans, sort_keys=False),
            )
            return snapshot_created

        if await hass.async_add_executor_job(_write_bans):
            _metric_increment(hass, "snapshots_created")
        _mark_config_write(hass)


def _update_allowlist_entry(hass: HomeAssistant, ip_addresses: list[str]) -> None:
    """Persist and apply the current allowlist without a Home Assistant restart."""
    _update_entry_options(hass, **{CONF_IP_ADDRESSES: ip_addresses})
    hass.http.app[KEY_ALLOWLIST] = _parse_allowlist(ip_addresses)
    _apply_blocked_networks(hass, hass.http.app[KEY_CONFIG_ENTRY])


def _update_blocked_networks_entry(hass: HomeAssistant, networks: list[str]) -> None:
    """Persist and apply blocked networks without a Home Assistant restart."""
    _update_entry_options(hass, **{CONF_BLOCKED_NETWORKS: networks})
    _apply_blocked_networks(hass, hass.http.app[KEY_CONFIG_ENTRY])


def _current_allowlist_strings(hass: HomeAssistant) -> list[str]:
    """Return the persisted allowlist strings."""
    return _entry_ip_addresses(hass.http.app[KEY_CONFIG_ENTRY])


def _current_blocked_network_strings(hass: HomeAssistant) -> list[str]:
    """Return the persisted blocked network strings."""
    return _entry_blocked_networks(hass.http.app[KEY_CONFIG_ENTRY])


async def _async_validate_panel_network_safety(
    hass: HomeAssistant,
    allowlist: list[str],
    blocked_networks: list[str],
    default_deny_enabled: bool,
) -> None:
    """Validate panel network edits against detected local access paths."""
    await _async_update_internal_bypass_networks(hass)

    from .config_flow import (
        UnprotectedLocalBlockError,
        _async_detect_home_assistant_subnets,
        _validate_local_block_safety,
    )

    try:
        _validate_local_block_safety(
            allowlist,
            blocked_networks,
            await _async_detect_home_assistant_subnets(hass),
            default_deny_enabled,
        )
    except UnprotectedLocalBlockError as err:
        raise HomeAssistantError(str(err)) from err


async def _async_panel_add_allowlist_network(
    hass: HomeAssistant, network_value: str
) -> None:
    """Add an allowlist network from the panel."""
    _async_add_allowlist_network(hass, network_value)


async def _async_panel_remove_allowlist_network(
    hass: HomeAssistant, network_value: str
) -> None:
    """Remove an allowlist network from the panel after safety checks."""
    network = parse_allowlist_network(network_value)
    remaining_networks = [
        current_network
        for current_network in _current_allowlist_strings(hass)
        if parse_allowlist_network(current_network) != network
    ]
    await _async_validate_panel_network_safety(
        hass,
        remaining_networks,
        _current_blocked_network_strings(hass),
        bool(hass.http.app.get(KEY_DEFAULT_DENY, False)),
    )
    _update_allowlist_entry(hass, remaining_networks)


async def _async_panel_add_blocked_network(
    hass: HomeAssistant, network_value: str
) -> None:
    """Add a managed blocked network from the panel."""
    network = parse_allowlist_network(network_value)
    if network.prefixlen == 0:
        raise HomeAssistantError("Blocking every address belongs in default-deny mode.")

    current = _current_blocked_network_strings(hass)
    normalized_network = str(network)
    current_networks = {
        parse_allowlist_network(current_network) for current_network in current
    }
    if network in current_networks:
        return

    updated = [*current, normalized_network]
    await _async_validate_panel_network_safety(
        hass,
        _current_allowlist_strings(hass),
        updated,
        bool(hass.http.app.get(KEY_DEFAULT_DENY, False)),
    )
    _update_blocked_networks_entry(hass, updated)


async def _async_panel_remove_blocked_network(
    hass: HomeAssistant, network_value: str
) -> None:
    """Remove a managed blocked network from the panel."""
    network = parse_allowlist_network(network_value)
    remaining_networks = [
        current_network
        for current_network in _current_blocked_network_strings(hass)
        if parse_allowlist_network(current_network) != network
    ]
    _update_blocked_networks_entry(hass, remaining_networks)


async def _async_panel_set_options(hass: HomeAssistant, options: object) -> None:
    """Persist and apply panel-managed booleans and threshold."""
    if not isinstance(options, dict):
        raise HomeAssistantError("Options must be a JSON object.")

    entry = hass.http.app[KEY_CONFIG_ENTRY]
    current_options = {
        CONF_AUTO_BAN_ENABLED: _entry_auto_ban_enabled(entry),
        CONF_BAN_NOTIFICATIONS_ENABLED: _entry_ban_notifications_enabled(entry),
        CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED: (
            _entry_allowlisted_login_notifications_enabled(entry)
        ),
        CONF_ALLOWLISTED_LOGINS_CAN_BAN: _entry_allowlisted_logins_can_ban(entry),
        CONF_DEFAULT_DENY_ENABLED: _entry_default_deny_enabled(entry),
        CONF_GEOIP_ENABLED: _entry_geoip_enabled(entry),
        CONF_LOGIN_ATTEMPTS_THRESHOLD: _entry_login_threshold(entry, hass),
        CONF_SIDEBAR_PANEL_ENABLED: _entry_sidebar_panel_enabled(entry),
    }
    for key in (
        CONF_AUTO_BAN_ENABLED,
        CONF_BAN_NOTIFICATIONS_ENABLED,
        CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED,
        CONF_ALLOWLISTED_LOGINS_CAN_BAN,
        CONF_DEFAULT_DENY_ENABLED,
        CONF_GEOIP_ENABLED,
        CONF_SIDEBAR_PANEL_ENABLED,
    ):
        if key in options:
            current_options[key] = bool(options[key])

    if CONF_LOGIN_ATTEMPTS_THRESHOLD in options:
        current_options[CONF_LOGIN_ATTEMPTS_THRESHOLD] = (
            _normalize_login_attempts_threshold(options[CONF_LOGIN_ATTEMPTS_THRESHOLD])
        )

    await _async_validate_panel_network_safety(
        hass,
        _current_allowlist_strings(hass),
        _current_blocked_network_strings(hass),
        bool(current_options[CONF_DEFAULT_DENY_ENABLED]),
    )
    if current_options[CONF_GEOIP_ENABLED]:
        geoip_path = _geoip_database_path(hass)
        if await hass.async_add_executor_job(_path_is_file, geoip_path):
            await _async_prepare_geoip_reader(hass)
        else:
            await _async_download_geoip_database(hass)
    else:
        _close_geoip_reader(hass)
    entry = _update_entry_options(hass, **current_options)
    _apply_ban_settings(hass, entry)
    _apply_blocked_networks(hass, entry)
    await _async_register_panel(
        hass, sidebar_enabled=bool(current_options[CONF_SIDEBAR_PANEL_ENABLED])
    )


def _panel_silence_allowlisted_login_notification(
    hass: HomeAssistant,
    ip_address_value: str,
    notification_id: object,
) -> None:
    """Silence allowlisted login notifications from a panel action link."""
    entry = hass.http.app[KEY_CONFIG_ENTRY]
    try:
        remote_addr = ip_address(ip_address_value)
    except ValueError as err:
        raise HomeAssistantError("Invalid IP address.") from err

    _silence_allowlisted_login_notifications(
        hass,
        entry,
        remote_addr,
        notification_id if isinstance(notification_id, str) else None,
    )


def _panel_unsilence_allowlisted_login_notification(
    hass: HomeAssistant,
    ip_address_value: str,
) -> None:
    """Unsilence allowlisted login notifications from the admin panel API."""
    entry = hass.http.app[KEY_CONFIG_ENTRY]
    try:
        remote_addr = ip_address(ip_address_value)
    except ValueError as err:
        raise HomeAssistantError("Invalid IP address.") from err

    _unsilence_allowlisted_login_notifications(hass, entry, remote_addr)


def _async_cleanup_entry_metadata(hass: HomeAssistant, entry: ConfigEntry) -> None:
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

    if isinstance(legacy_entry_id, str):
        legacy_entry = hass.config_entries.async_get_entry(legacy_entry_id)
        if legacy_entry is not None and legacy_entry.domain == LEGACY_DOMAIN:
            _LOGGER.info("Removing migrated legacy ban_allowlist config entry")

            async def _remove_migrated_legacy_entry() -> None:
                if hass.config_entries.async_get_entry(legacy_entry_id) is None:
                    return
                with suppress(UnknownEntry):
                    await hass.config_entries.async_remove(legacy_entry_id)

            hass.async_create_task(_remove_migrated_legacy_entry())


@callback
def _async_remove_legacy_entries(hass: HomeAssistant) -> None:
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

        hass.async_create_task(_remove_legacy_entry())


def _cleanup_destination(cleanup_root: Path, name: str, timestamp: str) -> Path:
    """Return a non-existing cleanup destination path."""
    destination = cleanup_root / f"{name}-{timestamp}"
    suffix = 2
    while destination.exists():
        destination = cleanup_root / f"{name}-{timestamp}-{suffix}"
        suffix += 1
    return destination


def _move_to_cleanup(
    cleanup_root: Path, source: Path, name: str, timestamp: str
) -> str | None:
    """Move a stale path into cleanup storage and return a failed source path."""
    try:
        cleanup_root.mkdir(parents=True, exist_ok=True)
        destination = _cleanup_destination(cleanup_root, name, timestamp)
        shutil.move(str(source), str(destination))
    except (OSError, shutil.Error):
        _LOGGER.warning("Could not move stale cleanup path %s", source, exc_info=True)
        return str(source)

    _LOGGER.info("Moved stale cleanup path %s to %s", source, destination)
    return None


def _move_legacy_component_folder(hass: HomeAssistant) -> list[str]:
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
                    failure = _move_to_cleanup(
                        cleanup_root,
                        legacy_path,
                        LEGACY_DOMAIN,
                        timestamp,
                    )
                    if failure is not None:
                        failures.append(failure)

    old_backup_root = Path(hass.config.path(LEGACY_BACKUP_DIR))
    if old_backup_root.is_dir():
        failure = _move_to_cleanup(
            cleanup_root,
            old_backup_root,
            LEGACY_BACKUP_DIR,
            timestamp,
        )
        if failure is not None:
            failures.append(failure)

    return failures


async def _async_cleanup_legacy_component_folder(hass: HomeAssistant) -> None:
    """Move stale legacy files once the new integration is running."""
    if hass.data.get(KEY_LEGACY_FOLDER_CLEANED):
        return
    hass.data[KEY_LEGACY_FOLDER_CLEANED] = True
    failures = await hass.async_add_executor_job(_move_legacy_component_folder, hass)
    _async_update_legacy_folder_cleanup_issue(hass, failures)


def _async_schedule_legacy_folder_cleanup(hass: HomeAssistant) -> None:
    """Move stale legacy files in the background after startup-critical setup."""
    existing_task = hass.data.get(KEY_LEGACY_FOLDER_CLEANUP_TASK)
    if existing_task is not None and not existing_task.done():
        return

    task = hass.async_create_task(_async_cleanup_legacy_component_folder(hass))
    hass.data[KEY_LEGACY_FOLDER_CLEANUP_TASK] = task

    def _legacy_folder_cleanup_done(done_task: Task[None]) -> None:
        hass.data.pop(KEY_LEGACY_FOLDER_CLEANUP_TASK, None)
        try:
            done_task.result()
        except CancelledError:
            pass
        except Exception:
            _LOGGER.warning("Legacy folder cleanup failed", exc_info=True)
        _async_update_health_issue(hass)

    task.add_done_callback(_legacy_folder_cleanup_done)


def _async_schedule_legacy_cleanup(hass: HomeAssistant) -> None:
    """Remove old-domain entries now and once Home Assistant has started."""
    _async_remove_legacy_entries(hass)

    if hass.data.get(KEY_LEGACY_CLEANUP_SCHEDULED):
        return

    hass.data[KEY_LEGACY_CLEANUP_SCHEDULED] = True
    async_at_started(hass, _async_remove_legacy_entries)


def _dismiss_removed_ip_notifications(
    hass: HomeAssistant, removed_addrs: Iterable[IPAddress]
) -> None:
    """Dismiss Home Assistant HTTP notifications for IPs that were unbanned."""
    from homeassistant.components import persistent_notification

    removed_ips = {str(remote_addr) for remote_addr in removed_addrs}
    if not removed_ips:
        return

    notifications = persistent_notification._async_get_or_create_notifications(
        hass
    )  # noqa: SLF001

    _dismiss_ban_notification_for_ips(hass, {ip_address(ip) for ip in removed_ips})

    login_notification = notifications.get(NOTIFICATION_ID_LOGIN)
    if login_notification and any(
        removed_ip in login_notification["message"] for removed_ip in removed_ips
    ):
        persistent_notification.async_dismiss(hass, NOTIFICATION_ID_LOGIN)


async def _async_add_ip_ban(hass: HomeAssistant, ip_address_value: str) -> None:
    """Add an IP ban immediately."""
    try:
        remote_addr = ip_address(ip_address_value)
    except ValueError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_ip_address",
            translation_placeholders={ATTR_IP_ADDRESS: ip_address_value},
        ) from err

    if _is_allowed(
        remote_addr,
        hass.http.app.get(
            KEY_INTERNAL_BYPASS_NETWORKS, _supervisor_internal_networks()
        ),
    ):
        raise ServiceValidationError(
            f"{remote_addr} is a Home Assistant internal address."
        )

    if _is_allowed(remote_addr, hass.http.app.get(KEY_ALLOWLIST, ())):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="ip_address_allowlisted",
            translation_placeholders={ATTR_IP_ADDRESS: str(remote_addr)},
        )

    if _is_blocked(remote_addr, hass.http.app.get(KEY_BLOCKED_NETWORKS, ())):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="ip_address_blocked_network",
            translation_placeholders={ATTR_IP_ADDRESS: str(remote_addr)},
        )

    await _ban_manager(hass).async_add_ban(remote_addr)
    hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS].pop(remote_addr, None)


async def _async_remove_ip_ban(hass: HomeAssistant, ip_address_value: str) -> None:
    """Remove an IP ban immediately."""
    try:
        remote_addr = ip_address(ip_address_value)
    except ValueError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_ip_address",
            translation_placeholders={ATTR_IP_ADDRESS: ip_address_value},
        ) from err

    ban_manager = _ban_manager(hass)
    removed_ban = ban_manager.ip_bans_lookup.pop(remote_addr, None)
    if removed_ban is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="ip_address_not_banned",
            translation_placeholders={ATTR_IP_ADDRESS: str(remote_addr)},
        )

    hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS].pop(remote_addr, None)
    await _async_rewrite_ip_bans_file(hass, ban_manager)
    _dismiss_removed_ip_notifications(hass, [remote_addr])


async def _async_remove_all_ip_bans(hass: HomeAssistant) -> None:
    """Remove every IP ban immediately."""
    ban_manager = _ban_manager(hass)
    removed_addrs = list(ban_manager.ip_bans_lookup)
    ban_manager.ip_bans_lookup.clear()
    hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS].clear()
    await _async_rewrite_ip_bans_file(hass, ban_manager)
    _dismiss_removed_ip_notifications(hass, removed_addrs)


async def _async_replace_ip_bans(
    hass: HomeAssistant, ip_address_values: list[str]
) -> None:
    """Replace the live IP ban list immediately."""
    remote_addrs = [
        ip_address(ip_address_value) for ip_address_value in ip_address_values
    ]
    remote_addr_set = set(remote_addrs)

    ban_manager = _ban_manager(hass)
    existing_bans = ban_manager.ip_bans_lookup
    preserved_bans = dict(existing_bans)
    removed_addrs = set(preserved_bans) - remote_addr_set
    updated_bans = {
        remote_addr: preserved_bans.get(remote_addr, IpBan(remote_addr))
        for remote_addr in remote_addrs
    }
    existing_bans.clear()
    existing_bans.update(
        {
            ip_ban.ip_address: ip_ban
            for ip_ban in sorted(
                updated_bans.values(),
                key=_ip_ban_chronological_key,
            )
        }
    )

    failed_attempts = hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS]
    for remote_addr in removed_addrs | remote_addr_set:
        failed_attempts.pop(remote_addr, None)

    await _async_rewrite_ip_bans_file(hass, ban_manager)
    _dismiss_removed_ip_notifications(hass, removed_addrs)


def _chronological_ip_bans(ban_manager: IpBanManager) -> list[IpBan]:
    """Return IP bans ordered by oldest ban first."""
    return sorted(
        ban_manager.ip_bans_lookup.values(),
        key=_ip_ban_chronological_key,
    )


def _ip_ban_chronological_key(ip_ban: IpBan) -> tuple[datetime, int, bytes]:
    """Return a stable chronological sort key for an IP ban."""
    banned_at = ip_ban.banned_at
    if banned_at.tzinfo is None:
        banned_at = dt_util.as_utc(banned_at)
    return (banned_at, ip_ban.ip_address.version, ip_ban.ip_address.packed)


def _async_add_allowlist_network(hass: HomeAssistant, network_value: str) -> None:
    """Add an allowlist network immediately."""
    try:
        network = parse_allowlist_network(network_value)
    except ValueError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_network",
            translation_placeholders={ATTR_NETWORK: network_value},
        ) from err

    if network.prefixlen == 0:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unsafe_allowlist_network",
            translation_placeholders={ATTR_NETWORK: str(network)},
        )

    banned_ips = _ban_manager(hass).ip_bans_lookup
    if any(banned_ip in network for banned_ip in banned_ips):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="network_contains_banned_ip",
            translation_placeholders={ATTR_NETWORK: str(network)},
        )

    current = _current_allowlist_strings(hass)
    normalized_network = str(network)
    current_networks = {
        parse_allowlist_network(current_network) for current_network in current
    }
    if network not in current_networks:
        _update_allowlist_entry(hass, [*current, normalized_network])


async def _async_remove_allowlist_network(
    hass: HomeAssistant, network_value: str
) -> None:
    """Remove an allowlist network immediately."""
    try:
        network = parse_allowlist_network(network_value)
    except ValueError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_network",
            translation_placeholders={ATTR_NETWORK: network_value},
        ) from err

    remaining_networks = [
        current_network
        for current_network in _current_allowlist_strings(hass)
        if parse_allowlist_network(current_network) != network
    ]
    try:
        await _async_validate_panel_network_safety(
            hass,
            remaining_networks,
            _current_blocked_network_strings(hass),
            bool(hass.http.app.get(KEY_DEFAULT_DENY, False)),
        )
    except HomeAssistantError as err:
        raise ServiceValidationError(str(err)) from err
    _update_allowlist_entry(hass, remaining_networks)


def _register_services(hass: HomeAssistant) -> None:  # noqa: D202
    """Register live ban and allowlist management services."""

    async def add_ip_ban(call: ServiceCall) -> None:
        await _async_add_ip_ban(hass, call.data[ATTR_IP_ADDRESS])

    async def remove_ip_ban(call: ServiceCall) -> None:
        await _async_remove_ip_ban(hass, call.data[ATTR_IP_ADDRESS])

    async def remove_all_ip_bans(call: ServiceCall) -> None:
        if not call.data[ATTR_CONFIRM]:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="clear_all_ip_bans_confirmation_required",
            )
        await _async_remove_all_ip_bans(hass)

    async def add_allowlist_network(call: ServiceCall) -> None:
        _async_add_allowlist_network(hass, call.data[ATTR_NETWORK])

    async def remove_allowlist_network(call: ServiceCall) -> None:
        await _async_remove_allowlist_network(hass, call.data[ATTR_NETWORK])

    async def export_config(call: ServiceCall) -> None:
        await _async_export_config(hass)

    async def import_config(call: ServiceCall) -> None:
        await _async_import_config(hass)

    hass.services.async_register(
        DOMAIN, SERVICE_ADD_IP_BAN, add_ip_ban, schema=IP_ADDRESS_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REMOVE_IP_BAN, remove_ip_ban, schema=IP_ADDRESS_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_ALL_IP_BANS,
        remove_all_ip_bans,
        schema=REMOVE_ALL_IP_BANS_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_ALLOWLIST_NETWORK,
        add_allowlist_network,
        schema=NETWORK_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_ALLOWLIST_NETWORK,
        remove_allowlist_network,
        schema=NETWORK_SCHEMA,
    )
    hass.services.async_register(DOMAIN, SERVICE_EXPORT_CONFIG, export_config)
    hass.services.async_register(DOMAIN, SERVICE_IMPORT_CONFIG, import_config)


async def _async_register_static_assets(hass: HomeAssistant) -> None:
    """Register stable local URLs for notification assets."""
    if hass.http.app.get(KEY_STATIC_PATH_REGISTERED):
        return

    icon_path = str(Path(__file__).with_name("icon.png"))
    panel_path = str(Path(__file__).with_name("panel.js"))
    if hasattr(hass.http, "async_register_static_paths"):
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    NOTIFICATION_ICON_URL,
                    icon_path,
                    cache_headers=True,
                ),
                StaticPathConfig(
                    PANEL_JS_URL,
                    panel_path,
                    cache_headers=False,
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
        register_static_path(
            PANEL_JS_URL,
            panel_path,
            cache_headers=False,
        )
    hass.http.app[KEY_STATIC_PATH_REGISTERED] = True


async def _async_register_panel(
    hass: HomeAssistant, *, sidebar_enabled: bool = True
) -> None:
    """Register the bundled IP Ban Manager panel."""
    if (
        hass.data.get(KEY_PANEL_REGISTERED)
        and hass.data.get(KEY_PANEL_SIDEBAR_ENABLED) == sidebar_enabled
    ):
        return

    if hass.data.get(KEY_PANEL_REGISTERED):
        _async_remove_panel(hass)

    from homeassistant.components import panel_custom

    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=DOMAIN,
        webcomponent_name=PANEL_WEB_COMPONENT,
        sidebar_title=ENTRY_TITLE if sidebar_enabled else None,
        sidebar_icon="mdi:shield-lock-outline" if sidebar_enabled else None,
        module_url=PANEL_JS_URL,
        require_admin=True,
        config_panel_domain=DOMAIN,
    )
    hass.data[KEY_PANEL_REGISTERED] = True
    hass.data[KEY_PANEL_SIDEBAR_ENABLED] = sidebar_enabled


def _async_remove_panel(hass: HomeAssistant) -> None:
    """Remove the bundled panel during unload."""
    if not hass.data.pop(KEY_PANEL_REGISTERED, False):
        return
    hass.data.pop(KEY_PANEL_SIDEBAR_ENABLED, None)

    from homeassistant.components import frontend

    frontend.async_remove_panel(hass, DOMAIN, warn_if_unknown=False)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up IP Ban Manager and import YAML configuration."""
    emergency_disabled = _emergency_disable_requested(hass, config)
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
                context={"source": SOURCE_IMPORT},
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
    hass.http.register_view(SilenceAllowlistedLoginNotificationsView())
    hass.http.register_view(IPBanManagerStatusView())
    hass.http.register_view(IPBanManagerManageView())
    _LOGGER.debug("Ban manager %s", ban_manager)
    _install_load_bans_patch(hass, ban_manager)
    await _async_update_internal_bypass_networks(hass)
    _apply_ban_settings(hass, entry)
    _apply_blocked_networks(hass, entry)
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
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_update_health_issue(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload IP Ban Manager."""
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
    hass.http.app.pop(KEY_REVERSE_DNS_CACHE, None)
    hass.data.pop(KEY_HEALTH, None)
    hass.data.pop(KEY_METRICS, None)
    hass.data.pop(KEY_BAN_FILE_WRITE_LOCK, None)
    for service in (
        SERVICE_ADD_ALLOWLIST_NETWORK,
        SERVICE_ADD_IP_BAN,
        SERVICE_EXPORT_CONFIG,
        SERVICE_IMPORT_CONFIG,
        SERVICE_REMOVE_ALL_IP_BANS,
        SERVICE_REMOVE_ALLOWLIST_NETWORK,
        SERVICE_REMOVE_IP_BAN,
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
    await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return True
