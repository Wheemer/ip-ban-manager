"""Initial panel rendering must not wait for optional remote enrichment."""

from unittest.mock import AsyncMock

import pytest

from custom_components.ip_ban_manager import panel
from custom_components.ip_ban_manager.const import DOMAIN

from .test_setup import setup_ip_ban_manager


@pytest.mark.asyncio
async def test_initial_payload_skips_external_work_and_keeps_saved_settings(
    hass, monkeypatch
):
    """The first response exposes real settings without contacting NPM or geocoding."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    activity = AsyncMock(return_value=[])
    discovery = AsyncMock(return_value={"addon_detected": True})
    region = AsyncMock(return_value={"country_code": "CA"})
    locations = AsyncMock(return_value={})
    monkeypatch.setattr(panel, "async_npm_activity", activity)
    monkeypatch.setattr(panel, "async_detect_npm_addon", discovery)
    monkeypatch.setattr(panel, "async_local_geoip_region", region)
    monkeypatch.setattr(panel, "_async_activity_locations", locations)
    initial = await panel.async_panel_payload(hass, entry, initial=True)
    assert initial["enrichment_pending"] is True
    assert initial["settings"]["ip_addresses"] == panel.entry_ip_addresses(entry)
    assert initial["translations"]
    for operation in (activity, discovery, region, locations):
        operation.assert_not_awaited()
    complete = await panel.async_panel_payload(hass, entry)
    assert complete["enrichment_pending"] is False
    assert complete["settings"] == initial["settings"]
    assert complete["nginx_proxy_manager"]["addon_detected"] is True
    for operation in (activity, discovery, region, locations):
        operation.assert_awaited_once()
