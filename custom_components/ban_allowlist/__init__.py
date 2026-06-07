"""The Ban Allowlist integration."""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
)
from pathlib import Path
from tempfile import NamedTemporaryFile

import voluptuous as vol
from aiohttp.web import AppKey, Request
from homeassistant.components.http import ban as http_ban
from homeassistant.components.http.ban import (
    ATTR_BANNED_AT,
    KEY_BAN_MANAGER,
    KEY_FAILED_LOGIN_ATTEMPTS,
    NOTIFICATION_ID_BAN,
    NOTIFICATION_ID_LOGIN,
    IpBan,
    IpBanManager,
)
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import yaml as yaml_util

from .const import (
    ATTR_BANNED_IPS,
    ATTR_CONFIRM,
    ATTR_FAILED_LOGIN_ATTEMPTS,
    ATTR_IP_ADDRESS,
    ATTR_NETWORK,
    ATTR_NETWORKS,
    CONF_ALLOWED_IPS,
    CONF_BANNED_IPS,
    CONF_IP_ADDRESSES,
    DOMAIN,
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

ENTRY_TITLE = "IP Ban Manager"
LEGACY_ENTRY_TITLES = {"IP Ban Allowlist"}
IP_BAN_DISABLED_ISSUE_ID = "ip_ban_disabled"
HTTP_IP_BAN_DOCS_URL = (
    "https://www.home-assistant.io/integrations/http/#ip-filtering-and-banning"
)

KEY_ALLOWLIST = AppKey[tuple[IPNetwork, ...]]("ban_allowlist_networks")
KEY_CONFIG_ENTRY = AppKey[ConfigEntry]("ban_allowlist_config_entry")
KEY_ORIGINAL_ADD_BAN = AppKey[AddBanCallable]("ban_allowlist_original_add_ban")

PLATFORMS = ["sensor"]

_ORIGINAL_PROCESS_WRONG_LOGIN = http_ban.process_wrong_login

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_IP_ADDRESSES): vol.All(cv.ensure_list, [cv.string]),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

