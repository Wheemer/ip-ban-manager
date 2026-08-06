"""In-memory metrics helpers for IP Ban Manager."""

from __future__ import annotations

from typing import cast

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import ATTR_LAST_CONFIG_WRITE
from .storage_keys import KEY_METRICS


def metrics(hass: HomeAssistant) -> dict[str, object]:
    """Return mutable in-memory integration metrics."""
    return cast(
        dict[str, object],
        hass.data.setdefault(
            KEY_METRICS,
            {
                "panel_api_calls": 0,
                "panel_api_errors": 0,
                "config_writes": 0,
                "snapshots_created": 0,
                "geoip_lookups": 0,
                "reverse_dns_lookups": 0,
                "reverse_dns_cache_hits": 0,
                ATTR_LAST_CONFIG_WRITE: None,
            },
        ),
    )


def metric_int(metrics_data: dict[str, object], key: str) -> int:
    """Return an in-memory metric value as an integer."""
    value = metrics_data.get(key, 0)
    return value if isinstance(value, int) else 0


def metric_increment(hass: HomeAssistant, key: str) -> None:
    """Increment a numeric integration metric."""
    metrics_data = metrics(hass)
    metrics_data[key] = metric_int(metrics_data, key) + 1


def mark_config_write(hass: HomeAssistant) -> None:
    """Record that IP Ban Manager wrote managed configuration."""
    metrics_data = metrics(hass)
    metrics_data["config_writes"] = metric_int(metrics_data, "config_writes") + 1
    metrics_data[ATTR_LAST_CONFIG_WRITE] = dt_util.utcnow().isoformat()
