"""Test the IP Ban Manager config flow."""

from __future__ import annotations

from datetime import datetime, timezone
from ipaddress import IPv4Address, ip_address
from pathlib import Path
from typing import Any, cast

import pytest
from homeassistant.components.http.ban import KEY_BAN_MANAGER, IpBanManager
from homeassistant.core import HomeAssistant
from homeassistant.loader import DATA_CUSTOM_COMPONENTS, async_get_custom_components
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from voluptuous.schema_builder import Optional as VolOptional

import custom_components.ip_ban_manager as ipbm
from custom_components.ip_ban_manager import KEY_ALLOWLIST
from custom_components.ip_ban_manager import config_flow as ban_config_flow
from custom_components.ip_ban_manager.config_flow import (
    DEFAULT_ALLOWED_IPS,
    _format_banned_at,
)
from custom_components.ip_ban_manager.const import (
    ATTR_BANNED_IPS,
    CONF_ALLOWED_IPS,
    CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED,
    CONF_ALLOWLISTED_LOGINS_CAN_BAN,
    CONF_AUTO_BAN_ENABLED,
    CONF_BAN_NOTIFICATIONS_ENABLED,
    CONF_BANNED_IPS,
    CONF_BLOCKED_NETWORKS,
    CONF_DEFAULT_DENY_ENABLED,
    CONF_IP_ADDRESSES,
    CONF_LOGIN_ATTEMPTS_THRESHOLD,
    CONF_SIDEBAR_PANEL_ENABLED,
    DOMAIN,
    LEGACY_DOMAIN,
)


def expected_setup_data(ip_addresses: list[str]) -> dict[str, object]:
    """Return expected first-run config entry data."""
    return {
        CONF_IP_ADDRESSES: ip_addresses,
        CONF_AUTO_BAN_ENABLED: True,
        CONF_BAN_NOTIFICATIONS_ENABLED: True,
        CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED: True,
        CONF_ALLOWLISTED_LOGINS_CAN_BAN: False,
        CONF_DEFAULT_DENY_ENABLED: False,
        CONF_LOGIN_ATTEMPTS_THRESHOLD: 0,
    }


def expected_options_data(
    ip_addresses: list[str], threshold: int = 0
) -> dict[str, object]:
    """Return expected options-flow data."""
    return {
        CONF_IP_ADDRESSES: ip_addresses,
        CONF_AUTO_BAN_ENABLED: True,
        CONF_BAN_NOTIFICATIONS_ENABLED: True,
        CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED: True,
        CONF_ALLOWLISTED_LOGINS_CAN_BAN: False,
        CONF_DEFAULT_DENY_ENABLED: False,
        CONF_SIDEBAR_PANEL_ENABLED: True,
        CONF_LOGIN_ATTEMPTS_THRESHOLD: threshold,
        CONF_BLOCKED_NETWORKS: [],
    }


async def no_detected_subnets(hass: HomeAssistant) -> list[str]:
    """Return no detected subnet for config-flow tests."""
    return []


async def detected_subnets(hass: HomeAssistant) -> list[str]:
    """Return a detected subnet for config-flow tests."""
    return ["192.168.1.0/24"]


async def detected_dual_stack_subnets(hass: HomeAssistant) -> list[str]:
    """Return detected IPv4 and IPv6 subnets for config-flow tests."""
    return ["192.168.1.0/24", "fd12:3456:789a::/64"]


async def mixed_adapters(hass: HomeAssistant) -> list[dict[str, object]]:
    """Return mixed network adapters for subnet detection tests."""
    return [
        {
            "name": "lo",
            "index": 1,
            "enabled": True,
            "auto": True,
            "default": False,
            "ipv4": [{"address": "127.0.0.1", "network_prefix": 8}],
            "ipv6": [{"address": "::1", "network_prefix": 128}],
        },
        {
            "name": "eth0",
            "index": 2,
            "enabled": True,
            "auto": True,
            "default": True,
            "ipv4": [{"address": "192.168.1.40", "network_prefix": 24}],
            "ipv6": [
                {"address": "fe80::1234", "network_prefix": 64},
                {"address": "fd12:3456:789a::40", "network_prefix": 64},
            ],
        },
        {
            "name": "fallback",
            "index": 3,
            "enabled": True,
            "auto": True,
            "default": False,
            "ipv4": [{"address": "169.254.1.2", "network_prefix": 16}],
            "ipv6": [{"address": "ff02::1", "network_prefix": 16}],
        },
    ]


