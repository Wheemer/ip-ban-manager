"""Regression tests for the November lifecycle and concurrency audit."""

from __future__ import annotations

import asyncio
import threading
from datetime import timedelta
from ipaddress import ip_address, ip_network
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.components import persistent_notification
from homeassistant.components.http.ban import IpBan

from custom_components.ip_ban_manager import (
    ban_lookup,
    ban_ops,
    dns_allowlist,
    geoip,
    http_views,
    internal_networks,
)
from custom_components.ip_ban_manager import nginx_proxy_manager as npm
from custom_components.ip_ban_manager import notifications, panel, reverse_dns
from custom_components.ip_ban_manager.const import CONF_BAN_NOTIFICATIONS_ENABLED
from custom_components.ip_ban_manager.storage_keys import (
    KEY_ALLOWLIST,
    KEY_BAN_FILE_WRITE_LOCK,
    KEY_CONFIG_ENTRY,
    KEY_REVERSE_DNS_CACHE,
)


@pytest.mark.asyncio
async def test_concurrent_unbans_preserve_both_removals(monkeypatch):
    """Waiting removals must mutate the latest lookup under its lock."""
    a, b = ip_address("203.0.113.1"), ip_address("203.0.113.2")
    lock = asyncio.Lock()
    manager = SimpleNamespace(ip_bans_lookup={a: IpBan(a), b: IpBan(b)})
    hass = SimpleNamespace(
        data={KEY_BAN_FILE_WRITE_LOCK: lock},
        http=SimpleNamespace(app={ban_ops.KEY_FAILED_LOGIN_ATTEMPTS: {}}),
    )
    monkeypatch.setattr(ban_ops, "ban_manager", lambda _: manager)
    monkeypatch.setattr(ban_ops, "_async_rewrite_ip_bans_file_unlocked", AsyncMock())
    monkeypatch.setattr(ban_ops, "dismiss_ban_notification_for_ips", Mock())
    monkeypatch.setattr(ban_ops, "record_ip_unbanned", Mock())
    await lock.acquire()
    tasks = [
        asyncio.create_task(ban_ops.async_remove_ip_ban(hass, str(address)))
        for address in (a, b)
    ]
    await asyncio.sleep(0)
    lock.release()
    await asyncio.gather(*tasks)
    assert manager.ip_bans_lookup == {}


@pytest.mark.asyncio
async def test_cancelled_commit_finishes_executor_before_unlock(monkeypatch):
    """Cancellation cannot leave a durable write running behind a released lock."""
    a, b = ip_address("203.0.113.1"), ip_address("203.0.113.2")
    lock = asyncio.Lock()
    hass = SimpleNamespace(data={KEY_BAN_FILE_WRITE_LOCK: lock})
    manager = SimpleNamespace(ip_bans_lookup={a: IpBan(a)})
    started, release = threading.Event(), threading.Event()
    disk = {}

    def write(snapshot):
        started.set()
        assert release.wait(5)
        disk.update(snapshot)

    async def persist(hass, manager):
        await asyncio.to_thread(write, dict(manager.ip_bans_lookup))

    monkeypatch.setattr(ban_ops, "_async_rewrite_ip_bans_file_unlocked", persist)
    task = asyncio.create_task(
        ban_ops.async_commit_ip_bans(hass, manager, {b: IpBan(b)})
    )
    try:
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        assert lock.locked()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert disk == manager.ip_bans_lookup
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_dns_failure_expires_old_trust(monkeypatch):
    """A brief outage is tolerated, but stale addresses eventually lose trust."""
    hass = SimpleNamespace(
        data={}, http=SimpleNamespace(app={KEY_CONFIG_ENTRY: object()})
    )
    monkeypatch.setattr(
        dns_allowlist, "entry_ip_addresses", lambda _: ["phone.example.org"]
    )
    resolver = AsyncMock(return_value={"203.0.113.9"})
    monkeypatch.setattr(dns_allowlist, "_async_resolve_hostname", resolver)
    clock = [0.0]
    monkeypatch.setattr(dns_allowlist, "monotonic", lambda: clock[0])
    await dns_allowlist.async_refresh_dns_allowlist(hass)
    resolver.return_value = set()
    clock[0] = 60.0
    await dns_allowlist.async_refresh_dns_allowlist(hass)
    assert ip_network("203.0.113.9/32") in hass.http.app[KEY_ALLOWLIST]
    clock[0] = 301.0
    await dns_allowlist.async_refresh_dns_allowlist(hass)
    assert hass.http.app[KEY_ALLOWLIST] == ()


