"""NPM outage retries remain bounded and recover without another policy event."""

import asyncio
import socket
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import ClientConnectionError, ClientSession, web
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ip_ban_manager import nginx_proxy_manager as npm
from custom_components.ip_ban_manager.const import CONF_NPM, DOMAIN
from custom_components.ip_ban_manager.storage_keys import KEY_CONFIG_ENTRY


@pytest.fixture
def retry_env(monkeypatch):
    """Supply an isolated entry and manually advanced retry timers."""
    config = {"enabled": True, "base_url": "http://npm.test:81", "token": "token"}
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NPM: config})
    hass = SimpleNamespace(
        data={},
        http=SimpleNamespace(app={KEY_CONFIG_ENTRY: entry}),
        async_create_task=lambda coro, *args: asyncio.create_task(coro),
    )
    timers = []
    now = dt_util.utcnow()

    def timer(hass, callback, when):
        remove = Mock()
        timers.append((callback, when, remove))
        return remove

    monkeypatch.setattr(npm, "async_track_point_in_utc_time", timer)
    monkeypatch.setattr(npm.dt_util, "utcnow", lambda: now)
    monkeypatch.setattr(npm, "NPM_SYNC_DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(npm.ir, "async_create_issue", Mock())
    monkeypatch.setattr(npm.ir, "async_delete_issue", Mock())
    return hass, config, timers, now


async def run_sync(hass):
    """Finish one debounced synchronization without real delays."""
    npm.schedule_npm_sync(hass)
    await asyncio.wait_for(hass.data[npm.KEY_NPM_SYNC_TASK], 2)


@pytest.mark.asyncio
async def test_outage_recovers_and_uses_latest_policy(retry_env, monkeypatch):
    """Recover automatically and reconcile the newest settings."""
    hass, config, timers, now = retry_env
    attempts = []
    offline = True

    async def sync(hass):
        attempts.append(npm.entry_npm_config(hass.http.app[KEY_CONFIG_ENTRY]))
        if offline:
            raise npm.NpmConnectionError("Server is offline")
        npm._runtime(hass)["last_error"] = None

    monkeypatch.setattr(npm, "async_sync_npm", sync)
    try:
        await run_sync(hass)
        assert len(timers) == 1
        assert timers[-1][1] == now + timedelta(seconds=30)
        assert npm.KEY_NPM_SYNC_TASK not in hass.data
        assert npm._runtime(hass)["last_error"] == "Server is offline"

        # New policy events are coalesced during backoff, not extra network calls.
        for _ in range(10):
            npm.schedule_npm_sync(hass)
        assert len(attempts) == 1
        assert len(timers) == 1
        updated = {**config, "protect_all_domains": True}
        hass.http.app[KEY_CONFIG_ENTRY] = MockConfigEntry(
            domain=DOMAIN, data={CONF_NPM: updated}
        )
        offline = False
        timers[-1][0](now)
        await asyncio.wait_for(hass.data[npm.KEY_NPM_SYNC_TASK], 2)
        assert len(attempts) == 2
        assert attempts[-1]["protect_all_domains"]
        assert npm._runtime(hass)["last_error"] is None
        assert npm._NPM_SYNC_RETRY_DELAY not in hass.data
        assert npm._NPM_SYNC_RETRY_TIMER not in hass.data
        timers[-1][0](now)
        assert npm.KEY_NPM_SYNC_TASK not in hass.data
        offline = True
        await run_sync(hass)
        assert timers[-1][1] == now + timedelta(seconds=30)
    finally:
        await npm.unload_npm_sync(hass)


@pytest.mark.asyncio
async def test_repeated_outage_backoff_is_capped(retry_env, monkeypatch):
    """An extended outage cannot cause busy polling or unlimited backoff."""
    hass, _, timers, now = retry_env
    monkeypatch.setattr(
        npm, "async_sync_npm", AsyncMock(side_effect=TimeoutError("offline"))
    )
    try:
        await run_sync(hass)
        for delay in (30, 60, 120, 240, 300, 300):
            assert timers[-1][1] == now + timedelta(seconds=delay)
            timers[-1][0](now)
            await asyncio.wait_for(hass.data[npm.KEY_NPM_SYNC_TASK], 2)
        assert npm._NPM_SYNC_RETRY_TIMER in hass.data
        npm.ir.async_create_issue.assert_called()
    finally:
        await npm.unload_npm_sync(hass)
    timers[-1][2].assert_called_once()
    assert npm._NPM_SYNC_RETRY_TIMER not in hass.data
    npm.ir.async_delete_issue.assert_called_with(
        hass, DOMAIN, npm.NPM_SYNC_FAILED_ISSUE_ID
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["disable", "disconnect", "unload"])
async def test_pending_retry_cannot_restart_after_stop(retry_env, monkeypatch, action):
    """Disabled, disconnected, and unloaded integrations cannot resume retries."""
    hass, config, timers, now = retry_env
    sync = AsyncMock(side_effect=TimeoutError("offline"))
    monkeypatch.setattr(npm, "async_sync_npm", sync)
    await run_sync(hass)
    callback = timers[-1][0]
    if action == "unload":
        await npm.unload_npm_sync(hass)
    else:
        updated = {**config, "enabled": False} if action == "disable" else {}
        hass.http.app[KEY_CONFIG_ENTRY] = MockConfigEntry(
            domain=DOMAIN, data={CONF_NPM: updated}
        )
    callback(now)
    await asyncio.sleep(0)
    assert npm.KEY_NPM_SYNC_TASK not in hass.data
    assert sync.await_count == 1
    await npm.unload_npm_sync(hass)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        npm.NpmAuthenticationError("sign in"),
        npm.NpmConfigurationError("bad location"),
        HomeAssistantError("HTTP 400"),
    ],
)
async def test_permanent_errors_do_not_retry(retry_env, monkeypatch, error):
    """Authentication and configuration problems require user intervention."""
    hass, _, timers, _ = retry_env
    monkeypatch.setattr(npm, "async_sync_npm", AsyncMock(side_effect=error))
    await run_sync(hass)
    assert timers == []
    assert npm._runtime(hass)["last_error"] == str(error)
    await npm.unload_npm_sync(hass)


