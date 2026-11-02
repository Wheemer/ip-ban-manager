"""Resolve DNS names configured in the allowlist without blocking Home Assistant."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Iterable
from datetime import timedelta
from ipaddress import ip_address, ip_network
from time import monotonic
from typing import Any

from homeassistant.components.http.ban import KEY_BAN_MANAGER
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .entry_helpers import entry_ip_addresses, parse_allowlist
from .ip_utils import is_allowlist_hostname
from .storage_keys import KEY_ALLOWLIST, KEY_CONFIG_ENTRY

KEY_DNS_ALLOWLIST_CACHE = "ip_ban_manager_dns_allowlist_cache"
KEY_DNS_ALLOWLIST_UNSUBSCRIBER = "ip_ban_manager_dns_allowlist_unsubscriber"
DNS_REFRESH_INTERVAL = 60
DNS_RESOLUTION_TIMEOUT = 10
DNS_STALE_GRACE_SECONDS = 300
KEY_DNS_ALLOWLIST_RESOLVED_AT = "ip_ban_manager_dns_allowlist_resolved_at"
KEY_DNS_ALLOWLIST_TASK = "ip_ban_manager_dns_allowlist_task"
KEY_DNS_ALLOWLIST_LOCK = "ip_ban_manager_dns_allowlist_lock"


def _hostnames(values: Iterable[str]) -> tuple[str, ...]:
    """Return unique normalized hostnames in configuration order."""
    result: list[str] = []
    for value in values:
        if is_allowlist_hostname(value) and value.lower().rstrip(".") not in result:
            result.append(value.lower().rstrip("."))
    return tuple(result)


def _resolve_hostname(hostname: str) -> set[str]:
    """Resolve a hostname using the system resolver."""
    return {
        str(ip_address(result[4][0]))
        for result in socket.getaddrinfo(
            hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
        )
    }


async def _async_resolve_hostname(hass: HomeAssistant, hostname: str) -> set[str]:
    """Resolve one hostname off the event loop with a bounded wait."""
    try:
        return await asyncio.wait_for(
            hass.async_add_executor_job(_resolve_hostname, hostname),
            DNS_RESOLUTION_TIMEOUT,
        )
    except (OSError, TimeoutError, ValueError):
        return set()


async def async_refresh_dns_allowlist(hass: HomeAssistant) -> None:
    """Refresh resolved hostname entries and update the live matcher."""
    lock = hass.data.setdefault(KEY_DNS_ALLOWLIST_LOCK, asyncio.Lock())
    async with lock:
        await _async_refresh_dns_allowlist(hass)


async def _async_refresh_dns_allowlist(hass: HomeAssistant) -> None:
    """Apply one serialized resolution snapshot."""
    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    if entry is None:
        return
    hostnames = _hostnames(entry_ip_addresses(entry))
    cache: dict[str, set[str]] = hass.data.setdefault(KEY_DNS_ALLOWLIST_CACHE, {})
    resolved_at = hass.data.setdefault(KEY_DNS_ALLOWLIST_RESOLVED_AT, {})
    resolved = await asyncio.gather(
        *(_async_resolve_hostname(hass, hostname) for hostname in hostnames)
    )
    for hostname, addresses in zip(hostnames, resolved, strict=True):
        if addresses:
            cache[hostname] = addresses
            resolved_at[hostname] = monotonic()
        elif (
            monotonic() - resolved_at.get(hostname, float("-inf"))
            >= DNS_STALE_GRACE_SECONDS
        ):
            cache.pop(hostname, None)
            resolved_at.pop(hostname, None)
    for hostname in tuple(cache):
        if hostname not in hostnames:
            cache.pop(hostname, None)
            resolved_at.pop(hostname, None)

    networks = list(parse_allowlist(entry_ip_addresses(entry)))
    for address in sorted(
        {address for hostname in hostnames for address in cache.get(hostname, set())}
    ):
        parsed = ip_address(address)
        networks.append(
            ip_network(f"{parsed}/{32 if parsed.version == 4 else 128}", strict=False)
        )
    allowlist = tuple(dict.fromkeys(networks))
    previous = hass.http.app.get(KEY_ALLOWLIST, ())
    hass.http.app[KEY_ALLOWLIST] = allowlist
    try:
        lookup = hass.http.app[KEY_BAN_MANAGER].ip_bans_lookup
    except (KeyError, AttributeError):
        return
    if hasattr(lookup, "allowlist"):
        lookup.allowlist = allowlist
    if previous != allowlist and hass.data.get("ip_ban_manager_npm_unsubscribers"):
        from .nginx_proxy_manager import schedule_npm_sync

        schedule_npm_sync(hass)


async def async_start_dns_allowlist(hass: HomeAssistant) -> None:
    """Resolve configured hostnames and schedule bounded refreshes."""
    await async_refresh_dns_allowlist(hass)

    @callback
    def _refresh(_now: Any) -> None:
        task = hass.data.get(KEY_DNS_ALLOWLIST_TASK)
        if task is None or task.done():
            hass.data[KEY_DNS_ALLOWLIST_TASK] = hass.async_create_task(
                async_refresh_dns_allowlist(hass)
            )

    hass.data[KEY_DNS_ALLOWLIST_UNSUBSCRIBER] = async_track_time_interval(
        hass, _refresh, timedelta(seconds=DNS_REFRESH_INTERVAL)
    )


async def async_stop_dns_allowlist(hass: HomeAssistant) -> None:
    """Stop hostname refreshes and clear their cache."""
    if unsubscribe := hass.data.pop(KEY_DNS_ALLOWLIST_UNSUBSCRIBER, None):
        unsubscribe()
    task = hass.data.pop(KEY_DNS_ALLOWLIST_TASK, None)
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    hass.data.pop(KEY_DNS_ALLOWLIST_CACHE, None)
    hass.data.pop(KEY_DNS_ALLOWLIST_RESOLVED_AT, None)
