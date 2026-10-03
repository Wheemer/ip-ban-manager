"""Tests for Nginx Proxy Manager edge protection."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from homeassistant.const import EVENT_COMPONENT_LOADED
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.ip_ban_manager import activity
from custom_components.ip_ban_manager import nginx_proxy_manager as npm
from custom_components.ip_ban_manager.const import CONF_NPM, DOMAIN

from .test_setup import setup_ip_ban_manager


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


@pytest.mark.parametrize(
    ("local_ip", "expected"),
    [
        ("192.168.2.66", "http://192.168.2.66:81"),
        ("fd00::21", "http://[fd00::21]:81"),
        ("127.0.0.1", "http://127.0.0.1:81"),
        ("0.0.0.0", ""),
        ("homeassistant.local", ""),
        (None, ""),
    ],
)
def test_suggested_npm_url_uses_ha_local_api_ip(
    local_ip: str | None, expected: str
) -> None:
    """The NPM suggestion never comes from the browser or external HA URL."""
    hass = cast(
        HomeAssistant,
        SimpleNamespace(config=SimpleNamespace(api=SimpleNamespace(local_ip=local_ip))),
    )

    assert npm.suggested_npm_url(hass) == expected


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


def test_region_auth_rules_use_local_ha_and_compact_npm_gate() -> None:
    """NPM receives an auth subrequest instead of a large country CIDR list."""
    hass = cast(
        HomeAssistant,
        SimpleNamespace(
            config=SimpleNamespace(api=SimpleNamespace(local_ip="192.168.1.40")),
            http=SimpleNamespace(server_port=8123),
        ),
    )

    rules = npm._region_auth_rules(
        hass, {npm.NPM_REGION_AUTH_SECRET_KEY: "test-secret"}
    )
    rendered = "\n".join(rules)

    assert "auth_request /api/ip_ban_manager/npm-region-auth;" in rendered
    assert 'proxy_set_header X-IP-Ban-Manager-Secret "test-secret";' in rendered
    assert "proxy_set_header X-IP-Ban-Manager-Client-IP $remote_addr;" in rendered
    assert "proxy_pass http://192.168.1.40:8123/api/ip_ban_manager/npm-region-auth;" in rendered
    assert len(rendered) < 2000


def test_callback_locations_bypass_non_exact_edge_restrictions() -> None:
    """NPM callback locations override inherited network/default-deny rules."""
    rules = npm._callback_location_rules(
        ["deny 203.0.113.10;"], frozenset({"alexa", "google_assistant"})
    )
    rendered = "\n".join(rules)

    assert "location = /auth/token {" in rendered
    assert "location = /api/google_assistant {" in rendered
    assert "location ^~ /api/webhook/ {" in rendered
    assert "location = /api/alexa {" in rendered
    assert "location ^~ /api/alexa/ {" in rendered
    location_count = rendered.count("location ")
    assert rendered.count("deny 203.0.113.10;") == location_count
    assert rendered.count("allow all;") == location_count
    assert rendered.count("include conf.d/include/proxy.conf;") == location_count


def test_region_auth_is_disabled_for_unprotected_callback_locations() -> None:
    """Unprotected callback routes bypass the inherited NPM region gate."""
    rendered = "\n".join(
        npm._callback_location_rules(
            [], frozenset({"google_assistant"}), region_auth_enabled=True
        )
    )

    assert rendered.count("auth_request off;") == rendered.count("location ")


def test_unconfigured_named_callback_is_not_added_to_edge_policy() -> None:
    """NPM receives named routes only for loaded HA integrations."""
    rendered = "\n".join(
        npm._callback_location_rules([], frozenset({"google_assistant"}))
    )

    assert "location = /api/google_assistant {" in rendered
    assert "/api/alexa" not in rendered


@pytest.mark.asyncio
async def test_callback_locations_are_not_generated_when_protection_is_disabled(
    hass: HomeAssistant,
) -> None:
    """Disabling callback protection leaves NPM's ordinary policy unchanged."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            "callback_route_protection_enabled": False,
        },
    )
    assert all("location " not in rule for rule in npm._policy_rules(hass, entry, True))


