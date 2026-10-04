"""Bounded incoming activity from Home Assistant and NPM."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from ipaddress import IPv4Network, IPv6Network, ip_address
from time import monotonic
from typing import Final
from urllib.parse import urlsplit

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

KEY_ACTIVITY_EVENTS: Final = "ip_ban_manager_activity_events"
KEY_ACTIVITY_HISTORY: Final = "ip_ban_manager_activity_history"
KEY_ACTIVITY_HISTORY_LOADED: Final = "ip_ban_manager_activity_history_loaded"
KEY_ACTIVITY_HISTORY_LOAD_TASK: Final = "ip_ban_manager_activity_history_load_task"
KEY_ACTIVITY_HISTORY_SAVE_TASK: Final = "ip_ban_manager_activity_history_save_task"
KEY_ACTIVITY_HISTORY_CLEARED_AT: Final = "ip_ban_manager_activity_history_cleared_at"
KEY_ACTIVITY_NPM_CACHE: Final = "ip_ban_manager_activity_npm_cache"
KEY_ACTIVITY_NPM_LOCK: Final = "ip_ban_manager_activity_npm_lock"
MAX_ACTIVITY_EVENTS: Final = 200
ACTIVITY_MAX_AGE_SECONDS: Final = 15 * 60
ACTIVITY_HISTORY_MAX_AGE_SECONDS: Final = 24 * 60 * 60
MAX_ACTIVITY_HISTORY_EVENTS: Final = 1000
MAX_ACTIVITY_HISTORY_DISPLAY_EVENTS: Final = 200
NPM_ACTIVITY_CACHE_SECONDS: Final = 10.0
_ACTIVITY_STORE_VERSION: Final = 1
_ACTIVITY_STORE_KEY: Final = "ip_ban_manager.activity"

_COMBINED_LOG = re.compile(
    r"^(?P<ip>\S+)\s+\S+\s+\S+\s+\[(?P<timestamp>[^]]+)\]\s+"
    r'"(?P<request>[^"\r\n]*)"\s+(?P<status>\d{3})'
)
_NPM_PROXY_LOG = re.compile(
    r"^\[(?P<timestamp>[^]]+)\]\s+\S+\s+\S+\s+(?P<status>\d{3})\s+-\s+"
    r'(?P<method>\S+)\s+\S+\s+\S+\s+"(?P<path>[^"\r\n]*)"\s+'
    r"\[Client\s+(?P<ip>[^]]+)\]"
)
_NGINX_TIMESTAMP = "%d/%b/%Y:%H:%M:%S %z"


def clear_activity(hass: HomeAssistant) -> None:
    """Clear live activity and NPM cache when the integration unloads."""
    hass.data.pop(KEY_ACTIVITY_EVENTS, None)
    hass.data.pop(KEY_ACTIVITY_NPM_CACHE, None)
    hass.data.pop(KEY_ACTIVITY_NPM_LOCK, None)


def _history(hass: HomeAssistant) -> list[dict[str, object]]:
    value = hass.data.setdefault(KEY_ACTIVITY_HISTORY, [])
    if not isinstance(value, list):
        value = []
        hass.data[KEY_ACTIVITY_HISTORY] = value
    return value


def _event_key(event: Mapping[str, object]) -> tuple[object, ...]:
    return (
        event.get("source"),
        event.get("ip"),
        event.get("path"),
        event.get("method"),
        event.get("status"),
        event.get("timestamp"),
    )


def _safe_path(value: object) -> str:
    """Keep only the request path; never retain query strings or fragments."""
    raw_path = str(value or "")
    parsed = urlsplit(raw_path)
    return (parsed.path or raw_path.split("?", 1)[0].split("#", 1)[0])[:512]


def _sanitize_event(event: Mapping[str, object]) -> dict[str, object] | None:
    try:
        normalized_ip = str(ip_address(str(event.get("ip") or "")))
    except ValueError:
        return None
    timestamp = dt_util.parse_datetime(str(event.get("timestamp") or ""))
    if timestamp is None:
        return None
    status = event.get("status")
    return {
        "source": str(event.get("source") or "")[:32],
        "ip": normalized_ip,
        "path": _safe_path(event.get("path")),
        "method": str(event.get("method") or "").upper()[:16],
        "status": status if isinstance(status, int) else None,
        "host": str(event.get("host") or "")[:255],
        "detail": str(event.get("detail") or "")[:160],
        "timestamp": timestamp.isoformat(),
    }


def _prune_history(hass: HomeAssistant) -> None:
    cutoff = dt_util.utcnow().timestamp() - ACTIVITY_HISTORY_MAX_AGE_SECONDS
    valid: list[dict[str, object]] = []
    for event in _history(hass):
        sanitized = _sanitize_event(event)
        parsed = dt_util.parse_datetime(str(event.get("timestamp") or ""))
        if (
            sanitized is not None
            and parsed is not None
            and parsed.timestamp() >= cutoff
        ):
            valid.append(sanitized)
    valid.sort(key=lambda event: str(event.get("timestamp") or ""), reverse=True)
    valid = valid[:MAX_ACTIVITY_HISTORY_EVENTS]
    history = _history(hass)
    history[:] = valid


def _sanitize_events(events: object) -> list[dict[str, object]]:
    if not isinstance(events, list):
        return []
    sanitized_events: list[dict[str, object]] = []
    for event in events:
        if isinstance(event, Mapping):
            sanitized = _sanitize_event(event)
            if sanitized is not None:
                sanitized_events.append(sanitized)
    return sanitized_events


def _history_store(hass: HomeAssistant) -> Store[dict[str, object]]:
    return Store(hass, _ACTIVITY_STORE_VERSION, _ACTIVITY_STORE_KEY)


def _history_cutoff(hass: HomeAssistant) -> datetime | None:
    value = hass.data.get(KEY_ACTIVITY_HISTORY_CLEARED_AT)
    return value if isinstance(value, datetime) else None


def _is_after_history_cutoff(hass: HomeAssistant, event: Mapping[str, object]) -> bool:
    cutoff = _history_cutoff(hass)
    if cutoff is None:
        return True
    timestamp = dt_util.parse_datetime(str(event.get("timestamp") or ""))
    return timestamp is not None and timestamp > cutoff


async def _async_load_history(hass: HomeAssistant) -> None:
    """Load history without delaying runtime hook installation."""
    existing = list(_history(hass))
    stored = await _history_store(hass).async_load()
    events = stored.get("events") if isinstance(stored, dict) else None
    cleared_at = stored.get("cleared_at") if isinstance(stored, dict) else None
    parsed_cleared_at = dt_util.parse_datetime(str(cleared_at or ""))
    if parsed_cleared_at is not None:
        hass.data[KEY_ACTIVITY_HISTORY_CLEARED_AT] = parsed_cleared_at
    history = _history(hass)
    combined = _sanitize_events(events) + existing
    unique: dict[tuple[object, ...], dict[str, object]] = {}
    for event in combined:
        unique[_event_key(event)] = event
    history[:] = list(unique.values())
    _prune_history(hass)
    hass.data[KEY_ACTIVITY_HISTORY_LOADED] = True


def async_start_history(hass: HomeAssistant) -> None:
    """Start history loading in the background after runtime setup begins."""
    if hass.data.get(KEY_ACTIVITY_HISTORY_LOADED):
        return
    hass.data[KEY_ACTIVITY_HISTORY_LOADED] = True
    hass.data[KEY_ACTIVITY_HISTORY_LOAD_TASK] = hass.async_create_task(
        _async_load_history(hass), "IP Ban Manager activity history load"
    )


async def _async_save_history(hass: HomeAssistant) -> None:
    _prune_history(hass)
    payload: dict[str, object] = {"events": list(_history(hass))}
    if cutoff := _history_cutoff(hass):
        payload["cleared_at"] = cutoff.isoformat()
    await _history_store(hass).async_save(payload)


async def _async_delayed_save_history(hass: HomeAssistant) -> None:
    try:
        await asyncio.sleep(2)
        await _async_save_history(hass)
    except asyncio.CancelledError:
        raise
    finally:
        hass.data.pop(KEY_ACTIVITY_HISTORY_SAVE_TASK, None)


def _schedule_history_save(hass: HomeAssistant) -> None:
    task = hass.data.get(KEY_ACTIVITY_HISTORY_SAVE_TASK)
    if task is None or task.done():
        hass.data[KEY_ACTIVITY_HISTORY_SAVE_TASK] = hass.async_create_task(
            _async_delayed_save_history(hass)
        )


async def async_flush_history(hass: HomeAssistant) -> None:
    """Persist pending history before an integration unload."""
    load_task = hass.data.pop(KEY_ACTIVITY_HISTORY_LOAD_TASK, None)
    if load_task is not None and not load_task.done():
        await asyncio.gather(load_task, return_exceptions=True)
    task = hass.data.pop(KEY_ACTIVITY_HISTORY_SAVE_TASK, None)
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    if hass.data.get(KEY_ACTIVITY_HISTORY_LOADED):
        await _async_save_history(hass)


async def async_clear_history(hass: HomeAssistant) -> None:
    """Clear persisted history without affecting the live recent view."""
    load_task = hass.data.pop(KEY_ACTIVITY_HISTORY_LOAD_TASK, None)
    if load_task is not None and not load_task.done():
        await asyncio.gather(load_task, return_exceptions=True)
    save_task = hass.data.pop(KEY_ACTIVITY_HISTORY_SAVE_TASK, None)
    if save_task is not None and not save_task.done():
        save_task.cancel()
        await asyncio.gather(save_task, return_exceptions=True)
    _history(hass).clear()
    cleared_at = dt_util.utcnow()
    hass.data[KEY_ACTIVITY_HISTORY_CLEARED_AT] = cleared_at
    hass.data[KEY_ACTIVITY_NPM_CACHE] = None
    if hass.data.get(KEY_ACTIVITY_HISTORY_LOADED):
        await _history_store(hass).async_save(
            {"events": [], "cleared_at": cleared_at.isoformat()}
        )


def history_activity(hass: HomeAssistant) -> list[dict[str, object]]:
    """Return persisted, sanitized activity from the last 24 hours."""
    _prune_history(hass)
    return group_activity(_history(hass))[:MAX_ACTIVITY_HISTORY_DISPLAY_EVENTS]


def _events(hass: HomeAssistant) -> list[dict[str, object]]:
    value = hass.data.setdefault(KEY_ACTIVITY_EVENTS, [])
    if not isinstance(value, list):
        value = []
        hass.data[KEY_ACTIVITY_EVENTS] = value
    return value


def _prune(hass: HomeAssistant, now: float | None = None) -> None:
    current = monotonic() if now is None else now
    events = _events(hass)
    retained: list[dict[str, object]] = []
    for event in events:
        raw_monotonic = event.get("_monotonic")
        if isinstance(raw_monotonic, (int, float)) and (
            current - raw_monotonic <= ACTIVITY_MAX_AGE_SECONDS
        ):
            retained.append(event)
    events[:] = retained[-MAX_ACTIVITY_EVENTS:]


def record_activity(
    hass: HomeAssistant,
    *,
    source: str,
    ip: str,
    path: str = "",
    method: str = "",
    status: int | None = None,
    host: str = "",
    detail: str = "",
    timestamp: datetime | None = None,
) -> None:
    """Record one sanitized activity event in recent and 24-hour history."""
    try:
        normalized_ip = str(ip_address(ip))
    except ValueError:
        return
    now = monotonic()
    _prune(hass, now)
    events = _events(hass)
    event: dict[str, object] = {
        "source": source,
        "ip": normalized_ip,
        "path": _safe_path(path),
        "method": str(method or "").upper()[:16],
        "status": status if isinstance(status, int) else None,
        "host": str(host or "")[:255],
        "detail": str(detail or "")[:160],
        "timestamp": (timestamp or dt_util.utcnow()).isoformat(),
        "_monotonic": now,
    }
    events.append(event)
    del events[:-MAX_ACTIVITY_EVENTS]
    if hass.data.get(KEY_ACTIVITY_HISTORY_LOADED):
        clean_event = _sanitize_event(event)
        if clean_event is None or not _is_after_history_cutoff(hass, clean_event):
            return
        history = _history(hass)
        if not any(
            _event_key(existing) == _event_key(clean_event) for existing in history
        ):
            history.append(clean_event)
            _prune_history(hass)
            _schedule_history_save(hass)


def local_activity(hass: HomeAssistant) -> list[dict[str, object]]:
    """Return recent Home Assistant activity without internal bookkeeping."""
    _prune(hass)
    return group_activity(
        [
            {key: value for key, value in event.items() if not key.startswith("_")}
            for event in reversed(_events(hass))
        ]
    )


def group_activity(events: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """Group activity into one current summary per source IP."""
    grouped: dict[str, dict[str, object]] = {}
    counts: dict[str, int] = {}
    sources: dict[str, set[str]] = {}
    for event in events:
        ip_value = str(event.get("ip") or "")
        if not ip_value:
            continue
        raw_count = event.get("request_count")
        request_count = raw_count if isinstance(raw_count, int) else 1
        counts[ip_value] = counts.get(ip_value, 0) + request_count
        event_sources = event.get("sources")
        if isinstance(event_sources, list):
            sources.setdefault(ip_value, set()).update(
                str(source) for source in event_sources if source
            )
        else:
            sources.setdefault(ip_value, set()).add(str(event.get("source") or ""))
        current = grouped.get(ip_value)
        if current is None or str(event.get("timestamp") or "") > str(
            current.get("timestamp") or ""
        ):
            grouped[ip_value] = dict(event)

    summaries: list[dict[str, object]] = []
    for ip_value, event in grouped.items():
        event["request_count"] = counts[ip_value]
        event["sources"] = sorted(source for source in sources[ip_value] if source)
        summaries.append(event)
    return sorted(
        summaries,
        key=lambda event: str(event.get("timestamp") or ""),
        reverse=True,
    )


def without_allowlisted_activity(
    events: Sequence[Mapping[str, object]],
    allowlist: tuple[IPv4Network | IPv6Network, ...],
) -> list[dict[str, object]]:
    """Remove trusted sources from the panel activity views."""
    filtered: list[dict[str, object]] = []
    for event in events:
        try:
            address = ip_address(str(event.get("ip") or ""))
        except ValueError:
            continue
        if any(address in network for network in allowlist):
            continue
        filtered.append(dict(event))
    return filtered


def without_policy_denied_activity(
    events: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Remove requests rejected before they reached the protected service."""
    return [
        dict(event)
        for event in events
        if not (
            event.get("status") == 403
            and str(event.get("source") or "")
            in {"home_assistant", "nginx_proxy_manager"}
        )
    ]


