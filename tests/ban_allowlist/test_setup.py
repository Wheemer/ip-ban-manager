"""Test Ban Allowlist setup."""

import logging
from ipaddress import IPv4Address, ip_address
from typing import Any, cast

import pytest
from homeassistant.components.http import ban as http_ban
from homeassistant.components.http.ban import (
    KEY_BAN_MANAGER,
    KEY_FAILED_LOGIN_ATTEMPTS,
    IpBanManager,
)
from homeassistant.core import HomeAssistant
from homeassistant.loader import DATA_CUSTOM_COMPONENTS, async_get_custom_components
from homeassistant.setup import async_setup_component

from custom_components.ban_allowlist.const import DOMAIN


def check_records(records: list[logging.LogRecord]) -> None:
    """Check log records don't have any warnings/errors."""
    for record in records:
        if record.levelno >= logging.WARNING:
            msg = record.getMessage()
            if msg.startswith(
                "We found a custom integration ban_allowlist which has not been tested by Home Assistant"
            ):
                continue
            raise Exception(msg)


async def setup_ban_allowlist(hass: HomeAssistant) -> None:
    """Configure ban_allowlist and dependencies."""
    hass.data[DATA_CUSTOM_COMPONENTS] = None
    assert list((await async_get_custom_components(hass)).keys()) == ["ban_allowlist"]
    await async_setup_component(hass, "http", {})
    await async_setup_component(
        hass,
        DOMAIN,
        {DOMAIN: {"ip_addresses": ["192.168.1.1", "172.17.0.0/24"]}, "foo": "bar"},
    )


@pytest.mark.anyio
async def test_setup(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    """Test setup of ban allowlist."""
    await setup_ban_allowlist(hass)
    check_records(caplog.records)


@pytest.mark.anyio
async def test_hit_allowlist(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test hitting the allowlist."""
    await setup_ban_allowlist(hass)
    await cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER]).async_add_ban(
        IPv4Address("192.168.1.1")
    )
    await cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER]).async_add_ban(
        IPv4Address("10.0.0.1")
    )
    await cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER]).async_add_ban(
        IPv4Address("172.17.0.10")
    )
    await cast(IpBanManager, hass.http.app[KEY_BAN_MANAGER]).async_add_ban(
        IPv4Address("172.17.1.10")
    )
    check_records(caplog.records)

    messages = []

    for record in caplog.records:
        if record.levelno < logging.INFO or not record.name.startswith(
            "custom_components.ban_allowlist"
        ):
            continue

        messages.append(record.getMessage())

    assert messages == [
        "Setting allowlist with ['192.168.1.1/32', '172.17.0.0/24']",
        "Not adding 192.168.1.1 to ban list, as it's in the allowlist",
        "Banning IP 10.0.0.1",
        "Not adding 172.17.0.10 to ban list, as it's in the allowlist",
        "Banning IP 172.17.1.10",
    ]


@pytest.mark.anyio
async def test_ignored_wrong_login_does_not_increment_attempts(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test allowlisted login failures don't count toward a ban."""
    await setup_ban_allowlist(hass)

    remote_addr = ip_address("192.168.1.1")
    hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS][remote_addr] = 1

    class MockRequest:
        remote = "192.168.1.1"
        app = hass.http.app

    await http_ban.process_wrong_login(cast(Any, MockRequest()))
    check_records(caplog.records)

    assert remote_addr not in hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS]

    messages = []

    for record in caplog.records:
        if record.levelno < logging.INFO or not record.name.startswith(
            "custom_components.ban_allowlist"
        ):
            continue

        messages.append(record.getMessage())

    assert messages == [
        "Setting allowlist with ['192.168.1.1/32', '172.17.0.0/24']",
        "Ignoring invalid authentication from 192.168.1.1 because it is in the allowlist",
    ]
