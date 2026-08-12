"""Exact IP ban operations for IP Ban Manager."""

from __future__ import annotations

from asyncio import Lock
from collections.abc import Iterable
from datetime import datetime
from ipaddress import ip_address
from pathlib import Path

import yaml
from homeassistant.components.http.ban import (
    ATTR_BANNED_AT,
    KEY_BAN_MANAGER,
    KEY_FAILED_LOGIN_ATTEMPTS,
    NOTIFICATION_ID_BAN,
    NOTIFICATION_ID_LOGIN,
    IpBan,
    IpBanManager,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util

from .audit import record_ip_unbanned
from .ban_lookup import _is_allowed, _is_blocked, _supervisor_internal_networks
from .const import ATTR_IP_ADDRESS, DOMAIN, SOURCE_PANEL, SOURCE_SERVICE, SOURCE_SETUP
from .entry_helpers import allowlisted_logins_can_ban
from .file_store import (
    atomic_write_text,
    snapshot_dir,
    snapshot_existing_file,
)
from .metrics import mark_config_write, metric_increment
from .storage_keys import (
    KEY_ALLOWLIST,
    KEY_BAN_FILE_WRITE_LOCK,
    KEY_BLOCKED_NETWORKS,
    KEY_INTERNAL_BYPASS_NETWORKS,
    IPAddress,
)


def ban_manager(hass: HomeAssistant) -> IpBanManager:
    """Return Home Assistant's loaded IP ban manager."""
    try:
        return hass.http.app[KEY_BAN_MANAGER]
    except KeyError as err:
        raise HomeAssistantError(
            "Home Assistant IP banning is not enabled. Set http.ip_ban_enabled to true."
        ) from err


def ip_ban_file_payload(ban_manager_: IpBanManager) -> dict[str, dict[str, str]]:
    """Return the serialized ban mapping for ip_bans.yaml."""
    return {
        str(ip_ban.ip_address): {
            ATTR_BANNED_AT: (
                ip_ban.banned_at.isoformat()
                if isinstance(ip_ban.banned_at, datetime)
                else ip_ban.banned_at
            )
        }
        for ip_ban in chronological_ip_bans(ban_manager_)
    }


async def async_rewrite_ip_bans_file(
    hass: HomeAssistant, ban_manager_: IpBanManager
) -> None:
    """Rewrite ip_bans.yaml from a stable snapshot of the live ban manager."""
    lock = hass.data.setdefault(KEY_BAN_FILE_WRITE_LOCK, Lock())
    async with lock:
        ban_path = ban_manager_.path
        ip_bans = ip_ban_file_payload(ban_manager_)
        snapshots = snapshot_dir(hass)

        def _write_bans() -> bool:
            path = Path(ban_path)
            snapshot_created = snapshot_existing_file(path, snapshots)
            if not ip_bans:
                path.unlink(missing_ok=True)
                return snapshot_created

            atomic_write_text(
                ban_path,
                yaml.safe_dump(ip_bans, sort_keys=False),
            )
            return snapshot_created

        if await hass.async_add_executor_job(_write_bans):
            metric_increment(hass, "snapshots_created")
        mark_config_write(hass)


def dismiss_ban_notification_for_ips(
    hass: HomeAssistant, removed_ips: Iterable[IPAddress]
) -> None:
    """Dismiss Home Assistant's ban notification when it describes removed IPs."""
    from homeassistant.components import persistent_notification

    removed_ip_strings = {str(remote_addr) for remote_addr in removed_ips}
    if not removed_ip_strings:
        return

    notifications = persistent_notification._async_get_or_create_notifications(
        hass
    )  # noqa: SLF001
    ban_notification = notifications.get(NOTIFICATION_ID_BAN)
    if ban_notification and any(
        removed_ip in ban_notification["message"] for removed_ip in removed_ip_strings
    ):
        persistent_notification.async_dismiss(hass, NOTIFICATION_ID_BAN)

    login_notification = notifications.get(NOTIFICATION_ID_LOGIN)
    if login_notification and any(
        removed_ip in login_notification["message"] for removed_ip in removed_ip_strings
    ):
        persistent_notification.async_dismiss(hass, NOTIFICATION_ID_LOGIN)


async def async_add_ip_ban(hass: HomeAssistant, ip_address_value: str) -> None:
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

    await ban_manager(hass).async_add_ban(remote_addr)
    hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS].pop(remote_addr, None)


