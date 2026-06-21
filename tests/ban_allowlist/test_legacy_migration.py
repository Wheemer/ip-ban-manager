"""Test legacy ban_allowlist compatibility loader."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.loader import DATA_CUSTOM_COMPONENTS, async_get_custom_components
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ip_ban_manager.const import CONF_IP_ADDRESSES

LEGACY_DOMAIN = "ban_allowlist"
TARGET_DOMAIN = "ip_ban_manager"


@pytest.mark.asyncio
async def test_legacy_entry_imports_to_ip_ban_manager(hass: HomeAssistant) -> None:
    """Test a stored ban_allowlist entry is imported and removed."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    custom_components = await async_get_custom_components(hass)
    assert LEGACY_DOMAIN in custom_components
    assert TARGET_DOMAIN in custom_components
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="ban_allowlist",
        data={CONF_IP_ADDRESSES: ["192.168.1.1", "127.0.0.1"]},
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await hass.async_block_till_done()

    assert not hass.config_entries.async_entries(LEGACY_DOMAIN)
    entries = hass.config_entries.async_entries(TARGET_DOMAIN)
    assert len(entries) == 1
    assert entries[0].title == "IP Ban Manager"
    assert entries[0].data == {CONF_IP_ADDRESSES: ["192.168.1.1", "127.0.0.1"]}


@pytest.mark.asyncio
async def test_legacy_entry_removed_when_target_exists(hass: HomeAssistant) -> None:
    """Test legacy entry is removed without duplicating IP Ban Manager."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert LEGACY_DOMAIN in (await async_get_custom_components(hass))
    target_entry = MockConfigEntry(
        domain=TARGET_DOMAIN,
        title="IP Ban Manager",
        data={CONF_IP_ADDRESSES: ["127.0.0.1"]},
    )
    target_entry.add_to_hass(hass)
    legacy_entry = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="IP Ban Allowlist",
        data={CONF_IP_ADDRESSES: ["192.168.1.1"]},
    )
    legacy_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(legacy_entry.entry_id)
    await hass.async_block_till_done()
    await hass.async_block_till_done()

    assert not hass.config_entries.async_entries(LEGACY_DOMAIN)
    assert len(hass.config_entries.async_entries(TARGET_DOMAIN)) == 1
