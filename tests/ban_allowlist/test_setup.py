"""Test Ban Allowlist setup."""

import logging
from ipaddress import IPv4Address, ip_address
from pathlib import Path
from typing import Any, cast

import pytest
from homeassistant.components import persistent_notification
from homeassistant.components.http import ban as http_ban
from homeassistant.components.http.ban import (
    KEY_BAN_MANAGER,
    KEY_FAILED_LOGIN_ATTEMPTS,
    NOTIFICATION_ID_BAN,
    NOTIFICATION_ID_LOGIN,
    IpBanManager,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.loader import DATA_CUSTOM_COMPONENTS, async_get_custom_components
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ban_allowlist import (
    IP_BAN_DISABLED_ISSUE_ID,
    KEY_ALLOWLIST,
    current_status,
)
from custom_components.ban_allowlist.const import (
    ATTR_BANNED_IPS,
    ATTR_CONFIRM,
    ATTR_FAILED_LOGIN_ATTEMPTS,
    ATTR_IP_ADDRESS,
    ATTR_NETWORK,
    ATTR_NETWORKS,
    CONF_ALLOWED_IPS,
    CONF_BANNED_IPS,
    CONF_IP_ADDRESSES,
    DOMAIN,
    SERVICE_ADD_ALLOWLIST_NETWORK,
    SERVICE_ADD_IP_BAN,
    SERVICE_REMOVE_ALL_IP_BANS,
    SERVICE_REMOVE_ALLOWLIST_NETWORK,
    SERVICE_REMOVE_IP_BAN,
)


def check_records(records: list[logging.LogRecord]) -> None:
    """Check log records don't have any warnings/errors."""
    for record in records:
        if record.levelno >= logging.WARNING:
            msg = record.getMessage()
            if msg.startswith(
                "We found a custom integration ban_allowlist which has not been tested by Home Assistant"
            ):
                continue
            raise Exception(msg)


async def setup_ban_allowlist(hass: HomeAssistant) -> None:
    """Configure ban_allowlist and dependencies."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert list((await async_get_custom_components(hass)).keys()) == ["ban_allowlist"]
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        data={CONF_IP_ADDRESSES: ["192.168.1.1", "172.17.0.0/24"]},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_yaml_import(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test YAML configuration is imported into a config entry."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert list((await async_get_custom_components(hass)).keys()) == ["ban_allowlist"]
    await async_setup_component(hass, "http", {})
    assert await async_setup_component(
        hass,
        DOMAIN,
        {DOMAIN: {CONF_IP_ADDRESSES: ["192.168.1.1", "172.17.0.0/24"]}},
    )
    await hass.async_block_till_done()
    check_records(caplog.records)

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].data == {CONF_IP_ADDRESSES: ["192.168.1.1", "172.17.0.0/24"]}
    assert [str(ip) for ip in hass.http.app[KEY_ALLOWLIST]] == [
        "192.168.1.1/32",
        "172.17.0.0/24",
    ]


@pytest.mark.asyncio
async def test_yaml_import_normalizes_ipv4_wildcard(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test YAML import accepts IPv4 wildcard shorthand."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert list((await async_get_custom_components(hass)).keys()) == ["ban_allowlist"]
    await async_setup_component(hass, "http", {})
    assert await async_setup_component(
        hass,
        DOMAIN,
        {DOMAIN: {CONF_IP_ADDRESSES: ["192.168.1.*"]}},
    )
    await hass.async_block_till_done()
    check_records(caplog.records)

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].data == {CONF_IP_ADDRESSES: ["192.168.1.0/24"]}
    assert [str(ip) for ip in hass.http.app[KEY_ALLOWLIST]] == ["192.168.1.0/24"]


@pytest.mark.asyncio
async def test_setup(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    """Test setup of ban allowlist."""
    await setup_ban_allowlist(hass)
    check_records(caplog.records)
    assert hass.services.has_service(DOMAIN, SERVICE_ADD_IP_BAN)


@pytest.mark.asyncio
async def test_setup_creates_repair_when_ip_banning_disabled(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test setup creates a visible repair when native IP banning is disabled."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert list((await async_get_custom_components(hass)).keys()) == ["ban_allowlist"]
    await async_setup_component(hass, "http", {"http": {"ip_ban_enabled": False}})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        data={CONF_IP_ADDRESSES: ["192.168.1.1"]},
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    issue = ir.async_get(hass).async_get_issue(DOMAIN, IP_BAN_DISABLED_ISSUE_ID)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING
    assert not hass.services.has_service(DOMAIN, SERVICE_ADD_IP_BAN)

    warning_messages = [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ]
    assert any("requires http.ip_ban_enabled" in msg for msg in warning_messages)


@pytest.mark.asyncio
async def test_setup_clears_repair_when_ip_banning_enabled(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test setup clears the repair once native IP banning is available."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        IP_BAN_DISABLED_ISSUE_ID,
        is_fixable=False,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=IP_BAN_DISABLED_ISSUE_ID,
    )

    await setup_ban_allowlist(hass)
    check_records(caplog.records)

    assert ir.async_get(hass).async_get_issue(DOMAIN, IP_BAN_DISABLED_ISSUE_ID) is None


@pytest.mark.asyncio
async def test_setup_renames_legacy_entry_title(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test old config entry titles are updated after the integration rename."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert list((await async_get_custom_components(hass)).keys()) == ["ban_allowlist"]
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Allowlist",
        data={CONF_IP_ADDRESSES: ["192.168.1.1"]},
        options={
            CONF_IP_ADDRESSES: ["192.168.1.1"],
            CONF_BANNED_IPS: ["10.0.0.1"],
        },
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    check_records(caplog.records)

    stored_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert stored_entry is not None
    assert stored_entry.title == "IP Ban Manager"
    assert stored_entry.options == {CONF_IP_ADDRESSES: ["192.168.1.1"]}


@pytest.mark.asyncio
async def test_setup_reads_legacy_allowed_ips_option(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test old allowed_ips option data is still honored."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert list((await async_get_custom_components(hass)).keys()) == ["ban_allowlist"]
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        data={CONF_IP_ADDRESSES: ["192.168.1.1"]},
        options={CONF_ALLOWED_IPS: ["10.0.0.0/24"]},
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    check_records(caplog.records)

    assert [str(ip) for ip in hass.http.app[KEY_ALLOWLIST]] == ["10.0.0.0/24"]


@pytest.mark.asyncio
async def test_diagnostic_sensors_expose_counts(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test diagnostic sensors expose meaningful counts and details."""
    await setup_ban_allowlist(hass)
    check_records(caplog.records)

    active_bans = hass.states.get("sensor.ip_ban_manager_active_bans")
    assert active_bans is not None
    assert active_bans.state == "0"
    assert active_bans.attributes[ATTR_BANNED_IPS] == []

    allowlisted_networks = hass.states.get("sensor.ip_ban_manager_allowlisted_networks")
    assert allowlisted_networks is not None
    assert allowlisted_networks.state == "2"
    assert allowlisted_networks.attributes[ATTR_NETWORKS] == [
        "192.168.1.1/32",
        "172.17.0.0/24",
    ]

    failed_login_sources = hass.states.get("sensor.ip_ban_manager_failed_login_sources")
    assert failed_login_sources is not None
    assert failed_login_sources.state == "0"
    assert failed_login_sources.attributes[ATTR_FAILED_LOGIN_ATTEMPTS] == {}


@pytest.mark.asyncio
async def test_hit_allowlist(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test hitting the allowlist."""
    await setup_ban_allowlist(hass)
    await cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER]).async_add_ban(
        IPv4Address("192.168.1.1")
    )
    await cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER]).async_add_ban(
        IPv4Address("10.0.0.1")
    )
    await cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER]).async_add_ban(
        IPv4Address("172.17.0.10")
    )
    await cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER]).async_add_ban(
        IPv4Address("172.17.1.10")
    )
    check_records(caplog.records)

    messages = []

    for record in caplog.records:
        if record.levelno < logging.INFO or not record.name.startswith(
            "custom_components.ban_allowlist"
        ):
            continue

        messages.append(record.getMessage())

    assert messages == [
        "Setting allowlist with ['192.168.1.1/32', '172.17.0.0/24']",
        "Not adding 192.168.1.1 to ban list, as it's in the allowlist",
        "Banning IP 10.0.0.1",
        "Not adding 172.17.0.10 to ban list, as it's in the allowlist",
        "Banning IP 172.17.1.10",
    ]


@pytest.mark.asyncio
async def test_ignored_wrong_login_does_not_increment_attempts(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test allowlisted login failures don't count toward a ban."""
    await setup_ban_allowlist(hass)

    remote_addr = ip_address("192.168.1.1")
    hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS][remote_addr] = 1

    class MockRequest:
        remote = "192.168.1.1"
        app = hass.http.app

    await http_ban.process_wrong_login(cast(Any, MockRequest()))
    check_records(caplog.records)

    assert remote_addr not in hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS]

    messages = []

    for record in caplog.records:
        if record.levelno < logging.INFO or not record.name.startswith(
            "custom_components.ban_allowlist"
        ):
            continue

        messages.append(record.getMessage())

    assert messages == [
        "Setting allowlist with ['192.168.1.1/32', '172.17.0.0/24']",
        "Ignoring invalid authentication from 192.168.1.1 because it is in the allowlist",
    ]


