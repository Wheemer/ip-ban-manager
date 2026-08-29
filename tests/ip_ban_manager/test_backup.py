"""Focused tests split out from test_setup."""

# mypy: ignore-errors

# flake8: noqa
# ruff: noqa: F403,F405

from .test_setup import *


@pytest.mark.asyncio
async def test_export_config_service_writes_manual_backup(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test the manual export service writes a readable backup file."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_BLOCKED_NETWORKS: ["203.0.113.0/24"],
            CONF_DEFAULT_DENY_ENABLED: False,
            CONF_SILENCED_ALLOWLISTED_LOGIN_IPS: ["10.0.0.25"],
        },
    )
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    ban_manager.path = str(tmp_path / "ip_bans.yaml")
    ban_manager.ip_bans_lookup[ip_address("198.51.100.7")] = IpBan(
        "198.51.100.7",
        ipbm.dt_util.utcnow(),
    )

    await hass.services.async_call(DOMAIN, SERVICE_EXPORT_CONFIG, {}, blocking=True)
    check_records(caplog.records)

    export_path = Path(hass.config.path(DOMAIN, "ip-ban-manager-backup.yaml"))
    payload = yaml.safe_load(export_path.read_text(encoding="utf8"))

    assert payload["domain"] == DOMAIN
    assert payload["format_version"] == 1
    assert payload["settings"][CONF_IP_ADDRESSES] == ["192.168.1.1", "172.17.0.0/24"]
    assert payload["settings"][CONF_BLOCKED_NETWORKS] == ["203.0.113.0/24"]
    assert payload["settings"][CONF_SILENCED_ALLOWLISTED_LOGIN_IPS] == ["10.0.0.25"]
    assert "198.51.100.7" in payload[ATTR_BANNED_IPS]


@pytest.mark.asyncio
async def test_import_config_service_restores_on_disk_backup(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test the import service restores the on-disk backup file."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    ban_manager.path = str(tmp_path / "ip_bans.yaml")

    await hass.services.async_call(DOMAIN, SERVICE_EXPORT_CONFIG, {}, blocking=True)
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_BLOCKED_NETWORKS: ["203.0.113.0/24"],
            CONF_DEFAULT_DENY_ENABLED: True,
        },
    )
    await hass.services.async_call(DOMAIN, SERVICE_IMPORT_CONFIG, {}, blocking=True)
    check_records(caplog.records)

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.options[CONF_DEFAULT_DENY_ENABLED] is False
    assert entry.options.get(CONF_BLOCKED_NETWORKS, []) == []


