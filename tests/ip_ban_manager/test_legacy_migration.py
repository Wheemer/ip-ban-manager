"""Focused tests split out from test_setup."""

# mypy: ignore-errors

# flake8: noqa
# ruff: noqa: F403,F405

from .test_setup import *


def test_cleanup_destination_does_not_overwrite_existing_path(tmp_path: Path) -> None:
    """Test cleanup destinations stay unique when a timestamp collides."""
    cleanup_root = tmp_path / ".cleanup"
    cleanup_root.mkdir()
    (cleanup_root / "ban_allowlist-20260629-120000").mkdir()

    assert _cleanup_destination(cleanup_root, "ban_allowlist", "20260629-120000") == (
        cleanup_root / "ban_allowlist-20260629-120000-2"
    )


@pytest.mark.asyncio
async def test_yaml_import(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test YAML configuration is imported into a config entry."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
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
    assert entries[0].data == expected_yaml_import_data(
        ["192.168.1.1", "172.17.0.0/24"]
    )
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
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
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
    assert entries[0].data == expected_yaml_import_data(["192.168.1.0/24"])
    assert [str(ip) for ip in hass.http.app[KEY_ALLOWLIST]] == ["192.168.1.0/24"]


@pytest.mark.asyncio
async def test_yaml_import_normalizes_ipv6_wildcard(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test YAML import accepts IPv6 wildcard shorthand."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    assert await async_setup_component(
        hass,
        DOMAIN,
        {DOMAIN: {CONF_IP_ADDRESSES: ["2001:db8:1:2:*"]}},
    )
    await hass.async_block_till_done()
    check_records(caplog.records)

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].data == expected_yaml_import_data(["2001:db8:1:2::/64"])
    assert [str(ip) for ip in hass.http.app[KEY_ALLOWLIST]] == ["2001:db8:1:2::/64"]


@pytest.mark.asyncio
async def test_yaml_disable_ban_manager_creates_repair_without_import(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test the YAML emergency kill switch disables setup without importing."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    assert await async_setup_component(
        hass,
        DOMAIN,
        {DOMAIN: CONF_DISABLED},
    )
    await hass.async_block_till_done()
    check_records(caplog.records)

    assert not hass.config_entries.async_entries(DOMAIN)
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, INTEGRATION_DISABLED_BY_YAML_ISSUE_ID
    )
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING


@pytest.mark.asyncio
async def test_yaml_disable_ban_manager_skips_existing_entry_setup(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test the YAML emergency kill switch keeps an entry from loading hooks."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        data={CONF_IP_ADDRESSES: ["127.0.0.1"]},
    )
    entry.add_to_hass(hass)

    assert await async_setup_component(
        hass,
        DOMAIN,
        {DOMAIN: CONF_DISABLED},
    )
    await hass.async_block_till_done()
    check_records(caplog.records)

    assert not hass.services.has_service(DOMAIN, SERVICE_ADD_IP_BAN)
    assert KEY_CONFIG_ENTRY not in hass.http.app
    assert KEY_ALLOWLIST not in hass.http.app
    assert KEY_PANEL_REGISTERED not in hass.data


@pytest.mark.asyncio
async def test_yaml_disable_ban_manager_accepts_legacy_key(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test the previous emergency disable key remains accepted."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    assert await async_setup_component(
        hass,
        DOMAIN,
        {DOMAIN: {CONF_DISABLE_BAN_MANAGER: True}},
    )
    await hass.async_block_till_done()
    check_records(caplog.records)

    assert not hass.config_entries.async_entries(DOMAIN)
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, INTEGRATION_DISABLED_BY_YAML_ISSUE_ID
        )
        is not None
    )


@pytest.mark.asyncio
async def test_emergency_disable_file_creates_repair_without_import(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test the emergency disable file disables setup without importing."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    disable_file = Path(hass.config.path("ip_ban_manager.disabled"))
    disable_file.touch()
    try:
        await async_setup_component(hass, "http", {})

        assert await async_setup_component(
            hass,
            DOMAIN,
            {DOMAIN: {CONF_IP_ADDRESSES: ["192.168.1.1"]}},
        )
        await hass.async_block_till_done()
        check_records(caplog.records)

        assert not hass.config_entries.async_entries(DOMAIN)
        assert (
            ir.async_get(hass).async_get_issue(
                DOMAIN, INTEGRATION_DISABLED_BY_YAML_ISSUE_ID
            )
            is not None
        )
    finally:
        disable_file.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_emergency_disable_file_and_yaml_together_skip_existing_entry_setup(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test either emergency disable path can keep an entry from loading hooks."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    disable_file = Path(hass.config.path("ip_ban_manager.disabled"))
    disable_file.touch()
    try:
        await async_setup_component(hass, "http", {})
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="IP Ban Manager",
            data={CONF_IP_ADDRESSES: ["127.0.0.1"]},
        )
        entry.add_to_hass(hass)

        assert await async_setup_component(
            hass,
            DOMAIN,
            {DOMAIN: CONF_DISABLED},
        )
        await hass.async_block_till_done()
        check_records(caplog.records)

        assert not hass.services.has_service(DOMAIN, SERVICE_ADD_IP_BAN)
        assert KEY_CONFIG_ENTRY not in hass.http.app
        assert KEY_ALLOWLIST not in hass.http.app
        assert KEY_PANEL_REGISTERED not in hass.data
    finally:
        disable_file.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_legacy_yaml_import_is_absorbed(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test leftover ban_allowlist YAML is imported by IP Ban Manager."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    assert await async_setup_component(
        hass,
        DOMAIN,
        {LEGACY_DOMAIN: {CONF_IP_ADDRESSES: ["192.168.1.1", "172.17.0.0/24"]}},
    )
    await hass.async_block_till_done()
    check_records(caplog.records)

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].data == expected_yaml_import_data(
        ["192.168.1.1", "172.17.0.0/24"]
    )
    assert [str(ip) for ip in hass.http.app[KEY_ALLOWLIST]] == [
        "192.168.1.1/32",
        "172.17.0.0/24",
    ]


