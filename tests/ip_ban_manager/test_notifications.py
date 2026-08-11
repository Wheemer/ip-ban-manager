"""Focused tests split out from test_setup."""

# mypy: ignore-errors

# flake8: noqa
# ruff: noqa: F403,F405

from custom_components.ip_ban_manager.geoip import geoip_location_from_result
from custom_components.ip_ban_manager.storage_keys import IPAddress

from .test_setup import *


@pytest.mark.asyncio
async def test_allowlisted_wrong_login_does_not_add_ban_notification(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test allowlisted login failures are reported but do not become bans."""
    await setup_ip_ban_manager(hass)

    remote_addr = ip_address("192.168.1.1")
    hass.http.app[KEY_LOGIN_THRESHOLD] = 2
    hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS][remote_addr] = 1

    existing_notifications = persistent_notification._async_get_or_create_notifications(
        hass
    )
    assert NOTIFICATION_ID_BAN not in existing_notifications

    class MockRequest:
        remote = "192.168.1.1"
        app = hass.http.app
        headers: dict[str, str] = {}
        rel_url = "/auth/login_flow/test"

    await http_ban.process_wrong_login(cast(Any, MockRequest()))

    assert hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS][remote_addr] == 2
    assert existing_notifications[NOTIFICATION_ID_LOGIN]["title"] == " "
    assert (
        "Allowlisted login failed"
        in existing_notifications[NOTIFICATION_ID_LOGIN]["message"]
    )
    assert "2/2" in existing_notifications[NOTIFICATION_ID_LOGIN]["message"]
    assert (
        "so it will not be banned"
        in existing_notifications[NOTIFICATION_ID_LOGIN]["message"]
    )
    login_message = existing_notifications[NOTIFICATION_ID_LOGIN]["message"]
    assert login_message.startswith("## <img ")
    assert login_message.count(NOTIFICATION_ICON_DATA_URL) == 1
    assert "/api/ip_ban_manager/icon.png" not in login_message
    assert "IP Ban Manager icon" not in login_message
    assert "Open settings" not in login_message
    assert ALLOWLISTED_LOGIN_SILENCE_LABEL in login_message
    assert f"/{DOMAIN}?action=silence_allowlisted_login" in login_message
    assert "&ip_address=192.168.1.1" in login_message
    assert f"&{ATTR_NOTIFICATION_ID}={NOTIFICATION_ID_LOGIN}" in login_message
    assert "&token=" not in login_message
    assert NOTIFICATION_ID_BAN not in existing_notifications

    messages = []

    for record in caplog.records:
        if record.levelno < logging.INFO or not record.name.startswith(
            "custom_components.ip_ban_manager"
        ):
            continue

        messages.append(record.getMessage())

    assert messages == [
        "Setting allowlist with ['192.168.1.1/32', '172.17.0.0/24']",
        "Allowlisted address 192.168.1.1 failed authentication but was not banned",
    ]


@pytest.mark.asyncio
async def test_allowlisted_wrong_login_does_not_duplicate_numeric_reverse_name(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test numeric reverse names are not shown as duplicated host/IP text."""
    await setup_ip_ban_manager(hass)
    monkeypatch.setattr(
        reverse_dns,
        "gethostbyaddr",
        lambda remote: (remote, [], [remote]),
    )

    remote_addr = ip_address("192.168.1.1")
    hass.http.app[KEY_LOGIN_THRESHOLD] = 3
    hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS][remote_addr] = 1

    class MockRequest:
        remote = "192.168.1.1"
        app = hass.http.app
        headers: dict[str, str] = {}
        rel_url = "/auth/login_flow/test"

    await http_ban.process_wrong_login(cast(Any, MockRequest()))

    notifications = persistent_notification._async_get_or_create_notifications(hass)
    message = notifications[NOTIFICATION_ID_LOGIN]["message"]
    assert "192.168.1.1 (192.168.1.1)" not in message
    assert "from 192.168.1.1." in message
    assert "2/3" in message


