"""Public region lists preserve policy boundaries and disabled settings."""

from ipaddress import ip_address
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
import yaml
from homeassistant.components.http.ban import KEY_BAN_MANAGER
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.ip_ban_manager import backup, geoip, panel
from custom_components.ip_ban_manager.const import DOMAIN
from custom_components.ip_ban_manager.http_views import IPBanManagerManageView
from custom_components.ip_ban_manager.region_rules import (
    entry_public_region_settings,
    legacy_public_region_settings,
    normalize_public_region_settings,
)

from .test_setup import MockViewRequest, setup_ip_ban_manager


@pytest.mark.parametrize(
    "enabled,rules",
    [
        (True, {}),
        ("false", {}),
        (False, {"CA": True}),
        (False, {"CA": -1}),
        (False, {"CA": 101}),
        (False, {"CA": 1.5}),
        (False, {"Canada": 5}),
        (False, {"CA": 5, "ca": 6}),
        (False, {"CÅ": 5}),
    ],
)
def test_invalid_lists_are_rejected(enabled, rules):
    """Malformed and empty enabled policies never silently become valid."""
    with pytest.raises(HomeAssistantError):
        normalize_public_region_settings(enabled, rules)


def test_legacy_migration_does_not_expand_access():
    """Only the selected legacy boundary and its narrower overrides migrate."""
    thresholds = {"CA": 8, "CA-NL": 4, "US": 2}
    country = legacy_public_region_settings("country", "CA", "", thresholds, 5)
    province = legacy_public_region_settings(
        "subdivision", "CA", "CA-NL", thresholds, 5
    )
    assert country == {
        "public_region_enabled": True,
        "public_region_rules": {"CA": 8, "CA-NL": 4},
    }
    assert province["public_region_rules"] == {"CA-NL": 4}
    assert (
        legacy_public_region_settings("anywhere", "CA", "", thresholds, 5)[
            "public_region_enabled"
        ]
        is False
    )


@pytest.mark.parametrize(
    "country,subdivision,allowed",
    [
        ("CA", "CA-NL", True),
        ("CA", "CA-ON", False),
        ("US", "US-NY", True),
        ("FR", "FR-IDF", False),
        (None, None, False),
    ],
)
def test_list_matches_countries_and_subdivisions(
    hass, monkeypatch, country, subdivision, allowed
):
    """All listed regions match while unlisted and unknown public sources fail."""
    monkeypatch.setattr(
        geoip,
        "geoip_location_details_for_ip",
        lambda *args: geoip.GeoIPLocation(None, country, subdivision),
    )
    rules = frozenset({"CA-NL", "US"})
    assert geoip.geoip_regions_allow_ip(hass, ip_address("8.8.8.8"), rules) is allowed
    assert (
        geoip.geoip_regions_allow_ip(hass, ip_address("2606:4700:4700::1111"), rules)
        is allowed
    )
    assert geoip.geoip_regions_allow_ip(hass, ip_address("127.0.0.1"), rules)
    assert geoip.geoip_regions_allow_ip(hass, ip_address("fe80::1"), rules)


@pytest.mark.asyncio
async def test_disabled_rules_are_preserved_and_stop_enforcing(
    hass: HomeAssistant, monkeypatch
):
    """Toggle off preserves rows and restores the normal global threshold."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    monkeypatch.setattr(panel, "path_is_file", lambda path: True)
    monkeypatch.setattr(panel, "async_prepare_geoip_reader", AsyncMock())
    monkeypatch.setattr(panel, "geoip_reader", lambda hass: object())
    monkeypatch.setattr(
        geoip,
        "geoip_location_details_for_ip",
        lambda *args: geoip.GeoIPLocation(None, "CA", "CA-NL"),
    )
    rules = {"CA": 8, "CA-NL": 3, "US": 2}
    await panel.async_panel_set_options(
        hass,
        {
            "public_region_enabled": True,
            "public_region_rules": rules,
            "confirmed": True,
        },
    )
    assert geoip.regional_login_threshold_for_ip(hass, ip_address("8.8.8.8")) == 3
    assert (
        hass.http.app[KEY_BAN_MANAGER].ip_bans_lookup.geoip_access_allowed is not None
    )
    await panel.async_panel_set_options(hass, {"public_region_enabled": False})
    saved = entry_public_region_settings(hass, entry)
    assert saved == {"public_region_enabled": False, "public_region_rules": rules}
    assert geoip.regional_login_threshold_for_ip(hass, ip_address("8.8.8.8")) is None
    assert hass.http.app[KEY_BAN_MANAGER].ip_bans_lookup.geoip_access_allowed is None
    await panel.async_panel_set_options(
        hass, {"public_region_enabled": True, "confirmed": True}
    )
    assert geoip.regional_login_threshold_for_ip(hass, ip_address("8.8.8.8")) == 3


@pytest.mark.asyncio
async def test_enable_requires_confirmation_and_database(hass: HomeAssistant):
    """Failed validation cannot change the persisted settings."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    before = dict(entry.options)
    options = {"public_region_enabled": True, "public_region_rules": {"CA": 5}}
    with pytest.raises(HomeAssistantError, match="Confirm"):
        await panel.async_panel_set_options(hass, options)
    with pytest.raises(HomeAssistantError, match="database"):
        await panel.async_panel_set_options(hass, {**options, "confirmed": True})
    assert entry.options == before


