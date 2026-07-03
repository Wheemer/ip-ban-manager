"""Test IP Ban Manager setup."""

import logging
from ipaddress import IPv4Address, ip_address
from pathlib import Path
from typing import Any, cast

import pytest
from aiohttp.web import Response
from aiohttp.web_exceptions import HTTPForbidden
from homeassistant.components import persistent_notification
from homeassistant.components.http import ban as http_ban
from homeassistant.components.http.ban import (
    KEY_BAN_MANAGER,
    KEY_FAILED_LOGIN_ATTEMPTS,
    KEY_LOGIN_THRESHOLD,
    NOTIFICATION_ID_BAN,
    NOTIFICATION_ID_LOGIN,
    IpBanManager,
)
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.loader import DATA_CUSTOM_COMPONENTS, async_get_custom_components
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.ip_ban_manager as ipbm
from custom_components.ip_ban_manager import (
    _ORIGINAL_PROCESS_WRONG_LOGIN,
    ALLOWLISTED_LOGIN_ESCALATION_THRESHOLD,
    ALLOWLISTED_LOGIN_SILENCE_LABEL,
    ALLOWLISTED_LOGIN_SILENCE_URL,
    CONFIG_ENTRY_URL_TEMPLATE,
    INTEGRATION_CONFIG_URL,
    INTEGRATION_DISABLED_BY_YAML_ISSUE_ID,
    IP_BAN_DISABLED_ISSUE_ID,
    KEY_ALLOWLIST,
    KEY_BLOCKED_NETWORKS,
    KEY_CONFIG_ENTRY,
    KEY_ORIGINAL_ADD_BAN,
    KEY_ORIGINAL_LOAD_BANS,
    KEY_PANEL_REGISTERED,
    KEY_PANEL_SIDEBAR_ENABLED,
    LEGACY_BACKUP_DIR,
    LEGACY_CLEANUP_DIR,
    LEGACY_FOLDER_CLEANUP_FAILED_ISSUE_ID,
    LEGACY_YAML_PRESENT_ISSUE_ID,
    NOTIFICATION_ICON_DATA_URL,
    SilenceAllowlistedLoginNotificationsView,
    _add_manager_links_to_http_notifications,
    _allowlist_process_wrong_login,
    _async_cleanup_legacy_component_folder,
    _async_panel_set_options,
    _async_remove_legacy_entries,
    _cleanup_destination,
    current_status,
)
from custom_components.ip_ban_manager.const import (
    ATTR_ALLOWLISTED_LOGINS_CAN_BAN,
    ATTR_BANNED_IPS,
    ATTR_BLOCKED_NETWORKS,
    ATTR_CONFIRM,
    ATTR_DEFAULT_DENY_ENABLED,
    ATTR_FAILED_LOGIN_ATTEMPTS,
    ATTR_IP_ADDRESS,
    ATTR_NETWORK,
    ATTR_NETWORKS,
    CONF_ALLOWED_IPS,
    CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED,
    CONF_ALLOWLISTED_LOGINS_CAN_BAN,
    CONF_BANNED_IPS,
    CONF_BLOCKED_NETWORKS,
    CONF_DEFAULT_DENY_ENABLED,
    CONF_DISABLE_BAN_MANAGER,
    CONF_DISABLED,
    CONF_IP_ADDRESSES,
    CONF_SIDEBAR_PANEL_ENABLED,
    CONF_SILENCED_ALLOWLISTED_LOGIN_IPS,
    DOMAIN,
    LEGACY_DOMAIN,
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
            if (
                msg.startswith(
                    "We found a custom integration ip_ban_manager which has not been tested by Home Assistant"
                )
                or msg.startswith(
                    "We found a custom integration ban_allowlist which has not been tested by Home Assistant"
                )
                or msg.startswith(
                    "IP Ban Manager is disabled by configuration.yaml emergency override"
                )
                or msg.startswith(
                    "IP Ban Manager config entry setup skipped because ip_ban_manager is disabled"
                )
            ):
                continue
            raise Exception(msg)


def test_repository_ships_one_hacs_integration_folder() -> None:
    """Test HACS can only discover the real integration folder."""
    repo_root = Path(__file__).parents[2]
    integration_folders = sorted(
        path.name
        for path in (repo_root / "custom_components").iterdir()
        if path.is_dir() and not path.name.startswith("__")
    )

    assert integration_folders == [DOMAIN]


def test_cleanup_destination_does_not_overwrite_existing_path(tmp_path: Path) -> None:
    """Test cleanup destinations stay unique when a timestamp collides."""
    cleanup_root = tmp_path / ".cleanup"
    cleanup_root.mkdir()
    (cleanup_root / "ban_allowlist-20260629-120000").mkdir()

    assert _cleanup_destination(cleanup_root, "ban_allowlist", "20260629-120000") == (
        cleanup_root / "ban_allowlist-20260629-120000-2"
    )


async def setup_ip_ban_manager(hass: HomeAssistant) -> None:
    """Configure ip_ban_manager and dependencies."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
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
    assert entries[0].data == {CONF_IP_ADDRESSES: ["192.168.1.0/24"]}
    assert [str(ip) for ip in hass.http.app[KEY_ALLOWLIST]] == ["192.168.1.0/24"]


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
    assert entries[0].data == {CONF_IP_ADDRESSES: ["192.168.1.1", "172.17.0.0/24"]}
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

    monkeypatch.setattr(
        "custom_components.ip_ban_manager.shutil.move", _raise_move_error
    )

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
async def test_setup_entry_removes_legacy_entry_from_all_entries(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test cleanup is based on all runtime entries, not only domain indexes."""
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

    assert all(
        entry.domain != LEGACY_DOMAIN for entry in hass.config_entries.async_entries()
    )


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


@pytest.mark.asyncio
async def test_setup(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    """Test setup of IP Ban Manager."""
    await setup_ip_ban_manager(hass)
    check_records(caplog.records)
    assert hass.services.has_service(DOMAIN, SERVICE_ADD_IP_BAN)


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
        "Repeated allowlisted login failures"
        in existing_notifications[NOTIFICATION_ID_LOGIN]["message"]
    )
    assert "2/2" in existing_notifications[NOTIFICATION_ID_LOGIN]["message"]
    assert (
        "so it was not banned"
        in existing_notifications[NOTIFICATION_ID_LOGIN]["message"]
    )
    login_message = existing_notifications[NOTIFICATION_ID_LOGIN]["message"]
    assert login_message.startswith("## <img ")
    assert login_message.count(NOTIFICATION_ICON_DATA_URL) == 1
    assert "/api/ip_ban_manager/icon.png" not in login_message
    assert "IP Ban Manager icon" not in login_message
    assert "Open settings" not in login_message
    assert ALLOWLISTED_LOGIN_SILENCE_LABEL in login_message
    assert f"{ALLOWLISTED_LOGIN_SILENCE_URL}?ip_address=192.168.1.1" in login_message
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
async def test_ipv4_mapped_allowlisted_wrong_login_does_not_become_ban(
    hass: HomeAssistant,
) -> None:
    """Test IPv4-mapped IPv6 clients still match IPv4 allowlist entries."""
    await setup_ip_ban_manager(hass)

    remote_addr = ip_address("192.168.1.1")
    hass.http.app[KEY_LOGIN_THRESHOLD] = 1

    class MockRequest:
        remote = "::ffff:192.168.1.1"
        app = hass.http.app
        headers: dict[str, str] = {}
        rel_url = "/auth/login_flow/test"

    await http_ban.process_wrong_login(cast(Any, MockRequest()))

    assert hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS][remote_addr] == 1
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    assert remote_addr not in ban_manager.ip_bans_lookup


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
    assert "Open settings" in message


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
    assert "Open settings" in ban_message
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
    assert ALLOWLISTED_LOGIN_SILENCE_LABEL not in message
    assert NOTIFICATION_ID_BAN not in notifications


