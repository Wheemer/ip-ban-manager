"""Rate-limited diagnostics for requests rejected by managed ban rules."""

from __future__ import annotations

import logging
from time import monotonic
from typing import Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers.http import current_request

from .storage_keys import IPAddress

_LOGGER = logging.getLogger(__name__)

KEY_BLOCKED_REQUEST_LOG_STATE: Final = "ip_ban_manager_blocked_request_log_state"

BLOCK_REASON_EXACT_BAN: Final = "exact IP ban"
BLOCK_REASON_NETWORK: Final = "blocked network"
BLOCK_REASON_REGION: Final = "Public Region Lock"
BLOCK_REASON_DEFAULT_DENY: Final = "outside Allowed IPs"

LOG_INTERVAL_SECONDS: Final = 60.0
MAX_TRACKED_BLOCKS: Final = 1024


def clear_blocked_request_log_state(hass: HomeAssistant) -> None:
    """Clear diagnostic throttling state when logging is disabled or unloaded."""
    hass.data.pop(KEY_BLOCKED_REQUEST_LOG_STATE, None)


def log_blocked_request(
    hass: HomeAssistant, remote_addr: IPAddress, reason: str
) -> None:
    """Log one rejected request without exposing query strings or headers."""
    request = current_request.get()
    if request is None:
        return

    method = str(getattr(request, "method", "UNKNOWN") or "UNKNOWN").upper()[:32]
    path = str(getattr(request, "path", "/") or "/")[:512]
    key = (str(remote_addr), reason)
    now = monotonic()
    state: dict[tuple[str, str], tuple[float, int]] = hass.data.setdefault(
        KEY_BLOCKED_REQUEST_LOG_STATE, {}
    )
    previous = state.get(key)
    if previous is not None and now - previous[0] < LOG_INTERVAL_SECONDS:
        last_logged, suppressed = previous
        state[key] = (last_logged, suppressed + 1)
        return

    suppressed = previous[1] if previous is not None else 0

    if key not in state and len(state) >= MAX_TRACKED_BLOCKS:
        oldest = min(state, key=lambda candidate: state[candidate][0])
        state.pop(oldest, None)

    suffix = f"; {suppressed} similar requests suppressed" if suppressed else ""
    _LOGGER.warning(
        "Blocked request from %s: %s %r (reason: %s%s)",
        remote_addr,
        method,
        path,
        reason,
        suffix,
    )
    state[key] = (now, 0)
