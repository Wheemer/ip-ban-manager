"""Focused tests split out from test_setup."""

# mypy: ignore-errors

# flake8: noqa
# ruff: noqa: F403,F405

from .test_setup import *


@pytest.mark.asyncio
async def test_setup_creates_repair_when_ip_banning_disabled(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test setup creates a visible repair when native IP banning is disabled."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
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

    await setup_ip_ban_manager(hass)
    check_records(caplog.records)

    assert ir.async_get(hass).async_get_issue(DOMAIN, IP_BAN_DISABLED_ISSUE_ID) is None


@pytest.mark.asyncio
async def test_setup_removes_deprecated_banned_ips_option(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test deprecated options are removed during setup."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
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
async def test_setup_renames_legacy_entry_title(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test old config entry titles are updated after the integration rename."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ban_allowlist",
        data={CONF_IP_ADDRESSES: ["192.168.1.1"]},
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    check_records(caplog.records)

    stored_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert stored_entry is not None
    assert stored_entry.title == "IP Ban Manager"


@pytest.mark.asyncio
async def test_setup_reads_legacy_allowed_ips_option(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test old allowed_ips option data is still honored."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
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
    await setup_ip_ban_manager(hass)
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

    blocked_networks = hass.states.get("sensor.ip_ban_manager_blocked_networks")
    assert blocked_networks is not None
    assert blocked_networks.state == "0"
    assert blocked_networks.attributes[ATTR_BLOCKED_NETWORKS] == []

    failed_login_sources = hass.states.get("sensor.ip_ban_manager_failed_login_sources")
    assert failed_login_sources is not None
    assert failed_login_sources.state == "0"
    assert failed_login_sources.attributes[ATTR_FAILED_LOGIN_ATTEMPTS] == {}

    for state in (
        active_bans,
        allowlisted_networks,
        blocked_networks,
        failed_login_sources,
    ):
        assert state.attributes["state_class"] == "measurement"
        assert ATTR_UNIT_OF_MEASUREMENT not in state.attributes
