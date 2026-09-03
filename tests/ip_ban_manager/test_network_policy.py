"""Focused tests split out from test_setup."""

# mypy: ignore-errors

# flake8: noqa
# ruff: noqa: F403,F405

from types import SimpleNamespace

from homeassistant.helpers.http import current_request

from custom_components.ip_ban_manager.ban_lookup import protected_callback_path

from .test_setup import *


@pytest.mark.parametrize(
    ("path", "component_domain"),
    [
        ("/api/webhook/google-callback", None),
        ("/api/fitbit/callback", "fitbit"),
        ("/api/google_assistant", "google_assistant"),
        ("/api/alexa/smart_home", "alexa"),
        ("/api/loqed/webhook", "loqed"),
        ("/api/notify.html5/callback", "html5"),
        ("/api/telegram_webhooks", "telegram_bot"),
        ("/auth/token", None),
    ],
)
def test_protected_callback_routes_bypass_managed_network_policy(
    path: str, component_domain: str | None
) -> None:
    """Protected callbacks bypass region, network, and default-deny rules."""
    remote_addr = IPv4Address("108.177.68.100")
    lookup = ipbm.NetworkAwareBanLookup(
        {},
        (IPv4Network("0.0.0.0/0"),),
        (),
        True,
        internal_bypass_networks=(),
        geoip_access_allowed=lambda _remote_addr: False,
        callback_path_is_protected=lambda candidate: protected_callback_path(
            candidate,
            (
                frozenset({component_domain})
                if component_domain is not None
                else frozenset()
            ),
        ),
    )
    token = current_request.set(cast(Any, SimpleNamespace(path=path)))
    try:
        assert remote_addr not in lookup
    finally:
        current_request.reset(token)


def test_protected_callback_route_does_not_override_exact_ban() -> None:
    """An explicit exact ban remains authoritative on callback routes."""
    remote_addr = IPv4Address("108.177.68.100")
    lookup = ipbm.NetworkAwareBanLookup(
        {remote_addr: IpBan(remote_addr)},
        (),
        (),
        False,
        internal_bypass_networks=(),
        callback_path_is_protected=protected_callback_path,
    )
    token = current_request.set(cast(Any, SimpleNamespace(path="/auth/token")))
    try:
        assert remote_addr in lookup
    finally:
        current_request.reset(token)


def test_unconfigured_integration_callback_route_remains_managed() -> None:
    """Named routes are not exempt when their integration is not loaded."""
    remote_addr = IPv4Address("108.177.68.100")
    lookup = ipbm.NetworkAwareBanLookup(
        {},
        (),
        (),
        True,
        internal_bypass_networks=(),
        callback_path_is_protected=lambda path: protected_callback_path(
            path, frozenset({"google_assistant"})
        ),
    )
    token = current_request.set(cast(Any, SimpleNamespace(path="/api/alexa")))
    try:
        assert remote_addr in lookup
    finally:
        current_request.reset(token)


@pytest.mark.parametrize(
    "path",
    [
        "/auth/authorize",
        "/auth/token/extra",
        "/api/callback-looking-but-not-a-callback",
        "/api/google_assistant/extra",
        "/api/not-a-webhook",
    ],
)
def test_unrelated_routes_remain_managed(path: str) -> None:
    """Similar-looking routes do not gain a policy bypass."""
    remote_addr = IPv4Address("108.177.68.100")
    lookup = ipbm.NetworkAwareBanLookup(
        {},
        (),
        (),
        True,
        internal_bypass_networks=(),
    )
    token = current_request.set(cast(Any, SimpleNamespace(path=path)))
    try:
        assert remote_addr in lookup
    finally:
        current_request.reset(token)


@pytest.mark.asyncio
async def test_setup_applies_blocked_networks_with_allowlist_precedence(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test managed blocked networks are enforced behind the native ban lookup."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        data={
            CONF_IP_ADDRESSES: ["203.0.113.10"],
            CONF_BLOCKED_NETWORKS: ["203.0.113.0/24"],
        },
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    check_records(caplog.records)

    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    assert [str(network) for network in hass.http.app[KEY_BLOCKED_NETWORKS]] == [
        "203.0.113.0/24"
    ]
    assert ip_address("203.0.113.25") in ban_manager.ip_bans_lookup
    assert ip_address("203.0.113.10") not in ban_manager.ip_bans_lookup