@pytest.mark.asyncio
async def test_ban_hook_uses_current_allowlist(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test the ban hook reads the current app allowlist."""
    await setup_ban_allowlist(hass)
    hass.http.app[KEY_ALLOWLIST] = ()

    await cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER]).async_add_ban(
        IPv4Address("192.168.1.1")
    )
    check_records(caplog.records)

    messages = []

    for record in caplog.records:
        if record.levelno < logging.INFO or not record.name.startswith(
            "custom_components.ban_allowlist"
        ):
            continue

        messages.append(record.getMessage())

    assert messages == [
        "Setting allowlist with ['192.168.1.1/32', '172.17.0.0/24']",
        "Banning IP 192.168.1.1",
    ]


@pytest.mark.asyncio
async def test_live_ban_services_update_memory_and_file(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test IP bans can be added and removed without restarting Home Assistant."""
    await setup_ban_allowlist(hass)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    ban_manager.path = str(tmp_path / "ip_bans.yaml")

    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_IP_BAN,
        {ATTR_IP_ADDRESS: "10.0.0.1"},
        blocking=True,
    )
    check_records(caplog.records)

    assert ip_address("10.0.0.1") in ban_manager.ip_bans_lookup
    assert "10.0.0.1" in Path(ban_manager.path).read_text(encoding="utf8")

    await hass.services.async_call(
        DOMAIN,
        SERVICE_REMOVE_IP_BAN,
        {ATTR_IP_ADDRESS: "10.0.0.1"},
        blocking=True,
    )
    check_records(caplog.records)

    assert ip_address("10.0.0.1") not in ban_manager.ip_bans_lookup
    assert Path(ban_manager.path).read_text(encoding="utf8") == "{}\n"


