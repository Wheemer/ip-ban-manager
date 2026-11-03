"""Atomic country and subdivision replacement from the panel."""

from typing import Any, cast

import pytest

from custom_components.ip_ban_manager.http_views import IPBanManagerManageView
from custom_components.ip_ban_manager.region_rules import entry_public_region_settings

from .test_setup import MockViewRequest, setup_ip_ban_manager


@pytest.mark.asyncio
async def test_replace_country_with_provinces_and_back(hass):
    """Require explicit replacement and preserve sibling and unrelated regions."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries("ip_ban_manager")[0]
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            "public_region_enabled": False,
            "public_region_rules": {"CA": 5, "US": 6},
        },
    )
    view = IPBanManagerManageView()

    async def submit(value, **extra):
        return await view.post(
            cast(
                Any,
                MockViewRequest(
                    hass.http.app,
                    data={
                        "action": "set_public_region",
                        "value": value,
                        "threshold": 4,
                        **extra,
                    },
                ),
            )
        )

    assert (await submit("CA-NL")).status == 400
    assert entry_public_region_settings(hass, entry)["public_region_rules"] == {
        "CA": 5,
        "US": 6,
    }
    assert (await submit("CA-NL", replace_regions=["CA"], confirmed=True)).status == 200
    assert (await submit("CA-ON")).status == 200
    assert entry_public_region_settings(hass, entry)["public_region_rules"] == {
        "CA-NL": 4,
        "CA-ON": 4,
        "US": 6,
    }
    assert (await submit("CA", replace_regions=["CA-NL"], confirmed=True)).status == 400
    assert (
        await submit("CA", replace_regions=["CA-NL", "US"], confirmed=True)
    ).status == 400
    assert (
        await submit("CA", replace_regions=["CA-NL", "CA-ON"], confirmed=True)
    ).status == 200
    assert entry_public_region_settings(hass, entry)["public_region_rules"] == {
        "CA": 4,
        "US": 6,
    }
