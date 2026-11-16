"""Exercise actual concurrency boundaries rather than timing-only assumptions."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from ipaddress import ip_address
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.ip_ban_manager import geoip, http_patches, http_views
from custom_components.ip_ban_manager.geoip_cache import GeoIPPrefixCache
from custom_components.ip_ban_manager.runtime_lifecycle import (
    RuntimeClosingError,
    async_drain_runtime,
    open_runtime,
    runtime_operation,
)
from custom_components.ip_ban_manager.storage_keys import KEY_GEOIP_READER


def test_authoritative_prefix_cache_bounds_expiry_and_families():
    """Keep leaf boundaries, expiry, address families, and LRU limits distinct."""
    cache = GeoIPPrefixCache[str](2)
    cache.put(ip_address("8.8.8.8"), 24, 10, "US")
    assert cache.get(ip_address("8.8.8.9"), 1) == "US"
    assert cache.get(ip_address("8.8.9.1"), 1) is None
    assert cache.get(ip_address("2001:4860::1"), 1) is None
    cache.put(ip_address("2001:4860::1"), 48, 10, "IPv6")
    assert cache.get(ip_address("2001:4860::2"), 1) == "IPv6"
    cache.put(ip_address("9.9.9.9"), 32, 10, "CH")
    assert len(cache.entries) == 2
    assert cache.get(ip_address("8.8.8.8"), 1) is None
    assert cache.get(ip_address("9.9.9.9"), 10) is None
    cache.put(ip_address("8.8.8.8"), 128, 10, "invalid")
    assert len(cache.entries) == 1


def test_prefix_hit_avoids_another_decode():
    """Reuse a database leaf without broadening it to a neighboring network."""

    class Reader:
        calls = 0

        def get_with_prefix_len(self, address):
            self.calls += 1
            return {"country": {"iso_code": "US"}}, 24

    reader = Reader()
    hass = SimpleNamespace(
        data={}, http=SimpleNamespace(app={KEY_GEOIP_READER: reader})
    )
    first = geoip.geoip_location_details_for_ip(hass, ip_address("8.8.8.8"))
    assert geoip.geoip_location_details_for_ip(hass, ip_address("8.8.8.9")) == first
    assert reader.calls == 1
    geoip.geoip_location_details_for_ip(hass, ip_address("8.8.9.1"))
    assert reader.calls == 2


@pytest.mark.parametrize("retire", [False, True])
def test_background_decode_does_not_block_cache_or_reader_retirement(retire):
    """An active read cannot block cache hits or be closed underneath itself."""
    started, release, finished = Event(), Event(), Event()

    class Reader:
        closed = False

        def get(self, address):
            if address == "9.9.9.9":
                started.set()
                release.wait(2)
                assert not self.closed
                finished.set()
            return {"country": {"iso_code": "US"}}

        def close(self):
            self.closed = True

    reader = Reader()
    hass = SimpleNamespace(
        data={}, http=SimpleNamespace(app={KEY_GEOIP_READER: reader})
    )
    geoip.geoip_location_details_for_ip(hass, ip_address("8.8.8.8"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            geoip.geoip_location_details_for_ip, hass, ip_address("9.9.9.9")
        )
        try:
            assert started.wait(2)
            if retire:
                geoip.close_geoip_reader(hass)
                assert not reader.closed
            else:
                assert geoip.geoip_location_details_for_ip(hass, ip_address("8.8.8.8"))
            assert (
                not finished.is_set()
            ), "A cache read or retire must not wait for the decoder"
        finally:
            release.set()
        result = pending.result(3)
    if retire:
        assert result is None
        assert reader.closed
        assert geoip._LOOKUP_CACHE_KEY not in hass.http.app


@pytest.mark.asyncio
async def test_runtime_drain_waits_for_operation_not_entire_caller_task():
    """Drain admitted mutations without waiting for a long-running automation."""
    hass = SimpleNamespace(data={})
    entered, release, caller_done = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def caller():
        async with runtime_operation(hass):
            entered.set()
            await release.wait()
        await caller_done.wait()

    active = asyncio.create_task(caller())
    await entered.wait()
    drain = asyncio.create_task(async_drain_runtime(hass))
    await asyncio.sleep(0)
    assert not drain.done()
    with pytest.raises(RuntimeClosingError):
        async with runtime_operation(hass):
            pass
    release.set()
    await asyncio.wait_for(drain, 2)
    assert not active.done()
    open_runtime(hass)
    async with runtime_operation(hass):
        pass
    caller_done.set()
    await active


@pytest.mark.asyncio
async def test_dispatch_refuses_new_calls_during_drain():
    """Do not run a captured handler after unload has stopped admission."""
    handler = AsyncMock()
    hass = SimpleNamespace(
        data={http_views.KEY_HTTP_VIEW_HANDLERS: {"status_get": handler}}
    )
    request = SimpleNamespace(app={http_views.KEY_HASS: hass})
    view = SimpleNamespace(json=lambda data, status_code: (data, status_code))
    await async_drain_runtime(hass)
    response = await http_views.async_dispatch_http_view(view, request, "status_get")
    assert response[1] == 503
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_unavailable_http_responses_do_not_echo_exception_details(monkeypatch):
    """Internal lifecycle exception details must never reach the HTTP response."""
    from aiohttp.web import HTTPServiceUnavailable

    @asynccontextmanager
    async def unavailable(hass):
        raise RuntimeClosingError("private-detail /config/internal-file")
        yield

    hass = SimpleNamespace(
        data={http_views.KEY_HTTP_VIEW_HANDLERS: {"status_get": AsyncMock()}}
    )
    request = SimpleNamespace(app={http_views.KEY_HASS: hass})
    monkeypatch.setattr(http_views, "runtime_operation", unavailable)
    view = SimpleNamespace(json=lambda data, status_code: (data, status_code))
    response = await http_views.async_dispatch_http_view(view, request, "status_get")
    assert response[1] == 503
    assert "private-detail" not in str(response[0])
    monkeypatch.setattr(http_patches, "runtime_operation", unavailable)
    with pytest.raises(HTTPServiceUnavailable) as error:
        await http_patches._allowlist_process_wrong_login(request)
    assert "private-detail" not in error.value.text
    assert error.value.status == 503


@pytest.mark.asyncio
async def test_async_geoip_warmup_is_bounded_and_shared(hass):
    """Bound cold work and share a lookup rather than queueing duplicates."""
    futures = []

    def submit(*args):
        pending = asyncio.get_running_loop().create_future()
        futures.append(pending)
        return pending

    fake = SimpleNamespace(
        data={},
        http=SimpleNamespace(app={KEY_GEOIP_READER: SimpleNamespace(get=lambda _: {})}),
        async_add_executor_job=submit,
    )
    lookups = [
        asyncio.create_task(
            geoip.async_warm_geoip_for_ip(fake, ip_address(f"8.8.8.{index}"))
        )
        for index in range(1, 5)
    ]
    await asyncio.sleep(0)
    duplicate = asyncio.create_task(
        geoip.async_warm_geoip_for_ip(fake, ip_address("8.8.8.1"))
    )
    await asyncio.sleep(0)
    assert len(futures) == 4
    assert not await geoip.async_warm_geoip_for_ip(fake, ip_address("9.9.9.9"))
    assert not any(task.done() for task in lookups)
    for future in futures:
        future.set_result(None)
    assert all(await asyncio.gather(*lookups, duplicate))
    assert not fake.data[geoip._ASYNC_LOOKUPS_KEY]


def test_reload_failure_restores_original_namespaces(monkeypatch):
    """Restore module dictionaries after an interrupted reload pass."""
    import custom_components.ip_ban_manager as integration
    from custom_components.ip_ban_manager import activity

    original = activity.record_activity

    def fail(module):
        activity.record_activity = None
        raise RuntimeError("injected reload failure")

    monkeypatch.setattr(integration.importlib, "reload", fail)
    with pytest.raises(RuntimeError, match="injected"):
        integration._reload_runtime_modules_sync()
    assert activity.record_activity is original


@pytest.mark.asyncio
async def test_reload_cancellation_waits_for_executor():
    """Cancellation cannot run cleanup while the reload worker still mutates code."""
    import custom_components.ip_ban_manager as integration

    pending = asyncio.get_running_loop().create_future()
    fake = SimpleNamespace(data={}, async_add_executor_job=lambda _: pending)
    reload = asyncio.create_task(integration._async_reload_runtime_modules(fake))
    await asyncio.sleep(0)
    reload.cancel()
    await asyncio.sleep(0)
    assert not reload.done()
    assert not pending.cancelled()
    pending.set_result(None)
    with pytest.raises(asyncio.CancelledError):
        await reload


def test_late_reload_failure_restores_root_aliases(monkeypatch):
    """A failure after rebinding must restore the coordinator's imported aliases."""
    import custom_components.ip_ban_manager as integration
    from custom_components.ip_ban_manager import activity

    original = integration._clear_activity
    actual_reload = integration.importlib.reload

    def changed(module):
        result = actual_reload(module)
        if module.__name__ == activity.__name__:

            def replacement(hass):
                return None

            replacement.__module__ = activity.__name__
            replacement.__name__ = "clear_activity"
            result.clear_activity = replacement
        return result

    monkeypatch.setattr(integration.importlib, "reload", changed)
    monkeypatch.setattr(
        integration,
        "_RELOADABLE_BINDINGS",
        {
            **integration._RELOADABLE_BINDINGS,
            "_invalid_audit_binding": ("activity", "missing_audit_attribute"),
        },
    )
    with pytest.raises(AttributeError, match="missing_audit_attribute"):
        integration._reload_runtime_modules_sync()
    assert integration._clear_activity is original
    assert activity.clear_activity is original


def test_reload_rebinds_imported_constant_alias(monkeypatch):
    """Rebind constants changed after an importing module already executed."""
    import custom_components.ip_ban_manager as integration
    from custom_components.ip_ban_manager import const

    original_reload = integration.importlib.reload
    sentinel = "reload-constant-sentinel"

    def changed_constant(module):
        result = original_reload(module)
        if module.__name__ == integration._RELOADABLE_MODULES[-1]:
            const.CONF_ACTIVITY_HISTORY_HOURS = sentinel
        return result

    with monkeypatch.context() as patch:
        patch.setattr(integration.importlib, "reload", changed_constant)
        integration._reload_runtime_modules_sync()
        # The source constant changes after its consumers have already reloaded.
        from custom_components.ip_ban_manager import entry_helpers

        assert entry_helpers.CONF_ACTIVITY_HISTORY_HOURS == sentinel
    integration._reload_runtime_modules_sync()
