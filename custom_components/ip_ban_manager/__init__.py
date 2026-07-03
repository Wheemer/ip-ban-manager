"""The IP Ban Manager integration."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from collections.abc import Awaitable, Callable, Collection, Iterable
from contextlib import suppress
from datetime import datetime
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
)
from pathlib import Path
from socket import gethostbyaddr, herror
from tempfile import NamedTemporaryFile
from urllib.parse import urlencode

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
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry, UnknownEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util
from voluptuous.schema_builder import Optional as vol_optional

from .const import (
    ATTR_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED,
    ATTR_ALLOWLISTED_LOGINS_CAN_BAN,
    ATTR_AUTO_BAN_ENABLED,
    ATTR_BAN_NOTIFICATIONS_ENABLED,
    ATTR_BANNED_IPS,
    ATTR_BLOCKED_NETWORKS,
    ATTR_CONFIRM,
    ATTR_DEFAULT_DENY_ENABLED,
    ATTR_FAILED_LOGIN_ATTEMPTS,
    ATTR_IP_ADDRESS,
    ATTR_LOGIN_ATTEMPTS_THRESHOLD,
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
    CONF_IP_ADDRESSES,
    CONF_LOGIN_ATTEMPTS_THRESHOLD,
    CONF_SIDEBAR_PANEL_ENABLED,
    CONF_SILENCED_ALLOWLISTED_LOGIN_IPS,
    DEFAULT_LOGIN_ATTEMPTS_THRESHOLD,
    DOMAIN,
    LEGACY_DOMAIN,
    SERVICE_ADD_ALLOWLIST_NETWORK,
    SERVICE_ADD_IP_BAN,
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
ALLOWLISTED_LOGIN_ESCALATION_THRESHOLD = 10
HTTP_IP_BAN_DOCS_URL = (
    "https://www.home-assistant.io/integrations/http/#ip-filtering-and-banning"
)
INTEGRATION_CONFIG_URL = f"/config/integrations/integration/{DOMAIN}"
CONFIG_ENTRY_URL_TEMPLATE = (
    f"/config/integrations/integration/{DOMAIN}?config_entry={{entry_id}}"
)
NOTIFICATION_LINK_LABEL = "Open settings"
ALLOWLISTED_LOGIN_SILENCE_LABEL = "Don't show for this address again"
ALLOWLISTED_LOGIN_SILENCE_URL = f"/api/{DOMAIN}/silence_allowlisted_login_notifications"
ENTRY_TITLE = "IP Ban Manager"
LEGACY_ENTRY_TITLES = {"IP Ban Allowlist", "ban_allowlist"}
NOTIFICATION_TITLE = " "
NOTIFICATION_ICON_URL = f"/api/{DOMAIN}/icon.png"
PANEL_WEB_COMPONENT = "ip-ban-manager-panel-v9"
PANEL_JS_URL = f"/api/{DOMAIN}/panel-v9.js"
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
KEY_ORIGINAL_ADD_BAN = AppKey[AddBanCallable]("ip_ban_manager_original_add_ban")
KEY_ORIGINAL_LOAD_BANS = AppKey[LoadBansCallable]("ip_ban_manager_original_load_bans")
KEY_STATIC_PATH_REGISTERED = AppKey[bool]("ip_ban_manager_static_path_registered")
KEY_PANEL_REGISTERED = AppKey[bool]("ip_ban_manager_panel_registered")
KEY_PANEL_SIDEBAR_ENABLED = AppKey[bool]("ip_ban_manager_panel_sidebar_enabled")
KEY_DISABLED_BY_YAML = AppKey[bool]("ip_ban_manager_disabled_by_yaml")
KEY_LEGACY_CLEANUP_SCHEDULED = AppKey[bool]("ip_ban_manager_legacy_cleanup_scheduled")
KEY_LEGACY_FOLDER_CLEANED = AppKey[bool]("ip_ban_manager_legacy_folder_cleaned")
LEGACY_BACKUP_DIR = "ip_ban_manager_legacy_backup"
LEGACY_CLEANUP_DIR = ".cleanup"

PLATFORMS = ["sensor"]

_ORIGINAL_PROCESS_WRONG_LOGIN = http_ban.process_wrong_login

CONFIG_SCHEMA = vol.Schema(
    {
        vol_optional(DOMAIN): vol.Schema(
            {
                vol_optional(CONF_DISABLE_BAN_MANAGER, default=False): cv.boolean,
                vol_optional(CONF_IP_ADDRESSES): vol.All(cv.ensure_list, [cv.string]),
            }
        ),
        vol_optional(LEGACY_DOMAIN): vol.Schema(
            {
                vol_optional(CONF_DISABLE_BAN_MANAGER, default=False): cv.boolean,
                vol_optional(CONF_IP_ADDRESSES): vol.All(cv.ensure_list, [cv.string]),
            }
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
    ) -> None:
        """Initialize the lookup from Home Assistant's exact IP bans."""
        super().__init__(values)
        self.blocked_networks = blocked_networks
        self.allowlist = allowlist
        self.default_deny_enabled = default_deny_enabled

    def __contains__(self, key: object) -> bool:
        """Return whether an IP is exactly banned or blocked by network."""
        if dict.__contains__(self, key):
            return True

        if not isinstance(key, (IPv4Address, IPv6Address)):
            return False

        remote_addr = _normalize_remote_addr(key)
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
    _handle_http_notifications(hass)
    _LOGGER.info(
        "Allowlisted address %s failed authentication but was not banned",
        remote_addr,
    )


