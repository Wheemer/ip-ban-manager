"""Tests for DNS-backed allowlist entries."""

from __future__ import annotations

from custom_components.ip_ban_manager import dns_allowlist


def test_hostnames_are_normalized_and_deduplicated() -> None:
    """Only valid hostnames are retained, in first-seen order."""
    assert dns_allowlist._hostnames(
        ["Phone.Example.Org.", "phone.example.org", "192.168.1.1", "ha"]
    ) == ("phone.example.org", "ha")


def test_resolve_hostname_collects_ipv4_and_ipv6(monkeypatch) -> None:
    """Both address families returned by the system resolver are accepted."""
    monkeypatch.setattr(
        dns_allowlist.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("192.0.2.10", 0)),
            (10, 1, 6, "", ("2001:db8::10", 0, 0, 0)),
        ],
    )
    assert dns_allowlist._resolve_hostname("phone.example.org") == {
        "192.0.2.10",
        "2001:db8::10",
    }
