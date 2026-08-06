"""Focused tests split out from test_setup."""

# mypy: ignore-errors

# flake8: noqa
# ruff: noqa: F403,F405

from .test_setup import *


@pytest.mark.asyncio
async def test_hit_allowlist(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test hitting the allowlist."""
    await setup_ip_ban_manager(hass)
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
            "custom_components.ip_ban_manager"
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
async def test_ban_hook_uses_current_allowlist(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test the ban hook reads the current app allowlist."""
    await setup_ip_ban_manager(hass)
    hass.http.app[KEY_ALLOWLIST] = ()

    await cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER]).async_add_ban(
        IPv4Address("192.168.1.1")
    )
    check_records(caplog.records)

    messages = []

    for record in caplog.records:
        if record.levelno < logging.INFO or not record.name.startswith(
            "custom_components.ip_ban_manager"
        ):
            continue

        messages.append(record.getMessage())

    assert messages == [
        "Setting allowlist with ['192.168.1.1/32', '172.17.0.0/24']",
        "Banning IP 192.168.1.1",
    ]


@pytest.mark.asyncio
async def test_ban_hook_works_after_adding_first_allowlist_entry(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test live allowlist additions work when setup started with no allowlist."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        data={CONF_IP_ADDRESSES: []},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN,
        SERVICE_ADD_ALLOWLIST_NETWORK,
        {ATTR_NETWORK: "10.0.0.0/24"},
        blocking=True,
    )
    await cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER]).async_add_ban(
        IPv4Address("10.0.0.25")
    )
    check_records(caplog.records)

    assert (
        ip_address("10.0.0.25")
        not in cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER]).ip_bans_lookup
    )
    assert [str(ip) for ip in hass.http.app[KEY_ALLOWLIST]] == ["10.0.0.0/24"]


