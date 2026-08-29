"""Tests for regional failed-login thresholds."""

from __future__ import annotations

from ipaddress import ip_address
from types import SimpleNamespace
from typing import cast

import pytest
from homeassistant.components.http.ban import KEY_LOGIN_THRESHOLD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.ip_ban_manager import geoip
from custom_components.ip_ban_manager.const import (
    CONF_AUTO_BAN_ENABLED,
    CONF_LOGIN_ATTEMPTS_THRESHOLD,
    CONF_REGIONAL_LOGIN_THRESHOLDS,
)
from custom_components.ip_ban_manager.entry_helpers import (
    normalize_regional_login_thresholds,
)
from custom_components.ip_ban_manager.storage_keys import KEY_CONFIG_ENTRY


def _hass_with_thresholds(thresholds: dict[str, int]) -> HomeAssistant:
    entry = SimpleNamespace(
        data={},
        options={
            CONF_AUTO_BAN_ENABLED: True,
            CONF_LOGIN_ATTEMPTS_THRESHOLD: 5,
            CONF_REGIONAL_LOGIN_THRESHOLDS: thresholds,
        },
    )
    app = {KEY_CONFIG_ENTRY: entry, KEY_LOGIN_THRESHOLD: 5}
    return cast(HomeAssistant, SimpleNamespace(http=SimpleNamespace(app=app)))


def test_regional_login_thresholds_are_normalized() -> None:
    """Country and subdivision retry limits are bounded and normalized."""
    assert normalize_regional_login_thresholds({"ca": 7, "ca-nl": 3}) == {
        "CA": 7,
        "CA-NL": 3,
    }


@pytest.mark.parametrize("region", ["C", "CAN", "CA-", "CA-TOO-LONG"])
def test_regional_login_thresholds_reject_invalid_codes(region: str) -> None:
    """Malformed region codes cannot reach the failed-login policy."""
    with pytest.raises(HomeAssistantError):
        normalize_regional_login_thresholds({region: 3})


def test_subdivision_threshold_precedes_country(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The most specific configured region controls the retry limit."""
    hass = _hass_with_thresholds({"CA": 7, "CA-NL": 3})
    monkeypatch.setattr(
        geoip,
        "geoip_location_details_for_ip",
        lambda _hass, _address: geoip.GeoIPLocation("Deer Lake, NL, CA", "CA", "CA-NL"),
    )

    assert geoip.effective_login_threshold_for_ip(hass, ip_address("8.8.8.8")) == 3


def test_country_threshold_precedes_global(monkeypatch: pytest.MonkeyPatch) -> None:
    """A country retry limit is used when no subdivision override exists."""
    hass = _hass_with_thresholds({"CA": 7})
    monkeypatch.setattr(
        geoip,
        "geoip_location_details_for_ip",
        lambda _hass, _address: geoip.GeoIPLocation("Deer Lake, NL, CA", "CA", "CA-NL"),
    )

    assert geoip.effective_login_threshold_for_ip(hass, ip_address("8.8.8.8")) == 7


def test_unknown_region_falls_back_to_global(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing GeoIP data never invents a regional threshold."""
    hass = _hass_with_thresholds({"CA": 7})
    monkeypatch.setattr(
        geoip, "geoip_location_details_for_ip", lambda _hass, _address: None
    )

    assert geoip.effective_login_threshold_for_ip(hass, ip_address("8.8.8.8")) == 5


def test_private_ip_uses_global_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """Private and Home Assistant-internal traffic bypasses GeoIP overrides."""
    hass = _hass_with_thresholds({"CA": 7, "CA-NL": 3})

    def fail_lookup(*args: object) -> None:
        raise AssertionError("private addresses must not use GeoIP")

    monkeypatch.setattr(geoip, "geoip_location_details_for_ip", fail_lookup)

    assert geoip.effective_login_threshold_for_ip(hass, ip_address("192.168.1.50")) == 5
