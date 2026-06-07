"""Test the Ban Allowlist config flow."""

from __future__ import annotations

from ipaddress import IPv4Address, ip_address
from pathlib import Path
from typing import cast

import pytest
from homeassistant.components.http.ban import KEY_BAN_MANAGER, IpBanManager
from homeassistant.core import HomeAssistant
from homeassistant.loader import DATA_CUSTOM_COMPONENTS, async_get_custom_components
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ban_allowlist import KEY_ALLOWLIST
from custom_components.ban_allowlist.const import (
    ATTR_BANNED_IPS,
    CONF_ALLOWED_IPS,
    CONF_BANNED_IPS,
    CONF_IP_ADDRESSES,
    DOMAIN,
)


async def load_ban_allowlist(hass: HomeAssistant) -> None:
    """Load the custom integration."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert list((await async_get_custom_components(hass)).keys()) == ["ban_allowlist"]


async def setup_options_entry(hass: HomeAssistant, tmp_path: Path) -> MockConfigEntry:
    """Set up an options-test config entry with Home Assistant HTTP loaded."""
    await load_ban_allowlist(hass)
    await async_setup_component(hass, "http", {})

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        unique_id=DOMAIN,
        data={CONF_IP_ADDRESSES: ["192.168.1.1", "172.17.0.0/24"]},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER]).path = str(
        tmp_path / "ip_bans.yaml"
    )
    return entry


@pytest.mark.asyncio
async def test_user_flow(hass: HomeAssistant) -> None:
    """Test creating an entry from the UI."""
    await load_ban_allowlist(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={CONF_IP_ADDRESSES: "192.168.1.1\n172.17.0.0/24"},
    )

    assert result["type"] == "create_entry"
    assert result["title"] == "IP Ban Manager"
    assert result["data"] == {CONF_IP_ADDRESSES: ["192.168.1.1", "172.17.0.0/24"]}


@pytest.mark.asyncio
async def test_user_flow_accepts_comma_separated_addresses(
    hass: HomeAssistant,
) -> None:
    """Test comma-separated addresses are normalized."""
    await load_ban_allowlist(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={CONF_IP_ADDRESSES: "192.168.1.1, 172.17.0.0/24"},
    )

    assert result["type"] == "create_entry"
    assert result["data"] == {CONF_IP_ADDRESSES: ["192.168.1.1", "172.17.0.0/24"]}


@pytest.mark.asyncio
async def test_user_flow_rejects_invalid_addresses(hass: HomeAssistant) -> None:
    """Test invalid addresses are rejected."""
    await load_ban_allowlist(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={CONF_IP_ADDRESSES: "not-an-ip"},
    )

    assert result["type"] == "form"
    assert result["errors"] == {CONF_IP_ADDRESSES: "invalid_ip_address"}


@pytest.mark.asyncio
async def test_user_flow_is_single_instance(hass: HomeAssistant) -> None:
    """Test only one IP Ban Manager entry can be configured."""
    await load_ban_allowlist(hass)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        unique_id=DOMAIN,
        data={CONF_IP_ADDRESSES: ["192.168.1.1"]},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
        data={CONF_IP_ADDRESSES: "172.17.0.0/24"},
    )

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


@pytest.mark.asyncio
async def test_options_flow_edits_live_lists(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test options edit both lists and update Home Assistant immediately."""
    entry = await setup_options_entry(hass, tmp_path)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    await ban_manager.async_add_ban(IPv4Address("10.0.0.1"))
    original_banned_at = ban_manager.ip_bans_lookup[ip_address("10.0.0.1")].banned_at

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    assert result["description_placeholders"]["networks"] == (
        "192.168.1.1/32\n172.17.0.0/24"
    )
    assert result["description_placeholders"][ATTR_BANNED_IPS] == (
        f"10.0.0.1 - {original_banned_at.isoformat()}"
    )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOWED_IPS: {CONF_ALLOWED_IPS: "192.168.1.1\n10.0.0.0/24"},
            CONF_BANNED_IPS: {
                CONF_BANNED_IPS: (
                    f"10.0.0.1 - {original_banned_at.isoformat()}\n10.0.0.2"
                )
            },
        },
    )

    assert result["type"] == "create_entry"
    assert result["data"] == {
        CONF_IP_ADDRESSES: ["192.168.1.1", "10.0.0.0/24"],
        CONF_BANNED_IPS: ["10.0.0.1", "10.0.0.2"],
    }
    assert [str(ip) for ip in hass.http.app[KEY_ALLOWLIST]] == [
        "192.168.1.1/32",
        "10.0.0.0/24",
    ]
    assert set(ban_manager.ip_bans_lookup) == {
        ip_address("10.0.0.1"),
        ip_address("10.0.0.2"),
    }
    assert ban_manager.ip_bans_lookup[ip_address("10.0.0.1")].banned_at == (
        original_banned_at
    )


@pytest.mark.asyncio
async def test_options_flow_removes_live_bans(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test removing a ban from the textarea removes it immediately."""
    entry = await setup_options_entry(hass, tmp_path)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    await ban_manager.async_add_ban(IPv4Address("10.0.0.1"))
    await ban_manager.async_add_ban(IPv4Address("10.0.0.2"))

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOWED_IPS: {CONF_ALLOWED_IPS: "192.168.1.1\n172.17.0.0/24"},
            CONF_BANNED_IPS: {CONF_BANNED_IPS: "10.0.0.2"},
        },
    )

    assert result["type"] == "create_entry"
    assert set(ban_manager.ip_bans_lookup) == {ip_address("10.0.0.2")}


@pytest.mark.asyncio
async def test_options_flow_rejects_invalid_banned_ip(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test invalid banned IP values are rejected."""
    entry = await setup_options_entry(hass, tmp_path)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOWED_IPS: {CONF_ALLOWED_IPS: "192.168.1.1\n172.17.0.0/24"},
            CONF_BANNED_IPS: {CONF_BANNED_IPS: "10.0.0.1\nnot-an-ip"},
        },
    )

    assert result["type"] == "form"
    assert result["errors"] == {ATTR_BANNED_IPS: "invalid_banned_ip"}
