"""Test the Ban Allowlist config flow."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.loader import DATA_CUSTOM_COMPONENTS, async_get_custom_components
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ban_allowlist.const import CONF_IP_ADDRESSES, DOMAIN


async def load_ban_allowlist(hass: HomeAssistant) -> None:
    """Load the custom integration."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert list((await async_get_custom_components(hass)).keys()) == ["ban_allowlist"]


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
    assert result["title"] == "IP Ban Allowlist"
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
    """Test only one Ban Allowlist entry can be configured."""
    await load_ban_allowlist(hass)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Allowlist",
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
async def test_options_flow(hass: HomeAssistant) -> None:
    """Test updating the allowlist from options."""
    await load_ban_allowlist(hass)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Allowlist",
        unique_id=DOMAIN,
        data={CONF_IP_ADDRESSES: ["192.168.1.1"]},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_IP_ADDRESSES: "172.17.0.0/24"},
    )

    assert result["type"] == "create_entry"
    assert result["data"] == {CONF_IP_ADDRESSES: ["172.17.0.0/24"]}
