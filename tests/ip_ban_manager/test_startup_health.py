"""Health repairs must not report partially initialized runtime state."""

from asyncio import CancelledError, Event, wait_for
from unittest.mock import AsyncMock

import pytest
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.ip_ban_manager as ipbm
import custom_components.ip_ban_manager.health as health
import custom_components.ip_ban_manager.legacy_migration as legacy
from custom_components.ip_ban_manager.const import DOMAIN
from custom_components.ip_ban_manager.storage_keys import (
    KEY_PANEL_REGISTERED,
    KEY_SETUP_IN_PROGRESS,
)

from .test_setup import setup_ip_ban_manager


@pytest.mark.asyncio
async def test_cleanup_finishes_before_panel_without_flashing_repair(
    hass, monkeypatch
) -> None:
    """Run the real cleanup callback while panel setup is deliberately held."""
    monkeypatch.setattr(ipbm, "_async_reload_runtime_modules", AsyncMock())
    monkeypatch.setattr(legacy, "move_legacy_component_folder", lambda _: [])
    checked = Event()
    created = []
    original_create = ir.async_create_issue
    original_panel = ipbm._async_register_panel

    def capture_issue(hass, domain, issue_id, **kwargs):
        if domain == DOMAIN and issue_id == health.HEALTH_CHECK_FAILED_ISSUE_ID:
            created.append(kwargs)
        return original_create(hass, domain, issue_id, **kwargs)

    async def background_health(hass):
        await health.async_update_health_issue(hass)
        checked.set()

    async def delayed_panel(hass, **kwargs):
        await wait_for(checked.wait(), 3)
        assert not hass.data.get(KEY_PANEL_REGISTERED)
        await original_panel(hass, **kwargs)

    monkeypatch.setattr(ir, "async_create_issue", capture_issue)
    monkeypatch.setattr(legacy, "async_update_health_issue", background_health)
    monkeypatch.setattr(ipbm, "_async_register_panel", delayed_panel)
    await setup_ip_ban_manager(hass)
    assert hass.data[KEY_PANEL_REGISTERED]
    assert created == []
    assert KEY_SETUP_IN_PROGRESS not in hass.data
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, health.HEALTH_CHECK_FAILED_ISSUE_ID)
        is None
    )


@pytest.mark.asyncio
async def test_missing_panel_after_setup_still_creates_repair(hass, monkeypatch):
    """Startup suppression must not hide a genuine later panel failure."""
    await setup_ip_ban_manager(hass)
    hass.data[KEY_PANEL_REGISTERED] = False
    await health.async_update_health_issue(hass)
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, health.HEALTH_CHECK_FAILED_ISSUE_ID)
        is not None
    )


@pytest.mark.asyncio
async def test_panel_never_registered_still_creates_final_repair(hass, monkeypatch):
    """The final setup check still reports an actual missing panel."""
    monkeypatch.setattr(ipbm, "_async_reload_runtime_modules", AsyncMock())
    monkeypatch.setattr(ipbm, "_async_register_panel", AsyncMock())
    await setup_ip_ban_manager(hass)
    assert KEY_SETUP_IN_PROGRESS not in hass.data
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, health.HEALTH_CHECK_FAILED_ISSUE_ID)
        is not None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError, CancelledError])
async def test_setup_error_or_cancellation_clears_health_guard(
    hass, monkeypatch, error
):
    """Failed or cancelled initialization cannot leave repairs muted."""
    entry = MockConfigEntry(domain=DOMAIN, data={})

    async def fail_setup(hass, entry):
        assert hass.data[KEY_SETUP_IN_PROGRESS]
        raise error()

    monkeypatch.setattr(ipbm, "_async_setup_entry", fail_setup)
    monkeypatch.setattr(ipbm, "_async_unload_runtime", AsyncMock())
    monkeypatch.setattr(hass.config_entries, "async_unload_platforms", AsyncMock())
    with pytest.raises(error):
        await ipbm.async_setup_entry(hass, entry)
    assert KEY_SETUP_IN_PROGRESS not in hass.data


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["status", "translations"])
async def test_health_check_yielding_across_setup_does_not_publish(
    hass, monkeypatch, stage
):
    """Recheck the guard after asynchronous health and translation reads."""
    await setup_ip_ban_manager(hass)
    hass.data[KEY_PANEL_REGISTERED] = False
    target = (
        "async_health_status"
        if stage == "status"
        else "async_load_health_issue_strings"
    )
    original = getattr(health, target)

    async def begin_setup(*args, **kwargs):
        result = await original(*args, **kwargs)
        hass.data[KEY_SETUP_IN_PROGRESS] = True
        return result

    monkeypatch.setattr(health, target, begin_setup)
    try:
        await health.async_update_health_issue(hass)
        assert (
            ir.async_get(hass).async_get_issue(
                DOMAIN, health.HEALTH_CHECK_FAILED_ISSUE_ID
            )
            is None
        )
    finally:
        hass.data.pop(KEY_SETUP_IN_PROGRESS, None)
