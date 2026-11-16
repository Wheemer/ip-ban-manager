"""Regression coverage for independently reported lifecycle and policy defects."""

from ipaddress import ip_address
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir

import custom_components.ip_ban_manager as ipbm
from custom_components.ip_ban_manager import geoip, health
from custom_components.ip_ban_manager import nginx_proxy_manager as npm
from custom_components.ip_ban_manager.const import DOMAIN
from custom_components.ip_ban_manager.storage_keys import KEY_GEOIP_READER

from .test_setup import setup_ip_ban_manager


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401])
async def test_npm_auth_status_does_not_depend_on_json(status):
    """Missing or nonstandard error bodies must still request authentication."""
    response = SimpleNamespace(status=status, json=AsyncMock(side_effect=ValueError))
    with pytest.raises(npm.NpmAuthenticationError):
        await npm.NpmClient._response_json(response)
    response.json.assert_not_called()


@pytest.mark.asyncio
async def test_health_transient_failure_eventually_escalates(hass, monkeypatch):
    """Startup grace cannot conceal a permanently broken reader."""
    await setup_ip_ban_manager(hass)
    clock = [100.0]
    monkeypatch.setattr(health, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        health,
        "async_health_status",
        AsyncMock(return_value={"health_issues": [{"key": "geoip_reader_not_ready"}]}),
    )
    await health.async_update_health_issue(hass)
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, health.HEALTH_CHECK_FAILED_ISSUE_ID) is None
    clock[0] += health.HEALTH_GRACE_SECONDS + 1
    await health.async_update_health_issue(hass)
    assert (
        registry.async_get_issue(DOMAIN, health.HEALTH_CHECK_FAILED_ISSUE_ID)
        is not None
    )
    health.clear_health_grace(hass)


@pytest.mark.asyncio
async def test_hooks_installed_before_panel_setup_await(hass, monkeypatch):
    """A slow frontend registration must not leave native banning unprotected."""
    from homeassistant.components.http import ban

    monkeypatch.setattr(ipbm, "_async_reload_runtime_modules", AsyncMock())
    original = ipbm._async_register_panel

    async def register(hass, **kwargs):
        assert ban.process_wrong_login is not ipbm._ORIGINAL_PROCESS_WRONG_LOGIN
        await original(hass, **kwargs)

    monkeypatch.setattr(ipbm, "_async_register_panel", register)
    await setup_ip_ban_manager(hass)


@pytest.mark.asyncio
async def test_geoip_cache_is_bounded_and_invalidated_on_close(hass, monkeypatch):
    """Repeated requests reuse normalized results, including misses."""
    await setup_ip_ban_manager(hass)
    reader = Mock()
    reader.get.return_value = {"country": {"iso_code": "CA"}}
    hass.http.app[KEY_GEOIP_READER] = reader
    monkeypatch.setattr(geoip, "GEOIP_CACHE_MAX_ENTRIES", 2)
    address = ip_address("8.8.8.8")
    for _ in range(10):
        assert (
            geoip.geoip_location_details_for_ip(
                hass, address, require_geoip_enabled=False
            ).country_code
            == "CA"
        )
    assert reader.get.call_count == 1
    for value in ("1.1.1.1", "9.9.9.9"):
        geoip.geoip_location_details_for_ip(
            hass, ip_address(value), require_geoip_enabled=False
        )
    assert len(hass.http.app[geoip._LOOKUP_CACHE_KEY]) == 2
    geoip.close_geoip_reader(hass)
    assert geoip._LOOKUP_CACHE_KEY not in hass.http.app
    reader.close.assert_called_once()


def test_invalid_download_preserves_previous_database(tmp_path, monkeypatch):
    """A successful HTTP/gzip transfer is not sufficient database validation."""
    from contextlib import contextmanager
    from gzip import compress
    from io import BytesIO

    path = tmp_path / "database.mmdb"
    path.write_bytes(b"previous-good-database")

    @contextmanager
    def download(_url):
        yield BytesIO(compress(b"not-an-mmdb"))

    monkeypatch.setattr(geoip, "open_geoip_download_url", download)
    monkeypatch.setattr(geoip, "geoip_download_urls", lambda: ["https://test.invalid"])
    monkeypatch.setattr(geoip, "open_geoip_reader", lambda _path: None)
    with pytest.raises(HomeAssistantError, match="invalid"):
        geoip.download_geoip_database_to_path(path)
    assert path.read_bytes() == b"previous-good-database"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.asyncio
async def test_npm_policy_put_preserves_editable_host_fields():
    """NPM must receive fields whose omitted defaults would reset user settings."""
    current = {
        "id": 7,
        "owner_user_id": 1,
        "advanced_config": "# user configuration\n",
        "http2_support": True,
        "hsts_enabled": True,
        "certificate_id": 12,
        "locations": [{"path": "/custom", "forward_host": "192.0.2.1"}],
    }
    client = object.__new__(npm.NpmClient)
    client._json = AsyncMock(side_effect=[current, {"meta": {"nginx_online": True}}])
    await client.update_proxy_host_policy(7, current["advanced_config"])
    payload = client._json.await_args.kwargs["payload"]
    for key in ("http2_support", "hsts_enabled", "certificate_id", "locations"):
        assert payload[key] == current[key]
    assert "id" not in payload and "owner_user_id" not in payload


@pytest.mark.asyncio
async def test_npm_policy_put_rejects_concurrent_user_config_change():
    """A fresh host read must not overwrite an edit made since sync began."""
    client = object.__new__(npm.NpmClient)
    client._json = AsyncMock(return_value={"advanced_config": "# new user edit"})
    with pytest.raises(HomeAssistantError, match="changed during sync"):
        await client.update_proxy_host_policy(7, "# older user edit")
    assert client._json.await_count == 1


