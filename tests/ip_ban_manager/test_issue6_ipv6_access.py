"""Reproduce hostname access using a real IPv6 link-local client address."""

from ipaddress import ip_address, ip_network
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.components.http.ban import KEY_BAN_MANAGER
from homeassistant.loader import DATA_CUSTOM_COMPONENTS, async_get_custom_components
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.ip_ban_manager as ipbm
from custom_components.ip_ban_manager import internal_networks, network_policy
from custom_components.ip_ban_manager.ban_lookup import NetworkAwareBanLookup
from custom_components.ip_ban_manager.const import (
    CONF_ALLOWLIST_ENTRY_META,
    CONF_BLOCKED_NETWORKS,
    CONF_DEFAULT_DENY_ENABLED,
    CONF_IP_ADDRESSES,
    CONF_IPV6_LOCAL_DEFAULTS_MIGRATED,
    DOMAIN,
)
from custom_components.ip_ban_manager.entry_helpers import entry_ip_addresses


@pytest.mark.asyncio
@pytest.mark.parametrize("client", ["fe80::2", "fe80::2%eth0"])
async def test_ipv4_allowlist_does_not_cover_link_local_ipv6_peer(monkeypatch, client):
    """Visible IPv6 allowance, not HA self protection, permits a LAN client."""
    adapters = [
        {
            "enabled": True,
            "default": True,
            "ipv4": [{"address": "192.168.1.15", "network_prefix": 24}],
            "ipv6": [{"address": "fe80::1", "network_prefix": 64}],
        }
    ]
    monkeypatch.setattr(
        internal_networks, "async_get_adapters", AsyncMock(return_value=adapters)
    )
    hass = SimpleNamespace()
    owned = await internal_networks.async_home_assistant_self_networks(hass)
    detected = await internal_networks.async_home_assistant_allowlist_safe_defaults(
        hass
    )
    lookup = NetworkAwareBanLookup(
        {},
        (),
        (ip_network("192.168.1.0/24"),),
        True,
        internal_bypass_networks=owned,
    )
    assert ip_address("192.168.1.20") not in lookup
    assert ip_address("fe80::1") not in lookup
    assert ip_address(client) in lookup
    assert "fe80::/64" in detected
    lookup.allowlist += (ip_network("fe80::/64"),)
    assert ip_address(client) not in lookup
    assert ip_address("2001:db8::2") in lookup


async def _setup_legacy(hass, monkeypatch, *, addresses=None, extra=None, ipv6=True):
    adapters = [
        {
            "enabled": True,
            "default": True,
            "ipv4": [{"address": "192.168.1.15", "network_prefix": 24}],
            "ipv6": (
                [
                    {"address": "fe80::1", "network_prefix": 64},
                    {"address": "fd12::1", "network_prefix": 64},
                    {"address": "2001:db8::1", "network_prefix": 64},
                ]
                if ipv6
                else []
            ),
        }
    ]
    monkeypatch.setattr(ipbm, "_async_reload_runtime_modules", AsyncMock())
    monkeypatch.setattr(
        internal_networks, "async_get_adapters", AsyncMock(return_value=adapters)
    )
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert DOMAIN in await async_get_custom_components(hass)
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_IP_ADDRESSES: (
                addresses if addresses is not None else ["127.0.0.1", "192.168.1.0/24"]
            ),
            CONF_DEFAULT_DENY_ENABLED: True,
            **(extra or {}),
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, adapters


