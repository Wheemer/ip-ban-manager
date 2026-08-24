"""Test IP Ban Manager setup."""

# flake8: noqa: F401

import builtins
import json
import logging
import sys
from asyncio import Event, wait_for
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address
from pathlib import Path
from typing import Any, cast
from unittest.mock import ANY, AsyncMock

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
from homeassistant.core import CoreState, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.loader import DATA_CUSTOM_COMPONENTS, async_get_custom_components
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.ip_ban_manager as ipbm
import custom_components.ip_ban_manager.config_flow as ban_config_flow
import custom_components.ip_ban_manager.geoip as ban_geoip
import custom_components.ip_ban_manager.geoip_lifecycle as ban_geoip_lifecycle
import custom_components.ip_ban_manager.http_patches as ipbm_http_patches
import custom_components.ip_ban_manager.legacy_migration as ban_legacy_migration
import custom_components.ip_ban_manager.network_policy as ban_network_policy
import custom_components.ip_ban_manager.notifications as ban_notifications
import custom_components.ip_ban_manager.panel as ban_panel
import custom_components.ip_ban_manager.panel_assets as ban_panel_assets
import custom_components.ip_ban_manager.reverse_dns as reverse_dns
import custom_components.ip_ban_manager.services as ban_services
from custom_components.ip_ban_manager import (
    _ORIGINAL_PROCESS_WRONG_LOGIN,
    ALLOWLISTED_LOGIN_ESCALATION_THRESHOLD,
    ALLOWLISTED_LOGIN_SILENCE_LABEL,
    ALLOWLISTED_LOGIN_SILENCE_URL,
    ATTR_NOTIFICATION_ID,
    INTEGRATION_CONFIG_URL,
    INTEGRATION_DISABLED_BY_YAML_ISSUE_ID,
    IP_BAN_DISABLED_ISSUE_ID,
    KEY_ALLOWLIST,
    KEY_BLOCKED_NETWORKS,
    KEY_CONFIG_ENTRY,
    KEY_DEFAULT_DENY,
    KEY_HEALTH,
    KEY_HTTP_VIEW_HANDLERS,
    KEY_HTTP_VIEWS,
    KEY_INTERNAL_BYPASS_NETWORKS,
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
    IPBanManagerPanelView,
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
    _process_allowlisted_wrong_login,
    _supervisor_internal_networks,
    current_status,
)
from custom_components.ip_ban_manager.const import (
    ATTR_ADDED_AT,
    ATTR_ALLOWLISTED_LOGINS_CAN_BAN,
    ATTR_ATTEMPTS,
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
    ATTR_SOURCE,
    ATTR_THRESHOLD,
    CONF_ALLOWED_IPS,
    CONF_ALLOWLIST_ENTRY_META,
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
    EVENT_ALLOWLIST_NETWORK_ADDED,
    EVENT_ALLOWLIST_NETWORK_REMOVED,
    EVENT_ALLOWLISTED_LOGIN_ESCALATED,
    EVENT_BLOCKED_NETWORK_ADDED,
    EVENT_BLOCKED_NETWORK_REMOVED,
    EVENT_IP_BANNED,
    EVENT_IP_UNBANNED,
    EVENT_LOGIN_THRESHOLD_REACHED,
    LEGACY_DOMAIN,
    SERVICE_ADD_ALLOWLIST_NETWORK,
    SERVICE_ADD_BLOCKED_NETWORK,
    SERVICE_ADD_IP_BAN,
    SERVICE_EXPORT_CONFIG,
    SERVICE_IMPORT_CONFIG,
    SERVICE_REMOVE_ALL_IP_BANS,
    SERVICE_REMOVE_ALLOWLIST_NETWORK,
    SERVICE_REMOVE_BLOCKED_NETWORK,
    SERVICE_REMOVE_IP_BAN,
    SERVICE_UPDATE_GEOIP,
    SOURCE_AUTO,
    SOURCE_PANEL,
    SOURCE_SERVICE,
    SOURCE_SETUP,
    SOURCE_YAML,
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
        self.headers: dict[str, str] = {}
        self.rel_url = "/auth/login_flow"

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
                or msg.startswith(
                    "Login attempt or request with invalid authentication"
                )
                or msg.startswith("Banned IP ")
            ):
                continue
            raise Exception(msg)