@pytest.mark.asyncio
async def test_mapped_ipv6_ban_can_be_removed_by_either_representation(hass):
    """Store one canonical exact ban and permit removal through its IPv6 alias."""
    from custom_components.ip_ban_manager import ban_ops

    await setup_ip_ban_manager(hass)
    await ban_ops.async_add_ip_ban(hass, "::ffff:8.8.8.8")
    assert ip_address("8.8.8.8") in ban_ops.ban_manager(hass).ip_bans_lookup
    await ban_ops.async_remove_ip_ban(hass, "::ffff:8.8.8.8")
    assert ip_address("8.8.8.8") not in ban_ops.ban_manager(hass).ip_bans_lookup


def test_scoped_ipv6_and_numeric_hostname_typos():
    """Zone identifiers do not change IP identity; numeric typos are not DNS names."""
    from custom_components.ip_ban_manager.ban_lookup import _normalize_remote_addr
    from custom_components.ip_ban_manager.ip_utils import is_allowlist_hostname

    assert _normalize_remote_addr(ip_address("fe80::1%eth0")) == ip_address("fe80::1")
    assert not is_allowlist_hostname("192.168.1.999")
    assert is_allowlist_hostname("phone.example.test")


@pytest.mark.asyncio
async def test_corrupt_geoip_result_is_negative_cached(hass):
    """Corrupt records fail closed without repeating the failing read per request."""
    import struct

    await setup_ip_ban_manager(hass)
    reader = Mock()
    reader.get.side_effect = struct.error("corrupt data")
    hass.http.app[KEY_GEOIP_READER] = reader
    for _ in range(5):
        assert (
            geoip.geoip_location_details_for_ip(
                hass, ip_address("8.8.4.4"), require_geoip_enabled=False
            )
            is None
        )
    reader.get.assert_called_once()


@pytest.mark.asyncio
async def test_reverse_dns_deduplicates_concurrent_requests(hass, monkeypatch):
    """Concurrent failures from one IP must share a single external lookup."""
    import asyncio

    from custom_components.ip_ban_manager import reverse_dns

    await setup_ip_ban_manager(hass)
    lookup = AsyncMock(return_value="test.example.invalid")
    monkeypatch.setattr(reverse_dns, "_async_external_reverse_dns_name", lookup)
    names = await asyncio.gather(
        *(
            reverse_dns.async_reverse_dns_name(hass, ip_address("8.8.4.4"))
            for _ in range(10)
        )
    )
    assert names == ["test.example.invalid"] * 10
    lookup.assert_awaited_once()
    await reverse_dns.async_stop_reverse_dns(hass)


@pytest.mark.parametrize("value", [float("inf"), float("nan"), None, {}, True, 3.5])
def test_malformed_thresholds_are_validation_errors(value):
    """Malformed API values cannot escape as a 500 or truncate silently."""
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.ip_ban_manager.entry_helpers import (
        normalize_login_attempts_threshold,
    )

    with pytest.raises(HomeAssistantError):
        normalize_login_attempts_threshold(value)


def test_backup_rejects_nginx_secret_injection():
    """Backup fields interpolated into NGINX require a safe token alphabet."""
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.ip_ban_manager.backup import _npm_from_import

    with pytest.raises(HomeAssistantError, match="region_auth_secret"):
        _npm_from_import(
            {"nginx_proxy_manager": {"region_auth_secret": 'x"; return 200; #'}}
        )


@pytest.mark.asyncio
async def test_history_load_keeps_events_received_during_read(hass, monkeypatch):
    """Do not claim loaded early or lose requests while storage is being read."""
    import asyncio

    from homeassistant.util import dt as dt_util

    from custom_components.ip_ban_manager import activity

    entered, release = asyncio.Event(), asyncio.Event()

    async def load():
        entered.set()
        await release.wait()
        return {
            "events": [{"ip": "8.8.8.8", "timestamp": dt_util.utcnow().isoformat()}]
        }

    store = SimpleNamespace(async_load=load, async_save=AsyncMock())
    monkeypatch.setattr(activity, "_history_store", lambda _hass: store)
    activity.async_start_history(hass)
    try:
        await entered.wait()
        assert not hass.data.get(activity.KEY_ACTIVITY_HISTORY_LOADED)
        activity.record_activity(hass, source="home_assistant", ip="1.1.1.1")
        release.set()
        await hass.data[activity.KEY_ACTIVITY_HISTORY_LOAD_TASK]
        assert {event["ip"] for event in activity._history(hass)} == {
            "8.8.8.8",
            "1.1.1.1",
        }
    finally:
        release.set()
        await activity.async_flush_history(hass)


@pytest.mark.asyncio
async def test_history_read_failure_does_not_overwrite_disk(hass, monkeypatch):
    """A failed read cannot turn an empty in-memory buffer into a disk replacement."""
    from custom_components.ip_ban_manager import activity

    store = SimpleNamespace(
        async_load=AsyncMock(side_effect=OSError("read failed")), async_save=AsyncMock()
    )
    monkeypatch.setattr(activity, "_history_store", lambda _hass: store)
    activity.async_start_history(hass)
    await hass.data[activity.KEY_ACTIVITY_HISTORY_LOAD_TASK]
    assert not hass.data.get(activity.KEY_ACTIVITY_HISTORY_LOADED)
    await activity.async_flush_history(hass)
    store.async_save.assert_not_awaited()
