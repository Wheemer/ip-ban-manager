"""IP address helpers for IP Ban Manager."""

from __future__ import annotations

from ipaddress import IPv4Network, IPv6Network, ip_network

IPNetwork = IPv4Network | IPv6Network


def normalize_allowlist_network(value: str) -> str:
    """Return an IP/network string normalized for storage and matching."""
    candidate = value.strip()
    octets = candidate.split(".")
    if len(octets) == 4 and octets[-1] == "*":
        return str(ip_network(f"{'.'.join(octets[:3])}.0/24"))

    ip_network(candidate)
    return candidate


def parse_allowlist_network(value: str) -> IPNetwork:
    """Parse an IP/network string, including supported wildcard shorthand."""
    return ip_network(normalize_allowlist_network(value))