async def load_ip_ban_manager(hass: HomeAssistant) -> None:
    """Load the custom integration."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))


async def setup_options_entry(hass: HomeAssistant, tmp_path: Path) -> MockConfigEntry:
    """Set up an options-test config entry with Home Assistant HTTP loaded."""
    await load_ip_ban_manager(hass)
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
async def test_detect_home_assistant_subnets(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test subnet detection keeps only useful enabled local networks."""
    monkeypatch.setattr(ban_config_flow, "async_get_adapters", mixed_adapters)

    assert await ban_config_flow._async_detect_home_assistant_subnets(hass) == [
        "192.168.1.0/24",
        "fd12:3456:789a::/64",
    ]


@pytest.mark.asyncio
async def test_user_flow(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test creating an entry from the UI without text-list editing."""
    await load_ip_ban_manager(hass)
    monkeypatch.setattr(
        ban_config_flow,
        "_async_detect_home_assistant_subnets",
        no_detected_subnets,
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
    )

    assert result["type"] == "form"
    assert result["description_placeholders"]["home_assistant_subnets"] == "None"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            ban_config_flow.CONF_QUICK_ALLOWLIST: [
                ban_config_flow.QUICK_ALLOW_LOCALHOST
            ],
        },
    )

    assert result["type"] == "create_entry"
    assert result["title"] == "IP Ban Manager"
    assert result["data"] == expected_setup_data(DEFAULT_ALLOWED_IPS)


@pytest.mark.asyncio
async def test_user_flow_absorbs_legacy_entry(hass: HomeAssistant) -> None:
    """Test Add Integration absorbs a stale old-domain config entry."""
    await load_ip_ban_manager(hass)
    legacy_entry = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="IP Ban Manager",
        data={CONF_IP_ADDRESSES: ["127.0.0.1"]},
        options={CONF_IP_ADDRESSES: ["127.0.0.1", "192.168.1.0/24"]},
    )
    legacy_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
    )

    assert result["type"] == "create_entry"
    assert result["title"] == "IP Ban Manager"
    assert result["data"] == {
        CONF_IP_ADDRESSES: ["127.0.0.1", "192.168.1.0/24"],
        ban_config_flow.CONF_LEGACY_ENTRY_ID: legacy_entry.entry_id,
    }


@pytest.mark.asyncio
async def test_user_flow_can_add_detected_subnet(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test first-run setup can add the detected Home Assistant subnet."""
    await load_ip_ban_manager(hass)
    monkeypatch.setattr(
        ban_config_flow,
        "_async_detect_home_assistant_subnets",
        detected_subnets,
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
    )

    assert result["type"] == "form"
    assert result["description_placeholders"]["home_assistant_subnets"] == (
        "192.168.1.0/24\n"
    )
    quick_allowlist_marker = next(
        marker
        for marker in result["data_schema"].schema
        if marker.schema == ban_config_flow.CONF_QUICK_ALLOWLIST
    )
    assert quick_allowlist_marker.default() == [
        ban_config_flow.QUICK_ALLOW_LOCALHOST,
        ban_config_flow.QUICK_ALLOW_LOCAL_NETWORK,
    ]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            ban_config_flow.CONF_QUICK_ALLOWLIST: [
                ban_config_flow.QUICK_ALLOW_LOCALHOST,
                ban_config_flow.QUICK_ALLOW_LOCAL_NETWORK,
            ],
        },
    )

    assert result["type"] == "create_entry"
    assert result["data"] == expected_setup_data(
        [*DEFAULT_ALLOWED_IPS, "192.168.1.0/24"]
    )


