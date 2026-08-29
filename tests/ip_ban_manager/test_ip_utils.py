"""Tests for IP Ban Manager network parsing helpers."""

from __future__ import annotations

import pytest

from custom_components.ip_ban_manager.ip_utils import (
    normalize_allowlist_network,
    parse_allowlist_network,
)


def test_normalize_ipv4_wildcard() -> None:
    """Test IPv4 wildcard shorthand expands to /24 CIDR."""
    assert normalize_allowlist_network("192.168.1.*") == "192.168.1.0/24"
    assert str(parse_allowlist_network("10.20.30.*")) == "10.20.30.0/24"


def test_normalize_ipv6_wildcard_single_trailing_hextet() -> None:
    """Test a trailing IPv6 wildcard hextet expands to a /64."""
    assert normalize_allowlist_network("2001:db8:1:2:*") == "2001:db8:1:2::/64"
    assert str(parse_allowlist_network("2001:db8:1:2:*")) == "2001:db8:1:2::/64"


def test_normalize_ipv6_wildcard_trailing_double_colon_form() -> None:
    """Test ::* suffix matches the same shorthand as a trailing * hextet."""
    assert normalize_allowlist_network("2001:db8:1:2::*") == "2001:db8:1:2::/64"
    assert normalize_allowlist_network("2001:db8::*") == "2001:db8::/32"


def test_normalize_ipv6_wildcard_multiple_trailing_hextets() -> None:
    """Test redundant trailing wildcard hextets still normalize to /64."""
    assert normalize_allowlist_network("2001:db8:1:2:*:*") == "2001:db8:1:2::/64"
    assert normalize_allowlist_network("2001:db8:*:*") == "2001:db8::/32"


def test_normalize_ipv6_wildcard_link_local_shorthand() -> None:
    """Test a short IPv6 wildcard prefix expands predictably."""
    assert normalize_allowlist_network("fe80:*") == "fe80::/16"


@pytest.mark.parametrize(
    "value",
    [
        "*",
        "2001:db8:*:2:*",
        "2001:db8:1:2::1:*",
        "192.168.*.1",
    ],
)
def test_reject_invalid_wildcard_forms(value: str) -> None:
    """Test malformed wildcard shorthand is rejected."""
    with pytest.raises(ValueError):
        normalize_allowlist_network(value)