@pytest.mark.asyncio
async def test_remove_all_ip_bans_service(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test all IP bans can be removed without restarting Home Assistant."""
    await setup_ban_allowlist(hass)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    ban_manager.path = str(tmp_path / "ip_bans.yaml")
    await ban_manager.async_add_ban(IPv4Address("10.0.0.1"))
    await ban_manager.async_add_ban(IPv4Address("10.0.0.2"))
    hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS][ip_address("10.0.0.1")] = 2

    await hass.services.async_call(
        DOMAIN,
        SERVICE_REMOVE_ALL_IP_BANS,
        {ATTR_CONFIRM: True},
        blocking=True,
    )
    check_records(caplog.records)

    assert ban_manager.ip_bans_lookup == {}
    assert hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS] == {}
    assert Path(ban_manager.path).read_text(encoding="utf8") == "{}\n"


@pytest.mark.asyncio
async def test_remove_all_ip_bans_service_requires_confirmation(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test all-ban removal cannot happen by accident from a service call."""
    await setup_ban_allowlist(hass)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    ban_manager.path = str(tmp_path / "ip_bans.yaml")
    await ban_manager.async_add_ban(IPv4Address("10.0.0.1"))
    before_file = Path(ban_manager.path).read_text(encoding="utf8")

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_REMOVE_ALL_IP_BANS,
            {},
            blocking=True,
        )
    check_records(caplog.records)

    assert set(ban_manager.ip_bans_lookup) == {ip_address("10.0.0.1")}
    assert Path(ban_manager.path).read_text(encoding="utf8") == before_file