async def _process_allowlisted_wrong_login(
    request: Request, remote_addr: IPAddress
) -> None:
    """Record an allowlisted failed login without letting it become a ban."""
    hass = request.app[KEY_HASS]
    remote_host = request.remote or str(remote_addr)
    with suppress(herror):
        remote_host, _, _ = await hass.async_add_executor_job(
            gethostbyaddr, str(remote_addr)
        )

    base_msg = (
        "Login attempt or request with invalid authentication from"
        f" {remote_host} ({remote_addr})."
    )
    user_agent = request.headers.get("user-agent")
    log_msg = f"{base_msg} Requested URL: '{request.rel_url}'. ({user_agent})"
    notification_msg = f"{base_msg} See the log for details."

    logging.getLogger("homeassistant.components.http.ban").warning(log_msg)

    if KEY_BAN_MANAGER in request.app and request.app[KEY_LOGIN_THRESHOLD] >= 1:
        request.app[KEY_FAILED_LOGIN_ATTEMPTS][remote_addr] += 1

    _create_allowlisted_login_notification(hass, remote_addr, notification_msg)


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


def _allowlisted_login_silence_url(remote_addr: IPAddress) -> str:
    """Return the per-address allowlisted-login silence URL."""
    return f"{ALLOWLISTED_LOGIN_SILENCE_URL}?{urlencode({ATTR_IP_ADDRESS: str(remote_addr)})}"


def _with_allowlisted_login_silence_link(message: str, remote_addr: IPAddress) -> str:
    """Append the allowlisted-login silence link once."""
    if ALLOWLISTED_LOGIN_SILENCE_LABEL in message:
        return message
    return (
        f"{message}\n\n"
        f"[{ALLOWLISTED_LOGIN_SILENCE_LABEL}]"
        f"({_allowlisted_login_silence_url(remote_addr)})"
    )


def _dismiss_allowlisted_login_notifications(
    hass: HomeAssistant, remote_addr: IPAddress | None = None
) -> None:
    """Dismiss allowlisted-login notifications, including rewritten variants."""
    from homeassistant.components import persistent_notification

    notifications = persistent_notification._async_get_or_create_notifications(hass)
    matching_ids = {NOTIFICATION_ID_LOGIN}
    for notification_id, notification in notifications.items():
        message = notification["message"]
        if ALLOWLISTED_LOGIN_SILENCE_URL in message or (
            remote_addr is not None
            and str(remote_addr) in message
            and "Allowlisted login" in message
        ):
            matching_ids.add(notification_id)

    for notification_id in matching_ids:
        persistent_notification.async_dismiss(hass, notification_id)


def _notification_heading(notification_id: str, message: str) -> str:
    """Return the short message heading for a Home Assistant HTTP notification."""
    if notification_id == NOTIFICATION_ID_BAN:
        return "IP banned"
    if "allowlisted" in message.lower():
        if "threshold" in message.lower():
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


def _entry_silenced_allowlisted_login_ips(entry: ConfigEntry) -> set[IPAddress]:
    """Return allowlisted addresses with low-priority login notices silenced."""
    values = entry.options.get(
        CONF_SILENCED_ALLOWLISTED_LOGIN_IPS,
        entry.data.get(CONF_SILENCED_ALLOWLISTED_LOGIN_IPS, []),
    )
    silenced: set[IPAddress] = set()
    for value in values if isinstance(values, list) else []:
        with suppress(ValueError):
            silenced.add(ip_address(value))
    return silenced