@pytest.mark.asyncio
async def test_silence_allowlisted_login_notifications_view(
    hass: HomeAssistant,
) -> None:
    """Test the notification link can silence low-priority allowlisted login notifications."""
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

    class MockRequest:
        app = hass.http.app

    response = await SilenceAllowlistedLoginNotificationsView().get(
        cast(Any, MockRequest())
    )

    assert response.status == 200
    assert entry.options[CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED] is False
    assert NOTIFICATION_ID_LOGIN not in notifications


@pytest.mark.asyncio
async def test_silence_allowlisted_login_notifications_view_can_silence_address(
    hass: HomeAssistant,
) -> None:
    """Test the notification link can silence one allowlisted address."""
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

    class MockViewRequest:
        app = hass.http.app
        query = {ATTR_IP_ADDRESS: "192.168.1.1"}

    response = await SilenceAllowlistedLoginNotificationsView().get(
        cast(Any, MockViewRequest())
    )

    assert response.status == 200
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
            f"({ALLOWLISTED_LOGIN_SILENCE_URL}?ip_address=192.168.1.1)"
        ),
        " ",
        "ip_ban_manager_custom_allowlisted_login",
    )
    notifications = persistent_notification._async_get_or_create_notifications(hass)
    assert "ip_ban_manager_custom_allowlisted_login" in notifications

    class MockViewRequest:
        app = hass.http.app
        query = {ATTR_IP_ADDRESS: "192.168.1.1"}

    response = await SilenceAllowlistedLoginNotificationsView().get(
        cast(Any, MockViewRequest())
    )

    assert response.status == 200
    assert entry.options[CONF_SILENCED_ALLOWLISTED_LOGIN_IPS] == ["192.168.1.1"]
    assert "ip_ban_manager_custom_allowlisted_login" not in notifications


@pytest.mark.asyncio
async def test_silenced_allowlisted_login_address_escalates_after_repeated_failures(
    hass: HomeAssistant,
) -> None:
    """Test per-address allowlisted notification silence still escalates."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(
        entry, options={CONF_SILENCED_ALLOWLISTED_LOGIN_IPS: ["192.168.1.1"]}
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
    assert ALLOWLISTED_LOGIN_SILENCE_LABEL not in message
    assert NOTIFICATION_ID_BAN not in notifications


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
    assert "Open settings" in message
    assert "IP Ban Manager icon" not in message


@pytest.mark.asyncio
async def test_http_notifications_get_manager_links(hass: HomeAssistant) -> None:
    """Test Home Assistant HTTP notifications link to IP Ban Manager."""
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
        assert "Open settings" in message
        assert message.count(INTEGRATION_CONFIG_URL) == 1
        assert message.count(NOTIFICATION_ICON_DATA_URL) == 1
        assert message.startswith("## <img ")
        assert "/api/ip_ban_manager/icon.png" not in message
        assert "IP Ban Manager icon" not in message
        assert "Open integrations" not in message


@pytest.mark.asyncio
async def test_http_notifications_link_directly_to_config_entry(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test Home Assistant HTTP notifications link to the settings page."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    manager_url = CONFIG_ENTRY_URL_TEMPLATE.format(entry_id=entry.entry_id)
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
    assert message.endswith(f"[Open settings]({manager_url})")
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
    assert KEY_ORIGINAL_ADD_BAN not in hass.http.app
    assert KEY_ORIGINAL_LOAD_BANS not in hass.http.app


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

    assert status[ATTR_ALLOWLISTED_LOGINS_CAN_BAN] is False
    assert status[ATTR_DEFAULT_DENY_ENABLED] is False
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
