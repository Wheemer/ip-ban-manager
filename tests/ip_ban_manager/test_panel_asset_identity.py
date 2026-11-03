"""Build-specific panel filenames and web components."""

from typing import Any, cast

import pytest

from custom_components.ip_ban_manager import panel_assets
from custom_components.ip_ban_manager.http_views import IPBanManagerVersionedPanelView

from .test_setup import MockViewRequest, setup_ip_ban_manager


def test_asset_identity_changes_with_release_or_source():
    """Same bundle is stable; source-only updates also have a distinct name."""
    first = panel_assets.panel_asset_id("1.9.7", "first")
    assert first == panel_assets.panel_asset_id("1.9.7", "first")
    assert first != panel_assets.panel_asset_id("1.9.7", "second")
    assert first != panel_assets.panel_asset_id("1.9.8", "first")
    assert first.startswith("1-9-7-")


@pytest.mark.asyncio
async def test_versioned_panel_route_matches_registered_bundle(hass):
    """The registered filename serves exactly its corresponding web component."""
    await setup_ip_ban_manager(hass)
    url = await panel_assets.async_panel_js_url(hass)
    asset = url.rsplit("/panel-", 1)[1][:-3]
    view = IPBanManagerVersionedPanelView()
    request = cast(Any, MockViewRequest(hass.http.app))
    response = await view.get(request, asset)
    assert response.status == 200
    assert f'"ip-ban-manager-panel-{asset}"' in response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert (await view.get(request, "obsolete")).status == 404
    assert (await view.get(request, asset[:-1])).status == 404