@pytest.mark.asyncio
async def test_upgrade_adds_visible_link_local_once_and_preserves_removal(
    hass, monkeypatch
):
    """Real setup and reload restore legacy access without a hidden exemption."""
    entry, _ = await _setup_legacy(hass, monkeypatch)
    assert entry_ip_addresses(entry) == ["127.0.0.1", "192.168.1.0/24", "fe80::/64"]
    assert entry.options[CONF_IPV6_LOCAL_DEFAULTS_MIGRATED] is True
    assert entry.options[CONF_ALLOWLIST_ENTRY_META]["fe80::/64"]["source"] == "detected"
    lookup = hass.http.app[KEY_BAN_MANAGER].ip_bans_lookup
    for client in ["192.168.1.20", "::ffff:192.168.1.20", "fe80::2", "fe80::2%eth0"]:
        assert ip_address(client) not in lookup
    for client in ["fd12::2", "2001:db8::2", "198.51.100.2"]:
        assert ip_address(client) in lookup
    await network_policy.async_sync_detected_allowlist_defaults(hass)
    assert entry_ip_addresses(entry).count("fe80::/64") == 1
    network_policy.update_allowlist_entry(hass, ["127.0.0.1", "192.168.1.0/24"])
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert "fe80::/64" not in entry_ip_addresses(entry)
    assert ip_address("fe80::2") in hass.http.app[KEY_BAN_MANAGER].ip_bans_lookup


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra,addresses",
    [
        ({CONF_BLOCKED_NETWORKS: ["fe80::/64"]}, None),
        ({CONF_BLOCKED_NETWORKS: ["::/0"]}, None),
        ({CONF_ALLOWLIST_ENTRY_META: {"192.168.1.0/24": {"source": "panel"}}}, None),
        ({CONF_IPV6_LOCAL_DEFAULTS_MIGRATED: True}, None),
        ({}, ["192.168.1.0/24"]),
        ({}, ["127.0.0.1", "10.0.0.0/24"]),
    ],
)
async def test_upgrade_respects_custom_or_explicitly_blocked_ipv6(
    hass, monkeypatch, extra, addresses
):
    """Do not widen custom configurations or override explicit IPv6 blocks."""
    entry, _ = await _setup_legacy(hass, monkeypatch, addresses=addresses, extra=extra)
    assert "fe80::/64" not in entry_ip_addresses(entry)


@pytest.mark.asyncio
async def test_upgrade_waits_for_ipv6_discovery(hass, monkeypatch):
    """An empty discovery result must not consume the migration."""
    entry, adapters = await _setup_legacy(hass, monkeypatch, ipv6=False)
    assert not entry.options.get(CONF_IPV6_LOCAL_DEFAULTS_MIGRATED)
    adapters[0]["ipv6"] = [{"address": "fe80::1", "network_prefix": 64}]
    await network_policy.async_sync_detected_allowlist_defaults(hass)
    assert "fe80::/64" in entry_ip_addresses(entry)


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", ["fe80:0:0:0::/64", "fe80::/10"])
async def test_upgrade_does_not_duplicate_equivalent_ipv6(hass, monkeypatch, existing):
    """Existing larger allowances and expanded spelling are kept unchanged."""
    addresses = ["127.0.0.1", "192.168.1.0/24", existing]
    entry, _ = await _setup_legacy(hass, monkeypatch, addresses=addresses)
    assert entry_ip_addresses(entry) == addresses
    assert entry.options[CONF_IPV6_LOCAL_DEFAULTS_MIGRATED] is True


@pytest.mark.asyncio
async def test_upgrade_honors_detected_entry_metadata(hass, monkeypatch):
    """Recorded safe-default choices migrate, preserving existing metadata."""
    meta = {
        "192.168.1.0/24": {
            "source": "detected",
            "added_at": "2026-01-01T00:00:00+00:00",
        }
    }
    entry, _ = await _setup_legacy(
        hass, monkeypatch, extra={CONF_ALLOWLIST_ENTRY_META: meta}
    )
    assert "fe80::/64" in entry_ip_addresses(entry)
    assert (
        entry.options[CONF_ALLOWLIST_ENTRY_META]["192.168.1.0/24"]
        == meta["192.168.1.0/24"]
    )


@pytest.mark.asyncio
async def test_upgrade_accepts_legacy_broader_ipv4_local_allowance(hass, monkeypatch):
    """A broader existing LAN allowance still represents legacy safe defaults."""
    entry, _ = await _setup_legacy(
        hass, monkeypatch, addresses=["127.0.0.1", "192.168.0.0/16"]
    )
    assert "fe80::/64" in entry_ip_addresses(entry)


@pytest.mark.asyncio
async def test_completed_upgrade_does_not_write_options_again(hass, monkeypatch):
    """Repeated synchronization must be a no-op after migration."""
    entry, _ = await _setup_legacy(hass, monkeypatch)
    write = Mock(wraps=network_policy.update_entry_options)
    monkeypatch.setattr(network_policy, "update_entry_options", write)
    await network_policy.async_sync_detected_allowlist_defaults(hass)
    await network_policy.async_sync_detected_allowlist_defaults(hass)
    write.assert_not_called()
    assert entry_ip_addresses(entry).count("fe80::/64") == 1


