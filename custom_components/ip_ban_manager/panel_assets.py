"""Bundled panel asset helpers."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from aiohttp.web import Response
from homeassistant.core import HomeAssistant

from .const import DOMAIN

PANEL_JS_PATH = f"/api/{DOMAIN}/panel.js"
PANEL_WEB_COMPONENT = "ip-ban-manager-panel"


def integration_version() -> str:
    """Return the installed integration version from the bundled manifest."""
    return str(
        json.loads(
            Path(__file__).with_name("manifest.json").read_text(encoding="utf-8")
        )["version"]
    )


async def async_integration_version(hass: HomeAssistant) -> str:
    """Return the installed integration version without blocking the event loop."""
    return await hass.async_add_executor_job(integration_version)


def read_panel_js_source(version: str) -> str:
    """Read the bundled panel script with the installed version injected."""
    panel_path = Path(__file__).with_name("panel.js")
    return panel_path.read_text(encoding="utf-8").replace("__VERSION__", version)


async def async_panel_js_response(hass: HomeAssistant) -> Response:
    """Return panel.js with the manifest version baked into the header."""
    version = await async_integration_version(hass)
    return Response(
        body=await hass.async_add_executor_job(read_panel_js_source, version),
        content_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


def panel_js_cache_token() -> int:
    """Return the current panel script cache token."""
    panel_path = Path(__file__).with_name("panel.js")
    return int(panel_path.stat().st_mtime)


async def async_panel_js_url(hass: HomeAssistant) -> str:
    """Return the panel module URL with a cache token from the current file."""
    version = await async_integration_version(hass)
    cache_token = await hass.async_add_executor_job(panel_js_cache_token)
    return f"{PANEL_JS_PATH}?v={quote(version, safe='')}&t={cache_token}"
