"""IP address helpers for IP Ban Manager."""

from __future__ import annotations

from ipaddress import IPv4Network, IPv6Network, ip_network

IPNetwork = IPv4Network | IPv6Network


def _normalize_ipv4_wildcard(candidate: str) -> str | None:
    """Return CIDR for IPv4 wildcard shorthand, or None if not applicable."""
    octets = candidate.split(".")
    if len(octets) != 4 or octets[-1] != "*":
        return None
    if any(not part or part == "*" for part in octets[:-1]):
        raise ValueError("Invalid IPv4 wildcard network.")
    return str(ip_network(f"{'.'.join(octets[:3])}.0/24"))


def _normalize_ipv6_wildcard(candidate: str) -> str | None:
    """Return CIDR for IPv6 wildcard shorthand, or None if not applicable."""
    if "*" not in candidate:
        return None
    if "." in candidate:
        raise ValueError("Invalid IPv6 wildcard network.")

    value = candidate.strip().lower()
    if value.endswith("::*"):
        left = value[:-3]
        if not left or left.endswith(":"):
            raise ValueError("Invalid IPv6 wildcard network.")
        hextets = left.split(":")
        if not hextets or any(not part or part == "*" for part in hextets):
            raise ValueError("Invalid IPv6 wildcard network.")
        for part in hextets:
            int(part, 16)
        prefix_len = len(hextets) * 16
        if prefix_len <= 0 or prefix_len >= 128:
            raise ValueError("Invalid IPv6 wildcard network.")
        normalized_hextets = list(hextets)
        while len(normalized_hextets) < 8:
            normalized_hextets.append("0")
        address = ":".join(normalized_hextets)
        return str(ip_network(f"{address}/{prefix_len}", strict=False))

    hextets = value.split(":")
    if not hextets or any(not part for part in hextets if part != "*"):
        raise ValueError("Invalid IPv6 wildcard network.")

    try:
        first_star = hextets.index("*")
    except ValueError as err:
        raise ValueError("Invalid IPv6 wildcard network.") from err

    if any(part != "*" for part in hextets[first_star:]):
        raise ValueError("Invalid IPv6 wildcard network.")

    if first_star == 0:
        raise ValueError("Invalid IPv6 wildcard network.")

    for part in hextets[:first_star]:
        if part == "*":
            raise ValueError("Invalid IPv6 wildcard network.")
        int(part, 16)

    prefix_len = first_star * 16
    if prefix_len <= 0 or prefix_len >= 128:
        raise ValueError("Invalid IPv6 wildcard network.")

    normalized_hextets = [
        part if index < first_star else "0" for index, part in enumerate(hextets)
    ]
    while len(normalized_hextets) < 8:
        normalized_hextets.append("0")

    address = ":".join(normalized_hextets)
    return str(ip_network(f"{address}/{prefix_len}", strict=False))


def normalize_allowlist_network(value: str) -> str:
    """Return an IP/network string normalized for storage and matching."""
    candidate = value.strip()
    ipv4_wildcard = _normalize_ipv4_wildcard(candidate)
    if ipv4_wildcard is not None:
        return ipv4_wildcard

    ipv6_wildcard = _normalize_ipv6_wildcard(candidate)
    if ipv6_wildcard is not None:
        return ipv6_wildcard

    ip_network(candidate)
    return candidate


def parse_allowlist_network(value: str) -> IPNetwork:
    """Parse an IP/network string, including supported wildcard shorthand."""
    return ip_network(normalize_allowlist_network(value))
