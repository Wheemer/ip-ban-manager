"""Home Assistant HTTP ban monkey-patches for IP Ban Manager."""

from __future__ import annotations

import logging
import sys
from ipaddress import ip_address

from aiohttp.web import Request
from homeassistant.components.http import ban as http_ban
from homeassistant.components.http.ban import (
    KEY_BAN_MANAGER,
    KEY_FAILED_LOGIN_ATTEMPTS,
    KEY_LOGIN_THRESHOLD,
    IpBanManager,
)
from homeassistant.components.http.const import KEY_HASS
from homeassistant.core import HomeAssistant

from .audit import (
    current_mutation_source,
    record_ip_banned,
    record_login_threshold_reached,
)
from .ban_lookup import NetworkAwareBanLookup, _is_allowed, _normalize_remote_addr
from .const import SOURCE_AUTO
from .entry_helpers import allowlisted_logins_can_ban
from .ha_compat import assert_http_ban_hooks_available
from .network_policy import apply_blocked_networks
from .notifications import (
    create_allowlisted_login_notification,
    format_remote_display,
)
from .reverse_dns import async_reverse_dns_name
from .storage_keys import (
    KEY_ALLOWLIST,
    KEY_CONFIG_ENTRY,
    KEY_INTERNAL_BYPASS_NETWORKS,
    KEY_ORIGINAL_ADD_BAN,
    KEY_ORIGINAL_LOAD_BANS,
    IPAddress,
)

_LOGGER = logging.getLogger(__name__)

_ORIGINAL_PROCESS_WRONG_LOGIN = http_ban.process_wrong_login


def _handle_http_notifications(hass: HomeAssistant) -> None:
    """Rewrite Home Assistant HTTP notifications using the current module code."""
    from .notifications import handle_http_notifications

    handle_http_notifications(hass)


def _schedule_http_notification_rewrite(hass: HomeAssistant) -> None:
    """Rewrite again after Home Assistant finishes same-turn notification work."""
    hass.loop.call_soon(_handle_http_notifications, hass)


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


async def _async_handle_standard_wrong_login(request: Request) -> None:
    """Process failed logins that may become automatic exact bans."""
    await _ORIGINAL_PROCESS_WRONG_LOGIN(request)
    hass = request.app[KEY_HASS]
    _handle_http_notifications(hass)
    _schedule_http_notification_rewrite(hass)


async def _allowlist_process_wrong_login(request: Request) -> None:
    """Process failed logins while preventing allowlisted addresses from bans."""
    allowlist = request.app.get(KEY_ALLOWLIST, ())
    remote_addr = _request_remote_ip(request)
    hass = request.app[KEY_HASS]

    if remote_addr is None or not _is_allowed(remote_addr, allowlist):
        await _async_handle_standard_wrong_login(request)
        return

    if allowlisted_logins_can_ban(hass):
        await _async_handle_standard_wrong_login(request)
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
    remote_host = await async_reverse_dns_name(hass, remote_addr)

    remote_display = format_remote_display(remote_host, remote_addr)
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

    create_allowlisted_login_notification(hass, remote_addr, notification_msg)


def install_wrong_login_patch() -> None:
    """Install the Home Assistant failed-login hook once."""
    assert_http_ban_hooks_available()
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


def install_add_ban_patch(hass: HomeAssistant, ban_manager: IpBanManager) -> None:
    """Install the IP ban hook for this Home Assistant app once."""
    app = hass.http.app
    app.setdefault(KEY_ORIGINAL_ADD_BAN, ban_manager.async_add_ban)

    async def allowlist_async_add_ban(remote_addr: IPAddress) -> None:
        if _is_allowed(remote_addr, app.get(KEY_INTERNAL_BYPASS_NETWORKS, ())):
            _LOGGER.info(
                "Not adding %s to ban list, as it's a Home Assistant internal address",
                remote_addr,
            )
            return

        allowlist = app.get(KEY_ALLOWLIST, ())
        if _is_allowed(remote_addr, allowlist) and not allowlisted_logins_can_ban(hass):
            _LOGGER.info(
                "Not adding %s to ban list, as it's in the allowlist",
                remote_addr,
            )
            return

        active_ban_manager = app.get(KEY_BAN_MANAGER)
        already_banned = (
            active_ban_manager is not None
            and remote_addr in active_ban_manager.ip_bans_lookup
        )
        if already_banned:
            await app[KEY_ORIGINAL_ADD_BAN](remote_addr)
            return

        _LOGGER.info("Banning IP %s", remote_addr)
        should_record_threshold = current_mutation_source() == SOURCE_AUTO
        threshold = int(app.get(KEY_LOGIN_THRESHOLD, 0))
        attempts = int(app[KEY_FAILED_LOGIN_ATTEMPTS].get(remote_addr, 0))
        await app[KEY_ORIGINAL_ADD_BAN](remote_addr)
        if should_record_threshold and threshold >= 1 and attempts >= threshold:
            record_login_threshold_reached(
                hass,
                str(remote_addr),
                attempts=attempts,
                threshold=threshold,
            )
        record_ip_banned(hass, str(remote_addr))

    ban_manager.async_add_ban = allowlist_async_add_ban  # type: ignore[method-assign]


def install_load_bans_patch(hass: HomeAssistant, ban_manager: IpBanManager) -> None:
    """Keep managed network blocks applied after Home Assistant reloads bans."""
    app = hass.http.app
    app.setdefault(KEY_ORIGINAL_LOAD_BANS, ban_manager.async_load)

    async def network_aware_async_load() -> None:
        await app[KEY_ORIGINAL_LOAD_BANS]()
        entry = app.get(KEY_CONFIG_ENTRY)
        if entry is not None:
            apply_blocked_networks(hass, entry)

    ban_manager.async_load = network_aware_async_load  # type: ignore[method-assign]


def uninstall_patches(hass: HomeAssistant) -> None:
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
