"""Reproduce remaining independent-audit findings against runtime code."""

from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.ip_ban_manager import (
    activity,
    config_flow,
    legacy_migration,
    panel,
)
from custom_components.ip_ban_manager.const import DOMAIN
from custom_components.ip_ban_manager.ip_utils import is_allowlist_hostname
from custom_components.ip_ban_manager.storage_keys import KEY_LEGACY_FOLDER_CLEANED

from .test_setup import setup_ip_ban_manager


@pytest.mark.parametrize(
    "value", ["0x", "0x.1", "0x7f.1", "0x7f000001", "0x7f.0x0.0x0.0x1"]
)
def test_numeric_resolver_spellings_are_not_hostnames(value):
    """Legacy numeric resolver spellings must not become trusted DNS names."""
    assert not is_allowlist_hostname(value)


@pytest.mark.asyncio
async def test_unexpected_history_load_error_blocks_saving(hass, monkeypatch):
    """An unexpected storage failure must not permit overwriting unread history."""
    from types import SimpleNamespace

    store = SimpleNamespace(
        async_load=AsyncMock(side_effect=RuntimeError("storage failed")),
        async_save=AsyncMock(),
    )
    monkeypatch.setattr(activity, "_history_store", lambda _: store)
    await activity._async_load_history(hass)
    await activity._async_save_history(hass)
    store.async_save.assert_not_awaited()
    assert "storage failed" in hass.data[activity.KEY_ACTIVITY_HISTORY_ERROR]


@pytest.mark.asyncio
async def test_failed_legacy_cleanup_can_retry(hass, monkeypatch):
    """Do not permanently mark failed cleanup as completed."""
    monkeypatch.setattr(
        legacy_migration, "move_legacy_component_folder", lambda _: ["failed"]
    )
    monkeypatch.setattr(
        legacy_migration, "async_update_legacy_folder_cleanup_issue", lambda *_: None
    )
    await legacy_migration.async_cleanup_legacy_component_folder(hass)
    assert not hass.data.get(KEY_LEGACY_FOLDER_CLEANED)