@pytest.mark.asyncio
async def test_legacy_yaml_still_present_after_import_creates_repair(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test old ban_allowlist YAML creates a cleanup repair after migration."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        data={CONF_IP_ADDRESSES: ["127.0.0.1"]},
    )
    entry.add_to_hass(hass)

    assert await async_setup_component(
        hass,
        DOMAIN,
        {LEGACY_DOMAIN: {CONF_IP_ADDRESSES: ["192.168.1.1"]}},
    )
    await hass.async_block_till_done()
    check_records(caplog.records)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, LEGACY_YAML_PRESENT_ISSUE_ID)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING


@pytest.mark.asyncio
async def test_legacy_yaml_repair_clears_when_yaml_removed(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test old-YAML cleanup repair clears once legacy YAML is gone."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        LEGACY_YAML_PRESENT_ISSUE_ID,
        is_fixable=False,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=LEGACY_YAML_PRESENT_ISSUE_ID,
    )
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    check_records(caplog.records)

    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, LEGACY_YAML_PRESENT_ISSUE_ID) is None
    )


@pytest.mark.asyncio
async def test_setup_removes_leftover_legacy_entry(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test stale old-domain entries are removed when IP Ban Manager starts."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    legacy_entry = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="IP Ban Manager",
        data={CONF_IP_ADDRESSES: ["192.168.1.1"]},
    )
    legacy_entry.add_to_hass(hass)
    target_entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        data={CONF_IP_ADDRESSES: ["127.0.0.1"]},
    )
    target_entry.add_to_hass(hass)

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    check_records(caplog.records)

    assert not hass.config_entries.async_entries(LEGACY_DOMAIN)


@pytest.mark.asyncio
async def test_setup_entry_removes_leftover_legacy_entry(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test stale old-domain entries are removed when the config entry starts."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    legacy_entry = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="IP Ban Manager",
        data={CONF_IP_ADDRESSES: ["192.168.1.1"]},
    )
    legacy_entry.add_to_hass(hass)
    target_entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        data={CONF_IP_ADDRESSES: ["127.0.0.1"]},
    )
    target_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(target_entry.entry_id)
    await hass.async_block_till_done()
    check_records(caplog.records)

    assert not hass.config_entries.async_entries(LEGACY_DOMAIN)


@pytest.mark.asyncio
async def test_setup_entry_removes_migrated_legacy_entry_and_cleans_marker(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test setup removes the exact legacy entry captured by config flow."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    legacy_entry = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="IP Ban Manager",
        data={CONF_IP_ADDRESSES: ["192.168.1.1"]},
    )
    legacy_entry.add_to_hass(hass)
    target_entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        data={
            CONF_IP_ADDRESSES: ["192.168.1.1"],
            CONF_LEGACY_ENTRY_ID: legacy_entry.entry_id,
        },
    )
    target_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(target_entry.entry_id)
    await hass.async_block_till_done()
    check_records(caplog.records)

    stored_entry = hass.config_entries.async_get_entry(target_entry.entry_id)
    assert stored_entry is not None
    assert stored_entry.data == {CONF_IP_ADDRESSES: ["192.168.1.1"]}
    assert not hass.config_entries.async_entries(LEGACY_DOMAIN)


