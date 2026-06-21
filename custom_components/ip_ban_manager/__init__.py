"""The IP Ban Manager integration."""

from __future__ import annotations

import logging
import os
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
    ATTR_AUTO_BAN_ENABLED,
    ATTR_BAN_NOTIFICATIONS_ENABLED,
    ATTR_BANNED_IPS,
    ATTR_BLOCKED_NETWORKS,
    ATTR_CONFIRM,
    ATTR_FAILED_LOGIN_ATTEMPTS,
    ATTR_IP_ADDRESS,
    ATTR_LOGIN_ATTEMPTS_THRESHOLD,
    ATTR_NATIVE_IP_BAN_ENABLED,
    ATTR_NETWORK,
    ATTR_NETWORKS,
    CONF_ALLOWED_IPS,
    CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED,
    CONF_AUTO_BAN_ENABLED,
    CONF_BAN_NOTIFICATIONS_ENABLED,
    CONF_BANNED_IPS,
    CONF_BLOCKED_NETWORKS,
    CONF_IP_ADDRESSES,
    CONF_LOGIN_ATTEMPTS_THRESHOLD,
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

IP_BAN_DISABLED_ISSUE_ID = "ip_ban_disabled"
ALLOWLISTED_LOGIN_ESCALATION_THRESHOLD = 10
HTTP_IP_BAN_DOCS_URL = (
    "https://www.home-assistant.io/integrations/http/#ip-filtering-and-banning"
)
INTEGRATION_CONFIG_URL = f"/config/integrations/integration/{DOMAIN}"
CONFIG_ENTRY_URL_TEMPLATE = (
    f"/config/integrations/integration/{DOMAIN}?config_entry={{entry_id}}"
)
NOTIFICATION_LINK_LABEL = "Open settings"
ALLOWLISTED_LOGIN_SILENCE_LABEL = "Allowlisted login notifications"
ALLOWLISTED_LOGIN_SILENCE_URL = f"/api/{DOMAIN}/silence_allowlisted_login_notifications"
ENTRY_TITLE = "IP Ban Manager"
LEGACY_ENTRY_TITLES = {"IP Ban Allowlist", "ban_allowlist"}
NOTIFICATION_TITLE = " "
NOTIFICATION_ICON_URL = f"/api/{DOMAIN}/icon.png"
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
KEY_ORIGINAL_ADD_BAN = AppKey[AddBanCallable]("ip_ban_manager_original_add_ban")
KEY_STATIC_PATH_REGISTERED = AppKey[bool]("ip_ban_manager_static_path_registered")
KEY_LEGACY_CLEANUP_SCHEDULED = AppKey[bool]("ip_ban_manager_legacy_cleanup_scheduled")

PLATFORMS = ["sensor"]

_ORIGINAL_PROCESS_WRONG_LOGIN = http_ban.process_wrong_login