@pytest.mark.asyncio
async def test_network_only_blocks_keep_ban_middleware_active(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test blocked networks work even when there are no exact IP bans."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        data={
            CONF_IP_ADDRESSES: ["127.0.0.1", "192.168.1.0/24"],
            CONF_BLOCKED_NETWORKS: ["0.0.0.0/1", "128.0.0.0/1"],
        },
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    check_records(caplog.records)

    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    assert ban_manager.ip_bans_lookup == {}
    assert bool(ban_manager.ip_bans_lookup)

    async def handler(request: Any) -> Response:
        return Response(text="ok")

    class BlockedRequest:
        app = hass.http.app
        remote = "8.8.8.8"

    with pytest.raises(HTTPForbidden):
        await http_ban.ban_middleware(cast(Any, BlockedRequest()), handler)

    class AllowedRequest:
        app = hass.http.app
        remote = "192.168.1.42"

    response = cast(
        Response,
        await http_ban.ban_middleware(cast(Any, AllowedRequest()), handler),
    )
    assert response.text == "ok"


@pytest.mark.asyncio
async def test_default_deny_blocks_everything_outside_allowlist(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test default-deny mode blocks all non-allowlisted addresses."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        data={
            CONF_IP_ADDRESSES: ["127.0.0.1", "192.168.1.0/24"],
            CONF_DEFAULT_DENY_ENABLED: True,
        },
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    check_records(caplog.records)

    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    assert bool(ban_manager.ip_bans_lookup)
    assert ip_address("8.8.8.8") in ban_manager.ip_bans_lookup
    assert ip_address("::ffff:8.8.8.8") in ban_manager.ip_bans_lookup
    assert ip_address("192.168.1.42") not in ban_manager.ip_bans_lookup
    assert ip_address("::ffff:192.168.1.42") not in ban_manager.ip_bans_lookup
    assert ip_address("127.0.0.1") not in ban_manager.ip_bans_lookup
    assert ip_address("::ffff:127.0.0.1") not in ban_manager.ip_bans_lookup

    blocked_networks = hass.states.get("sensor.ip_ban_manager_blocked_networks")
    assert blocked_networks is not None
    assert blocked_networks.attributes[ATTR_DEFAULT_DENY_ENABLED] is True


@pytest.mark.asyncio
async def test_default_deny_preserves_supervisor_frontend_check(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test default-deny mode does not block Supervisor's readiness check."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        data={
            CONF_IP_ADDRESSES: ["127.0.0.1"],
            CONF_DEFAULT_DENY_ENABLED: True,
        },
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    check_records(caplog.records)

    supervisor_addr = ip_address("172.30.32.2")
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    ban_manager.ip_bans_lookup[supervisor_addr] = IpBan(supervisor_addr)

    assert supervisor_addr not in ban_manager.ip_bans_lookup
    assert ip_address("172.30.33.254") not in ban_manager.ip_bans_lookup
    assert ip_address("172.30.34.1") not in ban_manager.ip_bans_lookup
    assert ip_address("172.31.0.1") in ban_manager.ip_bans_lookup


def test_supervisor_internal_networks_uses_supervisor_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test Supervisor bypass networks adapt to the Supervisor environment."""
    monkeypatch.setenv("SUPERVISOR", "172.30.40.2")
    networks = _supervisor_internal_networks()

    assert ip_address("172.30.40.2") in networks[0]
    assert ip_address("172.30.255.254") in networks[0]
    assert ip_address("172.31.0.1") not in networks[0]


def test_supervisor_internal_networks_keeps_non_docker_env_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test unusual Supervisor addresses are not expanded into broad bypasses."""
    monkeypatch.setenv("SUPERVISOR", "192.0.2.10:8123")
    networks = _supervisor_internal_networks()

    assert str(networks[0]) == "192.0.2.10/32"
    assert ip_address("192.0.2.11") not in networks[0]


