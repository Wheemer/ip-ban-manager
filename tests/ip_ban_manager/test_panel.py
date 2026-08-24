"""Focused tests split out from test_setup."""

# mypy: ignore-errors

# flake8: noqa
# ruff: noqa: F403,F405

from .test_setup import *


@pytest.mark.asyncio
async def test_setup_entry_can_skip_sidebar_panel(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test setup can register the configure panel without a sidebar entry."""
    registered_sidebar_enabled: bool | None = None

    async def mock_register_panel(
        hass: HomeAssistant, *, sidebar_enabled: bool = True
    ) -> None:
        nonlocal registered_sidebar_enabled
        registered_sidebar_enabled = sidebar_enabled
        hass.data[KEY_PANEL_REGISTERED] = True
        hass.data[KEY_PANEL_SIDEBAR_ENABLED] = sidebar_enabled

    monkeypatch.setattr(ipbm, "_async_register_panel", mock_register_panel)
    monkeypatch.setattr(ban_panel, "async_register_panel", mock_register_panel)
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        data={
            CONF_IP_ADDRESSES: ["127.0.0.1"],
            CONF_SIDEBAR_PANEL_ENABLED: False,
        },
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    check_records(caplog.records)

    assert registered_sidebar_enabled is False
    assert hass.data[KEY_PANEL_REGISTERED] is True
    assert hass.data[KEY_PANEL_SIDEBAR_ENABLED] is False


@pytest.mark.asyncio
async def test_panel_options_can_disable_sidebar_panel(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test the panel API can hide the sidebar entry without removing Configure."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    registered_sidebar_enabled: bool | None = None

    async def mock_register_panel(
        hass: HomeAssistant, *, sidebar_enabled: bool = True
    ) -> None:
        nonlocal registered_sidebar_enabled
        registered_sidebar_enabled = sidebar_enabled
        hass.data[KEY_PANEL_REGISTERED] = True
        hass.data[KEY_PANEL_SIDEBAR_ENABLED] = sidebar_enabled

    monkeypatch.setattr(ipbm, "_async_register_panel", mock_register_panel)
    monkeypatch.setattr(ban_panel, "async_register_panel", mock_register_panel)

    await _async_panel_set_options(
        hass,
        {
            CONF_SIDEBAR_PANEL_ENABLED: False,
        },
    )

    assert registered_sidebar_enabled is False
    assert entry.options[CONF_SIDEBAR_PANEL_ENABLED] is False
    assert hass.data[KEY_PANEL_REGISTERED] is True
    assert hass.data[KEY_PANEL_SIDEBAR_ENABLED] is False


@pytest.mark.asyncio
async def test_panel_options_can_enable_default_deny_with_supervisor_network(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test default-deny ignores Supervisor internals when checking lockout safety."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    ipbm._update_allowlist_entry(hass, ["192.168.1.0/24"])

    async def detected_with_supervisor_network(hass: HomeAssistant) -> list[str]:
        return ["192.168.1.0/24", "172.30.32.0/23"]

    monkeypatch.setattr(
        ban_config_flow,
        "_async_detect_home_assistant_subnets",
        detected_with_supervisor_network,
    )

    await _async_panel_set_options(
        hass,
        {
            CONF_DEFAULT_DENY_ENABLED: True,
        },
    )

    assert entry.options[CONF_DEFAULT_DENY_ENABLED] is True


@pytest.mark.asyncio
async def test_panel_registration_requires_admin(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test the bundled panel is only available to administrators."""
    registered: dict[str, object] = {}
    removed: list[str] = []

    async def mock_register_panel(hass: HomeAssistant, **kwargs: object) -> None:
        registered.update(kwargs)

    def mock_remove_panel(hass: HomeAssistant, panel_id: str, **kwargs: object) -> None:
        removed.append(panel_id)

    monkeypatch.setattr(
        "homeassistant.components.panel_custom.async_register_panel",
        mock_register_panel,
    )
    monkeypatch.setattr(
        "homeassistant.components.frontend.async_remove_panel",
        mock_remove_panel,
    )

    await _async_register_panel(hass)

    assert removed == [DOMAIN]
    assert registered["frontend_url_path"] == DOMAIN
    assert registered["config_panel_domain"] == DOMAIN
    assert registered["require_admin"] is True
    assert registered["webcomponent_name"] == "ip-ban-manager-panel"
    module_url = cast(str, registered["module_url"])
    version = ban_panel_assets.integration_version()
    assert module_url.startswith(f"/api/{DOMAIN}/panel.js?v={version}&t=")


@pytest.mark.asyncio
async def test_panel_script_url_serves_current_bundle(hass: HomeAssistant) -> None:
    """Test panel.js serves the bundled script with the installed version."""
    await setup_ip_ban_manager(hass)
    request = cast(Any, MockViewRequest(hass.http.app))

    response = await IPBanManagerPanelView().get(request)
    assert response.status == 200
    assert response.text is not None
    assert (
        f'const PANEL_VERSION = "{ban_panel_assets.integration_version()}"'
        in response.text
    )
    assert 'customElements.define("ip-ban-manager-panel", IPBanManagerPanel)' in (
        response.text
    )
    assert "ip-ban-manager-panel-v27" not in response.text


@pytest.mark.asyncio
async def test_panel_options_clamp_login_threshold(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test direct panel/API writes cannot bypass threshold limits."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    await _async_panel_set_options(hass, {CONF_LOGIN_ATTEMPTS_THRESHOLD: 999})
    check_records(caplog.records)

    assert entry.options[CONF_LOGIN_ATTEMPTS_THRESHOLD] == 100
    assert hass.http.app[KEY_LOGIN_THRESHOLD] == 100

    await _async_panel_set_options(hass, {CONF_LOGIN_ATTEMPTS_THRESHOLD: -10})
    check_records(caplog.records)

    assert entry.options[CONF_LOGIN_ATTEMPTS_THRESHOLD] == 0
    assert hass.http.app[KEY_LOGIN_THRESHOLD] == 0


@pytest.mark.asyncio
async def test_status_view_requires_admin(hass: HomeAssistant) -> None:
    """Test the panel status endpoint requires an admin user."""
    await setup_ip_ban_manager(hass)

    response = await IPBanManagerStatusView().get(
        cast(Any, MockViewRequest(hass.http.app, user=MockNonAdminUser()))
    )

    assert response.status == 403


@pytest.mark.asyncio
async def test_status_view_returns_state_for_admin(hass: HomeAssistant) -> None:
    """Test the panel status endpoint returns live state for an admin user."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(
        entry, options={CONF_SILENCED_ALLOWLISTED_LOGIN_IPS: ["192.168.1.1"]}
    )

    response = await IPBanManagerStatusView().get(
        cast(Any, MockViewRequest(hass.http.app))
    )

    assert response.text is not None
    data = json.loads(response.text)
    assert response.status == 200
    assert data["ok"] is True
    assert data["version"] == ban_panel_assets.integration_version()
    assert data["status"][ATTR_HEALTH]["ok"] is True
    assert data["status"][ATTR_HEALTH][ATTR_HEALTH_ISSUES] == []
    assert data["status"][ATTR_METRICS]["panel_api_calls"] == 1
    assert data["settings"][CONF_SILENCED_ALLOWLISTED_LOGIN_IPS] == ["192.168.1.1"]


@pytest.mark.asyncio
async def test_status_view_reports_health_issue_for_panel_registration(
    hass: HomeAssistant,
) -> None:
    """Test the status payload exposes actionable health issues."""
    await setup_ip_ban_manager(hass)
    hass.data.pop(KEY_PANEL_REGISTERED)

    await _async_update_health_issue(hass)
    status = current_status(hass)
    health = cast(dict[str, Any], status[ATTR_HEALTH])

    assert health["ok"] is False
    issues = cast(list[dict[str, Any]], health[ATTR_HEALTH_ISSUES])
    assert any(issue.get("key") == "panel_not_registered" for issue in issues)


@pytest.mark.asyncio
async def test_manage_view_requires_admin_for_notification_silence(
    hass: HomeAssistant,
) -> None:
    """Test the panel action endpoint rejects non-admin notification actions."""
    await setup_ip_ban_manager(hass)

    response = await IPBanManagerManageView().post(
        cast(
            Any,
            MockViewRequest(
                hass.http.app,
                user=MockNonAdminUser(),
                data={
                    "action": "silence_allowlisted_login",
                    "value": "192.168.1.1",
                },
            ),
        )
    )

    assert response.status == 403


@pytest.mark.asyncio
async def test_manage_view_returns_structured_error(
    hass: HomeAssistant,
) -> None:
    """Test panel API errors are machine readable."""
    await setup_ip_ban_manager(hass)

    response = await IPBanManagerManageView().post(
        cast(
            Any,
            MockViewRequest(
                hass.http.app,
                data={"action": "does_not_exist"},
            ),
        )
    )

    assert response.status == 400
    assert response.text is not None
    data = json.loads(response.text)
    assert data["ok"] is False
    assert data["error"] == "Unknown action."
    metrics = cast(dict[str, Any], current_status(hass)[ATTR_METRICS])
    assert metrics["panel_api_errors"] == 1


@pytest.mark.asyncio
async def test_manage_view_skips_unchanged_option_write(
    hass: HomeAssistant,
) -> None:
    """Test repeated option saves do not churn config storage."""
    await setup_ip_ban_manager(hass)

    status_response = await IPBanManagerStatusView().get(
        cast(Any, MockViewRequest(hass.http.app))
    )
    assert status_response.text is not None
    settings = json.loads(status_response.text)["settings"]

    request = MockViewRequest(
        hass.http.app,
        data={"action": "set_options", "options": settings},
    )
    response = await IPBanManagerManageView().post(cast(Any, request))
    assert response.status == 200
    writes_after_first_save = cast(dict[str, Any], current_status(hass)[ATTR_METRICS])[
        "config_writes"
    ]

    response = await IPBanManagerManageView().post(cast(Any, request))

    assert response.status == 200
    metrics = cast(dict[str, Any], current_status(hass)[ATTR_METRICS])
    assert metrics["config_writes"] == writes_after_first_save


@pytest.mark.asyncio
async def test_manage_view_can_silence_allowlisted_login_address(
    hass: HomeAssistant,
) -> None:
    """Test the panel action can silence one address and dismiss its notice."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    persistent_notification.async_create(
        hass,
        "Allowlisted login failed\n\n192.168.1.1 is allowlisted.",
        " ",
        NOTIFICATION_ID_LOGIN,
    )
    notifications = persistent_notification._async_get_or_create_notifications(hass)
    assert NOTIFICATION_ID_LOGIN in notifications

    response = await IPBanManagerManageView().post(
        cast(
            Any,
            MockViewRequest(
                hass.http.app,
                data={
                    "action": "silence_allowlisted_login",
                    "value": "192.168.1.1",
                    ATTR_NOTIFICATION_ID: NOTIFICATION_ID_LOGIN,
                },
            ),
        )
    )

    assert response.status == 200
    assert response.text is not None
    data = json.loads(response.text)
    assert data["ok"] is True
    assert data["status"][ATTR_HEALTH]["ok"] is True
    assert data["status"][ATTR_HEALTH][ATTR_HEALTH_ISSUES] == []
    assert data["settings"][CONF_SILENCED_ALLOWLISTED_LOGIN_IPS] == ["192.168.1.1"]
    assert entry.options[CONF_SILENCED_ALLOWLISTED_LOGIN_IPS] == ["192.168.1.1"]
    assert NOTIFICATION_ID_LOGIN not in notifications
    writes_after_first_silence = cast(
        dict[str, Any], current_status(hass)[ATTR_METRICS]
    )["config_writes"]

    persistent_notification.async_create(
        hass,
        "Allowlisted login failed\n\n192.168.1.1 is allowlisted.",
        " ",
        NOTIFICATION_ID_LOGIN,
    )

    response = await IPBanManagerManageView().post(
        cast(
            Any,
            MockViewRequest(
                hass.http.app,
                data={
                    "action": "silence_allowlisted_login",
                    "value": "192.168.1.1",
                    ATTR_NOTIFICATION_ID: NOTIFICATION_ID_LOGIN,
                },
            ),
        )
    )

    assert response.status == 200
    assert NOTIFICATION_ID_LOGIN not in notifications
    assert (
        cast(dict[str, Any], current_status(hass)[ATTR_METRICS])["config_writes"]
        == writes_after_first_silence
    )


@pytest.mark.asyncio
async def test_manage_view_can_unsilence_allowlisted_login_address(
    hass: HomeAssistant,
) -> None:
    """Test the panel API can remove one silenced allowlisted-login address."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_SILENCED_ALLOWLISTED_LOGIN_IPS: [
                "192.168.1.1",
                "192.168.1.2",
            ]
        },
    )

    response = await IPBanManagerManageView().post(
        cast(
            Any,
            MockViewRequest(
                hass.http.app,
                data={
                    "action": "unsilence_allowlisted_login",
                    "value": "192.168.1.1",
                },
            ),
        )
    )

    assert response.status == 200
    assert entry.options[CONF_SILENCED_ALLOWLISTED_LOGIN_IPS] == ["192.168.1.2"]


@pytest.mark.asyncio
async def test_status_view_returns_geoip_state_for_admin(
    hass: HomeAssistant,
) -> None:
    """Test the panel status endpoint exposes local GeoIP state."""
    await setup_ip_ban_manager(hass)

    status = current_status(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    assert status[ATTR_GEOIP_ENABLED] is False
    assert status[ATTR_GEOIP_DATABASE_PRESENT] is False
    assert entry.options.get(CONF_GEOIP_ENABLED) is None
    assert not Path(hass.config.path(DOMAIN, "geoip", "dbip-city-lite.mmdb")).exists()


@pytest.mark.asyncio
async def test_panel_enabling_geoip_downloads_database(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test enabling GeoIP through the panel downloads the local database."""
    await setup_ip_ban_manager(hass)
    downloaded = False

    async def mock_download_geoip_database(mock_hass: HomeAssistant) -> None:
        nonlocal downloaded
        downloaded = mock_hass is hass

    monkeypatch.setattr(
        ban_panel, "async_download_geoip_database", mock_download_geoip_database
    )

    await _async_panel_set_options(hass, {CONF_GEOIP_ENABLED: True})

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert downloaded
    assert entry.options[CONF_GEOIP_ENABLED] is True


@pytest.mark.asyncio
async def test_panel_allowlist_add_fires_panel_sourced_event(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test panel allowlist mutations report source panel in automation events."""
    captured: list[dict[str, Any]] = []

    @callback
    def capture_event(event) -> None:
        captured.append(dict(event.data))

    remove = hass.bus.async_listen(EVENT_ALLOWLIST_NETWORK_ADDED, capture_event)

    await setup_ip_ban_manager(hass)
    response = await IPBanManagerManageView().post(
        cast(
            Any,
            MockViewRequest(
                hass.http.app,
                data={"action": "add_allowlist", "value": "203.0.113.0/24"},
            ),
        )
    )
    check_records(caplog.records)
    remove()

    assert response.status == 200
    assert captured == [
        {ATTR_NETWORK: "203.0.113.0/24", ATTR_SOURCE: SOURCE_PANEL},
    ]