CONFIG_SCHEMA = vol.Schema(
    {
        vol_optional(DOMAIN): vol.Schema(
            {
                vol.Required(CONF_IP_ADDRESSES): vol.All(cv.ensure_list, [cv.string]),
            }
        ),
        vol_optional(LEGACY_DOMAIN): vol.Schema(
            {
                vol.Required(CONF_IP_ADDRESSES): vol.All(cv.ensure_list, [cv.string]),
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
    ) -> None:
        """Initialize the lookup from Home Assistant's exact IP bans."""
        super().__init__(values)
        self.blocked_networks = blocked_networks
        self.allowlist = allowlist

    def __contains__(self, key: object) -> bool:
        """Return whether an IP is exactly banned or blocked by network."""
        if dict.__contains__(self, key):
            return True

        if not isinstance(key, (IPv4Address, IPv6Address)):
            return False

        if _is_allowed(key, self.allowlist):
            return False

        return any(key in network for network in self.blocked_networks)


def _is_allowed(remote_addr: IPAddress, allowlist: tuple[IPNetwork, ...]) -> bool:
    """Return whether a remote address is covered by the allowlist."""
    return any(remote_addr in allowed_network for allowed_network in allowlist)


def _is_blocked(
    remote_addr: IPAddress, blocked_networks: tuple[IPNetwork, ...]
) -> bool:
    """Return whether a remote address is covered by blocked networks."""
    return any(remote_addr in blocked_network for blocked_network in blocked_networks)


def _request_remote_ip(request: Request) -> IPAddress | None:
    """Parse the request's remote address, if Home Assistant provided one."""
    if request.remote is None:
        return None

    try:
        return ip_address(request.remote)
    except ValueError:
        _LOGGER.debug(
            "Ignoring invalid remote address from request: %s", request.remote
        )
        return None


async def _allowlist_process_wrong_login(request: Request) -> None:
    """Process failed logins while preventing allowlisted addresses from bans."""
    allowlist = request.app.get(KEY_ALLOWLIST, ())
    remote_addr = _request_remote_ip(request)

    if remote_addr is None or not _is_allowed(remote_addr, allowlist):
        await _ORIGINAL_PROCESS_WRONG_LOGIN(request)
        _handle_http_notifications(request.app[KEY_HASS])
        return

    await _process_allowlisted_wrong_login(request, remote_addr)
    _handle_http_notifications(request.app[KEY_HASS])
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


def _with_allowlisted_login_silence_link(message: str) -> str:
    """Append the allowlisted-login silence link once."""
    if ALLOWLISTED_LOGIN_SILENCE_LABEL in message:
        return message
    return (
        f"{message}\n\n"
        f"[{ALLOWLISTED_LOGIN_SILENCE_LABEL}]({ALLOWLISTED_LOGIN_SILENCE_URL})"
    )


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


def _should_notify_allowlisted_login(hass: HomeAssistant, attempts: int) -> bool:
    """Return whether an allowlisted failed login should notify the user."""
    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    if entry is None or _entry_allowlisted_login_notifications_enabled(entry):
        return True

    return attempts >= ALLOWLISTED_LOGIN_ESCALATION_THRESHOLD


def _create_allowlisted_login_notification(
    hass: HomeAssistant, remote_addr: IPAddress, base_message: str
) -> None:
    """Create an IP Ban Manager failed-login notification for an allowlisted source."""
    from homeassistant.components import persistent_notification

    failed_attempts = hass.http.app.get(KEY_FAILED_LOGIN_ATTEMPTS, {})
    attempts = int(failed_attempts.get(remote_addr, 0))
    threshold = int(hass.http.app.get(KEY_LOGIN_THRESHOLD, 0))
    if not _should_notify_allowlisted_login(hass, attempts):
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
        message = _with_allowlisted_login_silence_link(message)
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
        """Disable allowlisted failed-login notifications and dismiss the current notification."""
        from homeassistant.components import persistent_notification

        hass = request.app[KEY_HASS]
        entry = hass.http.app.get(KEY_CONFIG_ENTRY)
        if entry is None:
            return Response(text="IP Ban Manager is not loaded.", status=404)

        hass.config_entries.async_update_entry(
            entry,
            options={
                **entry.options,
                CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED: False,
            },
        )
        persistent_notification.async_dismiss(hass, NOTIFICATION_ID_LOGIN)
        return Response(
            text=(
                "Allowlisted login notifications are now silenced. "
                "IP Ban Manager will still notify after repeated failures."
            ),
            content_type="text/plain",
        )


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


def _install_add_ban_patch(hass: HomeAssistant, ban_manager: IpBanManager) -> None:
    """Install the IP ban hook for this Home Assistant app once."""
    app = hass.http.app
    app.setdefault(KEY_ORIGINAL_ADD_BAN, ban_manager.async_add_ban)

    async def allowlist_async_add_ban(remote_addr: IPAddress) -> None:
        allowlist = app.get(KEY_ALLOWLIST, ())
        if _is_allowed(remote_addr, allowlist):
            _LOGGER.info(
                "Not adding %s to ban list, as it's in the allowlist",
                remote_addr,
            )
            return

        _LOGGER.info("Banning IP %s", remote_addr)
        await app[KEY_ORIGINAL_ADD_BAN](remote_addr)

    ban_manager.async_add_ban = allowlist_async_add_ban  # type: ignore[method-assign]


def _uninstall_patches(hass: HomeAssistant) -> None:
    """Restore Home Assistant internals patched by this integration."""
    app = hass.http.app

    if http_ban.process_wrong_login is _allowlist_process_wrong_login:
        http_ban.process_wrong_login = _ORIGINAL_PROCESS_WRONG_LOGIN

    original_add_ban = app.pop(KEY_ORIGINAL_ADD_BAN, None)
    ban_manager = app.get(KEY_BAN_MANAGER)
    if original_add_ban is not None and ban_manager is not None:
        ban_manager.async_add_ban = original_add_ban
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
    allowlist = hass.http.app.get(KEY_ALLOWLIST, ())
    hass.http.app[KEY_BLOCKED_NETWORKS] = blocked_networks

    if not _native_ip_banning_enabled(hass):
        return

    ban_manager = hass.http.app[KEY_BAN_MANAGER]
    lookup = ban_manager.ip_bans_lookup
    if isinstance(lookup, NetworkAwareBanLookup):
        lookup.blocked_networks = blocked_networks
        lookup.allowlist = allowlist
        return

    ban_manager.ip_bans_lookup = NetworkAwareBanLookup(
        dict(lookup), blocked_networks, allowlist
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
    if hasattr(hass.http, "async_register_static_paths"):
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    NOTIFICATION_ICON_URL,
                    icon_path,
                    cache_headers=True,
                )
            ]
        )
    else:
        register_static_path = getattr(hass.http, "register_static_path")
        register_static_path(
            NOTIFICATION_ICON_URL,
            icon_path,
            cache_headers=True,
        )
    hass.http.app[KEY_STATIC_PATH_REGISTERED] = True


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up IP Ban Manager and import YAML configuration."""
    if hass.config_entries.async_entries(DOMAIN):
        _async_schedule_legacy_cleanup(hass)

    yaml_config = config.get(DOMAIN) or config.get(LEGACY_DOMAIN)
    if yaml_config is not None:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_IMPORT},
                data=dict(yaml_config),
            )
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up IP Ban Manager from a config entry."""
    _async_cleanup_entry_metadata(hass, entry)
    _async_schedule_legacy_cleanup(hass)
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
    hass.http.register_view(SilenceAllowlistedLoginNotificationsView())
    _LOGGER.debug("Ban manager %s", ban_manager)
    _apply_ban_settings(hass, entry)
    _apply_blocked_networks(hass, entry)
    allowlist = hass.http.app[KEY_ALLOWLIST]

    if len(allowlist) == 0:
        _LOGGER.info("Not setting allowlist, as no IPs set")
    else:
        _LOGGER.info("Setting allowlist with %s", [str(ip) for ip in allowlist])

    await _async_rewrite_ip_bans_file(hass, ban_manager)

    _install_wrong_login_patch()
    _install_add_ban_patch(hass, ban_manager)
    _handle_http_notifications(hass)

    _register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload IP Ban Manager."""
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
        hass.services.async_remove(DOMAIN, service)
    await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return True