@pytest.mark.asyncio
async def test_upload_config_restores_backup_yaml(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test uploaded YAML backup content validates and restores live settings."""
    await setup_ip_ban_manager(hass)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    ban_manager.path = str(tmp_path / "ip_bans.yaml")

    content = yaml.safe_dump(
        {
            "domain": DOMAIN,
            "format_version": 1,
            "settings": {
                CONF_IP_ADDRESSES: ["10.10.0.0/16", "127.0.0.1"],
                CONF_BLOCKED_NETWORKS: ["203.0.113.0/24"],
                CONF_AUTO_BAN_ENABLED: True,
                CONF_BAN_NOTIFICATIONS_ENABLED: False,
                CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED: False,
                CONF_ALLOWLISTED_LOGINS_CAN_BAN: True,
                CONF_DEFAULT_DENY_ENABLED: False,
                CONF_GEOIP_ENABLED: False,
                CONF_LOGIN_ATTEMPTS_THRESHOLD: 7,
                CONF_SIDEBAR_PANEL_ENABLED: False,
                CONF_SILENCED_ALLOWLISTED_LOGIN_IPS: ["10.10.0.5"],
            },
            ATTR_BANNED_IPS: {
                "198.51.100.7": {"banned_at": "2026-01-02T03:04:05+00:00"}
            },
        },
        sort_keys=False,
    )

    await ipbm._async_import_config_from_yaml(hass, content)  # noqa: SLF001
    check_records(caplog.records)

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.options[CONF_IP_ADDRESSES] == ["10.10.0.0/16", "127.0.0.1"]
    assert entry.options[CONF_BLOCKED_NETWORKS] == ["203.0.113.0/24"]
    assert entry.options[CONF_BAN_NOTIFICATIONS_ENABLED] is False
    assert entry.options[CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED] is False
    assert entry.options[CONF_ALLOWLISTED_LOGINS_CAN_BAN] is True
    assert entry.options[CONF_LOGIN_ATTEMPTS_THRESHOLD] == 7
    assert entry.options[CONF_SIDEBAR_PANEL_ENABLED] is False
    assert entry.options[CONF_SILENCED_ALLOWLISTED_LOGIN_IPS] == ["10.10.0.5"]
    assert [str(network) for network in hass.http.app[KEY_ALLOWLIST]] == [
        "10.10.0.0/16",
        "127.0.0.1/32",
    ]
    assert set(ban_manager.ip_bans_lookup) == {ip_address("198.51.100.7")}
    assert (
        ban_manager.ip_bans_lookup[ip_address("198.51.100.7")].banned_at.isoformat()
        == "2026-01-02T03:04:05+00:00"
    )
    assert "198.51.100.7" in Path(ban_manager.path).read_text(encoding="utf8")

    await ipbm._async_import_config_from_yaml(hass, content)  # noqa: SLF001
    check_records(caplog.records)

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.options[CONF_IP_ADDRESSES] == ["10.10.0.0/16", "127.0.0.1"]
    assert set(ban_manager.ip_bans_lookup) == {ip_address("198.51.100.7")}
    assert (
        ban_manager.ip_bans_lookup[ip_address("198.51.100.7")].banned_at.isoformat()
        == "2026-01-02T03:04:05+00:00"
    )


@pytest.mark.asyncio
async def test_download_config_returns_current_yaml_backup(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    """Test panel download returns the current settings as YAML content."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_BLOCKED_NETWORKS: ["203.0.113.0/24"],
            CONF_SILENCED_ALLOWLISTED_LOGIN_IPS: ["10.0.0.25"],
        },
    )
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    ban_manager.path = str(tmp_path / "ip_bans.yaml")
    ban_manager.ip_bans_lookup[ip_address("198.51.100.7")] = IpBan(
        "198.51.100.7",
        ipbm.dt_util.utcnow(),
    )

    response = await IPBanManagerManageView().post(
        cast(
            Any,
            MockViewRequest(hass.http.app, data={"action": "download_config"}),
        )
    )
    assert response.status == 200
    assert response.text is not None
    payload = json.loads(response.text)
    assert payload["ok"] is True
    download = payload["download"]
    assert download["filename"] == "ip-ban-manager-backup.yaml"
    parsed = yaml.safe_load(download["content"])
    assert parsed["domain"] == DOMAIN
    assert parsed["settings"][CONF_BLOCKED_NETWORKS] == ["203.0.113.0/24"]
    assert parsed["settings"][CONF_SILENCED_ALLOWLISTED_LOGIN_IPS] == ["10.0.0.25"]
    assert "198.51.100.7" in parsed[ATTR_BANNED_IPS]