@pytest.mark.asyncio
async def test_remove_ip_ban_dismisses_matching_notifications(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test removing one ban dismisses stale notifications for that IP."""
    await setup_ban_allowlist(hass)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    ban_manager.path = str(tmp_path / "ip_bans.yaml")
    await ban_manager.async_add_ban(IPv4Address("10.0.0.1"))
    await ban_manager.async_add_ban(IPv4Address("10.0.0.2"))
    hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS][ip_address("10.0.0.1")] = 2
    persistent_notification.async_create(
        hass,
        "Too many login attempts from 10.0.0.1",
        "Banning IP address",
        NOTIFICATION_ID_BAN,
    )
    persistent_notification.async_create(
        hass,
        "Login attempt or request with invalid authentication from host (10.0.0.1).",
        "Login attempt failed",
        NOTIFICATION_ID_LOGIN,
    )

    await hass.services.async_call(
        DOMAIN,
        SERVICE_REMOVE_IP_BAN,
        {ATTR_IP_ADDRESS: "10.0.0.1"},
        blocking=True,
    )
    check_records(caplog.records)

    assert set(ban_manager.ip_bans_lookup) == {ip_address("10.0.0.2")}
    notifications = persistent_notification._async_get_or_create_notifications(
        hass
    )  # noqa: SLF001
    assert NOTIFICATION_ID_BAN not in notifications
    assert NOTIFICATION_ID_LOGIN not in notifications


@pytest.mark.asyncio
async def test_remove_ip_ban_keeps_unrelated_notification(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test removing one ban does not dismiss a notification for a different IP."""
    await setup_ban_allowlist(hass)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    ban_manager.path = str(tmp_path / "ip_bans.yaml")
    await ban_manager.async_add_ban(IPv4Address("10.0.0.1"))
    await ban_manager.async_add_ban(IPv4Address("10.0.0.2"))
    persistent_notification.async_create(
        hass,
        "Too many login attempts from 10.0.0.2",
        "Banning IP address",
        NOTIFICATION_ID_BAN,
    )

    await hass.services.async_call(
        DOMAIN,
        SERVICE_REMOVE_IP_BAN,
        {ATTR_IP_ADDRESS: "10.0.0.1"},
        blocking=True,
    )
    check_records(caplog.records)

    notifications = persistent_notification._async_get_or_create_notifications(
        hass
    )  # noqa: SLF001
    assert NOTIFICATION_ID_BAN in notifications


@pytest.mark.asyncio
async def test_remove_ip_ban_rejects_unknown_ip_without_mutating_state(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test typo removals do not rewrite ban state or clear failed attempts."""
    await setup_ban_allowlist(hass)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    ban_manager.path = str(tmp_path / "ip_bans.yaml")
    await ban_manager.async_add_ban(IPv4Address("10.0.0.1"))
    hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS][ip_address("10.0.0.2")] = 2
    before_file = Path(ban_manager.path).read_text(encoding="utf8")

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_REMOVE_IP_BAN,
            {ATTR_IP_ADDRESS: "10.0.0.2"},
            blocking=True,
        )
    check_records(caplog.records)

    assert set(ban_manager.ip_bans_lookup) == {ip_address("10.0.0.1")}
    assert hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS][ip_address("10.0.0.2")] == 2
    assert Path(ban_manager.path).read_text(encoding="utf8") == before_file


@pytest.mark.asyncio
async def test_allowlist_services_update_live_options(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test allowlist entries can be added and removed without restarting."""
    await setup_ban_allowlist(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_ALLOWLIST_NETWORK,
        {ATTR_NETWORK: "10.0.0.0/24"},
        blocking=True,
    )
    check_records(caplog.records)

    assert hass.config_entries.async_entries(DOMAIN)[0].options[CONF_IP_ADDRESSES] == [
        "192.168.1.1",
        "172.17.0.0/24",
        "10.0.0.0/24",
    ]

    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_ALLOWLIST_NETWORK,
        {ATTR_NETWORK: "192.168.1.1/32"},
        blocking=True,
    )
    check_records(caplog.records)

    assert [str(ip) for ip in hass.http.app[KEY_ALLOWLIST]] == [
        "192.168.1.1/32",
        "172.17.0.0/24",
        "10.0.0.0/24",
    ]
    assert hass.config_entries.async_entries(DOMAIN)[0].options[CONF_IP_ADDRESSES] == [
        "192.168.1.1",
        "172.17.0.0/24",
        "10.0.0.0/24",
    ]

    await hass.services.async_call(
        DOMAIN,
        SERVICE_REMOVE_ALLOWLIST_NETWORK,
        {ATTR_NETWORK: "10.0.0.0/24"},
        blocking=True,
    )
    check_records(caplog.records)

    assert [str(ip) for ip in hass.http.app[KEY_ALLOWLIST]] == [
        "192.168.1.1/32",
        "172.17.0.0/24",
    ]

    await hass.services.async_call(
        DOMAIN,
        SERVICE_REMOVE_ALLOWLIST_NETWORK,
        {ATTR_NETWORK: "192.168.1.1/32"},
        blocking=True,
    )
    check_records(caplog.records)

    assert hass.config_entries.async_entries(DOMAIN)[0].options[CONF_IP_ADDRESSES] == [
        "172.17.0.0/24",
    ]


@pytest.mark.asyncio
async def test_allowlist_services_normalize_ipv4_wildcard(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test allowlist services accept IPv4 wildcard shorthand."""
    await setup_ban_allowlist(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_ALLOWLIST_NETWORK,
        {ATTR_NETWORK: "10.20.30.*"},
        blocking=True,
    )
    check_records(caplog.records)

    assert hass.config_entries.async_entries(DOMAIN)[0].options[CONF_IP_ADDRESSES] == [
        "192.168.1.1",
        "172.17.0.0/24",
        "10.20.30.0/24",
    ]
    assert [str(ip) for ip in hass.http.app[KEY_ALLOWLIST]] == [
        "192.168.1.1/32",
        "172.17.0.0/24",
        "10.20.30.0/24",
    ]

    await hass.services.async_call(
        DOMAIN,
        SERVICE_REMOVE_ALLOWLIST_NETWORK,
        {ATTR_NETWORK: "10.20.30.*"},
        blocking=True,
    )
    check_records(caplog.records)

    assert [str(ip) for ip in hass.http.app[KEY_ALLOWLIST]] == [
        "192.168.1.1/32",
        "172.17.0.0/24",
    ]


@pytest.mark.asyncio
async def test_allowlist_service_rejects_allowlisting_everything(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test service calls cannot add an allowlist entry that disables bans."""
    await setup_ban_allowlist(hass)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_ALLOWLIST_NETWORK,
            {ATTR_NETWORK: "0.0.0.0/0"},
            blocking=True,
        )
    check_records(caplog.records)

    assert [str(ip) for ip in hass.http.app[KEY_ALLOWLIST]] == [
        "192.168.1.1/32",
        "172.17.0.0/24",
    ]


@pytest.mark.asyncio
async def test_allowlist_service_rejects_network_containing_active_ban(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test service calls cannot allowlist a network with active bans inside it."""
    await setup_ban_allowlist(hass)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    ban_manager.path = str(tmp_path / "ip_bans.yaml")
    await ban_manager.async_add_ban(IPv4Address("10.0.0.25"))

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ADD_ALLOWLIST_NETWORK,
            {ATTR_NETWORK: "10.0.0.0/24"},
            blocking=True,
        )
    check_records(caplog.records)

    assert [str(ip) for ip in hass.http.app[KEY_ALLOWLIST]] == [
        "192.168.1.1/32",
        "172.17.0.0/24",
    ]
    assert set(ban_manager.ip_bans_lookup) == {ip_address("10.0.0.25")}


@pytest.mark.asyncio
async def test_allowlist_service_rejects_removing_final_entry(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test service calls cannot accidentally remove the final allowlist entry."""
    await setup_ban_allowlist(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_REMOVE_ALLOWLIST_NETWORK,
        {ATTR_NETWORK: "172.17.0.0/24"},
        blocking=True,
    )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_REMOVE_ALLOWLIST_NETWORK,
            {ATTR_NETWORK: "192.168.1.1"},
            blocking=True,
        )
    check_records(caplog.records)

    assert hass.config_entries.async_entries(DOMAIN)[0].options[CONF_IP_ADDRESSES] == [
        "192.168.1.1",
    ]
    assert [str(ip) for ip in hass.http.app[KEY_ALLOWLIST]] == ["192.168.1.1/32"]


@pytest.mark.asyncio
async def test_current_status_lists_live_state(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test the status helper formats the live lists for UI display."""
    await setup_ban_allowlist(hass)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    ban_manager.path = str(tmp_path / "ip_bans.yaml")
    await ban_manager.async_add_ban(IPv4Address("10.0.0.1"))
    hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS][ip_address("10.0.0.2")] = 1
    check_records(caplog.records)

    status = current_status(hass)

    assert status[ATTR_NETWORKS] == ["192.168.1.1/32", "172.17.0.0/24"]
    assert status[ATTR_BANNED_IPS] == [
        {
            "ip_address": "10.0.0.1",
            "banned_at": ban_manager.ip_bans_lookup[
                ip_address("10.0.0.1")
            ].banned_at.isoformat(),
        }
    ]
    assert status[ATTR_FAILED_LOGIN_ATTEMPTS] == {"10.0.0.2": 1}