@pytest.mark.asyncio
async def test_loaded_component_schedules_edge_policy_sync(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Loading an integration refreshes its named callback route in NPM."""
    await setup_ip_ban_manager(hass)
    events: list[str] = []
    monkeypatch.setattr(
        npm,
        "schedule_npm_sync",
        lambda _hass, event=None: events.append(event.event_type),
    )

    hass.bus.async_fire(EVENT_COMPONENT_LOADED, {"component": "google_assistant"})
    await hass.async_block_till_done()

    assert events == [EVENT_COMPONENT_LOADED]


@pytest.mark.asyncio
async def test_npm_activity_reads_selected_proxy_host_logs(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The activity view reads only the managed NPM proxy host access log."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_NPM: {
                "base_url": "http://npm.example.test:81",
                "token": "stored-token",
                "proxy_host_id": 4,
                "managed_host_ids": [4],
                "enabled": True,
            },
        },
    )
    client = AsyncMock()
    client.log_sources.return_value = {
        "hosts": {"proxy": [{"id": 4, "label": "ha.example.test"}]}
    }
    client.log_tail.return_value = {
        "lines": [
            '203.0.113.8 - - [03/Oct/2026:12:34:56 -0230] "GET /login HTTP/1.1" 401 12',
            '203.0.113.9 - - [03/Oct/2026:12:34:57 -0230] "GET /admin HTTP/1.1" 403 12',
        ]
    }
    monkeypatch.setattr(npm, "NpmClient", lambda *args: client)

    events = await npm.async_npm_activity(hass, entry)

    assert events[0]["ip"] == "203.0.113.8"
    assert events[0]["host"] == "ha.example.test"
    assert events[1]["ip"] == "203.0.113.9"
    assert "possible IP Ban Manager edge-policy deny" in events[1]["detail"]
    client.log_tail.assert_awaited_once_with(host_id=4)
    assert activity.history_activity(hass)[0]["ip"] == "203.0.113.9"


@pytest.mark.asyncio
async def test_detect_supervisor_npm_addon_prefills_local_url(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Supervisor discovery finds NPM without attempting authentication."""
    responses = []

    class FakeResponse:
        status = 200

        async def __aenter__(self) -> "FakeResponse":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def json(self, *, content_type: object = None) -> object:
            return {
                "addons": [
                    {
                        "name": "Nginx Proxy Manager",
                        "slug": "a0d7b954_nginxproxymanager",
                        "installed": True,
                        "state": "started",
                    }
                ]
            }

    class FakeSession:
        def request(
            self, method: str, url: str, **kwargs: object
        ) -> FakeResponse:
            responses.append((method, url, kwargs))
            return FakeResponse()

    monkeypatch.setenv("SUPERVISOR", "http://supervisor")
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-token")
    monkeypatch.setattr(npm, "async_get_clientsession", lambda _hass: FakeSession())
    monkeypatch.setattr(hass.config.api, "local_ip", "192.168.2.66")

    result = await npm.async_detect_npm_addon(hass)

    assert result["addon_detected"] is True
    assert result["addon_name"] == "Nginx Proxy Manager"
    assert result["detected_url"] == "http://192.168.2.66:81"
    assert responses[0][0:2] == ("GET", "http://supervisor/addons")
    assert responses[0][2]["headers"] == {
        "Authorization": "Bearer supervisor-token"
    }


@pytest.mark.asyncio
async def test_detect_supervisor_ignores_uninstalled_npm_addon(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An add-on repository entry is not enough to prefill NPM settings."""
    responses = []

    class FakeResponse:
        status = 200

        async def __aenter__(self) -> "FakeResponse":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def json(self, *, content_type: object = None) -> object:
            return {
                "addons": [
                    {
                        "name": "Nginx Proxy Manager",
                        "slug": "a0d7b954_nginxproxymanager",
                        "installed": False,
                        "state": "",
                    }
                ]
            }

    class FakeSession:
        def request(
            self, method: str, url: str, **kwargs: object
        ) -> FakeResponse:
            responses.append((method, url, kwargs))
            return FakeResponse()

    monkeypatch.setenv("SUPERVISOR", "http://supervisor")
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-token")
    monkeypatch.setattr(npm, "async_get_clientsession", lambda _hass: FakeSession())

    result = await npm.async_detect_npm_addon(hass)

    assert result == {"addon_detected": False}
    assert responses[0][0:2] == ("GET", "http://supervisor/addons")


@pytest.mark.asyncio
async def test_protect_all_domains_updates_every_active_host_only(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All-domain mode protects active hosts without HA callbacks on other apps."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_NPM: {
                "base_url": "http://npm.example.test:81",
                "token": "stored-token",
                "proxy_host_id": 4,
                "enabled": False,
            },
        },
    )
    client = AsyncMock()
    client.proxy_hosts.return_value = [
        {
            "id": 4,
            "domain_names": ["ha.example.test"],
            "enabled": True,
            "advanced_config": "# keep-ha",
        },
        {
            "id": 5,
            "domain_names": ["frigate.example.test"],
            "enabled": True,
            "advanced_config": "# keep-frigate",
        },
        {
            "id": 6,
            "domain_names": ["disabled.example.test"],
            "enabled": False,
            "advanced_config": "# keep-disabled",
        },
    ]
    monkeypatch.setattr(npm, "NpmClient", lambda *args: client)

    await npm.async_enable_npm(hass, protect_all_domains=True)

    calls = client.update_proxy_host_policy.await_args_list
    assert [call.args[0] for call in calls] == [5, 4]
    by_host = {call.args[0]: call.args[1] for call in calls}
    assert "# keep-ha" in by_host[4]
    assert "location = /auth/token {" in by_host[4]
    assert "# keep-frigate" in by_host[5]
    assert "location " not in by_host[5]
    saved = npm.entry_npm_config(entry)
    assert saved["protect_all_domains"] is True
    assert saved["managed_host_ids"] == [4, 5]


@pytest.mark.asyncio
async def test_switching_back_to_selected_host_removes_other_managed_blocks(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Turning all-domain mode off cleans every host no longer targeted."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    managed = npm._with_managed_config("# user", ["deny all;"])
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_NPM: {
                "base_url": "http://npm.example.test:81",
                "token": "stored-token",
                "proxy_host_id": 4,
                "enabled": True,
                "protect_all_domains": True,
                "managed_host_ids": [4, 5],
            },
        },
    )
    client = AsyncMock()
    client.proxy_hosts.return_value = [
        {
            "id": 4,
            "domain_names": ["ha.example.test"],
            "enabled": True,
            "advanced_config": managed,
        },
        {
            "id": 5,
            "domain_names": ["frigate.example.test"],
            "enabled": True,
            "advanced_config": managed,
        },
    ]
    monkeypatch.setattr(npm, "NpmClient", lambda *args: client)

    await npm.async_enable_npm(hass, protect_all_domains=False)

    client.update_proxy_host_policy.assert_any_await(5, "# user", access_list_id=None)
    saved = npm.entry_npm_config(entry)
    assert saved["protect_all_domains"] is False
    assert saved["managed_host_ids"] == [4]


@pytest.mark.asyncio
async def test_multi_host_failure_restores_hosts_changed_earlier() -> None:
    """A later NPM rejection cannot leave an earlier host partially updated."""
    client = AsyncMock()
    client.update_proxy_host_policy.side_effect = [
        None,
        HomeAssistantError("HTTP 400"),
        None,
    ]
    hosts = [
        npm.NpmProxyHost(4, ("ha.example.test",), 0, True, "# ha"),
        npm.NpmProxyHost(5, ("frigate.example.test",), 0, True, "# frigate"),
    ]

    with pytest.raises(
        HomeAssistantError, match="Earlier proxy-host changes were restored"
    ):
        await npm._reconcile_proxy_policies(
            client,
            hosts,
            selected_host_id=5,
            managed_id=0,
            previous_host_ids=set(),
            desired_rules={4: ["deny all;"], 5: ["deny all;"]},
        )

    assert client.update_proxy_host_policy.await_args_list[-1].args == (4, "# ha")
    assert client.update_proxy_host_policy.await_args_list[-1].kwargs == {
        "access_list_id": 0
    }


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

        async def __aenter__(self) -> "FakeResponse":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def json(self, *, content_type: object = None) -> object:
            return {"token": "jwt-token", "expires": "tomorrow"}

    class FakeSession:
        def request(
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
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"message": "Invalid credentials"}, "Invalid credentials"),
        (
            {"error": {"code": 400, "message": "Invalid proxy configuration"}},
            "Invalid proxy configuration",
        ),
        ({"error": {"message": {"unexpected": "object"}}}, "HTTP 400"),
        ({"error": None}, "HTTP 400"),
    ],
)
async def test_client_surfaces_npm_api_error(
    monkeypatch: pytest.MonkeyPatch, body: object, expected: str
) -> None:
    """NPM API messages are returned as useful Home Assistant errors."""

    class FakeResponse:
        status = 400

        async def __aenter__(self) -> "FakeResponse":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def json(self, *, content_type: object = None) -> object:
            return body

    class FakeSession:
        def request(self, *args: object, **kwargs: object) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(npm, "async_get_clientsession", lambda _hass: FakeSession())
    hass = cast(HomeAssistant, SimpleNamespace())
    client = npm.NpmClient(hass, "http://192.168.1.40:81")

    with pytest.raises(HomeAssistantError, match=expected):
        await client.authenticate("admin@example.test", "wrong")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["async_disable_npm", "async_disconnect_npm"])
@pytest.mark.parametrize("has_managed_rules", [False, True])
async def test_cleanup_after_npm_update_failure(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    has_managed_rules: bool,
) -> None:
    """Already removed rules need no PUT; failed real cleanup retains credentials."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    config = {
        "base_url": "http://npm.example.test:81",
        "token": "stored-token",
        "proxy_host_id": 4,
        "enabled": True,
    }
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_NPM: config}
    )
    custom = "proxy_set_header X-Test keep-me;\n\n"
    host = {
        "id": 4,
        "domain_names": ["ha.example.test"],
        "access_list_id": 9,
        "advanced_config": (
            npm._with_managed_config(custom, ["deny all;"])
            if has_managed_rules
            else custom
        ),
        "locations": [
            {
                "path": "/other",
                "forward_scheme": "http",
                "forward_host": "192.168.1.25",
                "forward_port": 8080,
            }
        ],
    }
    client = AsyncMock()
    client.refresh_token.return_value = {"token": "new-token"}
    client.proxy_hosts.return_value = [host]
    client.update_proxy_host_policy.side_effect = HomeAssistantError("HTTP 400")
    monkeypatch.setattr(npm, "NpmClient", lambda *args: client)

    if has_managed_rules:
        with pytest.raises(HomeAssistantError, match="HTTP 400"):
            await getattr(npm, operation)(hass)
        assert npm.entry_npm_config(entry) == config
        client.update_proxy_host_policy.assert_awaited_once_with(
            4, custom.rstrip(), access_list_id=None
        )
    else:
        await getattr(npm, operation)(hass)
        client.update_proxy_host_policy.assert_not_awaited()
        assert not npm.entry_npm_config(entry).get("enabled")
        if operation == "async_disconnect_npm":
            assert npm.entry_npm_config(entry) == {}
        else:
            assert npm.entry_npm_config(entry)["token"] == "new-token"
        assert host["advanced_config"] == custom
    client.delete_access_list.assert_not_awaited()