def _should_notify_allowlisted_login(
    hass: HomeAssistant, remote_addr: IPAddress, attempts: int
) -> bool:
    """Return whether an allowlisted failed login should notify the user."""
    if attempts >= ALLOWLISTED_LOGIN_ESCALATION_THRESHOLD:
        return True

    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    if entry is None:
        return True

    if remote_addr in _entry_silenced_allowlisted_login_ips(entry):
        return False

    return _entry_allowlisted_login_notifications_enabled(entry)


def _create_allowlisted_login_notification(
    hass: HomeAssistant, remote_addr: IPAddress, base_message: str
) -> None:
    """Create an IP Ban Manager failed-login notification for an allowlisted source."""
    from homeassistant.components import persistent_notification

    failed_attempts = hass.http.app.get(KEY_FAILED_LOGIN_ATTEMPTS, {})
    attempts = int(failed_attempts.get(remote_addr, 0))
    threshold = int(hass.http.app.get(KEY_LOGIN_THRESHOLD, 0))
    if not _should_notify_allowlisted_login(hass, remote_addr, attempts):
        return

    details = [base_message]
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
    if attempts < ALLOWLISTED_LOGIN_ESCALATION_THRESHOLD:
        message = _with_allowlisted_login_silence_link(message, remote_addr)
    persistent_notification.async_create(
        hass,
        message,
        NOTIFICATION_TITLE,
        NOTIFICATION_ID_LOGIN,
    )


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
        if (
            notification_id != NOTIFICATION_ID_LOGIN
            or "allowlisted" not in message.lower()
        ):
            message = _with_manager_link(hass, message)
        if (
            message == notification["message"]
            and notification["title"] == NOTIFICATION_TITLE
        ):
            continue

        persistent_notification.async_create(
            hass,
            message,
            NOTIFICATION_TITLE,
            notification_id,
        )


class SilenceAllowlistedLoginNotificationsView(HomeAssistantView):
    """Silence low-priority allowlisted failed-login notifications from a notification link."""

    name = "api:ip_ban_manager:silence_allowlisted_login_notifications"
    url = ALLOWLISTED_LOGIN_SILENCE_URL

    async def get(self, request: Request) -> Response:
        """Silence allowlisted failed-login notifications and dismiss the current notification."""
        hass = request.app[KEY_HASS]
        entry = hass.http.app.get(KEY_CONFIG_ENTRY)
        if entry is None:
            return Response(text="IP Ban Manager is not loaded.", status=404)

        ip_address_value = getattr(request, "query", {}).get(ATTR_IP_ADDRESS)
        if ip_address_value:
            try:
                remote_addr = ip_address(ip_address_value)
            except ValueError:
                return Response(text="Invalid IP address.", status=400)

            silenced_ips = [
                str(address) for address in _entry_silenced_allowlisted_login_ips(entry)
            ]
            if str(remote_addr) not in silenced_ips:
                silenced_ips.append(str(remote_addr))

            hass.config_entries.async_update_entry(
                entry,
                options={
                    **entry.options,
                    CONF_SILENCED_ALLOWLISTED_LOGIN_IPS: silenced_ips,
                },
            )
            _dismiss_allowlisted_login_notifications(hass, remote_addr)
            return Response(
                text=(
                    f"Allowlisted login notifications from {remote_addr} are now "
                    "silenced. IP Ban Manager will still notify after repeated "
                    "failures."
                ),
                content_type="text/plain",
            )

        hass.config_entries.async_update_entry(
            entry,
            options={
                **entry.options,
                CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED: False,
            },
        )
        _dismiss_allowlisted_login_notifications(hass)
        return Response(
            text=(
                "Allowlisted login notifications are now silenced. "
                "IP Ban Manager will still notify after repeated failures."
            ),
            content_type="text/plain",
        )


