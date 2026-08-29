"""Tests for Nginx Proxy Manager edge protection."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.ip_ban_manager import nginx_proxy_manager as npm


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://192.168.1.40:81", "http://192.168.1.40:81"),
        ("https://npm.example.test/", "https://npm.example.test"),
        ("https://npm.example.test/api", "https://npm.example.test"),
        ("http://[fd00::21]:81", "http://[fd00::21]:81"),
    ],
)
def test_normalize_npm_url(value: str, expected: str) -> None:
    """Valid NPM origins are normalized without changing their host."""
    assert npm.normalize_npm_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "npm.example.test",
        "ftp://npm.example.test",
        "https://user:secret@npm.example.test",
        "https://npm.example.test/admin",
        "https://npm.example.test?token=secret",
    ],
)
def test_normalize_npm_url_rejects_unsafe_values(value: str) -> None:
    """Credentials, unsupported schemes, and arbitrary paths are rejected."""
    with pytest.raises(HomeAssistantError):
        npm.normalize_npm_url(value)


def test_exact_external_url_matches_only_exact_domain() -> None:
    """The integration never guesses a proxy host from a partial hostname."""
    hosts: list[object] = [
        {
            "id": 1,
            "domain_names": ["ha.example.test"],
            "access_list_id": 0,
            "enabled": True,
            "advanced_config": "",
        },
        {
            "id": 2,
            "domain_names": ["other-ha.example.test"],
            "access_list_id": 0,
            "enabled": True,
            "advanced_config": "",
        },
    ]

    matches = npm.exact_external_url_matches(hosts, "HA.EXAMPLE.TEST.")

    assert [host.host_id for host in matches] == [1]


def test_managed_config_replacement_preserves_user_configuration() -> None:
    """Synchronization changes only the marked IP Ban Manager block."""
    original = "proxy_set_header X-Test keep-me;"
    first = npm._with_managed_config(original, ["allow 192.168.1.0/24;"])
    updated = npm._with_managed_config(first, ["deny 203.0.113.10;"])

    assert original in updated
    assert "allow 192.168.1.0/24;" not in updated
    assert "deny 203.0.113.10;" in updated
    assert updated.count(npm.NPM_CONFIG_BEGIN) == 1
    assert updated.count(npm.NPM_CONFIG_END) == 1


def test_incomplete_managed_config_is_rejected() -> None:
    """A damaged managed block cannot be overwritten silently."""
    with pytest.raises(HomeAssistantError, match="incomplete"):
        npm._without_managed_config(f"keep\n{npm.NPM_CONFIG_BEGIN}\ndeny all;\n")


@pytest.mark.asyncio
async def test_client_authenticate_uses_token_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NPM credentials are exchanged once for a stored access token."""
    requests: list[dict[str, Any]] = []

    class FakeResponse:
        status = 200

        async def json(self, *, content_type: object = None) -> object:
            return {"token": "jwt-token", "expires": "tomorrow"}

    class FakeSession:
        async def request(
            self, method: str, url: str, **kwargs: object
        ) -> FakeResponse:
            requests.append({"method": method, "url": url, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(npm, "async_get_clientsession", lambda _hass: FakeSession())
    hass = cast(HomeAssistant, SimpleNamespace())
    client = npm.NpmClient(hass, "http://192.168.1.40:81")

    token = await client.authenticate("admin@example.test", "secret")

    assert token == {"token": "jwt-token", "token_expires": "tomorrow"}
    assert requests == [
        {
            "method": "POST",
            "url": "http://192.168.1.40:81/api/tokens",
            "json": {"identity": "admin@example.test", "secret": "secret"},
            "headers": {
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            "timeout": npm.NPM_REQUEST_TIMEOUT,
        }
    ]


@pytest.mark.asyncio
async def test_client_surfaces_npm_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """NPM API messages are returned as useful Home Assistant errors."""

    class FakeResponse:
        status = 401

        async def json(self, *, content_type: object = None) -> object:
            return {"message": "Invalid credentials"}

    class FakeSession:
        async def request(self, *args: object, **kwargs: object) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(npm, "async_get_clientsession", lambda _hass: FakeSession())
    hass = cast(HomeAssistant, SimpleNamespace())
    client = npm.NpmClient(hass, "http://192.168.1.40:81")

    with pytest.raises(HomeAssistantError, match="Invalid credentials"):
        await client.authenticate("admin@example.test", "wrong")
