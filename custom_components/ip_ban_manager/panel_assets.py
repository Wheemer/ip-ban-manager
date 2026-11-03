"""Bundled panel asset helpers."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from aiohttp.web import Response
from homeassistant.core import HomeAssistant

from .const import DOMAIN

PANEL_JS_PATH = f"/api/{DOMAIN}/panel.js"
PANEL_WEB_COMPONENT = "ip-ban-manager-panel"
PANEL_VERSIONED_JS_PATH = f"/api/{DOMAIN}/panel-{{asset}}.js"


def panel_asset_id(version: str, source: str) -> str:
    """Identify the actual bundle, including unreleased local changes."""
    release = re.sub(r"[^a-z0-9]+", "-", version.lower()).strip("-")
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    return f"{release}-{digest}"


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


def read_panel_asset(version: str) -> tuple[str, str]:
    """Read the bundled panel script with the installed version injected."""
    panel_path = Path(__file__).with_name("panel.js")
    source = panel_path.read_text(encoding="utf-8")
    asset = panel_asset_id(version, source)
    component = f"{PANEL_WEB_COMPONENT}-{asset}"
    return asset, source.replace("__VERSION__", version).replace(
        "__PANEL_ELEMENT_NAME__", component
    )


def read_panel_js_source(version: str) -> str:
    """Return the current bundle with its version and element name injected."""
    return read_panel_asset(version)[1]


async def async_panel_js_response(
    hass: HomeAssistant, asset: str | None = None
) -> Response:
    """Return panel.js with the manifest version baked into the header."""
    version = await async_integration_version(hass)
    current_asset, source = await hass.async_add_executor_job(read_panel_asset, version)
    if asset is not None and asset != current_asset:
        return Response(status=404, headers={"Cache-Control": "no-store"})
    return Response(
        body=source,
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

    def bundle_url() -> str:
        source = Path(__file__).with_name("panel.js").read_text(encoding="utf-8")
        return f"/api/{DOMAIN}/panel-{panel_asset_id(version, source)}.js"

    return await hass.async_add_executor_job(bundle_url)