class IPBanManagerStatusView(HomeAssistantView):
    """Return live IP Ban Manager state for the bundled panel."""

    name = "api:ip_ban_manager:status"
    url = f"/api/{DOMAIN}/status"

    async def get(self, request: Request) -> Response:
        """Return live status and persisted editable values."""
        hass = request.app[KEY_HASS]
        entry = hass.http.app.get(KEY_CONFIG_ENTRY)
        if entry is None:
            return self.json_message("IP Ban Manager is not loaded.", status_code=404)

        return self.json(
            {
                "status": current_status(hass),
                "settings": {
                    CONF_IP_ADDRESSES: _entry_ip_addresses(entry),
                    CONF_BLOCKED_NETWORKS: _entry_blocked_networks(entry),
                    CONF_AUTO_BAN_ENABLED: _entry_auto_ban_enabled(entry),
                    CONF_BAN_NOTIFICATIONS_ENABLED: (
                        _entry_ban_notifications_enabled(entry)
                    ),
                    CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED: (
                        _entry_allowlisted_login_notifications_enabled(entry)
                    ),
                    CONF_ALLOWLISTED_LOGINS_CAN_BAN: (
                        _entry_allowlisted_logins_can_ban(entry)
                    ),
                    CONF_DEFAULT_DENY_ENABLED: _entry_default_deny_enabled(entry),
                    CONF_LOGIN_ATTEMPTS_THRESHOLD: _entry_login_threshold(entry, hass),
                    CONF_SIDEBAR_PANEL_ENABLED: _entry_sidebar_panel_enabled(entry),
                },
            }
        )


class IPBanManagerManageView(HomeAssistantView):
    """Apply live IP Ban Manager changes from the bundled panel."""

    name = "api:ip_ban_manager:manage"
    url = f"/api/{DOMAIN}/manage"

    async def post(self, request: Request) -> Response:
        """Apply one validated panel action."""
        hass = request.app[KEY_HASS]
        user = request.get("hass_user")
        if user is None or not user.is_admin:
            return self.json_message("Administrator access is required.", 403)

        try:
            data = await request.json()
        except ValueError:
            return self.json_message("Expected JSON request body.", 400)

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
            else:
                return self.json_message("Unknown action.", 400)
        except (HomeAssistantError, ValueError) as err:
            return self.json_message(str(err), 400)

        return self.json({"status": current_status(hass)})


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


def _allowlisted_logins_can_ban(hass: HomeAssistant) -> bool:
    """Return whether the current entry allows exact bans inside the allowlist."""
    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    return _entry_allowlisted_logins_can_ban(entry) if entry else False


def _current_login_threshold(hass: HomeAssistant) -> int:
    """Return Home Assistant's current live login-attempt threshold."""
    if hass.http is None:
        return DEFAULT_LOGIN_ATTEMPTS_THRESHOLD
    return max(
        0, int(hass.http.app.get(KEY_LOGIN_THRESHOLD, DEFAULT_LOGIN_ATTEMPTS_THRESHOLD))
    )