def is_recent_activity(event: Mapping[str, object]) -> bool:
    """Return whether an event belongs in the live Recent view."""
    timestamp = dt_util.parse_datetime(str(event.get("timestamp") or ""))
    if timestamp is None:
        return False
    age = dt_util.utcnow().timestamp() - dt_util.as_utc(timestamp).timestamp()
    return 0 <= age <= ACTIVITY_MAX_AGE_SECONDS


def _parse_log_timestamp(value: str) -> datetime | None:
    """Parse the timestamp formats emitted by supported NPM log formats."""
    parsed = dt_util.parse_datetime(value)
    if parsed is not None:
        return parsed
    for timestamp_format in (_NGINX_TIMESTAMP, "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, timestamp_format)
        except ValueError:
            continue
    return None


def parse_npm_access_line(
    line: str, *, host: str = "", timestamp: datetime | None = None
) -> dict[str, object] | None:
    """Parse NPM's native proxy format and standard combined access logs."""
    normalized_line = line.strip()
    npm_match = _NPM_PROXY_LOG.match(normalized_line)
    if npm_match is not None:
        ip_value = npm_match.group("ip")
        status_value = npm_match.group("status")
        method = npm_match.group("method")
        path = npm_match.group("path").split("?", 1)[0][:512]
        log_timestamp = npm_match.group("timestamp")
    else:
        match = _COMBINED_LOG.match(normalized_line)
        if match is None:
            return None
        ip_value = match.group("ip")
        status_value = match.group("status")
        request = match.group("request").split()
        method = request[0] if request else ""
        path = request[1].split("?", 1)[0][:512] if len(request) > 1 else ""
        log_timestamp = match.group("timestamp")
    try:
        normalized_ip = str(ip_address(ip_value))
        status = int(status_value)
    except ValueError:
        return None
    parsed_timestamp = timestamp or _parse_log_timestamp(log_timestamp)
    if parsed_timestamp is None:
        return None
    return {
        "source": "nginx_proxy_manager",
        "ip": normalized_ip,
        "method": method,
        "path": path,
        "status": status,
        "host": host,
        "detail": (
            "NPM access log; possible IP Ban Manager edge-policy deny"
            if status == 403
            else "NPM access log"
        ),
        "timestamp": (parsed_timestamp or dt_util.utcnow()).isoformat(),
    }


def merge_activity(
    hass: HomeAssistant, npm_events: Sequence[Mapping[str, object]] | None = None
) -> list[dict[str, object]]:
    """Return local and NPM activity, newest first, with duplicate rows collapsed."""
    merged: list[dict[str, object]] = [*local_activity(hass)]
    if npm_events:
        merged.extend(dict(event) for event in npm_events if is_recent_activity(event))
    return group_activity(merged)[:MAX_ACTIVITY_EVENTS]


def record_history_events(
    hass: HomeAssistant, events: Sequence[Mapping[str, object]]
) -> None:
    """Persist externally sourced activity after it has been sanitized."""
    if not hass.data.get(KEY_ACTIVITY_HISTORY_LOADED):
        return
    history = _history(hass)
    existing = {_event_key(event) for event in history}
    changed = False
    for event in events:
        clean_event = _sanitize_event(event)
        if (
            clean_event is None
            or not _is_after_history_cutoff(hass, clean_event)
            or _event_key(clean_event) in existing
        ):
            continue
        history.append(clean_event)
        existing.add(_event_key(clean_event))
        changed = True
    if changed:
        _prune_history(hass)
        _schedule_history_save(hass)