@pytest.mark.asyncio
async def test_allowlisted_wrong_login_keeps_real_reverse_name(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test real reverse names still show with the numeric address."""
    await setup_ip_ban_manager(hass)
    monkeypatch.setattr(
        reverse_dns,
        "gethostbyaddr",
        lambda remote: ("server.lan", [], [remote]),
    )

    class MockRequest:
        remote = "192.168.1.1"
        app = hass.http.app
        headers: dict[str, str] = {}
        rel_url = "/auth/login_flow/test"

    await http_ban.process_wrong_login(cast(Any, MockRequest()))

    notifications = persistent_notification._async_get_or_create_notifications(hass)
    message = notifications[NOTIFICATION_ID_LOGIN]["message"]
    assert "from server.lan (192.168.1.1)." not in message
    assert "from 192.168.1.1." in message
    assert "Reverse DNS: server.lan" in message


@pytest.mark.asyncio
async def test_allowlisted_wrong_login_caches_reverse_dns_name(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test repeated allowlisted failures do not repeat reverse-DNS lookups."""
    await setup_ip_ban_manager(hass)
    lookup_count = 0

    def fake_gethostbyaddr(remote: str) -> tuple[str, list[str], list[str]]:
        nonlocal lookup_count
        lookup_count += 1
        return "server.lan", [], [remote]

    monkeypatch.setattr(reverse_dns, "gethostbyaddr", fake_gethostbyaddr)

    class MockRequest:
        remote = "192.168.1.1"
        app = hass.http.app
        headers: dict[str, str] = {}
        rel_url = "/auth/login_flow/test"

    await http_ban.process_wrong_login(cast(Any, MockRequest()))
    await http_ban.process_wrong_login(cast(Any, MockRequest()))

    assert lookup_count == 1
    notifications = persistent_notification._async_get_or_create_notifications(hass)
    message = notifications[NOTIFICATION_ID_LOGIN]["message"]
    assert "from server.lan (192.168.1.1)." not in message
    assert "from 192.168.1.1." in message
    assert "Reverse DNS: server.lan" in message


@pytest.mark.asyncio
async def test_reverse_dns_public_ip_uses_external_lookup(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test public reverse DNS uses DNS-over-HTTPS as the primary lookup."""
    calls: list[str] = []

    async def fake_external(
        mock_hass: HomeAssistant, remote_addr: IPAddress
    ) -> str | None:
        calls.append(f"external:{remote_addr}")
        return "dns.google"

    async def fail_local(
        mock_hass: HomeAssistant, remote_addr: IPAddress
    ) -> str | None:
        raise AssertionError("local resolver should not run after external success")

    monkeypatch.setattr(reverse_dns, "_async_external_reverse_dns_name", fake_external)
    monkeypatch.setattr(reverse_dns, "_async_local_reverse_dns_name", fail_local)

    assert (
        await reverse_dns.async_reverse_dns_name(hass, ip_address("8.8.8.8"))
        == "dns.google"
    )
    assert calls == ["external:8.8.8.8"]


@pytest.mark.asyncio
async def test_reverse_dns_public_ip_does_not_fall_back_to_local_lookup(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test public reverse DNS does not use the local resolver after DoH misses."""
    calls: list[str] = []

    async def fake_external(
        mock_hass: HomeAssistant, remote_addr: IPAddress
    ) -> str | None:
        calls.append(f"external:{remote_addr}")
        return None

    async def fail_local(
        mock_hass: HomeAssistant, remote_addr: IPAddress
    ) -> str | None:
        raise AssertionError("local resolver should not run for public addresses")

    monkeypatch.setattr(reverse_dns, "_async_external_reverse_dns_name", fake_external)
    monkeypatch.setattr(reverse_dns, "_async_local_reverse_dns_name", fail_local)

    assert await reverse_dns.async_reverse_dns_name(hass, ip_address("8.8.4.4")) is None
    assert calls == ["external:8.8.4.4"]


@pytest.mark.asyncio
async def test_reverse_dns_private_ip_uses_local_lookup(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test private reverse DNS stays on Home Assistant's local resolver."""
    calls: list[str] = []

    async def fail_external(
        mock_hass: HomeAssistant, remote_addr: IPAddress
    ) -> str | None:
        raise AssertionError("external resolver should not run for private addresses")

    async def fake_local(
        mock_hass: HomeAssistant, remote_addr: IPAddress
    ) -> str | None:
        calls.append(f"local:{remote_addr}")
        return "server.lan"

    monkeypatch.setattr(reverse_dns, "_async_external_reverse_dns_name", fail_external)
    monkeypatch.setattr(reverse_dns, "_async_local_reverse_dns_name", fake_local)

    assert (
        await reverse_dns.async_reverse_dns_name(hass, ip_address("192.168.1.1"))
        == "server.lan"
    )
    assert calls == ["local:192.168.1.1"]


@pytest.mark.asyncio
async def test_allowlisted_wrong_login_skips_generic_notification_rewrite(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test allowlisted failures do not reprocess already-branded notifications."""
    await setup_ip_ban_manager(hass)

    def fail_rewrite(mock_hass: HomeAssistant) -> None:
        raise AssertionError("allowlisted path should create its own notification")

    monkeypatch.setattr(ipbm, "_handle_http_notifications", fail_rewrite)

    class MockRequest:
        remote = "192.168.1.1"
        app = hass.http.app
        headers: dict[str, str] = {}
        rel_url = "/auth/login_flow/test"

    await http_ban.process_wrong_login(cast(Any, MockRequest()))

    notifications = persistent_notification._async_get_or_create_notifications(hass)
    assert "Allowlisted login failed" in notifications[NOTIFICATION_ID_LOGIN]["message"]


@pytest.mark.asyncio
async def test_imported_auth_wrong_login_gets_branded_notification(
    hass: HomeAssistant,
) -> None:
    """Test auth modules that imported the HA hook also use our wrapper."""
    from homeassistant.components.auth import login_flow
    from homeassistant.components.websocket_api import auth as websocket_auth

    login_flow.process_wrong_login = _ORIGINAL_PROCESS_WRONG_LOGIN
    websocket_auth.process_wrong_login = _ORIGINAL_PROCESS_WRONG_LOGIN

    await setup_ip_ban_manager(hass)

    assert login_flow.process_wrong_login is _allowlist_process_wrong_login
    assert websocket_auth.process_wrong_login is _allowlist_process_wrong_login

    class MockRequest:
        remote = "10.0.0.50"
        app = hass.http.app
        headers: dict[str, str] = {}
        rel_url = "/auth/login_flow/test"

    await login_flow.process_wrong_login(cast(Any, MockRequest()))

    notifications = persistent_notification._async_get_or_create_notifications(hass)
    assert notifications[NOTIFICATION_ID_LOGIN]["title"] == " "
    message = notifications[NOTIFICATION_ID_LOGIN]["message"]
    assert message.startswith("## <img ")
    assert message.count(NOTIFICATION_ICON_DATA_URL) == 1
    assert "**Login attempt failed**" in message
    assert ALLOWLISTED_LOGIN_SILENCE_LABEL not in message
    assert f"/{DOMAIN}?action=silence_allowlisted_login" not in message
    assert message.endswith(f"[Open settings](/{DOMAIN})")
    assert "/config/integrations/" not in message


@pytest.mark.asyncio
async def test_allowlisted_wrong_login_can_become_exact_ban(
    hass: HomeAssistant,
) -> None:
    """Test opt-in failed logins from allowed networks can become exact bans."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(
        entry, options={CONF_ALLOWLISTED_LOGINS_CAN_BAN: True}
    )

    remote_addr = ip_address("192.168.1.1")
    hass.http.app[KEY_LOGIN_THRESHOLD] = 1

    class MockRequest:
        remote = "192.168.1.1"
        app = hass.http.app
        headers: dict[str, str] = {}
        rel_url = "/auth/login_flow/test"

    await http_ban.process_wrong_login(cast(Any, MockRequest()))

    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    assert remote_addr in ban_manager.ip_bans_lookup

    notifications = persistent_notification._async_get_or_create_notifications(hass)
    assert NOTIFICATION_ID_BAN in notifications
    ban_message = notifications[NOTIFICATION_ID_BAN]["message"]
    assert notifications[NOTIFICATION_ID_BAN]["title"] == " "
    assert ban_message.startswith("## <img ")
    assert ban_message.count(NOTIFICATION_ICON_DATA_URL) == 1
    assert "**IP banned**" in ban_message
    assert f"[Open settings](/{DOMAIN})" in ban_message
    assert "/config/integrations/" not in ban_message
    assert "Allowlisted login" not in ban_message


@pytest.mark.asyncio
async def test_quiet_allowlisted_wrong_logins_escalate_after_repeated_failures(
    hass: HomeAssistant,
) -> None:
    """Test muted allowlisted login notifications still escalate after repeated failures."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(
        entry, options={CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED: False}
    )

    remote_addr = ip_address("192.168.1.1")
    hass.http.app[KEY_LOGIN_THRESHOLD] = 5
    hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS][remote_addr] = (
        ALLOWLISTED_LOGIN_ESCALATION_THRESHOLD - 2
    )

    class MockRequest:
        remote = "192.168.1.1"
        app = hass.http.app
        headers: dict[str, str] = {}
        rel_url = "/auth/login_flow/test"

    notifications = persistent_notification._async_get_or_create_notifications(hass)

    await http_ban.process_wrong_login(cast(Any, MockRequest()))
    assert NOTIFICATION_ID_LOGIN not in notifications

    await http_ban.process_wrong_login(cast(Any, MockRequest()))
    assert notifications[NOTIFICATION_ID_LOGIN]["title"] == " "
    message = notifications[NOTIFICATION_ID_LOGIN]["message"]
    assert "Repeated allowlisted login failures" in message
    assert f"{ALLOWLISTED_LOGIN_ESCALATION_THRESHOLD} times" in message
    assert "Open settings" not in message
    assert ALLOWLISTED_LOGIN_SILENCE_LABEL in message
    assert f"/{DOMAIN}?action=silence_allowlisted_login" in message
    assert NOTIFICATION_ID_BAN not in notifications


@pytest.mark.asyncio
async def test_silence_allowlisted_login_notifications_view(
    hass: HomeAssistant,
) -> None:
    """Test an admin POST can globally silence allowlisted login notifications."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    persistent_notification.async_create(
        hass,
        "Allowlisted login failed",
        "IP Ban Manager",
        NOTIFICATION_ID_LOGIN,
    )
    notifications = persistent_notification._async_get_or_create_notifications(hass)
    assert NOTIFICATION_ID_LOGIN in notifications

    response = await SilenceAllowlistedLoginNotificationsView().post(
        cast(Any, MockViewRequest(hass.http.app))
    )

    assert response.status == 204
    assert entry.options[CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED] is False
    assert NOTIFICATION_ID_LOGIN not in notifications


@pytest.mark.asyncio
async def test_silence_allowlisted_login_notifications_view_requires_admin(
    hass: HomeAssistant,
) -> None:
    """Test the notification silence endpoint requires an admin user."""
    await setup_ip_ban_manager(hass)

    response = await SilenceAllowlistedLoginNotificationsView().post(
        cast(Any, MockViewRequest(hass.http.app, user=MockNonAdminUser()))
    )

    assert response.status == 403


@pytest.mark.asyncio
async def test_silence_allowlisted_login_notifications_view_rejects_get(
    hass: HomeAssistant,
) -> None:
    """Test GET cannot change silence state (CSRF-safe POST-only endpoint)."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    response = await SilenceAllowlistedLoginNotificationsView().get(
        cast(
            Any,
            MockViewRequest(
                hass.http.app,
                query={ATTR_IP_ADDRESS: "192.168.1.1"},
            ),
        )
    )

    assert response.status == 405
    assert entry.options.get(CONF_SILENCED_ALLOWLISTED_LOGIN_IPS) in (None, [])
    assert _entry_allowlisted_login_notifications_enabled(entry) is True


@pytest.mark.asyncio
async def test_silence_allowlisted_login_notifications_view_rejects_unauthenticated(
    hass: HomeAssistant,
) -> None:
    """Test the silence endpoint rejects requests without a Home Assistant user."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    response = await SilenceAllowlistedLoginNotificationsView().post(
        cast(
            Any,
            MockViewRequest(
                hass.http.app,
                has_user=False,
                data={ATTR_IP_ADDRESS: "192.168.1.1"},
            ),
        )
    )

    assert response.status == 403
    assert entry.options.get(CONF_SILENCED_ALLOWLISTED_LOGIN_IPS) in (None, [])


def test_silence_allowlisted_login_notifications_view_requires_auth() -> None:
    """Test the silence endpoint requires Home Assistant authentication."""
    assert SilenceAllowlistedLoginNotificationsView.requires_auth is True


@pytest.mark.asyncio
async def test_silence_allowlisted_login_notifications_view_dismisses_generated_notice(
    hass: HomeAssistant,
) -> None:
    """Test the generated notification action dismisses the visible notification."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    remote_addr = ip_address("192.168.1.1")
    hass.http.app[KEY_LOGIN_THRESHOLD] = 5

    class MockRequest:
        remote = "192.168.1.1"
        app = hass.http.app
        headers: dict[str, str] = {}
        rel_url = "/auth/login_flow/test"

    await http_ban.process_wrong_login(cast(Any, MockRequest()))

    notifications = persistent_notification._async_get_or_create_notifications(hass)
    assert NOTIFICATION_ID_LOGIN in notifications
    message = notifications[NOTIFICATION_ID_LOGIN]["message"]
    assert ALLOWLISTED_LOGIN_SILENCE_LABEL in message
    assert f"/{DOMAIN}?action=silence_allowlisted_login" in message
    assert "&ip_address=192.168.1.1" in message
    assert f"&{ATTR_NOTIFICATION_ID}={NOTIFICATION_ID_LOGIN}" in message
    assert ALLOWLISTED_LOGIN_SILENCE_URL not in message
    assert "&token=" not in message

    response = await SilenceAllowlistedLoginNotificationsView().post(
        cast(
            Any,
            MockViewRequest(
                hass.http.app,
                data={
                    ATTR_IP_ADDRESS: str(remote_addr),
                    ATTR_NOTIFICATION_ID: NOTIFICATION_ID_LOGIN,
                },
            ),
        )
    )

    assert response.status == 204
    assert entry.options[CONF_SILENCED_ALLOWLISTED_LOGIN_IPS] == [str(remote_addr)]
    assert NOTIFICATION_ID_LOGIN not in notifications


@pytest.mark.asyncio
async def test_silence_allowlisted_login_notifications_view_can_silence_address(
    hass: HomeAssistant,
) -> None:
    """Test an admin POST can silence one allowlisted address."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.http.app[KEY_LOGIN_THRESHOLD] = 5

    persistent_notification.async_create(
        hass,
        "Allowlisted login failed",
        "IP Ban Manager",
        NOTIFICATION_ID_LOGIN,
    )
    notifications = persistent_notification._async_get_or_create_notifications(hass)
    assert NOTIFICATION_ID_LOGIN in notifications

    response = await SilenceAllowlistedLoginNotificationsView().post(
        cast(
            Any,
            MockViewRequest(
                hass.http.app,
                data={ATTR_IP_ADDRESS: "192.168.1.1"},
            ),
        )
    )

    assert response.status == 204
    assert entry.options[CONF_SILENCED_ALLOWLISTED_LOGIN_IPS] == ["192.168.1.1"]
    assert NOTIFICATION_ID_LOGIN not in notifications

    class SilencedLoginRequest:
        remote = "192.168.1.1"
        app = hass.http.app
        headers: dict[str, str] = {}
        rel_url = "/auth/login_flow/test"

    await http_ban.process_wrong_login(cast(Any, SilencedLoginRequest()))
    assert NOTIFICATION_ID_LOGIN not in notifications

    class OtherLoginRequest:
        remote = "172.17.0.5"
        app = hass.http.app
        headers: dict[str, str] = {}
        rel_url = "/auth/login_flow/test"

    await http_ban.process_wrong_login(cast(Any, OtherLoginRequest()))
    assert NOTIFICATION_ID_LOGIN in notifications
    assert "172.17.0.5" in notifications[NOTIFICATION_ID_LOGIN]["message"]


@pytest.mark.asyncio
async def test_silence_allowlisted_login_notifications_view_dismisses_matching_notice(
    hass: HomeAssistant,
) -> None:
    """Test per-address silence dismisses matching rewritten notifications."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    persistent_notification.async_create(
        hass,
        (
            "Allowlisted login failed\n\n"
            "192.168.1.1 is allowlisted.\n\n"
            f"[{ALLOWLISTED_LOGIN_SILENCE_LABEL}]"
            f"(/{DOMAIN}?action=silence_allowlisted_login"
            "&ip_address=192.168.1.1"
            "&notification_id=ip_ban_manager_custom_allowlisted_login)"
        ),
        " ",
        "ip_ban_manager_custom_allowlisted_login",
    )
    notifications = persistent_notification._async_get_or_create_notifications(hass)
    assert "ip_ban_manager_custom_allowlisted_login" in notifications

    response = await SilenceAllowlistedLoginNotificationsView().post(
        cast(
            Any,
            MockViewRequest(
                hass.http.app,
                data={ATTR_IP_ADDRESS: "192.168.1.1"},
            ),
        )
    )

    assert response.status == 204
    assert entry.options[CONF_SILENCED_ALLOWLISTED_LOGIN_IPS] == ["192.168.1.1"]
    assert "ip_ban_manager_custom_allowlisted_login" not in notifications


@pytest.mark.asyncio
async def test_silence_allowlisted_login_notifications_preserves_order(
    hass: HomeAssistant,
) -> None:
    """Test per-address silence appends without reordering saved addresses."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_SILENCED_ALLOWLISTED_LOGIN_IPS: [
                "192.168.1.2",
                "192.168.1.1",
            ]
        },
    )

    response = await SilenceAllowlistedLoginNotificationsView().post(
        cast(
            Any,
            MockViewRequest(
                hass.http.app,
                data={ATTR_IP_ADDRESS: "192.168.1.3"},
            ),
        )
    )

    assert response.status == 204
    assert entry.options[CONF_SILENCED_ALLOWLISTED_LOGIN_IPS] == [
        "192.168.1.2",
        "192.168.1.1",
        "192.168.1.3",
    ]


