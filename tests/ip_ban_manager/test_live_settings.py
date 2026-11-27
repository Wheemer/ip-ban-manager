"""Exercise settings recovery through a real config-entry removal and reinstall."""

import json
import stat

import pytest
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant

from custom_components.ip_ban_manager.const import (
    ATTR_BANNED_IPS,
    CONF_BLOCKED_NETWORKS,
    CONF_IP_ADDRESSES,
    DOMAIN,
)
from custom_components.ip_ban_manager.entry_helpers import entry_blocked_networks
from custom_components.ip_ban_manager.file_store import config_export_path
from custom_components.ip_ban_manager.live_settings import settings_path

from .test_setup import setup_ip_ban_manager


@pytest.mark.asyncio
async def test_reinstall_restores_last_settings_and_warns(hass: HomeAssistant) -> None:
    """Restore restrictions automatically without overwriting either backup or bans."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    manual = config_export_path(hass)
    manual.write_text("manual backup", encoding="utf-8")
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_IP_ADDRESSES: ["192.168.1.0/24"],
            CONF_BLOCKED_NETWORKS: ["203.0.113.0/24"],
        },
    )
    await hass.async_block_till_done()
    recovery = settings_path(hass)
    assert stat.S_IMODE(recovery.stat().st_mode) == 0o600
    assert ATTR_BANNED_IPS not in json.loads(recovery.read_text(encoding="utf-8"))
    await hass.config_entries.async_remove(entry.entry_id)
    assert recovery.is_file()

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    restored = hass.config_entries.async_entries(DOMAIN)[0]
    assert restored.state.value == "loaded"
    assert entry_blocked_networks(restored) == ["203.0.113.0/24"]
    assert manual.read_text(encoding="utf-8") == "manual backup"
    notices = persistent_notification._async_get_or_create_notifications(hass)
    assert "ip_ban_manager_recovery" in notices


@pytest.mark.asyncio
async def test_invalid_recovery_is_not_overwritten(hass: HomeAssistant) -> None:
    """Leave a malformed recovery file intact and report setup failure."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await hass.config_entries.async_remove(entry.entry_id)
    recovery = settings_path(hass)
    recovery.write_text("{broken", encoding="utf-8")
    await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    await hass.async_block_till_done()
    assert hass.config_entries.async_entries(DOMAIN)[0].state.value == "setup_error"
    assert recovery.read_text(encoding="utf-8") == "{broken"


@pytest.mark.asyncio
async def test_existing_entry_migrates_and_subsequent_load_uses_disk(
    hass: HomeAssistant,
) -> None:
    """Create the live file on upgrade and use it instead of stale entry options."""
    path = settings_path(hass)
    assert not path.exists()
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["data"][CONF_IP_ADDRESSES] == list(entry.data[CONF_IP_ADDRESSES])
    saved["options"][CONF_BLOCKED_NETWORKS] = ["203.0.113.0/24"]
    path.write_text(json.dumps(saved), encoding="utf-8")
    assert not entry_blocked_networks(entry)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry_blocked_networks(entry) == ["203.0.113.0/24"]
    notices = persistent_notification._async_get_or_create_notifications(hass)
    assert "ip_ban_manager_recovery" not in notices
