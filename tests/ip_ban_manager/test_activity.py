"""Tests for bounded incoming activity tracking."""

from __future__ import annotations

from ipaddress import ip_network
from unittest.mock import AsyncMock

import pytest

from custom_components.ip_ban_manager import activity


def test_parse_npm_access_line() -> None:
    """Standard Nginx access lines expose the client and request details."""
    event = activity.parse_npm_access_line(
        '203.0.113.8 - - [03/Oct/2026:12:34:56 -0230] "GET /login?x=1 HTTP/1.1" 401 123 "-" "test"',
        host="ha.example.test",
    )

    assert event is not None
    assert event["source"] == "nginx_proxy_manager"
    assert event["ip"] == "203.0.113.8"
    assert event["method"] == "GET"
    assert event["path"] == "/login"
    assert event["status"] == 401
    assert event["host"] == "ha.example.test"
    assert event["detail"] == "NPM access log"
    assert event["timestamp"] == "2026-10-03T12:34:56-02:30"

    blocked = activity.parse_npm_access_line(
        '203.0.113.8 - - [03/Oct/2026:12:34:56 -0230] "GET / HTTP/1.1" 403 12',
        host="ha.example.test",
    )
    assert blocked is not None
    assert isinstance(blocked["detail"], str)
    assert "possible IP Ban Manager edge-policy deny" in blocked["detail"]

    native = activity.parse_npm_access_line(
        "[03/Oct/2026:12:34:57 -0230] - 200 403 - GET https ha.example.test "
        '"/admin?next=/" [Client 203.0.113.9] [Length 12] [Gzip -] '
        '[Sent-to 192.168.1.40:8123] "Test client" "-"',
        host="ha.example.test",
    )
    assert native is not None
    assert native["ip"] == "203.0.113.9"
    assert native["path"] == "/admin"
    assert native["status"] == 403


def test_parse_npm_access_line_rejects_non_access_text() -> None:
    """NPM error and system lines are not displayed as incoming activity."""
    assert activity.parse_npm_access_line("2026-10-03 ERROR nginx is offline") is None


def test_activity_is_grouped_to_one_summary_per_ip() -> None:
    """The panel shows one summary row per source IP, not one row per request."""
    events = [
        {
            "source": "home_assistant",
            "ip": "203.0.113.8",
            "path": "/auth/token",
            "method": "POST",
            "status": 401,
            "timestamp": "2026-10-03T12:00:00+00:00",
        },
        {
            "source": "nginx_proxy_manager",
            "ip": "203.0.113.8",
            "path": "/",
            "method": "GET",
            "status": 403,
            "timestamp": "2026-10-03T12:01:00+00:00",
        },
        {
            "source": "home_assistant",
            "ip": "203.0.113.9",
            "path": "/",
            "method": "GET",
            "status": 401,
            "timestamp": "2026-10-03T12:02:00+00:00",
        },
    ]

    grouped = activity.group_activity(events)

    assert [event["ip"] for event in grouped] == ["203.0.113.9", "203.0.113.8"]
    assert grouped[1]["request_count"] == 2
    assert grouped[1]["sources"] == ["home_assistant", "nginx_proxy_manager"]
    assert grouped[1]["path"] == "/"


def test_allowlisted_activity_is_hidden_from_panel_views() -> None:
    """Trusted sources are omitted from the activity area."""
    events = [
        {"ip": "192.168.1.20", "timestamp": "2026-10-03T12:00:00+00:00"},
        {"ip": "203.0.113.8", "timestamp": "2026-10-03T12:01:00+00:00"},
    ]

    filtered = activity.without_allowlisted_activity(
        events, (ip_network("192.168.1.0/24"),)
    )

    assert [event["ip"] for event in filtered] == ["203.0.113.8"]


def test_policy_denied_activity_is_hidden_from_panel_views() -> None:
    """Recent and historical views contain only requests that reached a service."""
    events = [
        {
            "source": "nginx_proxy_manager",
            "ip": "203.0.113.8",
            "status": 403,
        },
        {
            "source": "home_assistant",
            "ip": "203.0.113.9",
            "status": 403,
        },
        {
            "source": "nginx_proxy_manager",
            "ip": "203.0.113.10",
            "status": 401,
        },
    ]

    filtered = activity.without_policy_denied_activity(events)

    assert [event["ip"] for event in filtered] == ["203.0.113.10"]


def test_old_activity_is_not_recent() -> None:
    """The Recent view excludes stale proxy-log tail entries."""
    assert not activity.is_recent_activity({"timestamp": "2026-10-01T12:00:00+00:00"})


def test_unparseable_npm_timestamp_is_rejected() -> None:
    """An unknown log timestamp must never be promoted to the current time."""
    assert (
        activity.parse_npm_access_line(
            '203.0.113.8 - - [not-a-date] "GET / HTTP/1.1" 401 12'
        )
        is None
    )


def test_activity_is_bounded_and_redacts_internal_state(hass) -> None:
    """Activity remains memory-only, capped, and free of bookkeeping keys."""
    for index in range(activity.MAX_ACTIVITY_EVENTS + 10):
        activity.record_activity(
            hass,
            source="home_assistant",
            ip=f"192.0.2.{index % 250 + 1}",
            path="/auth/login_flow?secret=do-not-store#fragment",
            status=401,
        )

    events = activity.local_activity(hass)
    assert len(events) == activity.MAX_ACTIVITY_EVENTS
    assert events[0]["path"] == "/auth/login_flow"
    assert all(not key.startswith("_") for event in events for key in event)


def test_history_defaults_to_one_day_and_stays_sanitized(hass) -> None:
    """Persisted history uses a one-day retention boundary and safe fields."""
    hass.data[activity.KEY_ACTIVITY_HISTORY_LOADED] = True
    activity.record_activity(
        hass,
        source="home_assistant",
        ip="192.0.2.10",
        path="/api/test",
        method="GET",
        status=401,
    )

    history = activity.history_activity(hass)

    assert len(history) == 1
    assert history[0]["ip"] == "192.0.2.10"
    assert all(not key.startswith("_") for event in history for key in event)
    assert activity.ACTIVITY_HISTORY_MAX_AGE_SECONDS == 24 * 60 * 60
    assert activity.MAX_ACTIVITY_HISTORY_EVENTS == 1000
    assert activity.MAX_ACTIVITY_HISTORY_DISPLAY_EVENTS == 200


@pytest.mark.asyncio
async def test_clear_history_keeps_recent_activity(hass, monkeypatch) -> None:
    """Clearing persisted history does not erase the live recent view."""
    hass.data[activity.KEY_ACTIVITY_HISTORY_LOADED] = True
    activity.record_activity(
        hass, source="home_assistant", ip="192.0.2.11", path="/api/test"
    )
    store = type("StoreStub", (), {"async_save": AsyncMock()})()
    monkeypatch.setattr(activity, "_history_store", lambda _hass: store)

    await activity.async_clear_history(hass)

    assert activity.history_activity(hass) == []
    assert activity.local_activity(hass)[0]["ip"] == "192.0.2.11"
    saved = store.async_save.await_args.args[0]
    assert saved["events"] == []
    assert saved["cleared_at"]
