"""Per-entry metadata for allowlist and blocked-network rows."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_ADDED_AT,
    ATTR_NETWORK,
    ATTR_SOURCE,
    CONF_ALLOWLIST_ENTRY_META,
    CONF_BLOCKED_NETWORK_ENTRY_META,
    SOURCE_DEFAULT,
    SOURCE_DETECTED,
    SOURCE_IMPORT,
    SOURCE_SETUP,
)

NetworkEntryMeta = dict[str, str]
NetworkMetaStore = dict[str, NetworkEntryMeta]


def entry_allowlist_meta(entry: ConfigEntry) -> NetworkMetaStore:
    """Return stored allowlist entry metadata."""
    return _normalize_meta_store(
        entry.options.get(CONF_ALLOWLIST_ENTRY_META)
        or entry.data.get(CONF_ALLOWLIST_ENTRY_META)
    )


def entry_blocked_network_meta(entry: ConfigEntry) -> NetworkMetaStore:
    """Return stored blocked-network entry metadata."""
    return _normalize_meta_store(
        entry.options.get(CONF_BLOCKED_NETWORK_ENTRY_META)
        or entry.data.get(CONF_BLOCKED_NETWORK_ENTRY_META)
    )


def sync_network_list_meta(
    existing_meta: NetworkMetaStore,
    networks: list[str],
    *,
    source: str,
) -> NetworkMetaStore:
    """Keep metadata for surviving networks and stamp newly added ones."""
    synced = {
        network: existing_meta[network]
        for network in networks
        if network in existing_meta
    }
    added_at = dt_util.utcnow().isoformat()
    for network in networks:
        if network not in synced:
            synced[network] = {ATTR_ADDED_AT: added_at, ATTR_SOURCE: source}
    return synced


def build_setup_allowlist_meta(
    networks: list[str],
    *,
    default_networks: set[str],
    detected_networks: set[str],
) -> NetworkMetaStore:
    """Build initial allowlist metadata during setup."""
    added_at = dt_util.utcnow().isoformat()
    meta: NetworkMetaStore = {}
    for network in networks:
        if network in default_networks:
            source = SOURCE_DEFAULT
        elif network in detected_networks:
            source = SOURCE_DETECTED
        else:
            source = SOURCE_SETUP
        meta[network] = {ATTR_ADDED_AT: added_at, ATTR_SOURCE: source}
    return meta


def build_imported_meta(
    networks: list[str], *, source: str = SOURCE_IMPORT
) -> NetworkMetaStore:
    """Build metadata for networks restored from backup or YAML."""
    added_at = dt_util.utcnow().isoformat()
    return {
        network: {ATTR_ADDED_AT: added_at, ATTR_SOURCE: source} for network in networks
    }


def merge_imported_meta(
    existing_meta: NetworkMetaStore,
    networks: list[str],
    imported_meta: NetworkMetaStore | None,
    *,
    source: str = SOURCE_IMPORT,
) -> NetworkMetaStore:
    """Restore imported metadata when present, otherwise stamp import time."""
    if imported_meta is None:
        return sync_network_list_meta({}, networks, source=source)
    merged = sync_network_list_meta(existing_meta, networks, source=source)
    for network in networks:
        if network in imported_meta:
            merged[network] = _normalize_entry_meta(imported_meta[network])
    return merged


def format_network_entries(
    networks: list[str], meta_store: NetworkMetaStore
) -> list[dict[str, str]]:
    """Return panel/API rows for configured networks."""
    rows: list[dict[str, str]] = []
    for network in networks:
        row: dict[str, str] = {ATTR_NETWORK: network}
        entry_meta = meta_store.get(network)
        if entry_meta:
            row.update(entry_meta)
        rows.append(row)
    return rows


def _normalize_meta_store(value: object) -> NetworkMetaStore:
    """Return a stable metadata mapping from config-entry storage."""
    if not isinstance(value, dict):
        return {}
    normalized: NetworkMetaStore = {}
    for network, entry_meta in value.items():
        if not isinstance(network, str) or not isinstance(entry_meta, dict):
            continue
        normalized[network] = _normalize_entry_meta(entry_meta)
    return normalized


def _normalize_entry_meta(entry_meta: dict[str, Any]) -> NetworkEntryMeta:
    """Return a stable metadata record for one network row."""
    normalized: NetworkEntryMeta = {}
    added_at = entry_meta.get(ATTR_ADDED_AT)
    source = entry_meta.get(ATTR_SOURCE)
    if isinstance(added_at, str) and added_at:
        normalized[ATTR_ADDED_AT] = added_at
    if isinstance(source, str) and source:
        normalized[ATTR_SOURCE] = source
    return normalized
