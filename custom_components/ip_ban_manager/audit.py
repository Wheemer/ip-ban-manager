"""Events and logbook recording for IP Ban Manager mutations."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from homeassistant.components import logbook
from homeassistant.core import HomeAssistant

from .const import (
    ATTR_ATTEMPTS,
    ATTR_IP_ADDRESS,
    ATTR_NETWORK,
    ATTR_SOURCE,
    ATTR_THRESHOLD,
    DOMAIN,
    EVENT_ALLOWLIST_NETWORK_ADDED,
    EVENT_ALLOWLIST_NETWORK_REMOVED,
    EVENT_ALLOWLISTED_LOGIN_ESCALATED,
    EVENT_BLOCKED_NETWORK_ADDED,
    EVENT_BLOCKED_NETWORK_REMOVED,
    EVENT_IP_BANNED,
    EVENT_IP_UNBANNED,
    EVENT_LOGIN_THRESHOLD_REACHED,
    SOURCE_AUTO,
)

_mutation_source: ContextVar[str] = ContextVar(
    "ip_ban_manager_mutation_source", default=SOURCE_AUTO
)


@contextmanager
def mutation_source(source: str):
    """Set the mutation source for nested ban writes."""
    token = _mutation_source.set(source)
    try:
        yield
    finally:
        _mutation_source.reset(token)


def current_mutation_source() -> str:
    """Return the active mutation source."""
    return _mutation_source.get()


def _fire_event(hass: HomeAssistant, event_type: str, data: dict[str, Any]) -> None:
    """Fire a small, stable IP Ban Manager event."""
    hass.bus.async_fire(event_type, data)


def _log_change(hass: HomeAssistant, message: str) -> None:
    """Write a successful state change to the Home Assistant logbook."""
    logbook.async_log_entry(hass, "IP Ban Manager", message, domain=DOMAIN)


def record_ip_banned(
    hass: HomeAssistant, ip_address: str, source: str | None = None
) -> None:
    """Record that an exact IP ban was written."""
    active_source = source or current_mutation_source()
    _fire_event(
        hass,
        EVENT_IP_BANNED,
        {ATTR_IP_ADDRESS: ip_address, ATTR_SOURCE: active_source},
    )
    _log_change(hass, f"Banned {ip_address} ({active_source})")


def record_ip_unbanned(hass: HomeAssistant, ip_address: str, source: str) -> None:
    """Record that an exact IP ban was removed."""
    _fire_event(
        hass,
        EVENT_IP_UNBANNED,
        {ATTR_IP_ADDRESS: ip_address, ATTR_SOURCE: source},
    )
    _log_change(hass, f"Removed ban for {ip_address} ({source})")


def record_login_threshold_reached(
    hass: HomeAssistant,
    ip_address: str,
    *,
    attempts: int,
    threshold: int,
) -> None:
    """Record that a source reached the automatic ban threshold."""
    _fire_event(
        hass,
        EVENT_LOGIN_THRESHOLD_REACHED,
        {
            ATTR_IP_ADDRESS: ip_address,
            ATTR_ATTEMPTS: attempts,
            ATTR_THRESHOLD: threshold,
            ATTR_SOURCE: SOURCE_AUTO,
        },
    )


def record_allowlisted_login_escalated(
    hass: HomeAssistant,
    ip_address: str,
    *,
    attempts: int,
) -> None:
    """Record repeated allowlisted-login failures crossing the escalation threshold."""
    _fire_event(
        hass,
        EVENT_ALLOWLISTED_LOGIN_ESCALATED,
        {
            ATTR_IP_ADDRESS: ip_address,
            ATTR_ATTEMPTS: attempts,
            ATTR_SOURCE: SOURCE_AUTO,
        },
    )


def record_allowlist_network_added(
    hass: HomeAssistant, network: str, source: str
) -> None:
    """Record that an allowlist network was added."""
    _fire_event(
        hass,
        EVENT_ALLOWLIST_NETWORK_ADDED,
        {ATTR_NETWORK: network, ATTR_SOURCE: source},
    )
    _log_change(hass, f"Added allowlist network {network} ({source})")


def record_allowlist_network_removed(
    hass: HomeAssistant, network: str, source: str
) -> None:
    """Record that an allowlist network was removed."""
    _fire_event(
        hass,
        EVENT_ALLOWLIST_NETWORK_REMOVED,
        {ATTR_NETWORK: network, ATTR_SOURCE: source},
    )
    _log_change(hass, f"Removed allowlist network {network} ({source})")


def record_blocked_network_added(
    hass: HomeAssistant, network: str, source: str
) -> None:
    """Record that a blocked network was added."""
    _fire_event(
        hass,
        EVENT_BLOCKED_NETWORK_ADDED,
        {ATTR_NETWORK: network, ATTR_SOURCE: source},
    )
    _log_change(hass, f"Added blocked network {network} ({source})")


def record_blocked_network_removed(
    hass: HomeAssistant, network: str, source: str
) -> None:
    """Record that a blocked network was removed."""
    _fire_event(
        hass,
        EVENT_BLOCKED_NETWORK_REMOVED,
        {ATTR_NETWORK: network, ATTR_SOURCE: source},
    )
    _log_change(hass, f"Removed blocked network {network} ({source})")


def record_geoip_updated(hass: HomeAssistant, source: str) -> None:
    """Record that the local GeoIP database was updated."""
    _log_change(hass, f"Updated GeoIP database ({source})")