@pytest.mark.asyncio
async def test_silence_allowlisted_login_notifications_keeps_other_address_notices(
    hass: HomeAssistant,
) -> None:
    """Test per-address silence only dismisses notices for that address."""
    await setup_ip_ban_manager(hass)
    persistent_notification.async_create(
        hass,
        (
            "Allowlisted login failed\n\n"
            "192.168.1.1 is allowlisted.\n\n"
            f"[{ALLOWLISTED_LOGIN_SILENCE_LABEL}]"
            f"(/{DOMAIN}?action=silence_allowlisted_login"
            "&ip_address=192.168.1.1"
            "&notification_id=ip_ban_manager_custom_allowlisted_login_1)"
        ),
        " ",
        "ip_ban_manager_custom_allowlisted_login_1",
    )
    persistent_notification.async_create(
        hass,
        (
            "Allowlisted login failed\n\n"
            "192.168.1.2 is allowlisted.\n\n"
            f"[{ALLOWLISTED_LOGIN_SILENCE_LABEL}]"
            f"(/{DOMAIN}?action=silence_allowlisted_login"
            "&ip_address=192.168.1.2"
            "&notification_id=ip_ban_manager_custom_allowlisted_login_2)"
        ),
        " ",
        "ip_ban_manager_custom_allowlisted_login_2",
    )

    response = await SilenceAllowlistedLoginNotificationsView().post(
        cast(
            Any,
            MockViewRequest(
                hass.http.app,
                data={ATTR_IP_ADDRESS: "192.168.1.1"},
            ),
        )
    )

    notifications = persistent_notification._async_get_or_create_notifications(hass)
    assert response.status == 204
    assert "ip_ban_manager_custom_allowlisted_login_1" not in notifications
    assert "ip_ban_manager_custom_allowlisted_login_2" in notifications