@pytest.mark.asyncio
async def test_dns_cache_capacity_and_expiry(monkeypatch):
    """PTR entries are bounded and expired unrelated addresses are evicted."""
    old = ip_address("203.0.113.1")
    cache = {
        old: reverse_dns.ReverseDNSCacheEntry(
            None, reverse_dns.dt_util.utcnow() - timedelta(days=1)
        )
    }
    hass = SimpleNamespace(http=SimpleNamespace(app={KEY_REVERSE_DNS_CACHE: cache}))
    monkeypatch.setattr(reverse_dns, "metric_increment", Mock())
    monkeypatch.setattr(
        reverse_dns, "_async_local_reverse_dns_name", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(reverse_dns, "REVERSE_DNS_CACHE_MAX_ENTRIES", 2)
    for value in (2, 3, 4):
        await reverse_dns.async_reverse_dns_name(hass, ip_address(f"203.0.113.{value}"))
    assert old not in cache
    assert len(cache) == 2


def test_supervisor_bypass_is_exact(monkeypatch):
    """Absent Supervisor discovery must not trust a complete Docker range."""
    monkeypatch.delenv("SUPERVISOR", raising=False)
    assert ban_lookup._supervisor_internal_networks() == ()
    monkeypatch.setenv("SUPERVISOR", "172.30.32.2")
    assert ban_lookup._supervisor_internal_networks() == (ip_network("172.30.32.2/32"),)


def test_notification_heading_replaces_old_heading():
    """An escalation must not retain the previous heading underneath it."""
    first = notifications.with_notification_heading("Login attempt failed", "Failure")
    second = notifications.with_notification_heading(
        "Repeated allowlisted login failures", first
    )
    assert "**Login attempt failed**" not in second
    assert second.count("**Repeated allowlisted login failures**") == 1


def test_silence_does_not_dismiss_another_source(monkeypatch):
    """A stale action must leave a newer shared login notification alone."""
    hass = SimpleNamespace()
    current = {
        notifications.NOTIFICATION_ID_LOGIN: {"message": "Failure from 203.0.113.99"}
    }
    monkeypatch.setattr(
        persistent_notification, "_async_get_or_create_notifications", lambda _: current
    )
    dismiss = Mock()
    monkeypatch.setattr(persistent_notification, "async_dismiss", dismiss)
    notifications.dismiss_allowlisted_login_notifications(
        hass, ip_address("203.0.113.1")
    )
    dismiss.assert_not_called()


def test_silence_matches_whole_address_not_substring(monkeypatch):
    """Silencing .1 must never dismiss a custom allowlisted alert for .10."""
    hass = SimpleNamespace()
    current = {
        "custom-login": {"message": "Allowlisted login failed from 192.168.1.10"}
    }
    monkeypatch.setattr(
        persistent_notification, "_async_get_or_create_notifications", lambda _: current
    )
    dismiss = Mock()
    monkeypatch.setattr(persistent_notification, "async_dismiss", dismiss)
    notifications.dismiss_allowlisted_login_notifications(
        hass, ip_address("192.168.1.1")
    )
    dismiss.assert_not_called()


def test_allowlisted_notification_obeys_global_mute(monkeypatch):
    """Global mute must apply before persistent notification creation."""
    entry = SimpleNamespace(options={CONF_BAN_NOTIFICATIONS_ENABLED: False}, data={})
    hass = SimpleNamespace(http=SimpleNamespace(app={KEY_CONFIG_ENTRY: entry}))
    create = Mock()
    monkeypatch.setattr(notifications, "create_manager_notification", create)
    notifications.create_allowlisted_login_notification(
        hass, ip_address("203.0.113.1"), "Failure"
    )
    create.assert_not_called()


@pytest.mark.asyncio
async def test_location_cache_is_bounded(monkeypatch):
    """Successive activity batches cannot grow location storage indefinitely."""
    hass = SimpleNamespace(data={})

    async def executor(fn, *args):
        return fn(*args)

    hass.async_add_executor_job = executor
    monkeypatch.setattr(
        panel,
        "_activity_locations_sync",
        lambda _, addresses: {address: "Location" for address in addresses},
    )
    monkeypatch.setattr(panel, "_ACTIVITY_LOCATION_CACHE_MAX_ENTRIES", 2)
    for value in (1, 2, 3):
        await panel._async_activity_locations(hass, [{"ip": f"203.0.113.{value}"}])
    assert len(hass.data[panel._ACTIVITY_LOCATION_CACHE_KEY]) == 2


@pytest.mark.asyncio
async def test_npm_event_does_not_cancel_active_write(monkeypatch):
    """A policy event during sync queues a follow-up instead of interrupting it."""
    hass = SimpleNamespace(
        data={}, async_create_task=lambda coro, *args: asyncio.create_task(coro)
    )
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def sync(_hass):
        calls.append(1)
        entered.set()
        await release.wait()

    monkeypatch.setattr(npm, "async_sync_npm", sync)
    monkeypatch.setattr(npm, "NPM_SYNC_DEBOUNCE_SECONDS", 0)
    npm.schedule_npm_sync(hass)
    task = hass.data[npm.KEY_NPM_SYNC_TASK]
    try:
        await asyncio.wait_for(entered.wait(), 2)
        npm.schedule_npm_sync(hass)
        assert hass.data[npm.KEY_NPM_SYNC_TASK] is task
        assert not task.cancelling()
        release.set()
        await asyncio.wait_for(task, 2)
        assert len(calls) == 2
    finally:
        release.set()
        await npm.unload_npm_sync(hass)


@pytest.mark.asyncio
async def test_internal_self_protection_does_not_trust_ipv6_peer(monkeypatch):
    """Self protection uses a host address, not the entire link-local network."""
    adapters = [
        {
            "enabled": True,
            "ipv4": [],
            "ipv6": [{"address": "fe80::1", "network_prefix": 64}],
        }
    ]
    monkeypatch.setattr(
        internal_networks, "async_get_adapters", AsyncMock(return_value=adapters)
    )
    networks = await internal_networks.async_home_assistant_self_networks(
        SimpleNamespace()
    )
    assert ip_network("fe80::1/128") in networks
    assert not any(ip_address("fe80::2") in network for network in networks)


@pytest.mark.asyncio
async def test_http_dispatch_keeps_live_lookup_class(monkeypatch):
    """Ordinary HTTP requests must not reload shared policy modules."""
    lookup = ban_lookup.NetworkAwareBanLookup({}, (), (), False)
    handler = AsyncMock(return_value="ok")
    hass = SimpleNamespace(
        data={http_views.KEY_HTTP_VIEW_HANDLERS: {"status_get": handler}}
    )
    request = SimpleNamespace(app={http_views.KEY_HASS: hass})
    import importlib

    refresh = Mock()
    monkeypatch.setattr(importlib, "reload", refresh)
    assert (
        await http_views.async_dispatch_http_view(object(), request, "status_get")
        == "ok"
    )
    refresh.assert_not_called()
    assert isinstance(lookup, ban_lookup.NetworkAwareBanLookup)


@pytest.mark.asyncio
async def test_failed_platform_unload_keeps_runtime(hass, monkeypatch):
    """A refused platform unload leaves active services and hooks untouched."""
    import custom_components.ip_ban_manager as integration

    entry = object()
    teardown = Mock()
    monkeypatch.setattr(
        hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(integration, "_unregister_http_views", teardown)
    assert await integration.async_unload_entry(hass, entry) is False
    teardown.assert_not_called()


@pytest.mark.asyncio
async def test_npm_cancel_waits_for_remote_transaction():
    """Cancellation is delivered only after a remote policy operation finishes."""
    hass = SimpleNamespace(data={})
    entered, release = asyncio.Event(), asyncio.Event()
    completed = []

    @npm._serialized_policy
    async def operation(hass):
        entered.set()
        await release.wait()
        completed.append(True)

    task = asyncio.create_task(operation(hass))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert completed == [True]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_geoip_close_waits_for_worker_lookup(monkeypatch):
    """Closing a reader cannot race a background activity lookup using it."""
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()

    class Reader:
        def get(self, address):
            entered.set()
            assert release.wait(5)
            assert not closed.is_set()
            return {}

        def close(self):
            closed.set()

    hass = SimpleNamespace(
        data={}, http=SimpleNamespace(app={geoip.KEY_GEOIP_READER: Reader()})
    )
    monkeypatch.setattr(geoip, "metric_increment", Mock())
    lookup = asyncio.create_task(
        asyncio.to_thread(
            geoip.geoip_location_details_for_ip, hass, ip_address("8.8.8.8")
        )
    )
    closer = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        closer = asyncio.create_task(asyncio.to_thread(geoip.close_geoip_reader, hass))
        await asyncio.sleep(0)
        assert not closed.is_set()
        release.set()
        await lookup
        await closer
        assert closed.is_set()
    finally:
        release.set()
        await asyncio.gather(
            *[task for task in (lookup, closer) if task is not None],
            return_exceptions=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("original_enabled", [False, True])
async def test_failed_configuration_apply_restores_options(
    monkeypatch, original_enabled
):
    """A later apply error must restore the original persisted and live settings."""
    from custom_components.ip_ban_manager import entry_helpers, network_policy
    from custom_components.ip_ban_manager.configuration_transaction import (
        configuration_transaction,
    )

    original = {"original": True, "npm": {"enabled": original_enabled}}
    entry = SimpleNamespace(options=original, data={})

    def update(entry, **values):
        entry.options = values["options"]

    hass = SimpleNamespace(
        data={},
        http=SimpleNamespace(app={KEY_CONFIG_ENTRY: entry}),
        config_entries=SimpleNamespace(async_update_entry=update),
    )
    monkeypatch.setattr(dns_allowlist, "async_refresh_dns_allowlist", AsyncMock())
    monkeypatch.setattr(entry_helpers, "entry_geoip_enabled", lambda _: False)
    monkeypatch.setattr(entry_helpers, "entry_sidebar_panel_enabled", lambda _: True)
    apply = Mock()
    monkeypatch.setattr(network_policy, "apply_ban_settings", apply)
    monkeypatch.setattr(network_policy, "apply_blocked_networks", Mock())
    monkeypatch.setattr(geoip, "close_geoip_reader", Mock())
    monkeypatch.setattr(panel, "async_register_panel", AsyncMock())
    monkeypatch.setattr(npm, "schedule_npm_sync", Mock())
    monkeypatch.setattr(
        npm, "entry_npm_config", lambda entry: entry.options.get("npm", {})
    )
    disable = AsyncMock()
    sync = AsyncMock()
    monkeypatch.setattr(npm, "async_disable_npm", disable)
    monkeypatch.setattr(npm, "async_sync_npm", sync)
    with pytest.raises(OSError, match="disk full"):
        async with configuration_transaction(hass):
            entry.options = {"changed": True, "npm": {"enabled": not original_enabled}}
            raise OSError("disk full")
    assert entry.options == original
    apply.assert_called_once_with(hass, entry)
    if original_enabled:
        sync.assert_awaited_once_with(hass)
    else:
        disable.assert_awaited_once_with(hass)


def test_rewriting_identical_notification_does_not_recreate(monkeypatch):
    """An identical body/title must not trigger repeated notification updates."""
    hass = SimpleNamespace(data={}, http=SimpleNamespace(app={}))
    current = {
        notifications.NOTIFICATION_ID_LOGIN: {
            "message": "Failure from 8.8.8.8",
            "title": "Login attempt failed",
        }
    }

    def create(hass, message, title, notification_id):
        current[notification_id] = {"message": message, "title": title}

    monkeypatch.setattr(
        persistent_notification, "_async_get_or_create_notifications", lambda _: current
    )
    creator = Mock(side_effect=create)
    monkeypatch.setattr(persistent_notification, "async_create", creator)
    monkeypatch.setattr(notifications, "geoip_notification_detail", lambda *args: None)
    notifications.add_manager_links_to_http_notifications(hass)
    assert creator.call_count == 1
    notifications.add_manager_links_to_http_notifications(hass)
    assert creator.call_count == 1


def test_untrusted_source_not_labelled_allowlisted_at_ten_attempts(monkeypatch):
    """The escalation label requires trust, not just an attempt count."""
    address = ip_address("8.8.8.8")
    hass = SimpleNamespace(
        data={},
        http=SimpleNamespace(
            app={notifications.KEY_FAILED_LOGIN_ATTEMPTS: {address: 10}}
        ),
    )
    current = {
        notifications.NOTIFICATION_ID_LOGIN: {
            "message": "Failure from 8.8.8.8",
            "title": "Login attempt failed",
        }
    }
    monkeypatch.setattr(
        persistent_notification, "_async_get_or_create_notifications", lambda _: current
    )
    creator = Mock()
    monkeypatch.setattr(persistent_notification, "async_create", creator)
    monkeypatch.setattr(notifications, "geoip_notification_detail", lambda *args: None)
    notifications.add_manager_links_to_http_notifications(hass)
    assert "Repeated allowlisted" not in creator.call_args.args[1]


def test_npm_renders_resolved_dns_addresses(monkeypatch):
    """NPM accepts DDNS allowlist entries and never writes a hostname as CIDR."""
    hass = SimpleNamespace(
        data={
            dns_allowlist.KEY_DNS_ALLOWLIST_CACHE: {
                "phone.example.org": {"203.0.113.9", "2001:db8::9"}
            }
        },
        http=SimpleNamespace(app={}),
    )
    monkeypatch.setattr(
        npm, "entry_ip_addresses", lambda _: ["phone.example.org", "192.168.1.0/24"]
    )
    monkeypatch.setattr(npm, "entry_blocked_networks", lambda _: [])
    monkeypatch.setattr(npm, "entry_allowlisted_logins_can_ban", lambda _: False)
    rules = npm._policy_rules(hass, object(), True, include_callbacks=False)
    assert "allow 203.0.113.9/32;" in rules
    assert "allow 2001:db8::9/128;" in rules
    assert "allow 192.168.1.0/24;" in rules
    assert rules[-1] == "deny all;"


@pytest.mark.asyncio
async def test_failed_setup_cleans_partial_runtime(monkeypatch):
    """Even failed platform cleanup must not leave partially installed hooks."""
    import custom_components.ip_ban_manager as integration

    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_unload_platforms=AsyncMock(
                side_effect=RuntimeError("platform failed")
            )
        )
    )
    cleanup = AsyncMock()
    monkeypatch.setattr(
        integration,
        "_async_setup_entry",
        AsyncMock(side_effect=ValueError("setup failed")),
    )
    monkeypatch.setattr(integration, "_async_unload_runtime", cleanup)
    with pytest.raises(ValueError, match="setup failed"):
        await integration.async_setup_entry(hass, object())
    cleanup.assert_awaited_once_with(hass)


@pytest.mark.asyncio
async def test_restore_refreshes_dns_before_reporting_success(hass, monkeypatch):
    """Backup application immediately resolves hostname entries."""
    from custom_components.ip_ban_manager import backup

    from .test_setup import setup_ip_ban_manager

    await setup_ip_ban_manager(hass)
    refresh = AsyncMock()
    monkeypatch.setattr(backup, "async_refresh_dns_allowlist", refresh)
    await backup.async_apply_config_backup_payload(
        hass, {"settings": {"ip_addresses": ["phone.example.org"]}}
    )
    refresh.assert_awaited_once_with(hass)


@pytest.mark.asyncio
async def test_stop_history_does_not_unsubscribe_consumed_listener(monkeypatch):
    """HA already removes one-shot listeners before calling their handler."""
    from custom_components.ip_ban_manager import activity

    unsubscribe = Mock(side_effect=AssertionError("listener already removed"))
    listen = Mock(return_value=unsubscribe)
    hass = SimpleNamespace(
        data={},
        bus=SimpleNamespace(async_listen_once=listen),
        async_create_task=lambda coro, *args: asyncio.create_task(coro),
    )
    monkeypatch.setattr(activity, "_async_load_history", AsyncMock())
    save = AsyncMock()
    monkeypatch.setattr(activity, "_async_save_history", save)
    activity.async_start_history(hass)
    await listen.call_args.args[1](object())
    unsubscribe.assert_not_called()
    save.assert_awaited_once_with(hass)