def test_supervisor_internal_networks_supports_ipv6_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test IPv6 Supervisor addresses are preserved as exact bypasses."""
    monkeypatch.setenv("SUPERVISOR", "fd00::10")
    networks = _supervisor_internal_networks()

    assert str(networks[0]) == "fd00::10/128"
    assert ip_address("fd00::11") not in networks[0]


@pytest.mark.asyncio
async def test_default_deny_does_not_block_home_assistant_self_address(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test default-deny still bypasses exact Home Assistant self-addresses."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    ipbm._update_allowlist_entry(hass, ["10.10.10.0/24"])

    async def mock_self_networks(hass: HomeAssistant) -> tuple[IPv4Network, ...]:
        return (IPv4Network("192.168.1.40/32"),)

    monkeypatch.setattr(
        ban_network_policy, "async_home_assistant_self_networks", mock_self_networks
    )

    await _async_panel_set_options(hass, {CONF_DEFAULT_DENY_ENABLED: True})

    lookup = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER]).ip_bans_lookup
    assert isinstance(lookup, ipbm.NetworkAwareBanLookup)
    assert IPv4Address("192.168.1.40") not in lookup
    assert IPv4Address("192.168.1.41") in lookup
    assert entry.options[CONF_DEFAULT_DENY_ENABLED] is True


@pytest.mark.asyncio
async def test_setup_syncs_new_internal_defaults_when_safe_defaults_active(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test existing safe-default entries gain newly detected internal paths."""

    async def mock_safe_defaults(hass: HomeAssistant) -> list[str]:
        return ["192.168.1.0/24", "172.17.0.1/32"]

    async def mock_internal_defaults(hass: HomeAssistant) -> list[str]:
        return ["172.17.0.1/32"]

    async def skip_runtime_reload(mock_hass: HomeAssistant) -> None:
        return None

    monkeypatch.setattr(ipbm, "_async_reload_runtime_modules", skip_runtime_reload)

    monkeypatch.setattr(
        ban_network_policy,
        "async_home_assistant_allowlist_safe_defaults",
        mock_safe_defaults,
    )
    monkeypatch.setattr(
        ban_network_policy,
        "async_home_assistant_internal_allowlist_networks",
        mock_internal_defaults,
    )

    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        data={CONF_IP_ADDRESSES: ["127.0.0.1", "192.168.1.0/24"]},
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    stored_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert stored_entry is not None
    assert stored_entry.options[CONF_IP_ADDRESSES] == [
        "127.0.0.1",
        "192.168.1.0/24",
        "172.17.0.1/32",
    ]
    assert [str(network) for network in hass.http.app[KEY_ALLOWLIST]] == [
        "127.0.0.1/32",
        "192.168.1.0/24",
        "172.17.0.1/32",
    ]