@pytest.mark.asyncio
async def test_user_flow_can_add_detected_ipv4_and_ipv6_subnets(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test first-run setup can add detected IPv4 and IPv6 local subnets."""
    await load_ip_ban_manager(hass)
    monkeypatch.setattr(
        ban_config_flow,
        "_async_detect_home_assistant_subnets",
        detected_dual_stack_subnets,
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
    )

    assert result["type"] == "form"
    assert result["description_placeholders"]["home_assistant_subnets"] == (
        "192.168.1.0/24\nfd12:3456:789a::/64\n"
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            ban_config_flow.CONF_QUICK_ALLOWLIST: [
                ban_config_flow.QUICK_ALLOW_LOCALHOST,
                ban_config_flow.QUICK_ALLOW_LOCAL_NETWORK,
            ],
        },
    )

    assert result["type"] == "create_entry"
    assert result["data"] == expected_setup_data(
        [*DEFAULT_ALLOWED_IPS, "192.168.1.0/24", "fd12:3456:789a::/64"]
    )


@pytest.mark.asyncio
async def test_user_flow_only_shows_safe_first_run_options(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test first-run setup keeps advanced options out of the initial flow."""
    await load_ip_ban_manager(hass)
    monkeypatch.setattr(
        ban_config_flow,
        "_async_detect_home_assistant_subnets",
        detected_subnets,
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
    )
    assert result["type"] == "form"
    ban_options_marker = next(
        marker
        for marker in result["data_schema"].schema
        if marker.schema == ban_config_flow.CONF_BAN_OPTIONS
    )
    assert ban_options_marker.default() == [ban_config_flow.CONF_AUTO_BAN_CHECKBOX]


@pytest.mark.asyncio
async def test_user_flow_can_skip_localhost(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test first-run setup honors the visible localhost checkbox option."""
    await load_ip_ban_manager(hass)
    monkeypatch.setattr(
        ban_config_flow,
        "_async_detect_home_assistant_subnets",
        no_detected_subnets,
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
    )
    assert result["type"] == "form"
    ban_options_marker = next(
        marker
        for marker in result["data_schema"].schema
        if marker.schema == ban_config_flow.CONF_BAN_OPTIONS
    )
    assert ban_options_marker.default() == [ban_config_flow.CONF_AUTO_BAN_CHECKBOX]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            ban_config_flow.CONF_QUICK_ALLOWLIST: [],
        },
    )

    assert result["type"] == "create_entry"
    assert result["data"] == expected_setup_data([])


@pytest.mark.asyncio
async def test_user_flow_is_single_instance(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test only one IP Ban Manager entry can be configured."""
    await load_ip_ban_manager(hass)
    monkeypatch.setattr(
        ban_config_flow,
        "_async_detect_home_assistant_subnets",
        no_detected_subnets,
    )

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
async def test_user_flow_aborts_before_form_when_already_configured(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test Add Integration does not show a setup form after configuration."""
    await load_ip_ban_manager(hass)
    monkeypatch.setattr(
        ban_config_flow,
        "_async_detect_home_assistant_subnets",
        no_detected_subnets,
    )

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
    )

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


@pytest.mark.asyncio
async def test_yaml_import_is_ignored_after_entry_exists(
    hass: HomeAssistant,
) -> None:
    """Test stale YAML does not overwrite the UI-managed config entry."""
    await load_ip_ban_manager(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        unique_id=DOMAIN,
        data={CONF_IP_ADDRESSES: ["192.168.1.1"]},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "import"},
        data={CONF_IP_ADDRESSES: ["172.17.0.0/24"]},
    )

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"
    stored_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert stored_entry is not None
    assert stored_entry.data == {CONF_IP_ADDRESSES: ["192.168.1.1"]}


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
        f"10.0.0.1 - {_format_banned_at(original_banned_at.isoformat())}"
    )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOWED_IPS: {CONF_ALLOWED_IPS: "192.168.1.1\n10.0.1.0/24"},
            CONF_BANNED_IPS: {
                CONF_BANNED_IPS: (
                    f"10.0.0.1 - {original_banned_at.isoformat()}\n10.0.0.2"
                ),
                CONF_BLOCKED_NETWORKS: "",
            },
        },
    )

    assert result["type"] == "create_entry"
    assert result["data"] == expected_options_data(["192.168.1.1", "10.0.1.0/24"])
    assert [str(ip) for ip in hass.http.app[KEY_ALLOWLIST]] == [
        "192.168.1.1/32",
        "10.0.1.0/24",
    ]
    stored_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert stored_entry is not None
    assert stored_entry.options[CONF_IP_ADDRESSES] == [
        "192.168.1.1",
        "10.0.1.0/24",
    ]
    assert CONF_BANNED_IPS not in stored_entry.options
    assert set(ban_manager.ip_bans_lookup) == {
        ip_address("10.0.0.1"),
        ip_address("10.0.0.2"),
    }
    assert ban_manager.ip_bans_lookup[ip_address("10.0.0.1")].banned_at == (
        original_banned_at
    )
    ban_file = Path(ban_manager.path).read_text(encoding="utf8")
    assert "10.0.0.1" in ban_file
    assert original_banned_at.isoformat() in ban_file
    assert "10.0.0.2" in ban_file


@pytest.mark.asyncio
async def test_options_flow_accepts_unchanged_submit(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test Configure can be submitted without changing any values."""
    entry = await setup_options_entry(hass, tmp_path)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            ban_config_flow.SECTION_ALLOWED_IPS: {
                CONF_ALLOWED_IPS: "192.168.1.1\n172.17.0.0/24",
            },
            ban_config_flow.SECTION_BANNED_IPS: {
                ban_config_flow.CONF_BAN_OPTIONS: [
                    ban_config_flow.CONF_AUTO_BAN_CHECKBOX,
                    ban_config_flow.CONF_BAN_NOTIFICATIONS_CHECKBOX,
                    ban_config_flow.CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_CHECKBOX,
                    ban_config_flow.CONF_SIDEBAR_PANEL_CHECKBOX,
                ],
                CONF_LOGIN_ATTEMPTS_THRESHOLD: 0,
                CONF_BANNED_IPS: "",
                CONF_BLOCKED_NETWORKS: "",
            },
        },
    )

    assert result["type"] == "create_entry"
    assert result["data"] == expected_options_data(["192.168.1.1", "172.17.0.0/24"])


@pytest.mark.asyncio
async def test_options_flow_can_hide_sidebar_panel(
    hass: HomeAssistant, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test Configure can hide the sidebar while keeping the panel registered."""
    entry = await setup_options_entry(hass, tmp_path)
    registered_sidebar_enabled: bool | None = None

    async def mock_register_panel(
        hass: HomeAssistant, *, sidebar_enabled: bool = True
    ) -> None:
        nonlocal registered_sidebar_enabled
        registered_sidebar_enabled = sidebar_enabled

    monkeypatch.setattr(ipbm, "_async_register_panel", mock_register_panel)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    banned_schema = (
        result["data_schema"].schema[ban_config_flow.SECTION_BANNED_IPS].schema
    )
    options_marker = next(
        marker
        for marker in banned_schema.schema
        if marker.schema == ban_config_flow.CONF_BAN_OPTIONS
    )
    assert ban_config_flow.CONF_SIDEBAR_PANEL_CHECKBOX in options_marker.default()

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            ban_config_flow.SECTION_ALLOWED_IPS: {
                CONF_ALLOWED_IPS: "192.168.1.1\n172.17.0.0/24",
            },
            ban_config_flow.SECTION_BANNED_IPS: {
                ban_config_flow.CONF_BAN_OPTIONS: [
                    ban_config_flow.CONF_AUTO_BAN_CHECKBOX,
                    ban_config_flow.CONF_BAN_NOTIFICATIONS_CHECKBOX,
                    ban_config_flow.CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_CHECKBOX,
                ],
                CONF_LOGIN_ATTEMPTS_THRESHOLD: 0,
                CONF_BANNED_IPS: "",
                CONF_BLOCKED_NETWORKS: "",
            },
        },
    )

    assert result["type"] == "create_entry"
    assert result["data"] == {
        **expected_options_data(["192.168.1.1", "172.17.0.0/24"]),
        CONF_SIDEBAR_PANEL_ENABLED: False,
    }
    assert cast(Any, entry).options[CONF_SIDEBAR_PANEL_ENABLED] is False
    assert registered_sidebar_enabled is False


@pytest.mark.asyncio
async def test_options_flow_safe_default_checkboxes(
    hass: HomeAssistant, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test options show checked safe-default checkboxes for current entries."""
    entry = await setup_options_entry(hass, tmp_path)
    hass.config_entries.async_update_entry(
        cast(Any, entry),
        options={CONF_ALLOWED_IPS: ["127.0.0.1", "192.168.1.0/24"]},
    )
    monkeypatch.setattr(
        ban_config_flow,
        "_async_detect_home_assistant_subnets",
        detected_subnets,
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    allowed_schema = (
        result["data_schema"].schema[ban_config_flow.SECTION_ALLOWED_IPS].schema
    )
    quick_marker = next(
        marker
        for marker in allowed_schema.schema
        if marker.schema == ban_config_flow.CONF_QUICK_ALLOWLIST
    )
    assert quick_marker.default() == [
        ban_config_flow.QUICK_ALLOW_LOCALHOST,
        ban_config_flow.QUICK_ALLOW_LOCAL_NETWORK,
    ]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOWED_IPS: {
                ban_config_flow.CONF_QUICK_ALLOWLIST: [
                    ban_config_flow.QUICK_ALLOW_LOCALHOST,
                    ban_config_flow.QUICK_ALLOW_LOCAL_NETWORK,
                ],
                CONF_ALLOWED_IPS: "10.0.1.0/24",
            },
            CONF_BANNED_IPS: {
                ban_config_flow.CONF_BAN_OPTIONS: [
                    ban_config_flow.CONF_AUTO_BAN_CHECKBOX,
                    ban_config_flow.CONF_BAN_NOTIFICATIONS_CHECKBOX,
                    ban_config_flow.CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_CHECKBOX,
                    ban_config_flow.CONF_SIDEBAR_PANEL_CHECKBOX,
                ],
                CONF_LOGIN_ATTEMPTS_THRESHOLD: 5,
                CONF_BANNED_IPS: "",
                CONF_BLOCKED_NETWORKS: "",
            },
        },
    )

    assert result["type"] == "create_entry"
    assert result["data"] == expected_options_data(
        ["10.0.1.0/24", "127.0.0.1", "192.168.1.0/24"], threshold=5
    )
    stored_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert stored_entry is not None
    assert stored_entry.options[CONF_AUTO_BAN_ENABLED] is True
    assert stored_entry.options[CONF_LOGIN_ATTEMPTS_THRESHOLD] == 5

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOWED_IPS: {
                ban_config_flow.CONF_QUICK_ALLOWLIST: [],
                CONF_ALLOWED_IPS: "10.0.1.0/24",
            },
            CONF_BANNED_IPS: {
                ban_config_flow.CONF_BAN_OPTIONS: [
                    ban_config_flow.CONF_AUTO_BAN_CHECKBOX,
                    ban_config_flow.CONF_BAN_NOTIFICATIONS_CHECKBOX,
                    ban_config_flow.CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_CHECKBOX,
                    ban_config_flow.CONF_SIDEBAR_PANEL_CHECKBOX,
                ],
                CONF_LOGIN_ATTEMPTS_THRESHOLD: 5,
                CONF_BANNED_IPS: "",
                CONF_BLOCKED_NETWORKS: "",
            },
        },
    )

    assert result["type"] == "create_entry"
    assert result["data"] == expected_options_data(["10.0.1.0/24"], threshold=5)


