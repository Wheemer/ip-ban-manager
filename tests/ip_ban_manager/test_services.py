"""Focused tests split out from test_setup."""

# mypy: ignore-errors

# flake8: noqa
# ruff: noqa: F403,F405

from .test_setup import *


@pytest.mark.asyncio
async def test_live_ban_services_update_memory_and_file(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test IP bans can be added and removed without restarting Home Assistant."""
    await setup_ip_ban_manager(hass)
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
    assert not Path(ban_manager.path).exists()

    snapshots = sorted(Path(hass.config.path(DOMAIN, "snapshots")).glob("*.bak"))
    assert snapshots
    assert any(
        "10.0.0.1" in snapshot.read_text(encoding="utf8") for snapshot in snapshots
    )

    metrics = cast(dict[str, Any], current_status(hass)[ATTR_METRICS])
    assert metrics["config_writes"] >= 1
    assert metrics["snapshots_created"] >= 1
    assert metrics[ATTR_LAST_CONFIG_WRITE] is not None


@pytest.mark.asyncio
async def test_remove_all_ip_bans_service(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test all IP bans can be removed without restarting Home Assistant."""
    await setup_ip_ban_manager(hass)
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
    assert not Path(ban_manager.path).exists()


@pytest.mark.asyncio
async def test_remove_all_ip_bans_service_requires_confirmation(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test all-ban removal cannot happen by accident from a service call."""
    await setup_ip_ban_manager(hass)
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
async def test_remove_ip_ban_rejects_unknown_ip_without_mutating_state(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test typo removals do not rewrite ban state or clear failed attempts."""
    await setup_ip_ban_manager(hass)
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
    await setup_ip_ban_manager(hass)

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
    await setup_ip_ban_manager(hass)

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
async def test_allowlist_services_normalize_ipv6_wildcard(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test allowlist services accept IPv6 wildcard shorthand."""
    await setup_ip_ban_manager(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_ALLOWLIST_NETWORK,
        {ATTR_NETWORK: "2001:db8:1:2:*"},
        blocking=True,
    )
    check_records(caplog.records)

    assert hass.config_entries.async_entries(DOMAIN)[0].options[CONF_IP_ADDRESSES] == [
        "192.168.1.1",
        "172.17.0.0/24",
        "2001:db8:1:2::/64",
    ]
    assert [str(ip) for ip in hass.http.app[KEY_ALLOWLIST]] == [
        "192.168.1.1/32",
        "172.17.0.0/24",
        "2001:db8:1:2::/64",
    ]

    await hass.services.async_call(
        DOMAIN,
        SERVICE_REMOVE_ALLOWLIST_NETWORK,
        {ATTR_NETWORK: "2001:db8:1:2:*"},
        blocking=True,
    )
    check_records(caplog.records)

    assert [str(ip) for ip in hass.http.app[KEY_ALLOWLIST]] == [
        "192.168.1.1/32",
        "172.17.0.0/24",
    ]


@pytest.mark.asyncio
async def test_services_support_ipv6_bans_and_allowlist_networks(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test services accept IPv6 exact bans and allowlist networks."""
    await setup_ip_ban_manager(hass)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    ban_manager.path = str(tmp_path / "ip_bans.yaml")

    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_ALLOWLIST_NETWORK,
        {ATTR_NETWORK: "2001:db8::/64"},
        blocking=True,
    )
    check_records(caplog.records)

    assert [str(ip) for ip in hass.http.app[KEY_ALLOWLIST]] == [
        "192.168.1.1/32",
        "172.17.0.0/24",
        "2001:db8::/64",
    ]

    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_IP_BAN,
        {ATTR_IP_ADDRESS: "2001:db8:1::25"},
        blocking=True,
    )
    check_records(caplog.records)

    assert ip_address("2001:db8:1::25") in ban_manager.ip_bans_lookup
    assert "2001:db8:1::25" in Path(ban_manager.path).read_text(encoding="utf8")

    await hass.services.async_call(
        DOMAIN,
        SERVICE_REMOVE_IP_BAN,
        {ATTR_IP_ADDRESS: "2001:db8:1::25"},
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN,
        SERVICE_REMOVE_ALLOWLIST_NETWORK,
        {ATTR_NETWORK: "2001:db8::/64"},
        blocking=True,
    )
    check_records(caplog.records)

    assert ip_address("2001:db8:1::25") not in ban_manager.ip_bans_lookup
    assert [str(ip) for ip in hass.http.app[KEY_ALLOWLIST]] == [
        "192.168.1.1/32",
        "172.17.0.0/24",
    ]


@pytest.mark.asyncio
async def test_allowlist_service_rejects_allowlisting_everything(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test service calls cannot add an allowlist entry that disables bans."""
    await setup_ip_ban_manager(hass)

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
    await setup_ip_ban_manager(hass)
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
async def test_allowlist_service_can_remove_final_entry(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test service calls can remove the final allowlist entry."""
    await setup_ip_ban_manager(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_REMOVE_ALLOWLIST_NETWORK,
        {ATTR_NETWORK: "172.17.0.0/24"},
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN,
        SERVICE_REMOVE_ALLOWLIST_NETWORK,
        {ATTR_NETWORK: "192.168.1.1"},
        blocking=True,
    )
    check_records(caplog.records)

    assert hass.config_entries.async_entries(DOMAIN)[0].options[CONF_IP_ADDRESSES] == []
    assert hass.http.app[KEY_ALLOWLIST] == ()


@pytest.mark.asyncio
async def test_allowlist_service_rejects_removing_only_local_path(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test service calls cannot remove the allowlist path that prevents lockout."""
    monkeypatch.setattr(
        ban_config_flow,
        "_async_detect_home_assistant_subnets",
        detected_local_subnets,
    )
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_IP_ADDRESSES: ["192.168.1.0/24"],
            CONF_DEFAULT_DENY_ENABLED: True,
        },
        options={},
    )
    entry.add_to_hass(hass)
    await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_REMOVE_ALLOWLIST_NETWORK,
            {ATTR_NETWORK: "192.168.1.0/24"},
            blocking=True,
        )
    check_records(caplog.records)

    assert (
        hass.config_entries.async_entries(DOMAIN)[0].options.get(CONF_IP_ADDRESSES)
        is None
    )
    assert [str(ip) for ip in hass.http.app[KEY_ALLOWLIST]] == ["192.168.1.0/24"]


@pytest.mark.asyncio
async def test_ban_service_fires_event_and_logbook(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test ban services fire automation events and logbook entries."""
    from homeassistant.components import logbook

    captured: list[tuple[str, dict[str, Any]]] = []
    logbook_messages: list[str] = []

    @callback
    def capture_event(event) -> None:
        captured.append((event.event_type, dict(event.data)))

    remove = hass.bus.async_listen(EVENT_IP_BANNED, capture_event)
    monkeypatch.setattr(
        logbook,
        "async_log_entry",
        lambda _hass, _name, message, domain=None: logbook_messages.append(message),
    )

    await setup_ip_ban_manager(hass)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    ban_manager.path = str(tmp_path / "ip_bans.yaml")

    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_IP_BAN,
        {ATTR_IP_ADDRESS: "10.0.0.25"},
        blocking=True,
    )
    check_records(caplog.records)
    remove()

    assert captured == [
        (
            EVENT_IP_BANNED,
            {ATTR_IP_ADDRESS: "10.0.0.25", ATTR_SOURCE: SOURCE_SERVICE},
        )
    ]
    assert logbook_messages == ["Banned 10.0.0.25 (service)"]


@pytest.mark.asyncio
async def test_unban_service_fires_event(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test unban services fire automation events."""
    captured: list[tuple[str, dict[str, Any]]] = []

    @callback
    def capture_event(event) -> None:
        captured.append((event.event_type, dict(event.data)))

    remove = hass.bus.async_listen(EVENT_IP_UNBANNED, capture_event)

    await setup_ip_ban_manager(hass)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    ban_manager.path = str(tmp_path / "ip_bans.yaml")
    await ban_manager.async_add_ban(IPv4Address("10.0.0.25"))

    await hass.services.async_call(
        DOMAIN,
        SERVICE_REMOVE_IP_BAN,
        {ATTR_IP_ADDRESS: "10.0.0.25"},
        blocking=True,
    )
    check_records(caplog.records)
    remove()

    assert captured == [
        (
            EVENT_IP_UNBANNED,
            {ATTR_IP_ADDRESS: "10.0.0.25", ATTR_SOURCE: SOURCE_SERVICE},
        )
    ]


@pytest.mark.asyncio
async def test_allowlist_and_blocked_network_services_fire_events(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test allowlist and blocked-network services fire automation events."""
    captured: list[str] = []

    @callback
    def capture_event(event) -> None:
        captured.append(event.event_type)

    remove_added = hass.bus.async_listen(EVENT_ALLOWLIST_NETWORK_ADDED, capture_event)
    remove_removed = hass.bus.async_listen(
        EVENT_ALLOWLIST_NETWORK_REMOVED, capture_event
    )
    remove_blocked_added = hass.bus.async_listen(
        EVENT_BLOCKED_NETWORK_ADDED, capture_event
    )
    remove_blocked_removed = hass.bus.async_listen(
        EVENT_BLOCKED_NETWORK_REMOVED, capture_event
    )

    await setup_ip_ban_manager(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_ALLOWLIST_NETWORK,
        {ATTR_NETWORK: "203.0.113.0/24"},
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN,
        SERVICE_REMOVE_ALLOWLIST_NETWORK,
        {ATTR_NETWORK: "203.0.113.0/24"},
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_BLOCKED_NETWORK,
        {ATTR_NETWORK: "198.51.100.0/24"},
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN,
        SERVICE_REMOVE_BLOCKED_NETWORK,
        {ATTR_NETWORK: "198.51.100.0/24"},
        blocking=True,
    )
    check_records(caplog.records)
    remove_added()
    remove_removed()
    remove_blocked_added()
    remove_blocked_removed()

    assert captured == [
        EVENT_ALLOWLIST_NETWORK_ADDED,
        EVENT_ALLOWLIST_NETWORK_REMOVED,
        EVENT_BLOCKED_NETWORK_ADDED,
        EVENT_BLOCKED_NETWORK_REMOVED,
    ]


@pytest.mark.asyncio
async def test_update_geoip_service_writes_logbook(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test the update_geoip service records a logbook entry."""
    from homeassistant.components import logbook

    logbook_messages: list[str] = []
    monkeypatch.setattr(
        logbook,
        "async_log_entry",
        lambda _hass, _name, message, domain=None: logbook_messages.append(message),
    )
    monkeypatch.setattr(
        ban_services,
        "async_download_geoip_database",
        AsyncMock(return_value=None),
    )

    await setup_ip_ban_manager(hass)
    await hass.services.async_call(DOMAIN, SERVICE_UPDATE_GEOIP, {}, blocking=True)
    check_records(caplog.records)

    assert logbook_messages == ["Updated GeoIP database (service)"]


@pytest.mark.asyncio
async def test_remove_all_ip_bans_fires_unban_event_per_ip(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test clearing every ban fires one unban event per removed IP."""
    captured: list[str] = []

    @callback
    def capture_event(event) -> None:
        captured.append(event.data[ATTR_IP_ADDRESS])

    remove = hass.bus.async_listen(EVENT_IP_UNBANNED, capture_event)

    await setup_ip_ban_manager(hass)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    ban_manager.path = str(tmp_path / "ip_bans.yaml")
    await ban_manager.async_add_ban(IPv4Address("10.0.0.1"))
    await ban_manager.async_add_ban(IPv4Address("10.0.0.2"))

    await hass.services.async_call(
        DOMAIN,
        SERVICE_REMOVE_ALL_IP_BANS,
        {ATTR_CONFIRM: True},
        blocking=True,
    )
    check_records(caplog.records)
    remove()

    assert sorted(captured) == ["10.0.0.1", "10.0.0.2"]


@pytest.mark.asyncio
async def test_update_geoip_service_failure_does_not_log_success(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test a failed GeoIP update does not write a success logbook entry."""
    from homeassistant.components import logbook

    logbook_messages: list[str] = []
    monkeypatch.setattr(
        logbook,
        "async_log_entry",
        lambda _hass, _name, message, domain=None: logbook_messages.append(message),
    )

    async def fail_download(_hass: HomeAssistant) -> None:
        raise HomeAssistantError("GeoIP download failed.")

    monkeypatch.setattr(ban_services, "async_download_geoip_database", fail_download)

    await setup_ip_ban_manager(hass)
    with pytest.raises(HomeAssistantError, match="GeoIP download failed"):
        await hass.services.async_call(DOMAIN, SERVICE_UPDATE_GEOIP, {}, blocking=True)
    check_records(caplog.records)

    assert logbook_messages == []
