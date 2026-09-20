"""Regression coverage for real NPM 2.15.1 failure responses."""

from asyncio import Event
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from custom_components.ip_ban_manager import nginx_proxy_manager as npm
from custom_components.ip_ban_manager.const import CONF_NPM, DOMAIN

from .test_setup import setup_ip_ban_manager


@pytest.mark.asyncio
async def test_expired_token_marks_connection_for_sign_in(hass, monkeypatch):
    """NPM reports expired tokens as 400, not 401."""
    response = SimpleNamespace(
        status=400,
        json=AsyncMock(
            return_value={"error": {"code": 400, "message": "Token has expired"}}
        ),
    )
    session = SimpleNamespace(request=AsyncMock(return_value=response))
    monkeypatch.setattr(npm, "async_get_clientsession", lambda _: session)
    client = npm.NpmClient(hass, "http://npm.test:81", "expired")
    with pytest.raises(npm.NpmAuthenticationError, match="Sign in again"):
        await client.refresh_token()
    assert hass.data[npm.KEY_NPM_RUNTIME]["reauth_required"]


@pytest.mark.asyncio
async def test_http_success_with_offline_host_is_not_success(hass, monkeypatch):
    """NPM may persist invalid config and return HTTP 200 with nginx_online=false."""
    monkeypatch.setattr(
        npm.NpmClient,
        "_json",
        AsyncMock(
            return_value={
                "meta": {
                    "nginx_online": False,
                    "nginx_err": "duplicate location /api/webhook/",
                }
            }
        ),
    )
    client = npm.NpmClient(hass, "http://npm.test:81", "valid")
    with pytest.raises(npm.NpmConfigurationError, match="duplicate location"):
        await client.update_proxy_host_policy(4, "test")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/webhook/", "^~ /api/webhook/", "= /auth/token"])
async def test_custom_callback_conflict_rejected_before_write(path):
    """Detect duplicate generated routes before modifying the host."""
    client = AsyncMock()
    host = npm.NpmProxyHost(4, ("ha.test",), 0, True, "# custom", (path,))
    with pytest.raises(HomeAssistantError, match="conflicts"):
        await npm._apply_proxy_policy(client, host, 0, npm._callback_location_rules([]))
    client.update_proxy_host_policy.assert_not_awaited()


@pytest.mark.asyncio
async def test_custom_location_preserved_and_disconnect_can_remove_conflict():
    """Allow cleanup even when custom locations conflict with our old block."""
    client = AsyncMock()
    original = npm._with_managed_config("# custom", npm._callback_location_rules([]))
    host = npm.NpmProxyHost(4, ("ha.test",), 0, True, original, ("/api/webhook/",))
    await npm._apply_proxy_policy(client, host, 0, None)
    client.update_proxy_host_policy.assert_awaited_once_with(
        4, "# custom", access_list_id=None
    )


