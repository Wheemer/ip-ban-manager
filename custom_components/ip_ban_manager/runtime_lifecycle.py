"""Process-lifetime ownership of calls that must finish before Python reload."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

_STATE_KEY = "ip_ban_manager_runtime_calls"


class RuntimeClosingError(HomeAssistantError):
    """The integration is unloading and cannot start another operation."""


class _RuntimeCalls:
    def __init__(self) -> None:
        self.closing = False
        self.active = 0
        self.idle = asyncio.Event()
        self.idle.set()


def _state(hass: HomeAssistant) -> _RuntimeCalls:
    state = hass.data.get(_STATE_KEY)
    if state is None:
        state = _RuntimeCalls()
        hass.data[_STATE_KEY] = state
    return state


def open_runtime(hass: HomeAssistant) -> None:
    """Admit calls only after the replacement runtime has been bound."""
    _state(hass).closing = False


@asynccontextmanager
async def runtime_operation(hass: HomeAssistant) -> AsyncIterator[None]:
    """Own the operation's duration, not its caller's entire task lifetime."""
    state = _state(hass)
    if state.closing:
        raise RuntimeClosingError(
            "IP Ban Manager is unloading. Try again after reload."
        )
    state.active += 1
    state.idle.clear()
    try:
        yield
    finally:
        state.active -= 1
        if not state.active:
            state.idle.set()


async def async_drain_runtime(hass: HomeAssistant) -> None:
    """Reject new calls and finish admitted mutations before module replacement."""
    state = _state(hass)
    state.closing = True
    await state.idle.wait()
