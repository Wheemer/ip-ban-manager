"""Resolve DNS names configured in the allowlist without blocking Home Assistant."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Iterable
from datetime import timedelta
from ipaddress import ip_address, ip_network
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
    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    if entry is None:
        return
    hostnames = _hostnames(entry_ip_addresses(entry))
    cache: dict[str, set[str]] = hass.data.setdefault(KEY_DNS_ALLOWLIST_CACHE, {})
    resolved = await asyncio.gather(
        *(_async_resolve_hostname(hass, hostname) for hostname in hostnames)
    )
    for hostname, addresses in zip(hostnames, resolved, strict=True):
        if addresses:
            cache[hostname] = addresses
        elif hostname not in cache:
            cache.pop(hostname, None)
    for hostname in tuple(cache):
        if hostname not in hostnames:
            cache.pop(hostname, None)

    networks = list(parse_allowlist(entry_ip_addresses(entry)))
    for address in sorted(
        {address for hostname in hostnames for address in cache.get(hostname, set())}
    ):
        parsed = ip_address(address)
        networks.append(
            ip_network(f"{parsed}/{32 if parsed.version == 4 else 128}", strict=False)
        )
    allowlist = tuple(dict.fromkeys(networks))
    hass.http.app[KEY_ALLOWLIST] = allowlist
    try:
        lookup = hass.http.app[KEY_BAN_MANAGER].ip_bans_lookup
    except (KeyError, AttributeError):
        return
    if hasattr(lookup, "allowlist"):
        lookup.allowlist = allowlist


async def async_start_dns_allowlist(hass: HomeAssistant) -> None:
    """Resolve configured hostnames and schedule bounded refreshes."""
    await async_refresh_dns_allowlist(hass)

    @callback
    def _refresh(_now: Any) -> None:
        hass.async_create_task(async_refresh_dns_allowlist(hass))

    hass.data[KEY_DNS_ALLOWLIST_UNSUBSCRIBER] = async_track_time_interval(
        hass, _refresh, timedelta(seconds=DNS_REFRESH_INTERVAL)
    )


async def async_stop_dns_allowlist(hass: HomeAssistant) -> None:
    """Stop hostname refreshes and clear their cache."""
    if unsubscribe := hass.data.pop(KEY_DNS_ALLOWLIST_UNSUBSCRIBER, None):
        unsubscribe()
    hass.data.pop(KEY_DNS_ALLOWLIST_CACHE, None)