@pytest.mark.asyncio
async def test_options_flow_normalizes_ipv4_wildcard_addresses(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test options normalize IPv4 wildcard shorthand to CIDR."""
    entry = await setup_options_entry(hass, tmp_path)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOWED_IPS: {CONF_ALLOWED_IPS: "192.168.50.*"},
            CONF_BANNED_IPS: {CONF_BANNED_IPS: "", CONF_BLOCKED_NETWORKS: ""},
        },
    )

    assert result["type"] == "create_entry"
    assert result["data"] == expected_options_data(["192.168.50.0/24"])
    assert [str(ip) for ip in hass.http.app[KEY_ALLOWLIST]] == ["192.168.50.0/24"]


@pytest.mark.asyncio
async def test_options_flow_accepts_ipv6_addresses_and_networks(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test Configure accepts IPv6 allowed entries, exact bans, and networks."""
    entry = await setup_options_entry(hass, tmp_path)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOWED_IPS: {CONF_ALLOWED_IPS: "2001:db8::/64\n::1"},
            CONF_BANNED_IPS: {
                CONF_BANNED_IPS: "2001:db8:1::25",
                CONF_BLOCKED_NETWORKS: "2001:db8:2::/64",
            },
        },
    )

    assert result["type"] == "create_entry"
    assert result["data"] == {
        **expected_options_data(["2001:db8::/64", "::1"]),
        CONF_BLOCKED_NETWORKS: ["2001:db8:2::/64"],
    }
    assert [str(ip) for ip in hass.http.app[KEY_ALLOWLIST]] == [
        "2001:db8::/64",
        "::1/128",
    ]
    assert ip_address("2001:db8:1::25") in ban_manager.ip_bans_lookup
    assert ip_address("2001:db8:2::10") in ban_manager.ip_bans_lookup
    assert ip_address("2001:db8::10") not in ban_manager.ip_bans_lookup