@pytest.mark.asyncio
async def test_unchanged_policy_still_detaches_legacy_access_list() -> None:
    """No-op advanced config does not skip required legacy-list cleanup."""
    client = AsyncMock()
    client.access_lists.return_value = [{"id": 7, "name": npm.NPM_ACCESS_LIST_NAME}]
    host = npm.NpmProxyHost(4, ("ha.example.test",), 7, True, "")

    await npm._apply_proxy_policy(client, host, 7, None)

    client.update_proxy_host_policy.assert_awaited_once_with(4, "", access_list_id=0)
    client.delete_access_list.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_unchanged_policy_does_not_rewrite_host() -> None:
    """Repeated sync leaves an identical managed block untouched."""
    client = AsyncMock()
    rules = ["deny 203.0.113.10;"]
    host = npm.NpmProxyHost(
        4, ("ha.example.test",), 0, True, npm._with_managed_config("", rules)
    )

    await npm._apply_proxy_policy(client, host, 0, rules)

    client.update_proxy_host_policy.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_access_list_ignores_malformed_ids() -> None:
    """Malformed unrelated NPM rows do not break legacy-list cleanup."""

    class FakeClient:
        async def access_lists(self) -> list[object]:
            return [
                {"id": "not-an-id", "name": "Unrelated"},
                {"id": 7, "name": npm.NPM_ACCESS_LIST_NAME},
            ]

    managed = await npm._legacy_access_list(cast(Any, FakeClient()), 7)

    assert managed == {"id": 7, "name": npm.NPM_ACCESS_LIST_NAME}


