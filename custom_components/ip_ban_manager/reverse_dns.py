"""Reverse-DNS helpers for IP Ban Manager."""

from __future__ import annotations

import asyncio
import re
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from socket import gethostbyaddr, herror
from typing import Any

from aiohttp import ClientError, ClientTimeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .metrics import metric_increment
from .storage_keys import KEY_REVERSE_DNS_CACHE, IPAddress

REVERSE_DNS_CACHE_TTL = timedelta(minutes=10)
DNS_OVER_HTTPS_URL = "https://cloudflare-dns.com/dns-query"
DNS_TYPE_PTR = 12
DNS_OVER_HTTPS_TIMEOUT = ClientTimeout(total=3)
REVERSE_DNS_CACHE_MAX_ENTRIES = 1024
KEY_REVERSE_DNS_TASKS = "ip_ban_manager_reverse_dns_tasks"
REVERSE_DNS_MAX_PENDING = 16
_HOST_LABEL = re.compile(r"[A-Za-z0-9_-]{1,63}")


def _safe_hostname(value: str | None) -> str | None:
    """Accept DNS names, not control characters or injected notification markup."""
    if value is None:
        return None
    try:
        hostname = value.rstrip(".").encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if len(hostname) > 253 or not all(
        _HOST_LABEL.fullmatch(label) for label in hostname.split(".")
    ):
        return None
    return hostname


@dataclass(frozen=True)
class ReverseDNSCacheEntry:
    """Cached reverse-DNS lookup result for a remote address."""

    hostname: str | None
    expires_at: datetime


async def async_reverse_dns_name(
    hass: HomeAssistant, remote_addr: IPAddress
) -> str | None:
    """Return a cached reverse-DNS name for a remote address."""
    now = dt_util.utcnow()
    cache = hass.http.app.setdefault(KEY_REVERSE_DNS_CACHE, {})
    cached = cache.get(remote_addr)
    if cached is not None and cached.expires_at > now:
        metric_increment(hass, "reverse_dns_cache_hits")
        cache.pop(remote_addr)
        cache[remote_addr] = cached
        return cached.hostname

    metric_increment(hass, "reverse_dns_lookups")
    pending = hass.data.setdefault(KEY_REVERSE_DNS_TASKS, {})
    task = pending.get(remote_addr)
    if task is None:
        if len(pending) >= REVERSE_DNS_MAX_PENDING:
            return None
        task = hass.async_create_task(_async_resolve_name(hass, remote_addr))
        pending[remote_addr] = task

        def finished(done: asyncio.Task[str | None]) -> None:
            if pending.get(remote_addr) is done:
                pending.pop(remote_addr, None)

        task.add_done_callback(finished)
    try:
        hostname = await asyncio.shield(task)
    finally:
        if task.done() and pending.get(remote_addr) is task:
            pending.pop(remote_addr, None)

    hostname = _safe_hostname(hostname)

    if remote_addr not in cache and len(cache) >= REVERSE_DNS_CACHE_MAX_ENTRIES:
        cache.pop(next(iter(cache)), None)
    cache[remote_addr] = ReverseDNSCacheEntry(
        hostname=hostname,
        expires_at=dt_util.utcnow() + REVERSE_DNS_CACHE_TTL,
    )
    return hostname


async def _async_resolve_name(
    hass: HomeAssistant, remote_addr: IPAddress
) -> str | None:
    """Resolve one address; concurrent requests share the bounded worker."""
    if remote_addr.is_global:
        return await _async_external_reverse_dns_name(hass, remote_addr)
    try:
        return await asyncio.wait_for(
            _async_local_reverse_dns_name(hass, remote_addr), 3
        )
    except TimeoutError:
        return None


async def async_stop_reverse_dns(hass: HomeAssistant) -> None:
    """Cancel and join integration-owned lookup tasks on unload."""
    pending: dict[IPAddress, asyncio.Task[str | None]] = hass.data.pop(
        KEY_REVERSE_DNS_TASKS, {}
    )
    for task in pending.values():
        task.cancel()
    if pending:
        await asyncio.gather(*pending.values(), return_exceptions=True)


async def _async_external_reverse_dns_name(
    hass: HomeAssistant, remote_addr: IPAddress
) -> str | None:
    """Resolve public PTR records through DNS-over-HTTPS."""
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            DNS_OVER_HTTPS_URL,
            params={
                "name": remote_addr.reverse_pointer,
                "type": "PTR",
                "ct": "application/dns-json",
            },
            headers={"Accept": "application/dns-json"},
            timeout=DNS_OVER_HTTPS_TIMEOUT,
        ) as response:
            if response.status != 200:
                return None
            payload = await response.json(content_type=None)
    except (asyncio.TimeoutError, ClientError, OSError, ValueError):
        return None

    return _ptr_hostname_from_response(payload)


def _ptr_hostname_from_response(payload: Any) -> str | None:
    """Return the first PTR hostname from a DNS-over-HTTPS JSON response."""
    if not isinstance(payload, dict) or payload.get("Status") != 0:
        return None

    answers = payload.get("Answer")
    if not isinstance(answers, list):
        return None

    for answer in answers:
        if not isinstance(answer, dict) or answer.get("type") != DNS_TYPE_PTR:
            continue
        hostname = answer.get("data")
        if isinstance(hostname, str) and hostname.strip():
            return hostname.strip().rstrip(".")
    return None


async def _async_local_reverse_dns_name(
    hass: HomeAssistant, remote_addr: IPAddress
) -> str | None:
    """Resolve PTR records through Home Assistant's local resolver."""
    with suppress(herror, OSError):
        hostname, _, _ = await hass.async_add_executor_job(
            gethostbyaddr, str(remote_addr)
        )
        return hostname

    return None