@pytest.mark.asyncio
async def test_options_flow_rejects_banning_allowlisted_ip(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test the same IP cannot be both allowed and banned."""
    entry = await setup_options_entry(hass, tmp_path)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOWED_IPS: {CONF_ALLOWED_IPS: "192.168.1.1"},
            CONF_BANNED_IPS: {
                CONF_BANNED_IPS: "192.168.1.1",
                CONF_BLOCKED_NETWORKS: "",
            },
        },
    )

    assert result["type"] == "form"
    assert result["errors"] == {CONF_BANNED_IPS: "banned_ip_allowlisted"}
    assert ban_manager.ip_bans_lookup == {}
    stored_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert stored_entry is not None
    assert stored_entry.options == {}


@pytest.mark.asyncio
async def test_options_flow_can_clear_allowlist(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test deleting every allowed entry disables the allowlist."""
    entry = await setup_options_entry(hass, tmp_path)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOWED_IPS: {CONF_ALLOWED_IPS: ""},
            CONF_BANNED_IPS: {CONF_BANNED_IPS: "", CONF_BLOCKED_NETWORKS: ""},
        },
    )

    assert result["type"] == "create_entry"
    assert result["data"] == expected_options_data([])
    assert hass.http.app[KEY_ALLOWLIST] == ()


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
            CONF_BANNED_IPS: {
                CONF_BANNED_IPS: "10.0.0.2",
                CONF_BLOCKED_NETWORKS: "",
            },
        },
    )

    assert result["type"] == "create_entry"
    assert set(ban_manager.ip_bans_lookup) == {ip_address("10.0.0.2")}
    ban_file = Path(ban_manager.path).read_text(encoding="utf8")
    assert "10.0.0.1" not in ban_file
    assert "10.0.0.2" in ban_file