@pytest.mark.asyncio
async def test_failed_migration_write_can_be_retried(hass, monkeypatch):
    """Never record completion before the allowlist was saved successfully."""
    entry, adapters = await _setup_legacy(hass, monkeypatch, ipv6=False)
    adapters[0]["ipv6"] = [{"address": "fe80::1", "network_prefix": 64}]
    original = network_policy.update_entry_options
    monkeypatch.setattr(
        network_policy,
        "update_entry_options",
        Mock(side_effect=RuntimeError("write failed")),
    )
    with pytest.raises(RuntimeError, match="write failed"):
        await network_policy.async_sync_detected_allowlist_defaults(hass)
    assert not entry.options.get(CONF_IPV6_LOCAL_DEFAULTS_MIGRATED)
    assert "fe80::/64" not in entry_ip_addresses(entry)
    monkeypatch.setattr(network_policy, "update_entry_options", original)
    await network_policy.async_sync_detected_allowlist_defaults(hass)
    assert "fe80::/64" in entry_ip_addresses(entry)


@pytest.mark.asyncio
async def test_backup_restore_preserves_removal_and_migration_state(hass, monkeypatch):
    """Restoring on the same installation must not revive a removed allowance."""
    from custom_components.ip_ban_manager.backup import (
        async_apply_config_backup_payload,
        config_export_payload,
    )

    entry, _ = await _setup_legacy(hass, monkeypatch)
    network_policy.update_allowlist_entry(hass, ["127.0.0.1", "192.168.1.0/24"])
    backup = config_export_payload(hass, entry)
    assert backup["settings"][CONF_IPV6_LOCAL_DEFAULTS_MIGRATED] is True
    restored_options = dict(entry.options)
    restored_options.pop(CONF_IPV6_LOCAL_DEFAULTS_MIGRATED)
    hass.config_entries.async_update_entry(entry, options=restored_options)
    await async_apply_config_backup_payload(hass, backup)
    await network_policy.async_sync_detected_allowlist_defaults(hass)
    assert entry.options[CONF_IPV6_LOCAL_DEFAULTS_MIGRATED] is True
    assert "fe80::/64" not in entry_ip_addresses(entry)


@pytest.mark.asyncio
async def test_disabled_ipv6_adapter_is_not_trusted(hass, monkeypatch):
    """Inactive adapters must not expand detected local defaults."""
    entry, adapters = await _setup_legacy(hass, monkeypatch, ipv6=False)
    adapters.append(
        {
            "enabled": False,
            "default": False,
            "ipv4": [],
            "ipv6": [{"address": "fe80::1", "network_prefix": 64}],
        }
    )
    await network_policy.async_sync_detected_allowlist_defaults(hass)
    assert "fe80::/64" not in entry_ip_addresses(entry)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [None, False, "invalid"])
async def test_old_or_malformed_backup_cannot_reset_completed_migration(
    hass, monkeypatch, state
):
    """Old backups preserve completion; invalid metadata is rejected before writes."""
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.ip_ban_manager.backup import (
        async_apply_config_backup_payload,
        config_export_payload,
    )

    entry, _ = await _setup_legacy(hass, monkeypatch)
    network_policy.update_allowlist_entry(hass, ["127.0.0.1", "192.168.1.0/24"])
    backup = config_export_payload(hass, entry)
    if state is None:
        backup["settings"].pop(CONF_IPV6_LOCAL_DEFAULTS_MIGRATED)
    else:
        backup["settings"][CONF_IPV6_LOCAL_DEFAULTS_MIGRATED] = state
    if state == "invalid":
        before = dict(entry.options)
        with pytest.raises(HomeAssistantError):
            await async_apply_config_backup_payload(hass, backup)
        assert dict(entry.options) == before
    else:
        await async_apply_config_backup_payload(hass, backup)
    await network_policy.async_sync_detected_allowlist_defaults(hass)
    assert entry.options[CONF_IPV6_LOCAL_DEFAULTS_MIGRATED] is True
    assert "fe80::/64" not in entry_ip_addresses(entry)
