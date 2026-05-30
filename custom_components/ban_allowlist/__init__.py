"""The Ban Allowlist integration."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
    ip_network,
)

import voluptuous as vol
from aiohttp.web import AppKey, Request
from homeassistant.components.http import ban as http_ban
from homeassistant.components.http.ban import (
    KEY_BAN_MANAGER,
    KEY_FAILED_LOGIN_ATTEMPTS,
    IpBanManager,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

IPAddress = IPv4Address | IPv6Address
IPNetwork = IPv4Network | IPv6Network
AddBanCallable = Callable[[IPAddress], Awaitable[None]]

KEY_ALLOWLIST = AppKey[tuple[IPNetwork, ...]]("ban_allowlist_networks")
KEY_ORIGINAL_ADD_BAN = AppKey[AddBanCallable]("ban_allowlist_original_add_ban")

_ORIGINAL_PROCESS_WRONG_LOGIN = http_ban.process_wrong_login

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required("ip_addresses"): vol.All(cv.ensure_list, [cv.string]),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


def _is_allowed(remote_addr: IPAddress, allowlist: tuple[IPNetwork, ...]) -> bool:
    """Return whether a remote address is covered by the allowlist."""
    return any(remote_addr in allowed_network for allowed_network in allowlist)


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
    """Ignore failed login attempts from allowlisted addresses."""
    allowlist = request.app.get(KEY_ALLOWLIST, ())
    remote_addr = _request_remote_ip(request)

    if remote_addr is not None and _is_allowed(remote_addr, allowlist):
        attempts = request.app.get(KEY_FAILED_LOGIN_ATTEMPTS)
        if attempts is not None:
            attempts.pop(remote_addr, None)
        _LOGGER.info(
            "Ignoring invalid authentication from %s because it is in the allowlist",
            remote_addr,
        )
        return

    await _ORIGINAL_PROCESS_WRONG_LOGIN(request)


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


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Ban Allowlist from a config entry."""
    try:
        ban_manager: IpBanManager = hass.http.app[KEY_BAN_MANAGER]
    except KeyError:
        _LOGGER.warning(
            "Can't find ban manager. ban_allowlist requires http.ip_ban_enabled to be True, so disabling."
        )
        return True
    _LOGGER.debug("Ban manager %s", ban_manager)
    allowlist = tuple(
        ip_network(ip) for ip in config.get(DOMAIN, {}).get("ip_addresses", [])
    )
    hass.http.app[KEY_ALLOWLIST] = allowlist

    if len(allowlist) == 0:
        _LOGGER.info("Not setting allowlist, as no IPs set")
    else:
        _LOGGER.info("Setting allowlist with %s", [str(ip) for ip in allowlist])

        _install_wrong_login_patch()
        _install_add_ban_patch(hass, ban_manager)

    return True
