"""Exact ban mutations must not evaluate or report request policies."""

import asyncio
from ipaddress import ip_address
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
import yaml
from homeassistant.components.http.ban import KEY_BAN_MANAGER
from homeassistant.helpers.http import current_request

import custom_components.ip_ban_manager.ban_lookup as ban_lookup
from custom_components.ip_ban_manager.ban_ops import async_add_ip_ban
from custom_components.ip_ban_manager.http_views import IPBanManagerManageView
from custom_components.ip_ban_manager.storage_keys import KEY_ORIGINAL_ADD_BAN

from .test_setup import MockViewRequest, setup_ip_ban_manager


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["region", "default_deny"])
@pytest.mark.parametrize("source", ["panel", "service"])
async def test_exact_ban_persists_policy_denied_ip_without_activity(
    hass, tmp_path: Path, policy: str, source: str
) -> None:
    """A real HA add-ban writes even when request policy already denies the IP."""
    await setup_ip_ban_manager(hass)
    manager = hass.http.app[KEY_BAN_MANAGER]
    manager.path = str(tmp_path / "ip_bans.yaml")
    lookup = manager.ip_bans_lookup
    assert type(lookup).__name__ == "NetworkAwareBanLookup"
    target = ip_address("77.111.246.40")
    lookup.geoip_access_allowed = (lambda _: False) if policy == "region" else None
    lookup.default_deny_enabled = policy == "default_deny"
    observer = Mock()
    lookup.blocked_request_observer = observer
    assert target in lookup
    assert not dict.__contains__(lookup, target)

    request = MockViewRequest(
        hass.http.app, data={"action": "add_ban", "value": str(target)}
    )
    request.path = "/api/ip_ban_manager/manage"
    token = current_request.set(cast(Any, request))
    try:
        for _ in range(2):
            if source == "panel":
                response = await IPBanManagerManageView().post(cast(Any, request))
                assert response.status == 200
            else:
                await async_add_ip_ban(hass, str(target))
        observer.assert_not_called()
    finally:
        current_request.reset(token)

    assert dict.__contains__(lookup, target)
    saved = yaml.safe_load(Path(manager.path).read_text(encoding="utf-8"))
    assert str(target) in saved
    lookup.geoip_access_allowed = None
    lookup.default_deny_enabled = False
    assert target in lookup

    # Normal request enforcement and observation still work after the mutation.
    token = current_request.set(cast(Any, request))
    try:
        assert target in lookup
        observer.assert_called_once()
    finally:
        current_request.reset(token)


@pytest.mark.asyncio
async def test_exact_membership_is_task_local_and_restored_on_error() -> None:
    """A suspended mutation must never bypass enforcement in another request."""
    target = ip_address("77.111.246.40")
    lookup = ban_lookup.NetworkAwareBanLookup({}, (), (), True)
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def mutate() -> None:
        with pytest.raises(OSError), ban_lookup.exact_ban_membership():
            assert target not in lookup
            entered.set()
            await finish.wait()
            raise OSError("write failed")
        assert target in lookup

    task = asyncio.create_task(mutate())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert target in lookup
    finally:
        finish.set()
        await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_add_ban_failure_restores_request_policy(hass, monkeypatch) -> None:
    """The actual hook restores membership semantics when HA's writer fails."""
    await setup_ip_ban_manager(hass)
    lookup = hass.http.app[KEY_BAN_MANAGER].ip_bans_lookup
    lookup.default_deny_enabled = True
    monkeypatch.setitem(
        hass.http.app,
        KEY_ORIGINAL_ADD_BAN,
        AsyncMock(side_effect=OSError("write failed")),
    )
    with pytest.raises(OSError, match="write failed"):
        await async_add_ip_ban(hass, "77.111.246.40")
    assert ip_address("77.111.246.40") in lookup
