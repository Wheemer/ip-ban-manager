"""Tests for opt-in blocked-request diagnostics."""

# mypy: ignore-errors

# flake8: noqa
# ruff: noqa: F403,F405

from types import SimpleNamespace

from homeassistant.helpers.http import current_request

from .test_setup import *


def test_lookup_reports_each_enforcement_reason() -> None:
    """The observer receives the effective address and precise block reason."""
    remote_addr = ip_address("203.0.113.25")
    observed: list[tuple[object, str]] = []
    observer = lambda address, reason: observed.append((address, reason))
    request_token = current_request.set(
        cast(Any, SimpleNamespace(method="GET", path="/api/test"))
    )
    try:
        assert remote_addr in ipbm.NetworkAwareBanLookup(
            {remote_addr: IpBan(remote_addr)}, (), (), False,
            internal_bypass_networks=(), blocked_request_observer=observer,
        )
        assert remote_addr in ipbm.NetworkAwareBanLookup(
            {}, (IPv4Network("203.0.113.0/24"),), (), False,
            internal_bypass_networks=(), blocked_request_observer=observer,
        )
        assert remote_addr in ipbm.NetworkAwareBanLookup(
            {}, (), (), False, internal_bypass_networks=(),
            geoip_access_allowed=lambda _address: False,
            blocked_request_observer=observer,
        )
        assert remote_addr in ipbm.NetworkAwareBanLookup(
            {}, (), (), True, internal_bypass_networks=(),
            blocked_request_observer=observer,
        )
    finally:
        current_request.reset(request_token)

    assert observed == [
        (remote_addr, blocked_request_log.BLOCK_REASON_EXACT_BAN),
        (remote_addr, blocked_request_log.BLOCK_REASON_NETWORK),
        (remote_addr, blocked_request_log.BLOCK_REASON_REGION),
        (remote_addr, blocked_request_log.BLOCK_REASON_DEFAULT_DENY),
    ]


def test_lookup_does_not_report_allowed_or_protected_requests() -> None:
    """Allowlist and callback bypasses do not produce blocked diagnostics."""
    remote_addr = ip_address("203.0.113.25")
    observed: list[tuple[object, str]] = []
    lookup = ipbm.NetworkAwareBanLookup(
        {},
        (IPv4Network("0.0.0.0/0"),),
        (IPv4Network("203.0.113.0/24"),),
        True,
        internal_bypass_networks=(),
        geoip_access_allowed=lambda _address: False,
        blocked_request_observer=lambda address, reason: observed.append(
            (address, reason)
        ),
    )
    request_token = current_request.set(
        cast(Any, SimpleNamespace(method="GET", path="/api/test"))
    )
    try:
        assert remote_addr not in lookup
    finally:
        current_request.reset(request_token)

    assert observed == []


def test_blocked_request_log_is_redacted_and_rate_limited(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Diagnostics omit query data and collapse repeated requests."""
    times = iter((1.0, 2.0, 62.0))
    monkeypatch.setattr(blocked_request_log, "monotonic", lambda: next(times))
    request_token = current_request.set(
        cast(
            Any,
            SimpleNamespace(
                method="get",
                path="/api/test",
                rel_url="/api/test?access_token=secret",
            ),
        )
    )
    try:
        with caplog.at_level(logging.WARNING, logger=blocked_request_log.__name__):
            for _ in range(3):
                blocked_request_log.log_blocked_request(
                    hass,
                    ip_address("203.0.113.25"),
                    blocked_request_log.BLOCK_REASON_DEFAULT_DENY,
                )
    finally:
        current_request.reset(request_token)

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == blocked_request_log.__name__
    ]
    assert len(messages) == 2
    assert "203.0.113.25" in messages[0]
    assert "GET '/api/test'" in messages[0]
    assert "outside Allowed IPs" in messages[0]
    assert "access_token" not in " ".join(messages)
    assert "secret" not in " ".join(messages)
    assert "1 similar requests suppressed" in messages[1]


def test_blocked_request_log_escapes_control_characters(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An attacker cannot inject extra lines into the Home Assistant log."""
    request_token = current_request.set(
        cast(Any, SimpleNamespace(method="GET", path="/test\nforged"))
    )
    try:
        with caplog.at_level(logging.WARNING, logger=blocked_request_log.__name__):
            blocked_request_log.log_blocked_request(
                hass,
                ip_address("203.0.113.25"),
                blocked_request_log.BLOCK_REASON_NETWORK,
            )
    finally:
        current_request.reset(request_token)

    message = next(
        record.getMessage()
        for record in caplog.records
        if record.name == blocked_request_log.__name__
    )
    assert "/test\\nforged" in message
    assert "/test\nforged" not in message


def test_blocked_request_log_tracker_is_bounded(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unique attacking addresses cannot grow diagnostics state indefinitely."""
    state = {
        (f"203.0.113.{index}", blocked_request_log.BLOCK_REASON_NETWORK): (
            float(index),
            0,
        )
        for index in range(blocked_request_log.MAX_TRACKED_BLOCKS)
    }
    hass.data[KEY_BLOCKED_REQUEST_LOG_STATE] = state
    monkeypatch.setattr(blocked_request_log, "monotonic", lambda: 5000.0)
    request_token = current_request.set(
        cast(Any, SimpleNamespace(method="GET", path="/"))
    )
    try:
        blocked_request_log.log_blocked_request(
            hass,
            ip_address("198.51.100.7"),
            blocked_request_log.BLOCK_REASON_NETWORK,
        )
    finally:
        current_request.reset(request_token)

    assert len(state) == blocked_request_log.MAX_TRACKED_BLOCKS
    assert ("198.51.100.7", blocked_request_log.BLOCK_REASON_NETWORK) in state
    assert ("203.0.113.0", blocked_request_log.BLOCK_REASON_NETWORK) not in state
