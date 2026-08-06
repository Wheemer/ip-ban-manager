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
INTEGRATION_VERSION = json.loads(
    Path(__file__).with_name("manifest.json").read_text(encoding="utf-8")
)["version"]


def read_panel_js_source() -> str:
    """Read the bundled panel script with the installed version injected."""
    panel_path = Path(__file__).with_name("panel.js")
    return panel_path.read_text(encoding="utf-8").replace(
        "__VERSION__", INTEGRATION_VERSION
    )


async def async_panel_js_response(hass: HomeAssistant) -> Response:
    """Return panel.js with the manifest version baked into the header."""
    return Response(
        body=await hass.async_add_executor_job(read_panel_js_source),
        content_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


def panel_js_cache_token() -> int:
    """Return the current panel script cache token."""
    panel_path = Path(__file__).with_name("panel.js")
    return int(panel_path.stat().st_mtime)


async def async_panel_js_url(hass: HomeAssistant) -> str:
    """Return the panel module URL with a cache token from the current file."""
    cache_token = await hass.async_add_executor_job(panel_js_cache_token)
    return f"{PANEL_JS_PATH}?v={quote(INTEGRATION_VERSION, safe='')}&t={cache_token}"
