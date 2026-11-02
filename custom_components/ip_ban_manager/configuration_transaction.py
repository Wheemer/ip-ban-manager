"""Rollback boundaries for multi-step configuration changes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .storage_keys import KEY_CONFIG_ENTRY

_LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def configuration_transaction(hass: HomeAssistant) -> AsyncIterator[None]:
    """Restore saved options and live policy if applying a configuration fails."""
    from .dns_allowlist import async_refresh_dns_allowlist
    from .entry_helpers import entry_geoip_enabled, entry_sidebar_panel_enabled
    from .geoip import async_prepare_geoip_reader, close_geoip_reader
    from .network_policy import apply_ban_settings, apply_blocked_networks
    from .nginx_proxy_manager import (
        async_disable_npm,
        async_sync_npm,
        entry_npm_config,
        schedule_npm_sync,
    )
    from .panel import async_register_panel

    lock = hass.data.setdefault("ip_ban_manager_configuration_lock", asyncio.Lock())
    async with lock:
        entry = hass.http.app[KEY_CONFIG_ENTRY]
        options = dict(entry.options)
        npm_options = entry_npm_config(entry)
        try:
            yield
        except BaseException as original_error:

            async def restore() -> None:
                current_npm = entry_npm_config(entry)
                try:
                    if current_npm.get("enabled") and not npm_options.get("enabled"):
                        await async_disable_npm(hass)
                finally:
                    hass.config_entries.async_update_entry(entry, options=options)
                await async_refresh_dns_allowlist(hass)
                apply_ban_settings(hass, entry)
                apply_blocked_networks(hass, entry)
                if entry_geoip_enabled(entry):
                    await async_prepare_geoip_reader(hass)
                else:
                    close_geoip_reader(hass)
                await async_register_panel(
                    hass, sidebar_enabled=entry_sidebar_panel_enabled(entry)
                )
                if npm_options.get("enabled"):
                    await async_sync_npm(hass)
                else:
                    schedule_npm_sync(hass)

            task = asyncio.create_task(restore())
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
            except Exception as rollback_error:
                _LOGGER.exception(
                    "Failed to restore IP Ban Manager configuration after apply failure"
                )
                raise HomeAssistantError(
                    f"{original_error} Restoring the previous configuration also failed: {rollback_error}"
                ) from rollback_error
            raise