@pytest.mark.asyncio
async def test_options_flow_cannot_disable_required_geoip(hass):
    """The legacy management form must obey the same guard as the panel."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            "public_region_enabled": True,
            "public_region_rules": {"CA": 5},
            "geoip_enabled": True,
        },
    )
    flow = config_flow.OptionsFlow(entry)
    flow.hass = hass
    with pytest.raises(HomeAssistantError, match="GeoIP"):
        await flow._async_save_management_changes(
            ["192.168.1.1"],
            [],
            [],
            True,
            True,
            True,
            True,
            False,
            False,
            True,
            False,
            5,
        )
    assert entry.options["geoip_enabled"] is True


@pytest.mark.asyncio
async def test_direct_regional_threshold_is_not_silently_clamped(hass):
    """Direct option writes must reject out-of-range regional thresholds."""
    await setup_ip_ban_manager(hass)
    with pytest.raises(HomeAssistantError, match="100"):
        await panel.async_panel_set_options(
            hass, {"regional_login_thresholds": {"CA": 150}}
        )


@pytest.mark.asyncio
async def test_npm_permission_failure_is_not_expired_authentication():
    """A valid account without permission needs a distinct actionable error."""
    from types import SimpleNamespace

    from custom_components.ip_ban_manager import nginx_proxy_manager as npm

    with pytest.raises(HomeAssistantError, match="permissions") as error:
        await npm.NpmClient._response_json(
            SimpleNamespace(status=403, json=AsyncMock(return_value={}))
        )
    assert not isinstance(error.value, npm.NpmAuthenticationError)


@pytest.mark.asyncio
async def test_npm_location_payload_drops_response_metadata():
    """Read-only GET fields must not be sent back to strict PUT schemas."""
    from custom_components.ip_ban_manager import nginx_proxy_manager as npm

    client = object.__new__(npm.NpmClient)
    client._json = AsyncMock(
        side_effect=[
            {
                "advanced_config": None,
                "locations": [
                    {
                        "path": "/camera",
                        "forward_host": "192.0.2.10",
                        "created_on": "today",
                        "forward_path": None,
                        "access_list_id": 4,
                    }
                ],
                "meta": {
                    "nginx_online": False,
                    "nginx_err": "old error",
                    "owner_note": "keep",
                },
            },
            {},
        ]
    )
    await client.update_proxy_host_policy(1, "")
    payload = client._json.await_args.kwargs["payload"]
    assert payload["locations"] == [
        {"path": "/camera", "forward_host": "192.0.2.10", "access_list_id": 4}
    ]
    assert payload["meta"] == {"owner_note": "keep"}


def test_invalid_stored_npm_secret_is_replaced():
    """Pre-validation saved secrets can recover without storage edits."""
    from custom_components.ip_ban_manager import nginx_proxy_manager as npm

    config = npm._ensure_region_auth_secret({"region_auth_secret": 'broken"secret'})
    assert config["region_auth_secret"] != 'broken"secret'
    assert npm._ensure_region_auth_secret(config) == config


@pytest.mark.asyncio
async def test_null_npm_locations_and_ids_are_omitted():
    """Tolerate absent location lists without sending invalid null identifiers."""
    from custom_components.ip_ban_manager import nginx_proxy_manager as npm

    client = object.__new__(npm.NpmClient)
    client._json = AsyncMock(side_effect=[{"locations": None}, {}])
    await client.update_proxy_host_policy(1, "")
    assert client._json.await_args.kwargs["payload"]["locations"] == []
    client._json = AsyncMock(
        side_effect=[{"locations": [{"id": None, "path": "/camera"}]}, {}]
    )
    await client.update_proxy_host_policy(1, "")
    assert client._json.await_args.kwargs["payload"]["locations"] == [
        {"path": "/camera"}
    ]


@pytest.mark.asyncio
async def test_token_recovery_does_not_clear_policy_failure(hass):
    """Independent failures must recover independently."""
    from custom_components.ip_ban_manager import nginx_proxy_manager as npm

    await setup_ip_ban_manager(hass)
    npm._record_npm_failure(hass, HomeAssistantError("policy rejected"))
    npm._record_npm_failure(hass, HomeAssistantError("token rejected"), origin="token")
    npm._clear_sync_failure(hass, origin="token")
    runtime = npm._runtime(hass)
    assert runtime["last_error"] == "policy rejected"
    assert runtime["sync_failures"] == 2
    npm._clear_sync_failure(hass, origin="policy")
    assert "sync_failures" not in runtime


@pytest.mark.asyncio
async def test_unconfigured_npm_enable_does_not_create_failure(hass):
    """Incomplete setup is a form error, not a failed remote policy sync."""
    from custom_components.ip_ban_manager import nginx_proxy_manager as npm

    await setup_ip_ban_manager(hass)
    with pytest.raises(HomeAssistantError, match="Select"):
        await npm.async_enable_npm(hass)
    assert not npm._runtime(hass).get("sync_failures")


@pytest.mark.asyncio
async def test_local_region_cannot_restart_after_stop(hass, monkeypatch):
    """Late panel requests cannot create enrichment work during teardown."""
    from custom_components.ip_ban_manager import geoip

    lookup = AsyncMock()
    monkeypatch.setattr(geoip, "_async_local_geoip_region", lookup)
    await geoip.async_stop_local_region(hass)
    assert await geoip.async_local_geoip_region(hass) is None
    lookup.assert_not_awaited()


def test_openable_but_corrupt_database_preserves_old_file(tmp_path, monkeypatch):
    """Probe every candidate before the atomic replacement, not just metadata."""
    import gzip
    import io
    from unittest.mock import Mock

    from custom_components.ip_ban_manager import geoip

    path = tmp_path / "database.mmdb"
    path.write_bytes(b"previous working database")
    candidate = Mock()
    candidate.get.side_effect = [
        {"country": {"iso_code": "CA"}},
        ValueError("corrupt record"),
    ]
    monkeypatch.setattr(
        geoip, "geoip_download_urls", lambda: ["https://audit.invalid/db"]
    )
    monkeypatch.setattr(
        geoip,
        "open_geoip_download_url",
        lambda _: io.BytesIO(gzip.compress(b"x" * 2048)),
    )
    monkeypatch.setattr(geoip, "open_geoip_reader", lambda _: (candidate, 1))
    with pytest.raises(HomeAssistantError):
        geoip.download_geoip_database_to_path(path)
    assert path.read_bytes() == b"previous working database"
    candidate.close.assert_called_once()


@pytest.mark.parametrize("post_replace", [False, True])
def test_geoip_partial_or_reopened_corruption_keeps_previous(
    tmp_path, monkeypatch, post_replace
):
    """Partial probe success and fresh-open failure cannot destroy the database."""
    import gzip
    import io
    from unittest.mock import Mock

    from custom_components.ip_ban_manager import geoip

    path = tmp_path / "database.mmdb"
    path.write_bytes(b"known good")
    candidate = Mock()
    candidate.get.side_effect = (
        [{"country": "CA"}, {"country": "CA"}, {"country": "CA"}]
        if post_replace
        else [{"country": "CA"}, None, None]
    )
    monkeypatch.setattr(
        geoip, "geoip_download_urls", lambda: ["https://audit.invalid/db"]
    )
    monkeypatch.setattr(
        geoip,
        "open_geoip_download_url",
        lambda _: io.BytesIO(gzip.compress(b"x" * 2048)),
    )
    monkeypatch.setattr(
        geoip, "open_geoip_reader", Mock(side_effect=[(candidate, 1), None])
    )
    with pytest.raises(HomeAssistantError):
        geoip.download_geoip_database_to_path(path)
    assert path.read_bytes() == b"known good"
    candidate.close.assert_called_once()


@pytest.mark.asyncio
async def test_npm_rollback_read_failure_keeps_original_cause():
    """A secondary network error must not turn a rejected policy into an outage."""
    from custom_components.ip_ban_manager import nginx_proxy_manager as npm

    client = AsyncMock()
    original = npm.NpmConfigurationError("invalid location")
    client.update_proxy_host_policy.side_effect = original
    client.proxy_hosts.side_effect = npm.NpmConnectionError("offline during rollback")
    host = npm.NpmProxyHost(1, ("audit.test",), 0, True, "")
    with pytest.raises(HomeAssistantError, match="invalid location") as error:
        await npm._apply_proxy_policy(client, host, 0, ["deny all;"])
    assert error.value.__cause__ is original
    assert not npm._sync_error_is_transient(error.value)


@pytest.mark.asyncio
async def test_dns_allowance_does_not_delete_deliberate_exact_ban(hass, monkeypatch):
    """Startup reconciliation must not treat a dynamic DNS match as explicit consent."""
    from ipaddress import ip_address, ip_network

    from homeassistant.components.http.ban import KEY_BAN_MANAGER, IpBan

    from custom_components.ip_ban_manager import ban_ops
    from custom_components.ip_ban_manager.storage_keys import KEY_ALLOWLIST

    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "ip_addresses": ["phone.example.test"]}
    )
    address = ip_address("8.8.8.8")
    hass.http.app[KEY_ALLOWLIST] = (ip_network("8.8.8.8/32"),)
    hass.http.app[KEY_BAN_MANAGER].ip_bans_lookup[address] = IpBan(address)
    assert await ban_ops.async_remove_allowlisted_ip_bans(hass) == []
    assert dict.__contains__(hass.http.app[KEY_BAN_MANAGER].ip_bans_lookup, address)