@pytest.mark.asyncio
async def test_disable_recovers_host_after_custom_location_conflict(hass, monkeypatch):
    """Disable edge protection after a Custom Location breaks the managed host."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    managed = npm._with_managed_config(
        "# owner configuration", npm._callback_location_rules([])
    )
    config = {
        "base_url": "http://npm.test:81",
        "token": "valid",
        "proxy_host_id": 4,
        "enabled": True,
    }
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_NPM: config}
    )
    client = AsyncMock()
    client.refresh_token.return_value = {"token": "fresh"}
    client.proxy_hosts.return_value = [
        {
            "id": 4,
            "domain_names": ["ha.test"],
            "access_list_id": 0,
            "enabled": True,
            "advanced_config": managed,
            "locations": [
                {"path": "/internal-service/"},
                {"path": "/api/webhook/"},
            ],
            "meta": {
                "nginx_online": False,
                "nginx_err": 'duplicate location "/api/webhook/"',
            },
        }
    ]
    monkeypatch.setattr(npm, "NpmClient", lambda *args: client)

    await npm.async_disable_npm(hass)

    client.update_proxy_host_policy.assert_awaited_once_with(
        4, "# owner configuration", access_list_id=None
    )
    saved = npm.entry_npm_config(entry)
    assert saved["enabled"] is False
    assert saved["token"] == "fresh"


@pytest.mark.asyncio
async def test_failed_activation_rolls_back_our_write_only():
    """Restore the previous policy when nginx rejects our update."""
    client = AsyncMock()
    host = npm.NpmProxyHost(4, ("ha.test",), 9, True, "# custom", ("/other/",))
    rules = ["deny 203.0.113.4;"]
    proposed = npm._with_managed_config(host.advanced_config, rules)
    client.update_proxy_host_policy.side_effect = [
        npm.NpmConfigurationError("offline"),
        None,
    ]
    client.proxy_hosts.return_value = [
        {"id": 4, "access_list_id": 9, "advanced_config": proposed}
    ]
    with pytest.raises(HomeAssistantError, match="previous policy was restored"):
        await npm._apply_proxy_policy(client, host, 0, rules)
    assert client.update_proxy_host_policy.await_count == 2
    client.update_proxy_host_policy.assert_awaited_with(
        4, "# custom", access_list_id=None
    )


@pytest.mark.asyncio
async def test_rollback_does_not_overwrite_concurrent_custom_changes():
    """Do not restore over an owner's intervening change."""
    client = AsyncMock()
    host = npm.NpmProxyHost(4, ("ha.test",), 0, True, "# custom")
    client.update_proxy_host_policy.side_effect = npm.NpmConfigurationError("offline")
    client.proxy_hosts.return_value = [
        {"id": 4, "advanced_config": "# updated by owner"}
    ]
    with pytest.raises(npm.NpmConfigurationError):
        await npm._apply_proxy_policy(client, host, 0, ["deny all;"])
    assert client.update_proxy_host_policy.await_count == 1


@pytest.mark.asyncio
async def test_sign_in_repairs_expired_connection_without_disabling(hass, monkeypatch):
    """Replace expired credentials without first requiring a valid old token."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    config = {
        "base_url": "http://npm.test:81",
        "identity": "owner@test.invalid",
        "token": "expired",
        "proxy_host_id": 4,
        "enabled": True,
    }
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_NPM: config}
    )
    client = AsyncMock()
    client.base_url = config["base_url"]
    client.authenticate.return_value = {"token": "fresh"}
    client.proxy_hosts.return_value = [{"id": 4}]
    monkeypatch.setattr(npm, "NpmClient", lambda *args: client)
    disable = AsyncMock()
    monkeypatch.setattr(npm, "async_disable_npm", disable)
    sync = Mock()
    monkeypatch.setattr(npm, "schedule_npm_sync", sync)
    await npm.async_connect_npm(
        hass, config["base_url"], config["identity"], "new password"
    )
    assert npm.entry_npm_config(entry) == {**config, "token": "fresh"}
    disable.assert_not_awaited()
    sync.assert_called_once_with(hass)


@pytest.mark.asyncio
async def test_failed_sign_in_preserves_existing_connection(hass, monkeypatch):
    """Failed authentication must not erase existing settings."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    config = {
        "base_url": "http://npm.test:81",
        "identity": "owner@test.invalid",
        "token": "expired",
        "proxy_host_id": 4,
        "enabled": True,
    }
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_NPM: config}
    )
    client = AsyncMock()
    client.authenticate.side_effect = HomeAssistantError("Invalid credentials")
    monkeypatch.setattr(npm, "NpmClient", lambda *args: client)
    disable = AsyncMock()
    monkeypatch.setattr(npm, "async_disable_npm", disable)
    with pytest.raises(HomeAssistantError, match="Invalid credentials"):
        await npm.async_connect_npm(
            hass, config["base_url"], config["identity"], "wrong"
        )
    assert npm.entry_npm_config(entry) == config
    disable.assert_not_awaited()