@pytest.mark.asyncio
async def test_region_backup_roundtrip_and_repeated_import(hass: HomeAssistant):
    """Disabled lists and thresholds survive export and repeated restore."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    rules = {"CA-NL": 3, "US": 8}
    await panel.async_panel_set_options(
        hass, {"public_region_enabled": False, "public_region_rules": rules}
    )
    exported = backup.config_export_payload(hass, entry)
    assert exported["format_version"] == 3
    await panel.async_panel_set_options(hass, {"public_region_rules": {}})
    for _ in range(2):
        await backup.async_import_config_from_yaml(hass, yaml.safe_dump(exported))
        assert entry_public_region_settings(hass, entry) == {
            "public_region_enabled": False,
            "public_region_rules": rules,
        }


@pytest.mark.asyncio
async def test_region_actions_update_without_duplicates_and_remove_last(hass):
    """The manage handler updates one row and safely handles an empty list."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    view = IPBanManagerManageView()
    for count in (3, 7):
        response = await view.post(
            cast(
                Any,
                MockViewRequest(
                    hass.http.app,
                    data={
                        "action": "set_public_region",
                        "value": "ca-nl",
                        "threshold": count,
                    },
                ),
            )
        )
        assert response.status == 200
    assert entry_public_region_settings(hass, entry)["public_region_rules"] == {
        "CA-NL": 7
    }
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "public_region_enabled": True}
    )
    response = await view.post(
        cast(
            Any,
            MockViewRequest(
                hass.http.app,
                data={
                    "action": "remove_public_region",
                    "value": "CA-NL",
                    "confirmed": True,
                },
            ),
        )
    )
    assert response.status == 400
    assert entry_public_region_settings(hass, entry) == {
        "public_region_enabled": True,
        "public_region_rules": {"CA-NL": 7},
    }
    await panel.async_panel_set_options(hass, {"public_region_enabled": False})
    response = await view.post(
        cast(
            Any,
            MockViewRequest(
                hass.http.app,
                data={
                    "action": "remove_public_region",
                    "value": "CA-NL",
                },
            ),
        )
    )
    assert response.status == 200
    assert entry_public_region_settings(hass, entry) == {
        "public_region_enabled": False,
        "public_region_rules": {},
    }


@pytest.mark.asyncio
async def test_region_row_edit_is_atomic(hass):
    """Editing a row replaces it, while invalid edits preserve every row."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await panel.async_panel_set_options(
        hass,
        {"public_region_enabled": False, "public_region_rules": {"CA": 5, "US": 8}},
    )
    view = IPBanManagerManageView()
    for code, count, expected in (("US", 3, 400), ("bad!", 3, 400), ("CA-NL", 3, 200)):
        before = dict(entry.options)
        response = await view.post(
            cast(
                Any,
                MockViewRequest(
                    hass.http.app,
                    data={
                        "action": "set_public_region",
                        "original_region": "CA",
                        "value": code,
                        "threshold": count,
                    },
                ),
            )
        )
        assert response.status == expected
        if expected == 400:
            assert entry.options == before
    assert entry_public_region_settings(hass, entry)["public_region_rules"] == {
        "CA-NL": 3,
        "US": 8,
    }


@pytest.mark.asyncio
async def test_broken_database_does_not_enable_region_lock(hass, monkeypatch):
    """An unreadable database cannot activate a restriction."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    before = dict(entry.options)
    monkeypatch.setattr(panel, "path_is_file", lambda path: True)
    monkeypatch.setattr(panel, "async_prepare_geoip_reader", AsyncMock())
    monkeypatch.setattr(panel, "geoip_reader", lambda hass: None)
    with pytest.raises(HomeAssistantError, match="could not be loaded"):
        await panel.async_panel_set_options(
            hass,
            {
                "public_region_enabled": True,
                "public_region_rules": {"CA": 5},
                "confirmed": True,
            },
        )
    assert entry.options == before


@pytest.mark.asyncio
async def test_incomplete_new_backup_is_rejected(hass):
    """A new-format backup cannot silently discard its region list."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    before = dict(entry.options)
    with pytest.raises(HomeAssistantError, match="both public region"):
        await backup.async_import_config_from_yaml(
            hass,
            yaml.safe_dump({"domain": DOMAIN, "format_version": 3, "settings": {}}),
        )
    assert entry.options == before