async def async_remove_ip_ban(
    hass: HomeAssistant, ip_address_value: str, source: str = SOURCE_SERVICE
) -> None:
    """Remove an IP ban immediately."""
    try:
        remote_addr = ip_address(ip_address_value)
    except ValueError as err:
        if source == SOURCE_PANEL:
            raise HomeAssistantError("Invalid IP address.") from err
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_ip_address",
            translation_placeholders={ATTR_IP_ADDRESS: ip_address_value},
        ) from err

    ban_manager_ = ban_manager(hass)
    removed_ban = ban_manager_.ip_bans_lookup.pop(remote_addr, None)
    if removed_ban is None:
        if source == SOURCE_PANEL:
            raise HomeAssistantError(f"{remote_addr} is not banned.")
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="ip_address_not_banned",
            translation_placeholders={ATTR_IP_ADDRESS: str(remote_addr)},
        )

    hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS].pop(remote_addr, None)
    await async_rewrite_ip_bans_file(hass, ban_manager_)
    dismiss_ban_notification_for_ips(hass, [remote_addr])
    record_ip_unbanned(hass, str(remote_addr), source)


async def async_remove_allowlisted_ip_bans(hass: HomeAssistant) -> list[IPAddress]:
    """Remove exact bans that were written before allowlist protection loaded."""
    if allowlisted_logins_can_ban(hass):
        return []

    allowlist = hass.http.app.get(KEY_ALLOWLIST, ())
    if not allowlist:
        return []

    ban_manager_ = ban_manager(hass)
    removed_addrs = [
        remote_addr
        for remote_addr in list(ban_manager_.ip_bans_lookup)
        if _is_allowed(remote_addr, allowlist)
    ]
    if not removed_addrs:
        return []

    for remote_addr in removed_addrs:
        ban_manager_.ip_bans_lookup.pop(remote_addr, None)
        hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS].pop(remote_addr, None)

    await async_rewrite_ip_bans_file(hass, ban_manager_)
    dismiss_ban_notification_for_ips(hass, removed_addrs)
    for remote_addr in removed_addrs:
        record_ip_unbanned(hass, str(remote_addr), SOURCE_SETUP)

    return removed_addrs


async def async_remove_all_ip_bans(hass: HomeAssistant) -> None:
    """Remove every IP ban immediately."""
    ban_manager_ = ban_manager(hass)
    removed_addrs = list(ban_manager_.ip_bans_lookup)
    ban_manager_.ip_bans_lookup.clear()
    hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS].clear()
    await async_rewrite_ip_bans_file(hass, ban_manager_)
    dismiss_ban_notification_for_ips(hass, removed_addrs)
    for remote_addr in removed_addrs:
        record_ip_unbanned(hass, str(remote_addr), SOURCE_SERVICE)


async def async_replace_ip_bans(
    hass: HomeAssistant, ip_address_values: list[str]
) -> None:
    """Replace the live IP ban list immediately."""
    remote_addrs = [
        ip_address(ip_address_value) for ip_address_value in ip_address_values
    ]
    remote_addr_set = set(remote_addrs)

    ban_manager_ = ban_manager(hass)
    existing_bans = ban_manager_.ip_bans_lookup
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
                key=ip_ban_chronological_key,
            )
        }
    )

    failed_attempts = hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS]
    for remote_addr in removed_addrs | remote_addr_set:
        failed_attempts.pop(remote_addr, None)

    await async_rewrite_ip_bans_file(hass, ban_manager_)
    dismiss_ban_notification_for_ips(hass, removed_addrs)


def chronological_ip_bans(ban_manager_: IpBanManager) -> list[IpBan]:
    """Return IP bans ordered by oldest ban first."""
    return sorted(
        ban_manager_.ip_bans_lookup.values(),
        key=ip_ban_chronological_key,
    )


def ip_ban_chronological_key(ip_ban: IpBan) -> tuple[datetime, int, bytes]:
    """Return a stable chronological sort key for an IP ban."""
    banned_at = ip_ban.banned_at
    if banned_at.tzinfo is None:
        banned_at = dt_util.as_utc(banned_at)
    return (banned_at, ip_ban.ip_address.version, ip_ban.ip_address.packed)