@pytest.mark.asyncio
async def test_setup_skips_internal_default_sync_without_localhost(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test custom entries without localhost are not widened on setup."""

    async def mock_safe_defaults(hass: HomeAssistant) -> list[str]:
        return ["192.168.1.0/24", "172.17.0.1/32"]

    async def mock_internal_defaults(hass: HomeAssistant) -> list[str]:
        return ["172.17.0.1/32"]

    monkeypatch.setattr(
        ban_network_policy,
        "async_home_assistant_allowlist_safe_defaults",
        mock_safe_defaults,
    )
    monkeypatch.setattr(
        ban_network_policy,
        "async_home_assistant_internal_allowlist_networks",
        mock_internal_defaults,
    )

    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        data={CONF_IP_ADDRESSES: ["192.168.1.0/24"]},
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    stored_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert stored_entry is not None
    assert stored_entry.options.get(CONF_IP_ADDRESSES) is None
    assert entry.data[CONF_IP_ADDRESSES] == ["192.168.1.0/24"]


@pytest.mark.asyncio
async def test_setup_does_not_duplicate_equivalent_internal_defaults(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test existing equivalent internal entries are not duplicated."""

    async def mock_safe_defaults(hass: HomeAssistant) -> list[str]:
        return ["192.168.1.0/24", "172.17.0.1/32"]

    async def mock_internal_defaults(hass: HomeAssistant) -> list[str]:
        return ["172.17.0.1/32"]

    monkeypatch.setattr(
        ban_network_policy,
        "async_home_assistant_allowlist_safe_defaults",
        mock_safe_defaults,
    )
    monkeypatch.setattr(
        ban_network_policy,
        "async_home_assistant_internal_allowlist_networks",
        mock_internal_defaults,
    )

    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        data={
            CONF_IP_ADDRESSES: [
                "127.0.0.1",
                "192.168.1.0/24",
                "172.17.0.1",
            ]
        },
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    stored_entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert stored_entry is not None
    assert stored_entry.options.get(CONF_IP_ADDRESSES) is None
    assert entry.data[CONF_IP_ADDRESSES] == [
        "127.0.0.1",
        "192.168.1.0/24",
        "172.17.0.1",
    ]


@pytest.mark.asyncio
async def test_default_deny_does_not_block_ipv6_link_local_access(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test default-deny still allows enabled adapter IPv6 link-local access."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    ipbm._update_allowlist_entry(hass, ["192.168.1.0/24"])

    async def mock_self_networks(hass: HomeAssistant) -> tuple[IPv6Network, ...]:
        return (IPv6Network("fe80::/64"),)

    async def mock_detected_subnets(hass: HomeAssistant) -> list[str]:
        return ["192.168.1.0/24", "fe80::/64"]

    monkeypatch.setattr(
        ban_network_policy, "async_home_assistant_self_networks", mock_self_networks
    )
    monkeypatch.setattr(
        ban_config_flow,
        "_async_detect_home_assistant_subnets",
        mock_detected_subnets,
    )

    await _async_panel_set_options(hass, {CONF_DEFAULT_DENY_ENABLED: True})

    lookup = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER]).ip_bans_lookup
    assert isinstance(lookup, ipbm.NetworkAwareBanLookup)
    assert IPv6Address("fe80::8fa2:f2b9:c1f5:3a7a") not in lookup
    assert IPv6Address("fd12:3456:789a::42") in lookup
    assert entry.options[CONF_DEFAULT_DENY_ENABLED] is True


@pytest.mark.asyncio
async def test_ban_load_keeps_managed_network_blocks(
    hass: HomeAssistant, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Test HA ban file reloads do not drop managed network blocks."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    ban_path = tmp_path / "ip_bans.yaml"
    ban_path.write_text(
        "10.0.0.2:\n  banned_at: '2026-06-01T00:00:00+00:00'\n",
        encoding="utf-8",
    )
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    ban_manager.path = str(ban_path)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        data={
            CONF_IP_ADDRESSES: ["192.168.1.0/24"],
            CONF_BLOCKED_NETWORKS: ["10.0.0.0/24"],
        },
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    check_records(caplog.records)

    assert ip_address("10.0.0.3") in ban_manager.ip_bans_lookup
    assert ip_address("192.168.1.42") not in ban_manager.ip_bans_lookup

    await ban_manager.async_load()

    assert ip_address("10.0.0.2") in ban_manager.ip_bans_lookup
    assert ip_address("10.0.0.3") in ban_manager.ip_bans_lookup
    assert ip_address("192.168.1.42") not in ban_manager.ip_bans_lookup


@pytest.mark.asyncio
async def test_current_status_lists_live_state(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test the status helper formats the live lists for UI display."""
    await setup_ip_ban_manager(hass)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    ban_manager.path = str(tmp_path / "ip_bans.yaml")
    await ban_manager.async_add_ban(IPv4Address("10.0.0.1"))
    hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS][ip_address("10.0.0.2")] = 1
    check_records(caplog.records)

    status = current_status(hass)
    health = cast(dict[str, Any], status[ATTR_HEALTH])
    metrics = cast(dict[str, Any], status[ATTR_METRICS])

    assert status[ATTR_ALLOWLISTED_LOGINS_CAN_BAN] is False
    assert status[ATTR_DEFAULT_DENY_ENABLED] is False
    assert health["ok"] is True
    assert health[ATTR_HEALTH_ISSUES] == []
    assert "config_writes" in metrics
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
