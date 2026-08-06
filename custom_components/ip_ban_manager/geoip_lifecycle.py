"""GeoIP background lifecycle helpers."""

from __future__ import annotations

import logging
from asyncio import CancelledError, Task

from homeassistant.core import HomeAssistant

from .geoip import async_prepare_geoip_reader
from .health import async_update_health_issue
from .storage_keys import KEY_GEOIP_READER_PREPARE_TASK

_LOGGER = logging.getLogger(__name__)


def async_schedule_geoip_reader_prepare(hass: HomeAssistant) -> None:
    """Warm the local GeoIP reader without holding Home Assistant startup."""
    existing_task = hass.http.app.get(KEY_GEOIP_READER_PREPARE_TASK)
    if existing_task is not None and not existing_task.done():
        return

    task = hass.async_create_task(async_prepare_geoip_reader(hass))
    hass.http.app[KEY_GEOIP_READER_PREPARE_TASK] = task

    def _geoip_prepare_done(done_task: Task[None]) -> None:
        hass.http.app.pop(KEY_GEOIP_READER_PREPARE_TASK, None)
        try:
            done_task.result()
        except CancelledError:
            pass
        except Exception:
            _LOGGER.warning("GeoIP reader preparation failed", exc_info=True)
        hass.async_create_task(async_update_health_issue(hass))

    task.add_done_callback(_geoip_prepare_done)
