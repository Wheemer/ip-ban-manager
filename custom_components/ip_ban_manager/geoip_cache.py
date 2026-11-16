"""Bounded cache of authoritative MMDB leaf networks, never guessed subnets."""

from __future__ import annotations

from collections import OrderedDict
from ipaddress import IPv4Address, IPv6Address
from typing import Generic, TypeVar

_Value = TypeVar("_Value")


class GeoIPPrefixCache(Generic[_Value]):
    """Reuse a decoded record only inside the exact prefix returned by MMDB."""

    def __init__(self, limit: int) -> None:
        """Set a shared LRU bound across both address families."""
        self.limit = limit
        self.entries: OrderedDict[tuple[int, int, int], tuple[float, _Value]] = (
            OrderedDict()
        )
        self.buckets: dict[tuple[int, int], dict[int, tuple[float, _Value]]] = {}

    def _remove(self, key: tuple[int, int, int]) -> None:
        self.entries.pop(key, None)
        bucket_key = key[:2]
        bucket = self.buckets[bucket_key]
        bucket.pop(key[2], None)
        if not bucket:
            del self.buckets[bucket_key]

    def get(self, address: IPv4Address | IPv6Address, now: float) -> _Value | None:
        """Return a live authoritative record for this address, if cached."""
        number = int(address)
        bits = address.max_prefixlen
        for (version, prefix), bucket in tuple(self.buckets.items()):
            if version != address.version:
                continue
            network = (number >> (bits - prefix)) << (bits - prefix)
            cached = bucket.get(network)
            if cached is None:
                continue
            key = (version, prefix, network)
            if cached[0] <= now:
                self._remove(key)
                continue
            self.entries.move_to_end(key)
            return cached[1]
        return None

    def put(
        self,
        address: IPv4Address | IPv6Address,
        prefix: int,
        expires: float,
        value: _Value,
    ) -> None:
        """Store the database's leaf and evict the least-recently used leaf."""
        if type(prefix) is not int or not 0 <= prefix <= address.max_prefixlen:
            return
        shift = address.max_prefixlen - prefix
        network = (int(address) >> shift) << shift
        key = (address.version, prefix, network)
        self.entries[key] = (expires, value)
        self.entries.move_to_end(key)
        self.buckets.setdefault(key[:2], {})[network] = (expires, value)
        while len(self.entries) > self.limit:
            self._remove(next(iter(self.entries)))
