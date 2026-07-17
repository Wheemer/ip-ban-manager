"""Network-aware ban lookup helpers used by IP Ban Manager."""

from __future__ import annotations

import os
from contextlib import suppress
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
)
from urllib.parse import urlsplit

from homeassistant.components.http.ban import IpBan

IPAddress = IPv4Address | IPv6Address
IPNetwork = IPv4Network | IPv6Network

SUPERVISOR_DOCKER_PARENT_NETWORK = IPv4Network("172.30.0.0/16")
SUPERVISOR_INTERNAL_NETWORKS: tuple[IPNetwork, ...] = (
    SUPERVISOR_DOCKER_PARENT_NETWORK,
)


class NetworkAwareBanLookup(dict[IPAddress, IpBan]):
    """IP ban lookup that also blocks configured networks."""

    def __init__(
        self,
        values: dict[IPAddress, IpBan],
        blocked_networks: tuple[IPNetwork, ...],
        allowlist: tuple[IPNetwork, ...],
        default_deny_enabled: bool,
        internal_bypass_networks: tuple[IPNetwork, ...] | None = None,
    ) -> None:
        """Initialize the lookup from Home Assistant's exact IP bans."""
        super().__init__(values)
        self.blocked_networks = blocked_networks
        self.allowlist = allowlist
        self.default_deny_enabled = default_deny_enabled
        self.internal_bypass_networks = (
            internal_bypass_networks or _supervisor_internal_networks()
        )

    def __contains__(self, key: object) -> bool:
        """Return whether an IP is exactly banned or blocked by network."""
        if not isinstance(key, (IPv4Address, IPv6Address)):
            return False

        remote_addr = _normalize_remote_addr(key)
        if _is_allowed(remote_addr, self.internal_bypass_networks):
            return False

        if dict.__contains__(self, key):
            return True

        if remote_addr != key and dict.__contains__(self, remote_addr):
            return True

        if _is_allowed(remote_addr, self.allowlist):
            return False

        if _is_blocked(remote_addr, self.blocked_networks):
            return True

        return self.default_deny_enabled

    def __bool__(self) -> bool:
        """Keep Home Assistant's ban middleware active for network-only blocks."""
        return bool(
            dict.__len__(self) or self.blocked_networks or self.default_deny_enabled
        )


def _supervisor_host_from_env() -> str | None:
    """Return the Supervisor host from Home Assistant's Supervisor environment."""
    supervisor = os.environ.get("SUPERVISOR")
    if not supervisor:
        return None
    if "://" in supervisor:
        return urlsplit(supervisor).hostname
    if supervisor.count(":") == 1 and "." in supervisor:
        return supervisor.split(":", 1)[0]
    return supervisor


def _supervisor_internal_networks() -> tuple[IPNetwork, ...]:
    """Return narrow internal networks that should not be blocked by managed rules."""
    networks = list(SUPERVISOR_INTERNAL_NETWORKS)
    supervisor_host = _supervisor_host_from_env()
    if supervisor_host is None:
        return tuple(networks)

    with suppress(ValueError):
        supervisor_addr = ip_address(supervisor_host)
        if isinstance(supervisor_addr, IPv4Address):
            if supervisor_addr in SUPERVISOR_DOCKER_PARENT_NETWORK:
                networks.insert(0, SUPERVISOR_DOCKER_PARENT_NETWORK)
            else:
                networks.insert(0, IPv4Network(f"{supervisor_addr}/32"))
        else:
            networks.insert(0, IPv6Network(f"{supervisor_addr}/128"))

    return tuple(dict.fromkeys(networks))


def _normalize_remote_addr(remote_addr: IPAddress) -> IPAddress:
    """Normalize runtime addresses into the family users configured."""
    if isinstance(remote_addr, IPv6Address) and remote_addr.ipv4_mapped is not None:
        return remote_addr.ipv4_mapped

    return remote_addr


def _is_allowed(remote_addr: IPAddress, allowlist: tuple[IPNetwork, ...]) -> bool:
    """Return whether a remote address is covered by the allowlist."""
    normalized_addr = _normalize_remote_addr(remote_addr)
    return any(normalized_addr in allowed_network for allowed_network in allowlist)


def _is_blocked(
    remote_addr: IPAddress, blocked_networks: tuple[IPNetwork, ...]
) -> bool:
    """Return whether a remote address is covered by blocked networks."""
    normalized_addr = _normalize_remote_addr(remote_addr)
    return any(
        normalized_addr in blocked_network for blocked_network in blocked_networks
    )