@pytest.mark.asyncio
async def test_token_renewal_scheduled_before_expiry_and_removed_on_unload(
    hass, monkeypatch
):
    """Renew before expiry and cancel the timer on unload."""
    await setup_ip_ban_manager(hass)
    remove = Mock()
    timer = Mock(return_value=remove)
    monkeypatch.setattr(npm, "async_track_point_in_utc_time", timer)
    expires = dt_util.utcnow() + timedelta(days=1)
    npm._persist_npm_config(
        hass,
        {
            "base_url": "http://npm.test:81",
            "token": "token",
            "token_expires": expires.isoformat(),
        },
    )
    assert timer.call_args.args[2] == expires - timedelta(hours=1)
    await npm.unload_npm_sync(hass)
    remove.assert_called_once()
    assert npm.KEY_NPM_TOKEN_TIMER not in hass.data


@pytest.mark.asyncio
async def test_unload_waits_for_cancelled_npm_worker(hass):
    """Unload must not leave a cancelled NPM worker running into reload."""
    await setup_ip_ban_manager(hass)
    worker_started = Event()
    worker_stopped = Event()

    async def worker() -> None:
        worker_started.set()
        try:
            await Event().wait()
        finally:
            worker_stopped.set()

    task = hass.async_create_task(worker())
    hass.data[npm.KEY_NPM_SYNC_TASK] = task
    await worker_started.wait()

    await npm.unload_npm_sync(hass)

    assert task.cancelled()
    assert worker_stopped.is_set()
    assert npm.KEY_NPM_SYNC_TASK not in hass.data


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_renewal_does_not_replace_new_connection(hass, monkeypatch, failure):
    """Ignore results belonging to a replaced connection."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    old = {"base_url": "http://npm.test:81", "token": "old"}
    new = {"base_url": "http://npm.test:81", "token": "new"}
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_NPM: old}
    )

    async def refresh():
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_NPM: new}
        )
        if failure:
            raise HomeAssistantError("Connection lost")
        return {"token": "obsolete"}

    client = AsyncMock()
    client.refresh_token.side_effect = refresh
    monkeypatch.setattr(npm, "NpmClient", lambda *args: client)
    schedule = Mock()
    monkeypatch.setattr(npm, "_schedule_token_refresh", schedule)
    await npm._async_refresh_token(hass)
    assert npm.entry_npm_config(entry) == new
    schedule.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("expired", [False, True])
async def test_renewal_retries_only_transient_errors(hass, monkeypatch, expired):
    """Retry outages but require sign-in for expired credentials."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    config = {"base_url": "http://npm.test:81", "token": "old"}
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_NPM: config}
    )
    client = AsyncMock()
    client.refresh_token.side_effect = (
        npm.NpmAuthenticationError("expired")
        if expired
        else HomeAssistantError("offline")
    )
    monkeypatch.setattr(npm, "NpmClient", lambda *args: client)
    schedule = Mock()
    monkeypatch.setattr(npm, "_schedule_token_refresh", schedule)
    await npm._async_refresh_token(hass)
    assert schedule.call_count == (0 if expired else 1)
    assert npm.entry_npm_config(entry) == config


@pytest.mark.asyncio
async def test_token_renewal_never_updates_proxy_configuration(hass, monkeypatch):
    """Credential maintenance must not alter proxy policy."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    config = {"base_url": "http://npm.test:81", "token": "old", "enabled": True}
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_NPM: config}
    )
    client = AsyncMock()
    client.refresh_token.return_value = {
        "token": "fresh",
        "token_expires": (dt_util.utcnow() + timedelta(days=1)).isoformat(),
    }
    monkeypatch.setattr(npm, "NpmClient", lambda *args: client)
    await npm._async_refresh_token(hass)
    assert npm.entry_npm_config(entry)["token"] == "fresh"
    client.update_proxy_host_policy.assert_not_awaited()
    await npm.unload_npm_sync(hass)