@pytest.mark.asyncio
async def test_upload_config_preserves_exact_bans_when_section_is_missing(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test uploads without a banned_ips section do not clear current exact bans."""
    await setup_ip_ban_manager(hass)
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    ban_manager.path = str(tmp_path / "ip_bans.yaml")
    existing_ban = IpBan("198.51.100.7", ipbm.dt_util.utcnow())
    ban_manager.ip_bans_lookup[existing_ban.ip_address] = existing_ban

    content = yaml.safe_dump(
        {
            "domain": DOMAIN,
            "format_version": 1,
            "settings": {CONF_IP_ADDRESSES: ["10.10.0.0/16"]},
        },
        sort_keys=False,
    )

    await ipbm._async_import_config_from_yaml(hass, content)  # noqa: SLF001
    check_records(caplog.records)

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.options[CONF_IP_ADDRESSES] == ["10.10.0.0/16"]
    assert ban_manager.ip_bans_lookup == {existing_ban.ip_address: existing_ban}


@pytest.mark.asyncio
async def test_upload_config_rejects_unsafe_backup(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test unsafe uploaded backups fail before changing live settings."""
    await setup_ip_ban_manager(hass)
    content = yaml.safe_dump(
        {
            "domain": DOMAIN,
            "format_version": 1,
            "settings": {CONF_IP_ADDRESSES: ["10.0.0.0/24"]},
            ATTR_BANNED_IPS: {"10.0.0.25": {"banned_at": "2026-01-02T03:04:05+00:00"}},
        },
        sort_keys=False,
    )

    with pytest.raises(HomeAssistantError):
        await ipbm._async_import_config_from_yaml(hass, content)  # noqa: SLF001
    check_records(caplog.records)

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.options.get(CONF_IP_ADDRESSES) is None
    assert [str(network) for network in hass.http.app[KEY_ALLOWLIST]] == [
        "192.168.1.1/32",
        "172.17.0.0/24",
    ]


@pytest.mark.asyncio
async def test_upload_config_rejects_invalid_backup_ips(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test invalid hand-edited backup IP values fail cleanly."""
    await setup_ip_ban_manager(hass)
    content = yaml.safe_dump(
        {
            "domain": DOMAIN,
            "format_version": 1,
            "settings": {CONF_SILENCED_ALLOWLISTED_LOGIN_IPS: ["bad-ip"]},
        },
        sort_keys=False,
    )

    with pytest.raises(HomeAssistantError, match="Invalid IP address"):
        await ipbm._async_import_config_from_yaml(hass, content)  # noqa: SLF001
    check_records(caplog.records)

    content = yaml.safe_dump(
        {
            "domain": DOMAIN,
            "format_version": 1,
            ATTR_BANNED_IPS: {"bad-ip": {"banned_at": "2026-01-02T03:04:05+00:00"}},
        },
        sort_keys=False,
    )

    with pytest.raises(HomeAssistantError, match="Invalid IP address"):
        await ipbm._async_import_config_from_yaml(hass, content)  # noqa: SLF001
    check_records(caplog.records)

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.options.get(CONF_IP_ADDRESSES) is None
    assert entry.options.get(CONF_SILENCED_ALLOWLISTED_LOGIN_IPS) is None


@pytest.mark.asyncio
async def test_upload_config_rejects_malformed_backup_values(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test malformed backup values fail before changing live settings."""
    await setup_ip_ban_manager(hass)

    async def assert_rejected(content: str) -> None:
        with pytest.raises(HomeAssistantError):
            await ipbm._async_import_config_from_yaml(hass, content)  # noqa: SLF001
        check_records(caplog.records)
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        assert entry.options.get(CONF_IP_ADDRESSES) is None
        assert entry.options.get(CONF_BLOCKED_NETWORKS) is None
        assert entry.options.get(CONF_AUTO_BAN_ENABLED) is None

    await assert_rejected("settings: [")
    await assert_rejected(
        yaml.safe_dump(
            {
                "domain": DOMAIN,
                "format_version": 1,
                "settings": {CONF_AUTO_BAN_ENABLED: "definitely"},
            },
            sort_keys=False,
        )
    )
    await assert_rejected(
        yaml.safe_dump(
            {
                "domain": DOMAIN,
                "format_version": 1,
                "settings": {CONF_LOGIN_ATTEMPTS_THRESHOLD: "not-a-number"},
            },
            sort_keys=False,
        )
    )
    await assert_rejected(
        yaml.safe_dump(
            {
                "domain": DOMAIN,
                "format_version": 1,
                "settings": {CONF_IP_ADDRESSES: ["0.0.0.0/0"]},
            },
            sort_keys=False,
        )
    )
    await assert_rejected(
        yaml.safe_dump(
            {
                "domain": DOMAIN,
                "format_version": 1,
                "settings": {CONF_BLOCKED_NETWORKS: ["::/0"]},
            },
            sort_keys=False,
        )
    )