@pytest.mark.asyncio
async def test_options_flow_writes_bans_oldest_first(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test the ban file is ordered by ban time, oldest first."""
    entry = await setup_options_entry(hass, tmp_path)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    await ban_manager.async_add_ban(IPv4Address("10.0.0.1"))
    await ban_manager.async_add_ban(IPv4Address("10.0.0.2"))
    ban_manager.ip_bans_lookup[ip_address("10.0.0.1")].banned_at = datetime(
        2026, 1, 1, 12, 0, tzinfo=timezone.utc
    )
    ban_manager.ip_bans_lookup[ip_address("10.0.0.2")].banned_at = datetime(
        2026, 1, 3, 12, 0, tzinfo=timezone.utc
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOWED_IPS: {CONF_ALLOWED_IPS: "192.168.1.1\n172.17.0.0/24"},
            CONF_BANNED_IPS: {
                CONF_BANNED_IPS: "10.0.0.2\n10.0.0.3\n10.0.0.1",
                CONF_BLOCKED_NETWORKS: "",
            },
        },
    )

    assert result["type"] == "create_entry"
    assert list(ban_manager.ip_bans_lookup) == [
        ip_address("10.0.0.1"),
        ip_address("10.0.0.2"),
        ip_address("10.0.0.3"),
    ]
    ban_file = Path(ban_manager.path).read_text(encoding="utf8")
    assert ban_file.index("10.0.0.1") < ban_file.index("10.0.0.2")
    assert ban_file.index("10.0.0.2") < ban_file.index("10.0.0.3")


@pytest.mark.asyncio
async def test_options_flow_clears_every_ban(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test deleting the only ban from the textarea clears live bans."""
    entry = await setup_options_entry(hass, tmp_path)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    await ban_manager.async_add_ban(IPv4Address("10.0.0.1"))

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOWED_IPS: {CONF_ALLOWED_IPS: "192.168.1.1\n172.17.0.0/24"},
            CONF_BANNED_IPS: {CONF_BANNED_IPS: "", CONF_BLOCKED_NETWORKS: ""},
        },
    )

    assert result["type"] == "create_entry"
    assert ban_manager.ip_bans_lookup == {}
    assert not Path(ban_manager.path).exists()


@pytest.mark.asyncio
async def test_options_flow_handles_missing_ban_file(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test missing ip_bans.yaml is displayed and submitted as an empty list."""
    entry = await setup_options_entry(hass, tmp_path)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    assert not Path(ban_manager.path).exists()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    assert result["description_placeholders"][ATTR_BANNED_IPS] == "None"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOWED_IPS: {CONF_ALLOWED_IPS: "192.168.1.1\n172.17.0.0/24"},
            CONF_BANNED_IPS: {CONF_BANNED_IPS: "", CONF_BLOCKED_NETWORKS: ""},
        },
    )

    assert result["type"] == "create_entry"
    assert ban_manager.ip_bans_lookup == {}
    assert not Path(ban_manager.path).exists()


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
            CONF_BANNED_IPS: {
                CONF_BANNED_IPS: "10.0.0.1\nnot-an-ip",
                CONF_BLOCKED_NETWORKS: "",
            },
        },
    )

    assert result["type"] == "form"
    assert result["errors"] == {ATTR_BANNED_IPS: "invalid_banned_ip"}


@pytest.mark.asyncio
async def test_options_flow_rejects_wildcard_banned_ip(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test wildcard shorthand is not accepted for exact banned IPs."""
    entry = await setup_options_entry(hass, tmp_path)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOWED_IPS: {CONF_ALLOWED_IPS: "192.168.1.1\n172.17.0.0/24"},
            CONF_BANNED_IPS: {
                CONF_BANNED_IPS: "192.168.1.*",
                CONF_BLOCKED_NETWORKS: "",
            },
        },
    )

    assert result["type"] == "form"
    assert result["errors"] == {ATTR_BANNED_IPS: "invalid_banned_ip"}