def _entry_login_threshold(entry: ConfigEntry, hass: HomeAssistant) -> int:
    """Return the configured login-attempt threshold for a config entry."""
    return int(
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
        return

    ban_manager.ip_bans_lookup = NetworkAwareBanLookup(
        dict(lookup), blocked_networks, allowlist, default_deny_enabled
    )


def _update_entry_options(hass: HomeAssistant, **updates: object) -> None:
    """Persist config-entry options without dropping unrelated settings."""
    entry = hass.http.app[KEY_CONFIG_ENTRY]
    hass.config_entries.async_update_entry(entry, options={**entry.options, **updates})


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
        if isinstance(domain_config, dict) and domain_config.get(
            CONF_DISABLE_BAN_MANAGER
        ):
            return True

    return False


def _async_update_disabled_by_yaml_issue(
    hass: HomeAssistant, disabled_by_yaml: bool
) -> None:
    """Create or clear the Repair for the YAML emergency kill switch."""
    if disabled_by_yaml:
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


def _format_ip_ban(ip_ban: IpBan) -> dict[str, str]:
    """Return a stable UI/API representation of a ban entry."""
    return {
        ATTR_IP_ADDRESS: str(ip_ban.ip_address),
        ATTR_BANNED_AT: ip_ban.banned_at.isoformat(),
    }


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


def current_status(hass: HomeAssistant) -> dict[str, object]:
    """Return the live ban and allowlist status for UI surfaces."""
    ban_manager = hass.http.app.get(KEY_BAN_MANAGER)
    failed_attempts = hass.http.app.get(KEY_FAILED_LOGIN_ATTEMPTS, {})
    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
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
        ATTR_BANNED_IPS: [
            _format_ip_ban(ip_ban)
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


async def _async_rewrite_ip_bans_file(
    hass: HomeAssistant, ban_manager: IpBanManager
) -> None:
    """Rewrite ip_bans.yaml from a stable snapshot of the live ban manager."""
    ban_path = ban_manager.path
    ip_bans = _ip_ban_file_payload(ban_manager)

    def _write_bans() -> None:
        path = Path(ban_path)
        if not ip_bans:
            path.unlink(missing_ok=True)
            return

        _atomic_write_text(
            ban_path,
            yaml.safe_dump(ip_bans, sort_keys=False),
        )

    await hass.async_add_executor_job(_write_bans)


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
        raise HomeAssistantError(
            "That would block a detected local Home Assistant network without "
            "a matching allowed entry."
        ) from err


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
        CONF_LOGIN_ATTEMPTS_THRESHOLD: _entry_login_threshold(entry, hass),
        CONF_SIDEBAR_PANEL_ENABLED: _entry_sidebar_panel_enabled(entry),
    }
    for key in (
        CONF_AUTO_BAN_ENABLED,
        CONF_BAN_NOTIFICATIONS_ENABLED,
        CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED,
        CONF_ALLOWLISTED_LOGINS_CAN_BAN,
        CONF_DEFAULT_DENY_ENABLED,
        CONF_SIDEBAR_PANEL_ENABLED,
    ):
        if key in options:
            current_options[key] = bool(options[key])

    if CONF_LOGIN_ATTEMPTS_THRESHOLD in options:
        current_options[CONF_LOGIN_ATTEMPTS_THRESHOLD] = max(
            0, int(options[CONF_LOGIN_ATTEMPTS_THRESHOLD])
        )

    await _async_validate_panel_network_safety(
        hass,
        _current_allowlist_strings(hass),
        _current_blocked_network_strings(hass),
        bool(current_options[CONF_DEFAULT_DENY_ENABLED]),
    )
    _update_entry_options(hass, **current_options)
    _apply_ban_settings(hass, entry)
    _apply_blocked_networks(hass, entry)
    await _async_register_panel(
        hass, sidebar_enabled=bool(current_options[CONF_SIDEBAR_PANEL_ENABLED])
    )


def _async_cleanup_entry_metadata(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean stale options without changing live ban state."""
    if entry.title in LEGACY_ENTRY_TITLES:
        hass.config_entries.async_update_entry(entry, title=ENTRY_TITLE)

    if CONF_BANNED_IPS in entry.options:
        options = dict(entry.options)
        options.pop(CONF_BANNED_IPS, None)
        hass.config_entries.async_update_entry(entry, options=options)


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
    timestamp = dt_util.utcnow().strftime("%Y%m%d-%H%M%S")
    failures: list[str] = []

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


def _async_remove_allowlist_network(hass: HomeAssistant, network_value: str) -> None:
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
        _async_remove_allowlist_network(hass, call.data[ATTR_NETWORK])

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
    disabled_by_yaml = _yaml_disable_ban_manager(config)
    hass.data[KEY_DISABLED_BY_YAML] = disabled_by_yaml
    _async_update_disabled_by_yaml_issue(hass, disabled_by_yaml)
    if disabled_by_yaml:
        _LOGGER.warning(
            "IP Ban Manager is disabled by configuration.yaml emergency override"
        )
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
    if hass.data.get(KEY_DISABLED_BY_YAML):
        _LOGGER.warning(
            "IP Ban Manager config entry setup skipped because disable_ban_manager is true"
        )
        _async_update_disabled_by_yaml_issue(hass, True)
        return True

    _async_cleanup_entry_metadata(hass, entry)
    _async_schedule_legacy_cleanup(hass)
    await _async_cleanup_legacy_component_folder(hass)
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
    _apply_ban_settings(hass, entry)
    _apply_blocked_networks(hass, entry)
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

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload IP Ban Manager."""
    _async_remove_panel(hass)
    _uninstall_patches(hass)
    hass.http.app.pop(KEY_ALLOWLIST, None)
    hass.http.app.pop(KEY_BLOCKED_NETWORKS, None)
    hass.http.app.pop(KEY_CONFIG_ENTRY, None)
    for service in (
        SERVICE_ADD_ALLOWLIST_NETWORK,
        SERVICE_ADD_IP_BAN,
        SERVICE_REMOVE_ALL_IP_BANS,
        SERVICE_REMOVE_ALLOWLIST_NETWORK,
        SERVICE_REMOVE_IP_BAN,
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
    await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return True
