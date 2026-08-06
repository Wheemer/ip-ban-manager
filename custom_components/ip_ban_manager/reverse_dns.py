"""Reverse-DNS helpers for IP Ban Manager."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from socket import gethostbyaddr, herror

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .metrics import metric_increment
from .storage_keys import KEY_REVERSE_DNS_CACHE, IPAddress

REVERSE_DNS_CACHE_TTL = timedelta(minutes=10)


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

    hostname: str | None = None
    metric_increment(hass, "reverse_dns_lookups")
    with suppress(herror, OSError):
        hostname, _, _ = await hass.async_add_executor_job(
            gethostbyaddr, str(remote_addr)
        )

    cache[remote_addr] = ReverseDNSCacheEntry(
        hostname=hostname,
        expires_at=now + REVERSE_DNS_CACHE_TTL,
    )
    return hostname