@pytest.mark.asyncio
async def test_setup_entry_moves_stale_legacy_component_folder(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test setup moves the old HACS-installed legacy folder out of the loader path."""
    custom_components = tmp_path / "custom_components"
    integration_path = custom_components / DOMAIN
    integration_path.mkdir(parents=True)
    legacy_path = custom_components / LEGACY_DOMAIN
    legacy_path.mkdir(parents=True)
    (legacy_path / "manifest.json").write_text(
        '{"domain": "ban_allowlist", "name": "IP Ban Manager"}',
        encoding="utf-8",
    )
    hass.config.config_dir = str(tmp_path)

    await _async_cleanup_legacy_component_folder(hass)

    assert not legacy_path.exists()
    backups = list((integration_path / LEGACY_CLEANUP_DIR).iterdir())
    assert len(backups) == 1
    assert (backups[0] / "manifest.json").is_file()
    assert not (tmp_path / LEGACY_BACKUP_DIR).exists()


@pytest.mark.asyncio
async def test_setup_entry_deletes_nested_custom_components_folder(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test cleanup deletes the broken v1.5.2 nested HACS package folder."""
    integration_path = tmp_path / "custom_components" / DOMAIN
    nested_path = integration_path / "custom_components" / DOMAIN
    nested_path.mkdir(parents=True)
    (nested_path / "manifest.json").write_text(
        '{"domain": "ip_ban_manager", "name": "IP Ban Manager"}',
        encoding="utf-8",
    )
    hass.config.config_dir = str(tmp_path)

    await _async_cleanup_legacy_component_folder(hass)

    assert not (integration_path / "custom_components").exists()
    assert not (integration_path / LEGACY_CLEANUP_DIR).exists()


@pytest.mark.asyncio
async def test_setup_entry_moves_old_top_level_legacy_backup_folder(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test old IP Ban Manager cleanup folders are moved into the integration folder."""
    integration_path = tmp_path / "custom_components" / DOMAIN
    integration_path.mkdir(parents=True)
    old_backup_path = tmp_path / LEGACY_BACKUP_DIR
    old_backup_path.mkdir()
    (old_backup_path / "legacy.txt").write_text("old backup", encoding="utf-8")
    hass.config.config_dir = str(tmp_path)

    await _async_cleanup_legacy_component_folder(hass)

    assert not old_backup_path.exists()
    backups = list((integration_path / LEGACY_CLEANUP_DIR).iterdir())
    assert len(backups) == 1
    assert (backups[0] / "legacy.txt").read_text(encoding="utf-8") == "old backup"


@pytest.mark.asyncio
async def test_legacy_folder_cleanup_failure_creates_repair(
    hass: HomeAssistant,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test failed legacy folder cleanup creates a repair issue."""
    custom_components = tmp_path / "custom_components"
    integration_path = custom_components / DOMAIN
    integration_path.mkdir(parents=True)
    legacy_path = custom_components / LEGACY_DOMAIN
    legacy_path.mkdir(parents=True)
    (legacy_path / "manifest.json").write_text(
        '{"domain": "ban_allowlist", "name": "IP Ban Manager"}',
        encoding="utf-8",
    )
    hass.config.config_dir = str(tmp_path)

    def _raise_move_error(source: str, destination: str) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(ban_legacy_migration.shutil, "move", _raise_move_error)

    await _async_cleanup_legacy_component_folder(hass)

    assert legacy_path.is_dir()
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, LEGACY_FOLDER_CLEANUP_FAILED_ISSUE_ID
    )
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_placeholders is not None
    assert str(legacy_path) in issue.translation_placeholders["paths"]
    assert any("Could not move stale cleanup path" in msg for msg in caplog.messages)


@pytest.mark.asyncio
async def test_successful_legacy_folder_cleanup_clears_repair(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test successful legacy folder cleanup clears stale cleanup repairs."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        LEGACY_FOLDER_CLEANUP_FAILED_ISSUE_ID,
        is_fixable=False,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=LEGACY_FOLDER_CLEANUP_FAILED_ISSUE_ID,
    )
    integration_path = tmp_path / "custom_components" / DOMAIN
    integration_path.mkdir(parents=True)
    hass.config.config_dir = str(tmp_path)

    await _async_cleanup_legacy_component_folder(hass)

    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, LEGACY_FOLDER_CLEANUP_FAILED_ISSUE_ID
        )
        is None
    )


@pytest.mark.asyncio
async def test_legacy_cleanup_keeps_legacy_entry_without_target(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test cleanup does not remove the only legacy import source."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    legacy_entry = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="IP Ban Manager",
        data={CONF_IP_ADDRESSES: ["192.168.1.1"]},
    )
    legacy_entry.add_to_hass(hass)

    _async_remove_legacy_entries(hass)
    await hass.async_block_till_done()
    check_records(caplog.records)

    assert hass.config_entries.async_entries(LEGACY_DOMAIN) == [legacy_entry]


@pytest.mark.asyncio
async def test_started_event_removes_late_legacy_entry(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test stale old-domain entries added before startup completion are removed."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    hass.state = CoreState.starting
    target_entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        data={CONF_IP_ADDRESSES: ["127.0.0.1"]},
    )
    target_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(target_entry.entry_id)
    await hass.async_block_till_done()

    legacy_entry = MockConfigEntry(
        domain=LEGACY_DOMAIN,
        title="IP Ban Manager",
        data={CONF_IP_ADDRESSES: ["192.168.1.1"]},
    )
    legacy_entry.add_to_hass(hass)

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    hass.state = CoreState.running
    await hass.async_block_till_done()
    check_records(caplog.records)

    assert not hass.config_entries.async_entries(LEGACY_DOMAIN)