@pytest.mark.asyncio
async def test_options_flow_accepts_empty_blocked_networks(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test the options form can submit without managed blocked networks."""
    entry = await setup_options_entry(hass, tmp_path)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOWED_IPS: {CONF_ALLOWED_IPS: "192.168.1.1\n172.17.0.0/24"},
            CONF_BANNED_IPS: {CONF_BANNED_IPS: ""},
        },
    )

    assert result["type"] == "create_entry"
    assert result["data"] == {
        **expected_options_data(["192.168.1.1", "172.17.0.0/24"]),
        CONF_BLOCKED_NETWORKS: [],
    }
    stored_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert stored_entry is not None
    assert stored_entry.options[CONF_BLOCKED_NETWORKS] == []


@pytest.mark.asyncio
async def test_options_flow_banned_ips_field_is_optional(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test the UI can submit the banned IP field as blank."""
    entry = await setup_options_entry(hass, tmp_path)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    banned_schema = (
        result["data_schema"].schema[ban_config_flow.SECTION_BANNED_IPS].schema
    )
    banned_marker = next(
        marker for marker in banned_schema.schema if marker.schema == CONF_BANNED_IPS
    )

    assert isinstance(banned_marker, VolOptional)


@pytest.mark.asyncio
async def test_options_flow_confirms_clearing_all_banned_ips(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test clearing multiple exact IP bans requires explicit confirmation."""
    entry = await setup_options_entry(hass, tmp_path)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    await ban_manager.async_add_ban(IPv4Address("10.0.0.2"))
    await ban_manager.async_add_ban(IPv4Address("10.0.0.3"))

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOWED_IPS: {CONF_ALLOWED_IPS: "192.168.1.1"},
            CONF_BANNED_IPS: {CONF_BANNED_IPS: ""},
        },
    )

    assert result["type"] == "form"
    assert result["step_id"] == "confirm_clear_bans"
    assert result["description_placeholders"] == {"ban_count": "2"}
    assert set(ban_manager.ip_bans_lookup) == {
        ip_address("10.0.0.2"),
        ip_address("10.0.0.3"),
    }

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={ban_config_flow.CONF_CONFIRM_CLEAR_BANS: False},
    )

    assert result["type"] == "form"
    assert result["step_id"] == "confirm_clear_bans"
    assert result["errors"] == {"base": "confirmation_required"}
    assert set(ban_manager.ip_bans_lookup) == {
        ip_address("10.0.0.2"),
        ip_address("10.0.0.3"),
    }

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={ban_config_flow.CONF_CONFIRM_CLEAR_BANS: True},
    )

    assert result["type"] == "create_entry"
    assert result["data"] == expected_options_data(["192.168.1.1"])
    assert ban_manager.ip_bans_lookup == {}


@pytest.mark.asyncio
async def test_options_flow_accepts_wildcard_blocked_network(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test wildcard shorthand is accepted as a managed blocked network."""
    entry = await setup_options_entry(hass, tmp_path)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOWED_IPS: {CONF_ALLOWED_IPS: "192.168.1.1\n172.17.0.0/24"},
            CONF_BANNED_IPS: {
                CONF_BANNED_IPS: "",
                CONF_BLOCKED_NETWORKS: "192.168.1.*",
            },
        },
    )

    assert result["type"] == "create_entry"
    assert result["data"] == {
        **expected_options_data(["192.168.1.1", "172.17.0.0/24"]),
        CONF_BLOCKED_NETWORKS: ["192.168.1.0/24"],
    }
    stored_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert stored_entry is not None
    assert stored_entry.options[CONF_BLOCKED_NETWORKS] == ["192.168.1.0/24"]
    assert ip_address("192.168.1.50") in ban_manager.ip_bans_lookup
    assert ip_address("172.17.0.10") not in ban_manager.ip_bans_lookup


@pytest.mark.asyncio
async def test_options_flow_can_enable_default_deny(
    hass: HomeAssistant, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test Configure can block all non-allowlisted addresses."""
    entry = await setup_options_entry(hass, tmp_path)
    monkeypatch.setattr(
        ban_config_flow,
        "_async_detect_home_assistant_subnets",
        detected_subnets,
    )
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOWED_IPS: {
                ban_config_flow.CONF_QUICK_ALLOWLIST: [
                    ban_config_flow.QUICK_ALLOW_LOCAL_NETWORK
                ],
                CONF_ALLOWED_IPS: "",
            },
            CONF_BANNED_IPS: {
                ban_config_flow.CONF_BAN_OPTIONS: [
                    ban_config_flow.CONF_AUTO_BAN_CHECKBOX,
                    ban_config_flow.CONF_BAN_NOTIFICATIONS_CHECKBOX,
                    ban_config_flow.CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_CHECKBOX,
                    ban_config_flow.CONF_SIDEBAR_PANEL_CHECKBOX,
                ],
                ban_config_flow.CONF_ADVANCED_BAN_OPTIONS: [
                    ban_config_flow.CONF_DEFAULT_DENY_CHECKBOX,
                ],
                CONF_BANNED_IPS: "",
                CONF_BLOCKED_NETWORKS: "",
            },
        },
    )

    assert result["type"] == "create_entry"
    assert result["data"] == {
        **expected_options_data(["192.168.1.0/24"]),
        CONF_DEFAULT_DENY_ENABLED: True,
    }
    stored_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert stored_entry is not None
    assert stored_entry.options[CONF_DEFAULT_DENY_ENABLED] is True
    assert ip_address("8.8.8.8") in ban_manager.ip_bans_lookup
    assert ip_address("192.168.1.42") not in ban_manager.ip_bans_lookup


@pytest.mark.asyncio
async def test_options_flow_rejects_unprotected_local_blocked_network(
    hass: HomeAssistant, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test local network blocks require a matching local allowlist entry."""
    entry = await setup_options_entry(hass, tmp_path)
    monkeypatch.setattr(
        ban_config_flow,
        "_async_detect_home_assistant_subnets",
        detected_subnets,
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOWED_IPS: {CONF_ALLOWED_IPS: "172.17.0.0/24"},
            CONF_BANNED_IPS: {
                CONF_BANNED_IPS: "",
                CONF_BLOCKED_NETWORKS: "192.168.1.0/24",
            },
        },
    )

    assert result["type"] == "form"
    assert result["errors"] == {
        CONF_BLOCKED_NETWORKS: "local_network_block_unprotected"
    }


