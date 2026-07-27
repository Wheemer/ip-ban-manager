"""Tests for allowlist and blocked-network entry metadata."""

from __future__ import annotations

from custom_components.ip_ban_manager.const import (
    ATTR_ADDED_AT,
    ATTR_NETWORK,
    ATTR_SOURCE,
    SOURCE_CONFIGURE,
    SOURCE_DEFAULT,
    SOURCE_DETECTED,
    SOURCE_IMPORT,
    SOURCE_PANEL,
)
from custom_components.ip_ban_manager.entry_meta import (
    build_imported_meta,
    build_setup_allowlist_meta,
    format_network_entries,
    merge_imported_meta,
    sync_network_list_meta,
)


def test_sync_network_list_meta_preserves_existing_and_stamps_new() -> None:
    """Test syncing metadata keeps old rows and stamps new ones."""
    existing = {
        "192.168.1.0/24": {
            ATTR_ADDED_AT: "2026-01-01T00:00:00+00:00",
            ATTR_SOURCE: SOURCE_PANEL,
        }
    }
    synced = sync_network_list_meta(
        existing,
        ["192.168.1.0/24", "10.0.0.0/24"],
        source=SOURCE_CONFIGURE,
    )

    assert synced["192.168.1.0/24"] == existing["192.168.1.0/24"]
    assert synced["10.0.0.0/24"][ATTR_SOURCE] == SOURCE_CONFIGURE
    assert ATTR_ADDED_AT in synced["10.0.0.0/24"]


def test_build_setup_allowlist_meta_labels_sources() -> None:
    """Test setup metadata distinguishes defaults and detected networks."""
    meta = build_setup_allowlist_meta(
        ["127.0.0.1", "192.168.1.0/24", "2001:db8::/64"],
        default_networks={"127.0.0.1"},
        detected_networks={"192.168.1.0/24"},
    )

    assert meta["127.0.0.1"][ATTR_SOURCE] == SOURCE_DEFAULT
    assert meta["192.168.1.0/24"][ATTR_SOURCE] == SOURCE_DETECTED
    assert meta["2001:db8::/64"][ATTR_SOURCE] == "setup"


def test_merge_imported_meta_restores_backup_timestamps() -> None:
    """Test backup restore keeps imported timestamps when present."""
    imported_meta = {
        "192.168.1.0/24": {
            ATTR_ADDED_AT: "2026-02-02T12:00:00+00:00",
            ATTR_SOURCE: SOURCE_PANEL,
        }
    }
    merged = merge_imported_meta(
        {},
        ["192.168.1.0/24", "10.0.0.0/24"],
        imported_meta,
        source=SOURCE_IMPORT,
    )

    assert merged["192.168.1.0/24"] == imported_meta["192.168.1.0/24"]
    assert merged["10.0.0.0/24"][ATTR_SOURCE] == SOURCE_IMPORT


def test_format_network_entries_includes_metadata() -> None:
    """Test panel rows include stored metadata."""
    rows = format_network_entries(
        ["192.168.1.0/24"],
        {
            "192.168.1.0/24": {
                ATTR_ADDED_AT: "2026-02-02T12:00:00+00:00",
                ATTR_SOURCE: SOURCE_PANEL,
            }
        },
    )

    assert rows == [
        {
            ATTR_NETWORK: "192.168.1.0/24",
            ATTR_ADDED_AT: "2026-02-02T12:00:00+00:00",
            ATTR_SOURCE: SOURCE_PANEL,
        }
    ]


def test_build_imported_meta_stamps_every_network() -> None:
    """Test import metadata is created for every restored network."""
    meta = build_imported_meta(["192.168.1.0/24"], source=SOURCE_IMPORT)

    assert meta["192.168.1.0/24"][ATTR_SOURCE] == SOURCE_IMPORT
    assert ATTR_ADDED_AT in meta["192.168.1.0/24"]
