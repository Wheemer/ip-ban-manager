"""Test IP Ban Manager setup."""

import json
import logging
from asyncio import Event, wait_for
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
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
    IpBan,
    IpBanManager,
)
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.loader import DATA_CUSTOM_COMPONENTS, async_get_custom_components
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.ip_ban_manager as ipbm
import custom_components.ip_ban_manager.config_flow as ban_config_flow
from custom_components.ip_ban_manager import (
    _ORIGINAL_PROCESS_WRONG_LOGIN,
    ALLOWLISTED_LOGIN_ESCALATION_THRESHOLD,
    ALLOWLISTED_LOGIN_SILENCE_LABEL,
    ALLOWLISTED_LOGIN_SILENCE_URL,
    ATTR_NOTIFICATION_ID,
    CONFIG_ENTRY_URL_TEMPLATE,
    INTEGRATION_CONFIG_URL,
    INTEGRATION_DISABLED_BY_YAML_ISSUE_ID,
    IP_BAN_DISABLED_ISSUE_ID,
    KEY_ALLOWLIST,
    KEY_BLOCKED_NETWORKS,
    KEY_CONFIG_ENTRY,
    KEY_HEALTH,
    KEY_HTTP_VIEWS,
    KEY_METRICS,
    KEY_ORIGINAL_ADD_BAN,
    KEY_ORIGINAL_LOAD_BANS,
    KEY_PANEL_REGISTERED,
    KEY_PANEL_SIDEBAR_ENABLED,
    KEY_REVERSE_DNS_CACHE,
    LEGACY_BACKUP_DIR,
    LEGACY_CLEANUP_DIR,
    LEGACY_FOLDER_CLEANUP_FAILED_ISSUE_ID,
    LEGACY_YAML_PRESENT_ISSUE_ID,
    NOTIFICATION_ICON_DATA_URL,
    IPBanManagerManageView,
    IPBanManagerStatusView,
    SilenceAllowlistedLoginNotificationsView,
    _add_manager_links_to_http_notifications,
    _allowlist_process_wrong_login,
    _async_cleanup_legacy_component_folder,
    _async_panel_set_options,
    _async_register_panel,
    _async_remove_legacy_entries,
    _async_update_health_issue,
    _cleanup_destination,
    _create_allowlisted_login_notification,
    _entry_allowlisted_login_notifications_enabled,
    _supervisor_internal_networks,
    current_status,
)
from custom_components.ip_ban_manager.const import (
    ATTR_ALLOWLISTED_LOGINS_CAN_BAN,
    ATTR_BANNED_IPS,
    ATTR_BLOCKED_NETWORKS,
    ATTR_CONFIRM,
    ATTR_DEFAULT_DENY_ENABLED,
    ATTR_FAILED_LOGIN_ATTEMPTS,
    ATTR_GEOIP_DATABASE_PRESENT,
    ATTR_GEOIP_ENABLED,
    ATTR_HEALTH,
    ATTR_HEALTH_ISSUES,
    ATTR_IP_ADDRESS,
    ATTR_LAST_CONFIG_WRITE,
    ATTR_METRICS,
    ATTR_NETWORK,
    ATTR_NETWORKS,
    CONF_ALLOWED_IPS,
    CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED,
    CONF_ALLOWLISTED_LOGINS_CAN_BAN,
    CONF_AUTO_BAN_ENABLED,
    CONF_BAN_NOTIFICATIONS_ENABLED,
    CONF_BANNED_IPS,
    CONF_BLOCKED_NETWORKS,
    CONF_DEFAULT_DENY_ENABLED,
    CONF_DISABLE_BAN_MANAGER,
    CONF_DISABLED,
    CONF_GEOIP_ENABLED,
    CONF_IP_ADDRESSES,
    CONF_LEGACY_ENTRY_ID,
    CONF_LOGIN_ATTEMPTS_THRESHOLD,
    CONF_SIDEBAR_PANEL_ENABLED,
    CONF_SILENCED_ALLOWLISTED_LOGIN_IPS,
    DOMAIN,
    LEGACY_DOMAIN,
    SERVICE_ADD_ALLOWLIST_NETWORK,
    SERVICE_ADD_IP_BAN,
    SERVICE_EXPORT_CONFIG,
    SERVICE_IMPORT_CONFIG,
    SERVICE_REMOVE_ALL_IP_BANS,
    SERVICE_REMOVE_ALLOWLIST_NETWORK,
    SERVICE_REMOVE_IP_BAN,
)