def expected_yaml_import_data(ip_addresses: list[str]) -> dict[str, object]:
    """Return expected config-entry data for absorbed YAML allowlist rows."""
    return {
        CONF_IP_ADDRESSES: ip_addresses,
        CONF_ALLOWLIST_ENTRY_META: {
            ip_address: {ATTR_ADDED_AT: ANY, ATTR_SOURCE: SOURCE_YAML}
            for ip_address in ip_addresses
        },
    }


def test_repository_ships_one_hacs_integration_folder() -> None:
    """Test HACS can only discover the real integration folder."""
    repo_root = Path(__file__).parents[2]
    integration_folders = sorted(
        path.name
        for path in (repo_root / "custom_components").iterdir()
        if path.is_dir() and not path.name.startswith("__")
    )

    assert integration_folders == [DOMAIN]


def test_manifest_does_not_require_runtime_geoip_install() -> None:
    """Test GeoIP cannot block setup through runtime package installs."""
    manifest_path = (
        Path(__file__).parents[2] / "custom_components" / DOMAIN / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["requirements"] == []


def test_geoip_module_falls_back_to_vendored_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test the real GeoIP module loader falls back to the bundled reader."""
    original_import = builtins.__import__
    vendor_path = str(ban_geoip.MAXMINDDB_VENDOR_PATH)
    monkeypatch.setattr(sys, "path", [path for path in sys.path if path != vendor_path])
    monkeypatch.setitem(sys.modules, "maxminddb", None)

    def import_without_site_package(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "maxminddb" and vendor_path not in sys.path:
            raise ImportError("Simulated missing site-package maxminddb")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_without_site_package)

    module = ban_geoip._maxminddb_module()

    assert module.__name__ == "maxminddb"
    assert (
        Path(module.__file__)
        .resolve()
        .is_relative_to(ban_geoip.MAXMINDDB_VENDOR_PATH.resolve())
    )
    assert sys.path[0] == vendor_path


def test_geoip_reader_uses_bundled_reader_when_dependency_is_not_installed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test the bundled reader is used instead of requiring site-packages."""
    opened: list[str] = []

    class MockMaxMindDB:
        @staticmethod
        def open_database(path: str) -> object:
            opened.append(path)
            return object()

    database_path = tmp_path / "dbip-city-lite.mmdb"
    database_path.write_bytes(b"not a real database")
    monkeypatch.setitem(sys.modules, "maxminddb", None)
    monkeypatch.setattr(ban_geoip, "_maxminddb_module", lambda: MockMaxMindDB)

    assert ban_geoip.open_geoip_reader(database_path) is not None
    assert opened == [str(database_path)]


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
async def test_setup_entry_removes_preexisting_allowlisted_ban(
    hass: HomeAssistant,
) -> None:
    """Test startup clears exact bans written before allowlist protection loaded."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    remote_addr = ip_address("192.168.1.50")
    ban_manager.ip_bans_lookup[remote_addr] = IpBan(remote_addr)
    persistent_notification.async_create(
        hass,
        f"Banned IP {remote_addr} for too many failed login attempts.",
        "Banning IP address",
        NOTIFICATION_ID_BAN,
    )
    events: list[dict[str, str]] = []
    remove_listener = hass.bus.async_listen(
        EVENT_IP_UNBANNED, lambda event: events.append(event.data)
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        data={CONF_IP_ADDRESSES: ["192.168.1.0/24"]},
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    remove_listener()

    notifications = persistent_notification._async_get_or_create_notifications(
        hass
    )  # noqa: SLF001
    assert remote_addr not in ban_manager.ip_bans_lookup
    assert NOTIFICATION_ID_BAN not in notifications
    assert events == [{ATTR_IP_ADDRESS: str(remote_addr), ATTR_SOURCE: SOURCE_SETUP}]


@pytest.mark.asyncio
async def test_setup_entry_keeps_preexisting_allowlisted_ban_when_enabled(
    hass: HomeAssistant,
) -> None:
    """Test startup leaves exact allowlisted bans when the user opted into them."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    remote_addr = ip_address("192.168.1.50")
    ban_manager.ip_bans_lookup[remote_addr] = IpBan(remote_addr)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        data={
            CONF_IP_ADDRESSES: ["192.168.1.0/24"],
            CONF_ALLOWLISTED_LOGINS_CAN_BAN: True,
        },
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert remote_addr in ban_manager.ip_bans_lookup


@pytest.mark.asyncio
async def test_setup_entry_removes_preexisting_internal_ban_even_when_enabled(
    hass: HomeAssistant,
) -> None:
    """Test startup always clears exact bans for Home Assistant internal paths."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert "ip_ban_manager" in (await async_get_custom_components(hass))
    await async_setup_component(hass, "http", {})
    ban_manager = cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER])
    remote_addr = ip_address("172.30.32.2")
    ban_manager.ip_bans_lookup[remote_addr] = IpBan(remote_addr)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="IP Ban Manager",
        data={
            CONF_IP_ADDRESSES: [],
            CONF_ALLOWLISTED_LOGINS_CAN_BAN: True,
        },
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert remote_addr not in ban_manager.ip_bans_lookup


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

    monkeypatch.setattr(
        ban_legacy_migration, "async_cleanup_legacy_component_folder", slow_cleanup
    )

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
    assert hass.services.has_service(DOMAIN, SERVICE_ADD_BLOCKED_NETWORK)
    assert hass.services.has_service(DOMAIN, SERVICE_UPDATE_GEOIP)
    assert hass.services.has_service(DOMAIN, SERVICE_EXPORT_CONFIG)
    assert hass.services.has_service(DOMAIN, SERVICE_IMPORT_CONFIG)
    await wait_for(cleanup_started.wait(), timeout=1)

    cleanup_can_finish.set()
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_unload_cancels_legacy_folder_cleanup_without_health_refresh(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test cancelled legacy cleanup tasks do not recreate health state after unload."""
    cleanup_started = Event()
    cleanup_can_finish = Event()
    health_updates: list[HomeAssistant] = []

    async def slow_cleanup(mock_hass: HomeAssistant) -> None:
        cleanup_started.set()
        await cleanup_can_finish.wait()

    async def record_health_update(mock_hass: HomeAssistant) -> None:
        health_updates.append(mock_hass)

    monkeypatch.setattr(
        ban_legacy_migration, "async_cleanup_legacy_component_folder", slow_cleanup
    )
    monkeypatch.setattr(
        ban_legacy_migration, "async_update_health_issue", record_health_update
    )

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
    await wait_for(cleanup_started.wait(), timeout=1)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    cleanup_can_finish.set()
    await hass.async_block_till_done()
    assert health_updates == []


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

    monkeypatch.setattr(ban_geoip_lifecycle, "async_prepare_geoip_reader", slow_prepare)

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


@pytest.mark.asyncio
async def test_unload_cancels_geoip_prepare_without_health_refresh(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test cancelled GeoIP warmup tasks do not recreate health state after unload."""
    prepare_started = Event()
    prepare_can_finish = Event()
    health_updates: list[HomeAssistant] = []

    async def slow_prepare(mock_hass: HomeAssistant) -> None:
        prepare_started.set()
        await prepare_can_finish.wait()

    async def record_health_update(mock_hass: HomeAssistant) -> None:
        health_updates.append(mock_hass)

    monkeypatch.setattr(ban_geoip_lifecycle, "async_prepare_geoip_reader", slow_prepare)
    monkeypatch.setattr(
        ban_geoip_lifecycle, "async_update_health_issue", record_health_update
    )

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
    await wait_for(prepare_started.wait(), timeout=1)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    prepare_can_finish.set()
    await hass.async_block_till_done()
    assert health_updates == []


async def detected_local_subnets(hass: HomeAssistant) -> list[str]:
    """Return a detected local subnet for setup tests."""
    return ["192.168.1.0/24"]


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
async def test_setup(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    """Test setup of IP Ban Manager."""
    await setup_ip_ban_manager(hass)
    check_records(caplog.records)
    assert hass.services.has_service(DOMAIN, SERVICE_ADD_IP_BAN)


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
async def test_setup_entry_reregisters_http_views_after_unload(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test reloading rebinds handlers without registering duplicate HTTP routes."""
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
    assert KEY_HTTP_VIEW_HANDLERS in hass.data

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert KEY_HTTP_VIEWS not in hass.data
    assert KEY_HTTP_VIEW_HANDLERS not in hass.data
    assert count_view_resources() == 3

    response = await IPBanManagerStatusView().get(
        cast(Any, MockViewRequest(hass.http.app))
    )
    assert response.status == 404
    assert response.text is not None
    payload = json.loads(response.text)
    assert payload["ok"] is False
    assert "not loaded" in payload["error"].lower()

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    check_records(caplog.records)
    assert count_view_resources() == 3
    assert KEY_HTTP_VIEWS in hass.data
    assert KEY_HTTP_VIEW_HANDLERS in hass.data

    response = await IPBanManagerStatusView().get(
        cast(Any, MockViewRequest(hass.http.app))
    )
    assert response.status == 200


@pytest.mark.asyncio
async def test_allowlisted_login_escalation_fires_event(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test allowlisted-login escalation fires an automation event."""
    captured: list[dict[str, Any]] = []

    @callback
    def capture_event(event) -> None:
        captured.append(dict(event.data))

    remove = hass.bus.async_listen(EVENT_ALLOWLISTED_LOGIN_ESCALATED, capture_event)

    await setup_ip_ban_manager(hass)
    remote_addr = ip_address("192.168.1.1")
    hass.http.app[KEY_LOGIN_THRESHOLD] = 5
    hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS][remote_addr] = (
        ALLOWLISTED_LOGIN_ESCALATION_THRESHOLD - 1
    )
    await _process_allowlisted_wrong_login(
        cast(Any, MockViewRequest(hass.http.app)),
        remote_addr,
    )
    check_records(caplog.records)
    remove()

    assert captured == [
        {
            ATTR_IP_ADDRESS: "192.168.1.1",
            ATTR_ATTEMPTS: ALLOWLISTED_LOGIN_ESCALATION_THRESHOLD,
            ATTR_SOURCE: SOURCE_AUTO,
        }
    ]


@pytest.mark.asyncio
async def test_duplicate_allowlist_add_is_silent(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test duplicate allowlist adds do not fire events or logbook entries."""
    from homeassistant.components import logbook

    captured: list[str] = []
    logbook_messages: list[str] = []

    @callback
    def capture_event(event) -> None:
        captured.append(event.event_type)

    remove = hass.bus.async_listen(EVENT_ALLOWLIST_NETWORK_ADDED, capture_event)
    monkeypatch.setattr(
        logbook,
        "async_log_entry",
        lambda _hass, _name, message, domain=None: logbook_messages.append(message),
    )

    await setup_ip_ban_manager(hass)
    payload = {ATTR_NETWORK: "203.0.113.0/24"}
    await hass.services.async_call(
        DOMAIN, SERVICE_ADD_ALLOWLIST_NETWORK, payload, blocking=True
    )
    await hass.services.async_call(
        DOMAIN, SERVICE_ADD_ALLOWLIST_NETWORK, payload, blocking=True
    )
    check_records(caplog.records)
    remove()

    assert captured == [EVENT_ALLOWLIST_NETWORK_ADDED]
    assert logbook_messages == ["Added allowlist network 203.0.113.0/24 (service)"]


__all__ = [name for name in globals() if not name.startswith(("test_", "__"))]