@pytest.mark.asyncio
async def test_silence_allowlisted_login_notifications_matches_encoded_action_url(
    hass: HomeAssistant,
) -> None:
    """Test per-address silence dismisses notices matched by action URL."""
    await setup_ip_ban_manager(hass)
    persistent_notification.async_create(
        hass,
        (
            "## IP Ban Manager\n\n"
            "**Allowlisted login failed**\n\n"
            "A trusted source failed authentication.\n\n"
            f"[{ALLOWLISTED_LOGIN_SILENCE_LABEL}]"
            f"(/{DOMAIN}?action=silence_allowlisted_login"
            "&ip_address=%3A%3A1"
            "&notification_id=ip_ban_manager_encoded_allowlisted_login)"
        ),
        " ",
        "ip_ban_manager_encoded_allowlisted_login",
    )

    response = await SilenceAllowlistedLoginNotificationsView().post(
        cast(
            Any,
            MockViewRequest(
                hass.http.app,
                data={ATTR_IP_ADDRESS: "::1"},
            ),
        )
    )

    notifications = persistent_notification._async_get_or_create_notifications(hass)
    assert response.status == 204
    assert "ip_ban_manager_encoded_allowlisted_login" not in notifications


@pytest.mark.asyncio
async def test_allowlisted_notification_includes_geoip_location(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test allowlisted notifications include local GeoIP location details."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(entry, options={CONF_GEOIP_ENABLED: True})
    monkeypatch.setattr(
        ban_notifications,
        "geoip_location_for_ip",
        lambda _hass, remote_addr: (
            "Mountain View, United States" if str(remote_addr) == "8.8.8.8" else None
        ),
    )

    _create_allowlisted_login_notification(
        hass,
        ip_address("8.8.8.8"),
        (
            "Login attempt or request with invalid authentication from "
            "dns.google (8.8.8.8)."
        ),
    )

    notifications = persistent_notification._async_get_or_create_notifications(
        hass
    )  # noqa: SLF001
    message = notifications[NOTIFICATION_ID_LOGIN]["message"]
    assert "Location: Mountain View, United States" in message
    assert "<small><sub>IP geolocation by DB-IP.com</sub></small>" in message


@pytest.mark.asyncio
async def test_setup_entry_rewrites_existing_http_notifications(
    hass: HomeAssistant,
) -> None:
    """Test stale Home Assistant HTTP notifications are normalized on startup."""
    persistent_notification.async_create(
        hass,
        "Login attempt or request with invalid authentication from host (10.0.0.1).",
        "IP Ban Manager",
        NOTIFICATION_ID_LOGIN,
    )

    await setup_ip_ban_manager(hass)

    notifications = persistent_notification._async_get_or_create_notifications(
        hass
    )  # noqa: SLF001
    message = notifications[NOTIFICATION_ID_LOGIN]["message"]
    assert notifications[NOTIFICATION_ID_LOGIN]["title"] == " "
    assert message.startswith("## <img ")
    assert message.count(NOTIFICATION_ICON_DATA_URL) == 1
    assert "**Login attempt failed**" in message
    assert ALLOWLISTED_LOGIN_SILENCE_LABEL not in message
    assert f"/{DOMAIN}?action=silence_allowlisted_login" not in message
    assert message.endswith(f"[Open settings](/{DOMAIN})")
    assert "/config/integrations/" not in message
    assert "IP Ban Manager icon" not in message
    assert "from host (10.0.0.1)." not in message
    assert "from 10.0.0.1." in message
    assert "Reverse DNS: host" in message


@pytest.mark.asyncio
async def test_setup_entry_adds_geoip_to_rewritten_login_notification(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test rewritten Home Assistant login notifications include GeoIP details."""
    monkeypatch.setattr(
        ban_notifications,
        "geoip_location_for_ip",
        lambda _hass, remote_addr: (
            "Mountain View, United States" if str(remote_addr) == "8.8.8.8" else None
        ),
    )
    persistent_notification.async_create(
        hass,
        "Login attempt or request with invalid authentication from dns.google (8.8.8.8).",
        "Login attempt failed",
        NOTIFICATION_ID_LOGIN,
    )

    await setup_ip_ban_manager(hass)

    notifications = persistent_notification._async_get_or_create_notifications(
        hass
    )  # noqa: SLF001
    message = notifications[NOTIFICATION_ID_LOGIN]["message"]
    assert "Location: Mountain View, United States" in message
    assert "from dns.google (8.8.8.8)." not in message
    assert "from 8.8.8.8." in message
    assert "Reverse DNS: dns.google" in message
    assert "<small><sub>IP geolocation by DB-IP.com</sub></small>" in message
    assert ALLOWLISTED_LOGIN_SILENCE_LABEL not in message


@pytest.mark.asyncio
async def test_setup_entry_removes_wrong_public_login_silence_link(
    hass: HomeAssistant,
) -> None:
    """Test stale public login notifications do not keep allowlisted actions."""
    persistent_notification.async_create(
        hass,
        (
            "## IP Ban Manager\n\n"
            "**Login attempt failed**\n\n"
            "Login attempt or request with invalid authentication from "
            "dns.google (8.8.8.8). See the log for details.\n\n"
            "[Don't show for this address again]"
            "(/ip_ban_manager?action=silence_allowlisted_login"
            "&ip_address=8.8.8.8&notification_id=http-login)"
        ),
        " ",
        NOTIFICATION_ID_LOGIN,
    )

    await setup_ip_ban_manager(hass)

    notifications = persistent_notification._async_get_or_create_notifications(
        hass
    )  # noqa: SLF001
    message = notifications[NOTIFICATION_ID_LOGIN]["message"]
    assert "**Login attempt failed**" in message
    assert "from dns.google (8.8.8.8)." not in message
    assert "from 8.8.8.8." in message
    assert "Reverse DNS: dns.google" in message
    assert ALLOWLISTED_LOGIN_SILENCE_LABEL not in message
    assert f"/{DOMAIN}?action=silence_allowlisted_login" not in message
    assert message.endswith(f"[Open settings](/{DOMAIN})")


@pytest.mark.asyncio
async def test_setup_entry_rewrites_stale_allowlisted_notification_action(
    hass: HomeAssistant,
) -> None:
    """Test stale allowlisted notifications get the per-address silence action."""
    persistent_notification.async_create(
        hass,
        (
            "## IP Ban Manager\n\n"
            "**Allowlisted login failed**\n\n"
            "Login attempt or request with invalid authentication from "
            "192.168.1.1 (192.168.1.1). See the log for details.\n\n"
            "Current failed-login count: 2/3. 192.168.1.1 is allowlisted, "
            "so it will not be banned.\n\n"
            "[Allowlisted login notifications](/config/integrations/"
            "integration/ip_ban_manager)"
        ),
        "IP Ban Manager",
        NOTIFICATION_ID_LOGIN,
    )

    await setup_ip_ban_manager(hass)

    notifications = persistent_notification._async_get_or_create_notifications(
        hass
    )  # noqa: SLF001
    message = notifications[NOTIFICATION_ID_LOGIN]["message"]
    assert "Allowlisted login notifications" not in message
    assert ALLOWLISTED_LOGIN_SILENCE_LABEL in message
    assert f"/{DOMAIN}?action=silence_allowlisted_login" in message
    assert "&ip_address=192.168.1.1" in message
    assert f"&{ATTR_NOTIFICATION_ID}={NOTIFICATION_ID_LOGIN}" in message
    assert "&token=" not in message


@pytest.mark.asyncio
async def test_setup_entry_rewrites_stale_allowlisted_ipv6_notification_action(
    hass: HomeAssistant,
) -> None:
    """Test stale IPv6 allowlisted notifications get the silence action."""
    persistent_notification.async_create(
        hass,
        (
            "## IP Ban Manager\n\n"
            "**Allowlisted login failed**\n\n"
            "Login attempt or request with invalid authentication from "
            "localhost (::ffff:172.17.0.5). See the log for details.\n\n"
            "Current failed-login count: 2/3. ::ffff:172.17.0.5 is allowlisted, "
            "so it will not be banned.\n\n"
            "[Allowlisted login notifications](/config/integrations/"
            "integration/ip_ban_manager)"
        ),
        "IP Ban Manager",
        NOTIFICATION_ID_LOGIN,
    )

    await setup_ip_ban_manager(hass)

    notifications = persistent_notification._async_get_or_create_notifications(
        hass
    )  # noqa: SLF001
    message = notifications[NOTIFICATION_ID_LOGIN]["message"]
    assert "Allowlisted login notifications" not in message
    assert ALLOWLISTED_LOGIN_SILENCE_LABEL in message
    assert f"/{DOMAIN}?action=silence_allowlisted_login" in message
    assert "&ip_address=172.17.0.5" in message
    assert f"&{ATTR_NOTIFICATION_ID}={NOTIFICATION_ID_LOGIN}" in message
    assert "&token=" not in message


@pytest.mark.asyncio
async def test_http_notifications_use_integration_url_when_panel_not_loaded(
    hass: HomeAssistant,
) -> None:
    """Test HTTP notifications fall back to the integration page without a live panel."""
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

    _add_manager_links_to_http_notifications(hass)
    _add_manager_links_to_http_notifications(hass)

    notifications = persistent_notification._async_get_or_create_notifications(
        hass
    )  # noqa: SLF001
    assert notifications[NOTIFICATION_ID_BAN]["title"] == " "
    assert notifications[NOTIFICATION_ID_LOGIN]["title"] == " "
    assert "IP banned" in notifications[NOTIFICATION_ID_BAN]["message"]
    assert "Login attempt failed" in notifications[NOTIFICATION_ID_LOGIN]["message"]
    for notification_id in (NOTIFICATION_ID_BAN, NOTIFICATION_ID_LOGIN):
        message = notifications[notification_id]["message"]
        assert message.endswith(f"[Open settings]({INTEGRATION_CONFIG_URL})")
        assert f"[Open settings](/{DOMAIN})" not in message
        assert message.count(INTEGRATION_CONFIG_URL) == 1
        assert message.count(NOTIFICATION_ICON_DATA_URL) == 1
        assert message.startswith("## <img ")
        assert "/api/ip_ban_manager/icon.png" not in message
        assert "IP Ban Manager icon" not in message
        assert "Open integrations" not in message


@pytest.mark.asyncio
async def test_http_notifications_link_directly_to_live_panel(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test Home Assistant HTTP notifications link to the live panel."""
    await setup_ip_ban_manager(hass)
    persistent_notification.async_create(
        hass,
        "Too many login attempts from 10.0.0.1",
        "Banning IP address",
        NOTIFICATION_ID_BAN,
    )

    _add_manager_links_to_http_notifications(hass)
    check_records(caplog.records)

    notifications = persistent_notification._async_get_or_create_notifications(
        hass
    )  # noqa: SLF001
    assert notifications[NOTIFICATION_ID_BAN]["title"] == " "
    assert "IP banned" in notifications[NOTIFICATION_ID_BAN]["message"]
    message = notifications[NOTIFICATION_ID_BAN]["message"]
    assert message.endswith(f"[Open settings](/{DOMAIN})")
    assert "/config/integrations/" not in message
    assert "Open integrations" not in message


@pytest.mark.asyncio
async def test_http_notification_rewrites_old_brand_header(
    hass: HomeAssistant,
) -> None:
    """Test old or broken branded headers are normalized to the current format."""
    persistent_notification.async_create(
        hass,
        (
            '## <img src="/api/ip_ban_manager/icon.png" width="28" height="28" '
            'alt="IP Ban Manager icon">&nbsp;&nbsp;IP Ban Manager\n\n'
            "Too many login attempts from 10.0.0.1"
        ),
        "Banning IP address",
        NOTIFICATION_ID_BAN,
    )

    _add_manager_links_to_http_notifications(hass)

    notifications = persistent_notification._async_get_or_create_notifications(
        hass
    )  # noqa: SLF001
    message = notifications[NOTIFICATION_ID_BAN]["message"]
    assert notifications[NOTIFICATION_ID_BAN]["title"] == " "
    assert message.startswith("## <img ")
    assert message.count("IP Ban Manager") == 1
    assert message.count(NOTIFICATION_ICON_DATA_URL) == 1
    assert "/api/ip_ban_manager/icon.png" not in message
    assert "IP Ban Manager icon" not in message
    assert "Too many login attempts from 10.0.0.1" in message


@pytest.mark.asyncio
async def test_remove_ip_ban_dismisses_matching_notifications(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test removing one ban dismisses stale notifications for that IP."""
    await setup_ip_ban_manager(hass)
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
    await setup_ip_ban_manager(hass)
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


def test_geoip_location_includes_subdivision() -> None:
    """Test GeoIP locations prefer province/state short codes when available."""
    assert (
        geoip_location_from_result(
            {
                "city": {"names": {"en": "St. John's"}},
                "subdivisions": [
                    {"names": {"en": "Newfoundland and Labrador"}, "iso_code": "NL"}
                ],
                "country": {"names": {"en": "Canada"}, "iso_code": "CA"},
            }
        )
        == "St. John's, NL, CA"
    )


def test_geoip_location_shortens_known_subdivision_names() -> None:
    """Test GeoIP locations shorten known provinces without subdivision ISO codes."""
    assert (
        geoip_location_from_result(
            {
                "city": {"names": {"en": "Channel-Port aux Basques"}},
                "subdivisions": [{"names": {"en": "Newfoundland and Labrador"}}],
                "country": {"names": {"en": "Canada"}, "iso_code": "CA"},
            }
        )
        == "Channel-Port aux Basques, NL, CA"
    )


def test_geoip_location_keeps_unknown_subdivision_names() -> None:
    """Test GeoIP locations do not guess subdivision codes for other countries."""
    assert (
        geoip_location_from_result(
            {
                "city": {"names": {"en": "Berlin"}},
                "subdivisions": [{"names": {"en": "Berlin"}}],
                "country": {"names": {"en": "Germany"}, "iso_code": "DE"},
            }
        )
        == "Berlin, Berlin, DE"
    )


def test_geoip_location_falls_back_to_country_code() -> None:
    """Test GeoIP locations use ISO country code when country name is missing."""
    assert (
        geoip_location_from_result(
            {
                "subdivisions": [{"names": {"en": "Ontario"}}],
                "country": {"iso_code": "CA"},
            }
        )
        == "Ontario, CA"
    )
