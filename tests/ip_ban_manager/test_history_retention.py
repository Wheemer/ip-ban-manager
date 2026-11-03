"""Configurable incoming history retention regressions."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from custom_components.ip_ban_manager import activity
from custom_components.ip_ban_manager.entry_helpers import (
    normalize_activity_history_hours,
)
from custom_components.ip_ban_manager.storage_keys import KEY_CONFIG_ENTRY


@pytest.mark.parametrize("hours", [1, 24, 48, 168])
def test_retention_prunes_to_configured_window(hours):
    """Pruning uses the saved window without changing recent retention."""
    entry = SimpleNamespace(options={"activity_history_hours": hours}, data={})
    hass = SimpleNamespace(http=SimpleNamespace(app={KEY_CONFIG_ENTRY: entry}), data={})
    now = dt_util.utcnow()
    hass.data[activity.KEY_ACTIVITY_HISTORY] = [
        {
            "ip": "192.0.2.1",
            "timestamp": (now - timedelta(hours=hours - 0.5)).isoformat(),
        },
        {
            "ip": "192.0.2.2",
            "timestamp": (now - timedelta(hours=hours + 0.5)).isoformat(),
        },
    ]
    assert [event["ip"] for event in activity.history_activity(hass)] == ["192.0.2.1"]
    assert activity.ACTIVITY_MAX_AGE_SECONDS == 900


@pytest.mark.parametrize(
    "value", [True, False, 0, 169, -1, 1.5, None, "NaN", {}, "2.0"]
)
def test_retention_rejects_invalid_values(value):
    """Invalid settings and imported values cannot disable pruning."""
    with pytest.raises(HomeAssistantError):
        normalize_activity_history_hours(value)


@pytest.mark.asyncio
async def test_retention_option_and_backup_round_trip(hass):
    """The panel persists retention and backup restore includes it."""
    from custom_components.ip_ban_manager.backup import (
        async_apply_config_backup_payload,
        config_export_payload,
    )
    from custom_components.ip_ban_manager.panel import async_panel_set_options

    from .test_setup import setup_ip_ban_manager

    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries("ip_ban_manager")[0]
    await async_panel_set_options(hass, {"activity_history_hours": 48})
    assert entry.options["activity_history_hours"] == 48
    payload = config_export_payload(hass, entry)
    assert payload["settings"]["activity_history_hours"] == 48
    await async_panel_set_options(hass, {"activity_history_hours": 12})
    await async_apply_config_backup_payload(hass, payload)
    assert entry.options["activity_history_hours"] == 48
    payload["settings"]["activity_history_hours"] = 0
    with pytest.raises(HomeAssistantError):
        await async_apply_config_backup_payload(hass, payload)
    assert entry.options["activity_history_hours"] == 48