class MockAdminUser:
    """Minimal admin user for direct HomeAssistantView tests."""

    is_admin = True


class MockNonAdminUser:
    """Minimal non-admin user for direct HomeAssistantView tests."""

    is_admin = False


class MockViewRequest:
    """Minimal request object for direct HomeAssistantView tests."""

    def __init__(
        self,
        app: dict[Any, Any],
        *,
        user: object | None = None,
        has_user: bool = True,
        query: dict[str, str] | None = None,
        data: dict[str, object] | None = None,
    ) -> None:
        """Initialize the mock view request."""
        self.app = app
        self.query = query or {}
        self._data = data or {}
        self._has_user = has_user
        self._user = user if user is not None else MockAdminUser()

    def get(self, key: str, default: object | None = None) -> object | None:
        """Return request-scoped Home Assistant auth data."""
        if key == "hass_user":
            if not self._has_user:
                return default
            return self._user
        return default

    async def json(self) -> dict[str, object]:
        """Return the request JSON body."""
        return self._data


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
                or msg.startswith("IP Ban Manager is disabled by emergency override")
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
async def test_setup_entry_does_not_wait_for_legacy_folder_cleanup(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test stale-folder cleanup does not hold the setup path."""
    cleanup_started = Event()
    cleanup_can_finish = Event()

    async def slow_cleanup(mock_hass: HomeAssistant) -> None:
        cleanup_started.set()
        await cleanup_can_finish.wait()

    monkeypatch.setattr(ipbm, "_async_cleanup_legacy_component_folder", slow_cleanup)

    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        data={CONF_IP_ADDRESSES: ["192.168.1.1"]},
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    assert hass.services.has_service(DOMAIN, SERVICE_ADD_IP_BAN)
    assert hass.services.has_service(DOMAIN, SERVICE_EXPORT_CONFIG)
    assert hass.services.has_service(DOMAIN, SERVICE_IMPORT_CONFIG)
    await wait_for(cleanup_started.wait(), timeout=1)

    cleanup_can_finish.set()
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_setup_entry_does_not_wait_for_geoip_reader_prepare(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test GeoIP reader warmup does not hold the setup path."""
    prepare_started = Event()
    prepare_can_finish = Event()

    async def slow_prepare(mock_hass: HomeAssistant) -> None:
        prepare_started.set()
        await prepare_can_finish.wait()

    monkeypatch.setattr(ipbm, "_async_prepare_geoip_reader", slow_prepare)

    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        data={
            CONF_IP_ADDRESSES: ["192.168.1.1"],
            CONF_GEOIP_ENABLED: True,
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    assert hass.services.has_service(DOMAIN, SERVICE_ADD_IP_BAN)
    await wait_for(prepare_started.wait(), timeout=1)

    prepare_can_finish.set()
    await hass.async_block_till_done()


async def detected_local_subnets(hass: HomeAssistant) -> list[str]:
    """Return a detected local subnet for setup tests."""
    return ["192.168.1.0/24"]


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
async def test_default_deny_does_not_block_home_assistant_self_address(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test default-deny still bypasses exact Home Assistant self-addresses."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    ipbm._update_allowlist_entry(hass, ["10.10.10.0/24"])

    async def mock_self_networks(hass: HomeAssistant) -> tuple[IPv4Network, ...]:
        return (IPv4Network("192.168.1.40/32"),)

    monkeypatch.setattr(ipbm, "_async_home_assistant_self_networks", mock_self_networks)

    await _async_panel_set_options(hass, {CONF_DEFAULT_DENY_ENABLED: True})

    lookup = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER]).ip_bans_lookup
    assert isinstance(lookup, ipbm.NetworkAwareBanLookup)
    assert IPv4Address("192.168.1.40") not in lookup
    assert IPv4Address("192.168.1.41") in lookup
    assert entry.options[CONF_DEFAULT_DENY_ENABLED] is True


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

    monkeypatch.setattr(ipbm, "_async_home_assistant_self_networks", mock_self_networks)
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
async def test_panel_registration_requires_admin(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test the bundled panel is only available to administrators."""
    registered: dict[str, object] = {}

    async def mock_register_panel(hass: HomeAssistant, **kwargs: object) -> None:
        registered.update(kwargs)

    monkeypatch.setattr(
        "homeassistant.components.panel_custom.async_register_panel",
        mock_register_panel,
    )

    await _async_register_panel(hass)

    assert registered["frontend_url_path"] == DOMAIN
    assert registered["config_panel_domain"] == DOMAIN
    assert registered["require_admin"] is True


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

    for state in (
        active_bans,
        allowlisted_networks,
        blocked_networks,
        failed_login_sources,
    ):
        assert state.attributes["state_class"] == "measurement"
        assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == ""


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
        ipbm,
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
        ipbm,
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
    assert "from server.lan (192.168.1.1)." in message


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

    monkeypatch.setattr(ipbm, "gethostbyaddr", fake_gethostbyaddr)

    class MockRequest:
        remote = "192.168.1.1"
        app = hass.http.app
        headers: dict[str, str] = {}
        rel_url = "/auth/login_flow/test"

    await http_ban.process_wrong_login(cast(Any, MockRequest()))
    await http_ban.process_wrong_login(cast(Any, MockRequest()))

    assert lookup_count == 1
    notifications = persistent_notification._async_get_or_create_notifications(hass)
    assert (
        "from server.lan (192.168.1.1)."
        in notifications[NOTIFICATION_ID_LOGIN]["message"]
    )


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

    _async_update_health_issue(hass)
    status = current_status(hass)
    health = cast(dict[str, Any], status[ATTR_HEALTH])

    assert health["ok"] is False
    assert "The IP Ban Manager panel is not registered." in cast(
        list[str], health[ATTR_HEALTH_ISSUES]
    )


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
        ipbm, "_async_download_geoip_database", mock_download_geoip_database
    )

    await _async_panel_set_options(hass, {CONF_GEOIP_ENABLED: True})

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert downloaded
    assert entry.options[CONF_GEOIP_ENABLED] is True


@pytest.mark.asyncio
async def test_allowlisted_notification_includes_geoip_location(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test allowlisted notifications include local GeoIP location details."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(entry, options={CONF_GEOIP_ENABLED: True})
    monkeypatch.setattr(
        ipbm,
        "_geoip_location_for_ip",
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
async def test_silenced_allowlisted_login_address_stays_silenced_after_repeated_failures(
    hass: HomeAssistant,
) -> None:
    """Test per-address allowlisted notification silence suppresses repeated alerts."""
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
    assert NOTIFICATION_ID_LOGIN not in notifications
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
            "192.168.0.108 (192.168.0.108). See the log for details.\n\n"
            "Current failed-login count: 2/3. 192.168.0.108 is allowlisted, "
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
    assert "&ip_address=192.168.0.108" in message
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
            "localhost (::1). See the log for details.\n\n"
            "Current failed-login count: 2/3. ::1 is allowlisted, "
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
    assert "&ip_address=%3A%3A1" in message
    assert f"&{ATTR_NOTIFICATION_ID}={NOTIFICATION_ID_LOGIN}" in message
    assert "&token=" not in message


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
    assert KEY_REVERSE_DNS_CACHE not in hass.http.app
    assert KEY_HEALTH not in hass.data
    assert KEY_METRICS not in hass.data
    assert KEY_HTTP_VIEWS not in hass.data


@pytest.mark.asyncio
async def test_setup_entry_reregisters_http_views_after_unload(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test reloading does not register duplicate HTTP view routes."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    view_urls = {
        SilenceAllowlistedLoginNotificationsView.url,
        IPBanManagerStatusView.url,
        IPBanManagerManageView.url,
    }

    def count_view_resources() -> int:
        resources = set()
        for route in hass.http.app.router.routes():
            resource = route.resource
            if resource is not None and resource.canonical in view_urls:
                resources.add(resource.canonical)
        return len(resources)

    assert count_view_resources() == 3

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert KEY_HTTP_VIEWS not in hass.data
    assert count_view_resources() == 3

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    check_records(caplog.records)
    assert count_view_resources() == 3
    assert KEY_HTTP_VIEWS in hass.data


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
