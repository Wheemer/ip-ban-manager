"""Isolate integration-owned files between Home Assistant tests."""

import pytest


@pytest.fixture
def hass_config_dir(hass_tmp_config_dir: str) -> str:
    """Use an independent Home Assistant configuration directory per test."""
    return hass_tmp_config_dir
