"""Reverse-DNS helpers for IP Ban Manager."""

from __future__ import annotations

import asyncio
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
        return cached.hostname

    metric_increment(hass, "reverse_dns_lookups")
    if remote_addr.is_global:
        hostname = await _async_external_reverse_dns_name(hass, remote_addr)
    else:
        hostname = await _async_local_reverse_dns_name(hass, remote_addr)

    if hostname is not None:
        hostname = hostname.rstrip(".")

    cache[remote_addr] = ReverseDNSCacheEntry(
        hostname=hostname,
        expires_at=now + REVERSE_DNS_CACHE_TTL,
    )
    return hostname


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