@pytest.mark.asyncio
async def test_unload_during_failed_transaction_cannot_schedule_retry(
    retry_env, monkeypatch
):
    """A shielded transaction finishing during unload cannot revive the worker."""
    hass, _, timers, _ = retry_env
    entered, release = asyncio.Event(), asyncio.Event()

    @npm._serialized_policy
    async def sync(hass):
        entered.set()
        await release.wait()
        raise npm.NpmConnectionError("offline")

    monkeypatch.setattr(npm, "async_sync_npm", sync)
    npm.schedule_npm_sync(hass)
    await asyncio.wait_for(entered.wait(), 2)
    task = hass.data[npm.KEY_NPM_SYNC_TASK]
    try:
        unload = asyncio.create_task(npm.unload_npm_sync(hass))
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(unload, 2)
        assert task.done()
        assert timers == []
        assert npm.KEY_NPM_SYNC_TASK not in hass.data
        assert npm._NPM_SYNC_RETRY_TIMER not in hass.data
    finally:
        release.set()
        await npm.unload_npm_sync(hass)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
@pytest.mark.asyncio
async def test_http_temporary_errors_are_retryable_without_json(status):
    """Gateway errors remain retryable even when their response is HTML."""
    response = SimpleNamespace(status=status, json=AsyncMock())
    with pytest.raises(npm.NpmConnectionError):
        await npm.NpmClient._response_json(response)
    response.json.assert_not_awaited()


def test_wrapped_transport_error_remains_retryable():
    """Transactional rollback preserves the original error classification."""
    outer = HomeAssistantError("rollback also failed")
    outer.__cause__ = npm.NpmConnectionError("offline")
    assert npm._sync_error_is_transient(outer)
    assert npm._sync_error_is_transient(ClientConnectionError("offline"))


@pytest.mark.asyncio
async def test_real_http_transport_recovers_after_listener_starts(
    retry_env, monkeypatch, socket_enabled
):
    """Exercise real refused connections, then recover when the HTTP server returns."""
    hass, _, timers, now = retry_env
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    async def probe(request):
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/api/probe", probe)
    runner = web.AppRunner(app)
    await runner.setup()
    async with ClientSession() as session:
        monkeypatch.setattr(npm, "async_get_clientsession", lambda _: session)
        results = []

        async def sync(hass):
            client = npm.NpmClient(hass, f"http://127.0.0.1:{port}", "token")
            results.append(await client._json("GET", "probe"))
            npm._runtime(hass)["last_error"] = None

        monkeypatch.setattr(npm, "async_sync_npm", sync)
        try:
            await run_sync(hass)
            assert results == []
            assert len(timers) == 1
            assert "Could not connect" in npm._runtime(hass)["last_error"]
            await web.TCPSite(runner, "127.0.0.1", port).start()
            timers[-1][0](now)
            await asyncio.wait_for(hass.data[npm.KEY_NPM_SYNC_TASK], 2)
            assert results == [{"ok": True}]
            assert npm._runtime(hass)["last_error"] is None
            assert npm._NPM_SYNC_RETRY_TIMER not in hass.data
        finally:
            await npm.unload_npm_sync(hass)
            await runner.cleanup()