IP_ADDRESS_SCHEMA = vol.Schema({vol.Required(ATTR_IP_ADDRESS): cv.string})
NETWORK_SCHEMA = vol.Schema({vol.Required(ATTR_NETWORK): cv.string})
REMOVE_ALL_IP_BANS_SCHEMA = vol.Schema(
    {vol.Required(ATTR_CONFIRM, default=False): cv.boolean}
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


def _parse_allowlist(ip_addresses: list[str]) -> tuple[IPNetwork, ...]:
    """Parse configured IP addresses and networks."""
    return tuple(parse_allowlist_network(ip) for ip in ip_addresses)


def _entry_ip_addresses(entry: ConfigEntry) -> list[str]:
    """Return the configured allowlist for a config entry."""
    return entry.options.get(
        CONF_IP_ADDRESSES,
        entry.options.get(CONF_ALLOWED_IPS, entry.data.get(CONF_IP_ADDRESSES, [])),
    )


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


def _ip_ban_sort_key(ip_ban: IpBan) -> tuple[int, bytes]:
    """Sort IPv4 and IPv6 ban entries consistently."""
    return (ip_ban.ip_address.version, ip_ban.ip_address.packed)


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
    return {
        ATTR_NETWORKS: [
            str(network) for network in hass.http.app.get(KEY_ALLOWLIST, ())
        ],
        ATTR_BANNED_IPS: [
            _format_ip_ban(ip_ban)
            for ip_ban in sorted(
                (ban_manager.ip_bans_lookup.values() if ban_manager else ()),
                key=_ip_ban_sort_key,
            )
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


async def _async_rewrite_ip_bans_file(
    hass: HomeAssistant, ban_manager: IpBanManager
) -> None:
    """Rewrite ip_bans.yaml from the live ban manager."""

    def _write_bans() -> None:
        ip_bans = {
            str(ip_ban.ip_address): {
                ATTR_BANNED_AT: (
                    ip_ban.banned_at.isoformat()
                    if isinstance(ip_ban.banned_at, datetime)
                    else ip_ban.banned_at
                )
            }
            for ip_ban in sorted(
                ban_manager.ip_bans_lookup.values(), key=_ip_ban_sort_key
            )
        }
        _atomic_write_text(
            ban_manager.path,
            yaml_util.dump(ip_bans) if ip_bans else "{}\n",
        )

    await hass.async_add_executor_job(_write_bans)


def _update_allowlist_entry(hass: HomeAssistant, ip_addresses: list[str]) -> None:
    """Persist and apply the current allowlist without a Home Assistant restart."""
    entry = hass.http.app[KEY_CONFIG_ENTRY]
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_IP_ADDRESSES: ip_addresses}
    )
    hass.http.app[KEY_ALLOWLIST] = _parse_allowlist(ip_addresses)


def _current_allowlist_strings(hass: HomeAssistant) -> list[str]:
    """Return the persisted allowlist strings."""
    return _entry_ip_addresses(hass.http.app[KEY_CONFIG_ENTRY])


def _async_cleanup_entry_metadata(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean legacy config entry metadata without changing live ban state."""
    if entry.title in LEGACY_ENTRY_TITLES:
        hass.config_entries.async_update_entry(entry, title=ENTRY_TITLE)

    if CONF_BANNED_IPS in entry.options:
        options = dict(entry.options)
        options.pop(CONF_BANNED_IPS, None)
        hass.config_entries.async_update_entry(entry, options=options)


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

    ban_notification = notifications.get(NOTIFICATION_ID_BAN)
    if ban_notification and any(
        removed_ip in ban_notification["message"] for removed_ip in removed_ips
    ):
        persistent_notification.async_dismiss(hass, NOTIFICATION_ID_BAN)

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
    existing_bans.clear()
    existing_bans.update(
        {
            remote_addr: preserved_bans.get(remote_addr, IpBan(remote_addr))
            for remote_addr in sorted(
                remote_addr_set,
                key=lambda ip_addr: (ip_addr.version, ip_addr.packed),
            )
        }
    )

    failed_attempts = hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS]
    for remote_addr in removed_addrs | remote_addr_set:
        failed_attempts.pop(remote_addr, None)

    await _async_rewrite_ip_bans_file(hass, ban_manager)
    _dismiss_removed_ip_notifications(hass, removed_addrs)


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
    if not remaining_networks and _current_allowlist_strings(hass):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="clear_all_allowlist",
        )
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


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload Ban Allowlist when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Ban Allowlist and import YAML configuration."""
    if DOMAIN in config:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_IMPORT},
                data=dict(config[DOMAIN]),
            )
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Ban Allowlist from a config entry."""
    _async_cleanup_entry_metadata(hass, entry)

    try:
        ban_manager: IpBanManager = hass.http.app[KEY_BAN_MANAGER]
    except KeyError:
        _LOGGER.warning(
            "Can't find ban manager. ban_allowlist requires http.ip_ban_enabled to be True, so disabling."
        )
        _async_create_ip_ban_disabled_issue(hass)
        return True
    _async_delete_ip_ban_disabled_issue(hass)
    _LOGGER.debug("Ban manager %s", ban_manager)
    allowlist = _parse_allowlist(_entry_ip_addresses(entry))
    hass.http.app[KEY_ALLOWLIST] = allowlist
    hass.http.app[KEY_CONFIG_ENTRY] = entry

    if len(allowlist) == 0:
        _LOGGER.info("Not setting allowlist, as no IPs set")
    else:
        _LOGGER.info("Setting allowlist with %s", [str(ip) for ip in allowlist])

        _install_wrong_login_patch()
        _install_add_ban_patch(hass, ban_manager)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Ban Allowlist."""
    hass.http.app[KEY_ALLOWLIST] = ()
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