@pytest.mark.asyncio
async def test_unload_restores_home_assistant_hooks(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test unloading leaves Home Assistant's HTTP ban internals restored."""
    from homeassistant.components.auth import login_flow
    from homeassistant.components.websocket_api import auth as websocket_auth

    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    patched_add_ban = ban_manager.async_add_ban
    original_add_ban = hass.http.app[KEY_ORIGINAL_ADD_BAN]
    patched_load_bans = ban_manager.async_load
    original_load_bans = hass.http.app[KEY_ORIGINAL_LOAD_BANS]

    assert http_ban.process_wrong_login is _allowlist_process_wrong_login
    assert login_flow.process_wrong_login is _allowlist_process_wrong_login
    assert websocket_auth.process_wrong_login is _allowlist_process_wrong_login
    assert patched_add_ban is not original_add_ban
    assert patched_load_bans is not original_load_bans

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    check_records(caplog.records)

    assert http_ban.process_wrong_login is _ORIGINAL_PROCESS_WRONG_LOGIN
    assert login_flow.process_wrong_login is _ORIGINAL_PROCESS_WRONG_LOGIN
    assert websocket_auth.process_wrong_login is _ORIGINAL_PROCESS_WRONG_LOGIN
    assert ban_manager.async_add_ban is original_add_ban
    assert ban_manager.async_load is original_load_bans
    assert KEY_ALLOWLIST not in hass.http.app
    assert KEY_CONFIG_ENTRY not in hass.http.app
    assert KEY_DEFAULT_DENY not in hass.http.app
    assert KEY_INTERNAL_BYPASS_NETWORKS not in hass.http.app
    assert KEY_ORIGINAL_ADD_BAN not in hass.http.app
    assert KEY_ORIGINAL_LOAD_BANS not in hass.http.app
    assert KEY_REVERSE_DNS_CACHE not in hass.http.app
    assert KEY_HEALTH not in hass.data
    assert KEY_METRICS not in hass.data
    assert KEY_HTTP_VIEWS not in hass.data
    assert KEY_HTTP_VIEW_HANDLERS not in hass.data


@pytest.mark.asyncio
async def test_unload_removes_all_registered_services(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test unloading removes every service registered by IP Ban Manager."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    for service in ban_services.REGISTERED_SERVICES:
        assert hass.services.has_service(DOMAIN, service)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    check_records(caplog.records)

    for service in ban_services.REGISTERED_SERVICES:
        assert not hass.services.has_service(DOMAIN, service)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    check_records(caplog.records)

    for service in ban_services.REGISTERED_SERVICES:
        assert hass.services.has_service(DOMAIN, service)


@pytest.mark.asyncio
async def test_config_entry_reload_restores_runtime_hooks(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test config-entry reload tears down and restores integration runtime state."""
    from homeassistant.components.auth import login_flow
    from homeassistant.components.websocket_api import auth as websocket_auth

    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])

    assert http_ban.process_wrong_login is _allowlist_process_wrong_login
    assert hass.services.has_service(DOMAIN, SERVICE_EXPORT_CONFIG)
    assert KEY_HTTP_VIEW_HANDLERS in hass.data

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    check_records(caplog.records)

    assert http_ban.process_wrong_login is _allowlist_process_wrong_login
    assert login_flow.process_wrong_login is _allowlist_process_wrong_login
    assert websocket_auth.process_wrong_login is _allowlist_process_wrong_login
    assert KEY_CONFIG_ENTRY in hass.http.app
    assert KEY_HTTP_VIEW_HANDLERS in hass.data
    assert hass.services.has_service(DOMAIN, SERVICE_EXPORT_CONFIG)
    assert hass.services.has_service(DOMAIN, SERVICE_IMPORT_CONFIG)
    assert ban_manager.async_add_ban is not hass.http.app[KEY_ORIGINAL_ADD_BAN]

    response = await IPBanManagerStatusView().get(
        cast(Any, MockViewRequest(hass.http.app))
    )
    assert response.status == 200


@pytest.mark.asyncio
async def test_auto_ban_fires_threshold_event_after_successful_ban(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test auto-ban fires threshold reached after the ban write succeeds."""
    events: list[tuple[str, dict[str, Any]]] = []

    @callback
    def capture_event(event) -> None:
        events.append((event.event_type, dict(event.data)))

    remove_threshold = hass.bus.async_listen(
        EVENT_LOGIN_THRESHOLD_REACHED, capture_event
    )
    remove_banned = hass.bus.async_listen(EVENT_IP_BANNED, capture_event)

    await setup_ip_ban_manager(hass)
    remote_addr = ip_address("10.0.0.99")
    hass.http.app[KEY_LOGIN_THRESHOLD] = 3
    hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS][remote_addr] = 2

    class MockRequest:
        remote = "10.0.0.99"
        app = hass.http.app
        headers: dict[str, str] = {}
        rel_url = "/auth/login_flow/test"

    await http_ban.process_wrong_login(cast(Any, MockRequest()))
    check_records(caplog.records)
    remove_threshold()
    remove_banned()

    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    assert remote_addr in ban_manager.ip_bans_lookup
    assert events == [
        (
            EVENT_LOGIN_THRESHOLD_REACHED,
            {
                ATTR_IP_ADDRESS: "10.0.0.99",
                ATTR_ATTEMPTS: 3,
                ATTR_THRESHOLD: 3,
                ATTR_SOURCE: SOURCE_AUTO,
            },
        ),
        (
            EVENT_IP_BANNED,
            {ATTR_IP_ADDRESS: "10.0.0.99", ATTR_SOURCE: SOURCE_AUTO},
        ),
    ]


@pytest.mark.asyncio
async def test_auto_ban_write_failure_does_not_fire_events(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test failed native ban writes do not emit automation events."""
    captured: list[str] = []

    @callback
    def capture_event(event) -> None:
        captured.append(event.event_type)

    await setup_ip_ban_manager(hass)
    remote_addr = ip_address("10.0.0.99")
    hass.http.app[KEY_LOGIN_THRESHOLD] = 3
    hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS][remote_addr] = 2

    async def failing_add_ban(_remote_addr: Any) -> None:
        raise HomeAssistantError("native write failed")

    hass.http.app[KEY_ORIGINAL_ADD_BAN] = failing_add_ban
    remove_threshold = hass.bus.async_listen(
        EVENT_LOGIN_THRESHOLD_REACHED, capture_event
    )
    remove_banned = hass.bus.async_listen(EVENT_IP_BANNED, capture_event)

    class MockRequest:
        remote = "10.0.0.99"
        app = hass.http.app
        headers: dict[str, str] = {}
        rel_url = "/auth/login_flow/test"

    with pytest.raises(HomeAssistantError, match="native write failed"):
        await http_ban.process_wrong_login(cast(Any, MockRequest()))
    check_records(caplog.records)
    remove_threshold()
    remove_banned()

    assert captured == []


@pytest.mark.asyncio
async def test_allowlisted_auto_ban_refusal_does_not_fire_threshold_event(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test allowlisted sources that cannot be banned do not fire threshold events."""
    captured: list[str] = []

    @callback
    def capture_event(event) -> None:
        captured.append(event.event_type)

    remove = hass.bus.async_listen(EVENT_LOGIN_THRESHOLD_REACHED, capture_event)

    await setup_ip_ban_manager(hass)
    remote_addr = ip_address("192.168.1.1")
    hass.http.app[KEY_LOGIN_THRESHOLD] = 2
    hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS][remote_addr] = 1

    class MockRequest:
        remote = "192.168.1.1"
        app = hass.http.app
        headers: dict[str, str] = {}
        rel_url = "/auth/login_flow/test"

    await http_ban.process_wrong_login(cast(Any, MockRequest()))
    check_records(caplog.records)
    remove()

    assert captured == []
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    assert remote_addr not in ban_manager.ip_bans_lookup


@pytest.mark.asyncio
async def test_already_banned_source_does_not_fire_threshold_event(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test an already-banned source does not fire another threshold event."""
    captured: list[str] = []

    @callback
    def capture_event(event) -> None:
        captured.append(event.event_type)

    await setup_ip_ban_manager(hass)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    ban_manager.path = str(tmp_path / "ip_bans.yaml")
    remote_addr = ip_address("10.0.0.99")
    await ban_manager.async_add_ban(remote_addr)
    hass.http.app[KEY_LOGIN_THRESHOLD] = 3
    hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS][remote_addr] = 3

    remove_threshold = hass.bus.async_listen(
        EVENT_LOGIN_THRESHOLD_REACHED, capture_event
    )
    remove_banned = hass.bus.async_listen(EVENT_IP_BANNED, capture_event)

    class MockRequest:
        remote = "10.0.0.99"
        app = hass.http.app
        headers: dict[str, str] = {}
        rel_url = "/auth/login_flow/test"

    await http_ban.process_wrong_login(cast(Any, MockRequest()))
    check_records(caplog.records)
    remove_threshold()
    remove_banned()

    assert captured == []