@pytest.mark.asyncio
async def test_options_flow_rejects_unprotected_default_deny(
    hass: HomeAssistant, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test default-deny mode requires the detected local subnet to stay allowed."""
    entry = await setup_options_entry(hass, tmp_path)
    monkeypatch.setattr(
        ban_config_flow,
        "_async_detect_home_assistant_subnets",
        detected_subnets,
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOWED_IPS: {CONF_ALLOWED_IPS: "172.17.0.0/24"},
            CONF_BANNED_IPS: {
                ban_config_flow.CONF_BAN_OPTIONS: [
                    ban_config_flow.CONF_AUTO_BAN_CHECKBOX,
                    ban_config_flow.CONF_BAN_NOTIFICATIONS_CHECKBOX,
                    ban_config_flow.CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_CHECKBOX,
                ],
                ban_config_flow.CONF_ADVANCED_BAN_OPTIONS: [
                    ban_config_flow.CONF_DEFAULT_DENY_CHECKBOX,
                ],
                CONF_BANNED_IPS: "",
                CONF_BLOCKED_NETWORKS: "",
            },
        },
    )

    assert result["type"] == "form"
    assert result["errors"] == {
        CONF_BLOCKED_NETWORKS: "local_network_block_unprotected"
    }


@pytest.mark.asyncio
async def test_options_flow_rejects_default_deny_without_detected_subnet(
    hass: HomeAssistant, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test default-deny mode is not enabled when no local subnet can be proven."""
    entry = await setup_options_entry(hass, tmp_path)
    monkeypatch.setattr(
        ban_config_flow,
        "_async_detect_home_assistant_subnets",
        no_detected_subnets,
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOWED_IPS: {CONF_ALLOWED_IPS: "192.168.1.0/24"},
            CONF_BANNED_IPS: {
                ban_config_flow.CONF_ADVANCED_BAN_OPTIONS: [
                    ban_config_flow.CONF_DEFAULT_DENY_CHECKBOX,
                ],
                CONF_BANNED_IPS: "",
                CONF_BLOCKED_NETWORKS: "",
            },
        },
    )

    assert result["type"] == "form"
    assert result["errors"] == {
        CONF_BLOCKED_NETWORKS: "local_network_block_unprotected"
    }


@pytest.mark.asyncio
async def test_options_flow_rejects_local_block_with_only_one_local_host_allowed(
    hass: HomeAssistant, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test local network blocks require the detected local subnet to stay allowed."""
    entry = await setup_options_entry(hass, tmp_path)
    monkeypatch.setattr(
        ban_config_flow,
        "_async_detect_home_assistant_subnets",
        detected_subnets,
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOWED_IPS: {CONF_ALLOWED_IPS: "192.168.1.40"},
            CONF_BANNED_IPS: {
                CONF_BANNED_IPS: "",
                CONF_BLOCKED_NETWORKS: "192.168.1.0/24",
            },
        },
    )

    assert result["type"] == "form"
    assert result["errors"] == {
        CONF_BLOCKED_NETWORKS: "local_network_block_unprotected"
    }


@pytest.mark.asyncio
async def test_options_flow_can_allow_automatic_bans_inside_allowed_ips(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test Configure can opt into exact bans inside allowed networks."""
    entry = await setup_options_entry(hass, tmp_path)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOWED_IPS: {CONF_ALLOWED_IPS: "192.168.1.1\n172.17.0.0/24"},
            CONF_BANNED_IPS: {
                ban_config_flow.CONF_BAN_OPTIONS: [
                    ban_config_flow.CONF_AUTO_BAN_CHECKBOX,
                    ban_config_flow.CONF_BAN_NOTIFICATIONS_CHECKBOX,
                    ban_config_flow.CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_CHECKBOX,
                    ban_config_flow.CONF_SIDEBAR_PANEL_CHECKBOX,
                ],
                ban_config_flow.CONF_ADVANCED_BAN_OPTIONS: [
                    ban_config_flow.CONF_ALLOWLISTED_LOGINS_CAN_BAN_CHECKBOX,
                ],
                CONF_BANNED_IPS: "",
                CONF_BLOCKED_NETWORKS: "",
            },
        },
    )

    assert result["type"] == "create_entry"
    assert result["data"] == {
        **expected_options_data(["192.168.1.1", "172.17.0.0/24"]),
        CONF_ALLOWLISTED_LOGINS_CAN_BAN: True,
    }
    stored_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert stored_entry is not None
    assert stored_entry.options[CONF_ALLOWLISTED_LOGINS_CAN_BAN] is True
