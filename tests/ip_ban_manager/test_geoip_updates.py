"""Tests for opt-in DB-IP maintenance."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.error import HTTPError

import pytest

from custom_components.ip_ban_manager import geoip


def test_monthly_geoip_update_probe(tmp_path, monkeypatch):
    """A late release is not reported available until the URL exists."""
    monkeypatch.setattr(
        geoip.dt_util, "utcnow", lambda: datetime(2026, 12, 1, tzinfo=UTC)
    )
    monkeypatch.setattr(geoip, "_database_build_epoch", lambda _: 1793498400)
    path = tmp_path / "city.mmdb"
    probe = MagicMock(side_effect=HTTPError("", 404, "not found", {}, None))
    monkeypatch.setattr(geoip, "urlopen", probe)
    assert geoip.geoip_update_url(path) is None
    assert probe.call_count == 1
    assert probe.call_args.args[0].get_method() == "HEAD"
    probe.side_effect = None
    assert geoip.geoip_update_url(path).endswith("2026-12.mmdb.gz")


@pytest.mark.parametrize("enabled", [False, True])
async def test_geoip_auto_update_is_opt_in(hass, monkeypatch, enabled):
    """Both settings detect releases, but only opt-in installs one."""
    hass.http = SimpleNamespace(
        app={
            geoip.KEY_CONFIG_ENTRY: SimpleNamespace(
                options={geoip.CONF_GEOIP_AUTO_UPDATE: enabled}
            )
        }
    )
    monkeypatch.setattr(geoip, "geoip_update_url", lambda _: "https://example.com/db")
    download = AsyncMock()
    monkeypatch.setattr(geoip, "async_download_geoip_database", download)
    await geoip.async_check_geoip_update(hass)
    assert hass.data[geoip._UPDATE_AVAILABLE] is True
    if enabled:
        download.assert_awaited_once_with(hass, update_url="https://example.com/db")
    else:
        download.assert_not_awaited()