@pytest.mark.asyncio
async def test_disconnect_refreshes_token_before_removing_managed_rules(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disconnect can clean up NPM after the originally stored token expires."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_NPM: {
                "base_url": "http://192.168.1.40:81",
                "token": "old-token",
                "proxy_host_id": 4,
                "access_list_id": 0,
                "enabled": True,
            },
        },
    )
    calls: list[object] = []

    class FakeClient:
        def __init__(
            self, mock_hass: HomeAssistant, base_url: str, token: str = ""
        ) -> None:
            assert mock_hass is hass
            assert base_url == "http://192.168.1.40:81"
            assert token == "old-token"

        async def refresh_token(self) -> dict[str, str]:
            calls.append("refresh")
            return {"token": "new-token", "token_expires": "tomorrow"}

        async def proxy_hosts(self) -> list[object]:
            calls.append("hosts")
            return [
                {
                    "id": 4,
                    "domain_names": ["ha.example.test"],
                    "access_list_id": 0,
                    "enabled": True,
                    "advanced_config": (
                        "keep-this;\n"
                        f"{npm.NPM_CONFIG_BEGIN}\n"
                        "deny all;\n"
                        f"{npm.NPM_CONFIG_END}\n"
                    ),
                }
            ]

        async def update_proxy_host_policy(
            self,
            host_id: int,
            advanced_config: str,
            *,
            access_list_id: int | None = None,
        ) -> None:
            calls.append((host_id, advanced_config, access_list_id))

        async def delete_access_list(self, access_list_id: int) -> None:
            calls.append(("delete", access_list_id))

    monkeypatch.setattr(npm, "NpmClient", FakeClient)

    await npm.async_disconnect_npm(hass)

    assert calls == ["refresh", "hosts", (4, "keep-this;", None)]
    assert npm.entry_npm_config(entry) == {}
