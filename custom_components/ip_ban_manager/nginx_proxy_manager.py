"""Nginx Proxy Manager edge-policy integration."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from aiohttp import ClientError, ClientResponse, ClientTimeout
from homeassistant.components.http.ban import KEY_BAN_MANAGER
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_COMPONENT_LOADED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .ban_lookup import (
    CALLBACK_ROUTE_EXACT_PATHS,
    CALLBACK_ROUTE_PREFIXES,
    INTEGRATION_CALLBACK_EXACT_PATHS,
    INTEGRATION_CALLBACK_PREFIXES,
)
from .ban_ops import chronological_ip_bans
from .const import (
    CONF_NPM,
    EVENT_ALLOWLIST_NETWORK_ADDED,
    EVENT_ALLOWLIST_NETWORK_REMOVED,
    EVENT_BLOCKED_NETWORK_ADDED,
    EVENT_BLOCKED_NETWORK_REMOVED,
    EVENT_IP_BANNED,
    EVENT_IP_UNBANNED,
)
from .entry_helpers import (
    entry_allowlisted_logins_can_ban,
    entry_blocked_networks,
    entry_default_deny_enabled,
    entry_ip_addresses,
    update_entry_options,
)
from .ip_utils import parse_allowlist_network
from .runtime_options import entry_callback_route_protection_enabled
from .storage_keys import KEY_CONFIG_ENTRY

NPM_ACCESS_LIST_NAME = "IP Ban Manager"
NPM_CONFIG_BEGIN = "# BEGIN IP BAN MANAGER"
NPM_CONFIG_END = "# END IP BAN MANAGER"
NPM_REQUEST_TIMEOUT = ClientTimeout(total=15)
NPM_SYNC_DEBOUNCE_SECONDS = 1.0
_MANAGED_CONFIG_PATTERN = re.compile(
    rf"(?m)^[ \t]*{re.escape(NPM_CONFIG_BEGIN)}[ \t]*\r?\n"
    rf".*?^[ \t]*{re.escape(NPM_CONFIG_END)}[ \t]*(?:\r?\n)?",
    re.DOTALL,
)
# String keys remain stable when this module is loaded into a running HA process
# whose storage_keys module predates the feature.
KEY_NPM_RUNTIME = "ip_ban_manager_npm_runtime"
KEY_NPM_SYNC_TASK = "ip_ban_manager_npm_sync_task"
KEY_NPM_UNSUBSCRIBERS = "ip_ban_manager_npm_unsubscribers"


@dataclass(frozen=True, slots=True)
class NpmProxyHost:
    """Small, validated proxy-host record used by the panel."""

    host_id: int
    domain_names: tuple[str, ...]
    access_list_id: int
    enabled: bool
    advanced_config: str

    def panel_dict(self) -> dict[str, object]:
        """Return a non-sensitive panel representation."""
        return {
            "id": self.host_id,
            "domain_names": list(self.domain_names),
            "access_list_id": self.access_list_id,
            "enabled": self.enabled,
        }


def _config_entry(hass: HomeAssistant) -> ConfigEntry:
    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    if not isinstance(entry, ConfigEntry):
        raise HomeAssistantError("IP Ban Manager is not loaded.")
    return entry


def normalize_npm_url(value: object) -> str:
    """Return a normalized NPM origin, accepting an optional /api suffix."""
    raw = str(value or "").strip()
    if not raw:
        raise HomeAssistantError("Enter the Nginx Proxy Manager URL.")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HomeAssistantError(
            "Nginx Proxy Manager URL must begin with http:// or https://."
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HomeAssistantError("Nginx Proxy Manager URL is not valid.")
    path = parsed.path.rstrip("/")
    if path == "/api":
        path = ""
    if path:
        raise HomeAssistantError("Nginx Proxy Manager URL must not include a path.")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def external_hostname(hass: HomeAssistant) -> str:
    """Return the exact hostname configured as Home Assistant's external URL."""
    external_url = str(getattr(hass.config, "external_url", "") or "").strip()
    hostname = urlsplit(external_url).hostname
    return hostname.rstrip(".").lower() if hostname else ""


def _normalized_domain(value: object) -> str:
    domain = str(value or "").strip().rstrip(".").lower()
    try:
        return domain.encode("idna").decode("ascii")
    except UnicodeError:
        return domain


def _proxy_host(value: object) -> NpmProxyHost | None:
    if not isinstance(value, Mapping):
        return None
    try:
        host_id = int(value["id"])
        access_list_id = int(value.get("access_list_id") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    domains = tuple(
        domain
        for domain in (
            _normalized_domain(item) for item in value.get("domain_names", [])
        )
        if domain
    )
    if host_id < 1 or not domains:
        return None
    return NpmProxyHost(
        host_id=host_id,
        domain_names=domains,
        access_list_id=max(0, access_list_id),
        enabled=bool(value.get("enabled", True)),
        advanced_config=str(value.get("advanced_config") or ""),
    )


def exact_external_url_matches(
    hosts: list[object], hostname: str
) -> list[NpmProxyHost]:
    """Return only proxy hosts with an exact external-hostname match."""
    normalized = _normalized_domain(hostname)
    if not normalized:
        return []
    parsed = [host for item in hosts if (host := _proxy_host(item)) is not None]
    return [host for host in parsed if normalized in host.domain_names]


def entry_npm_config(entry: ConfigEntry) -> dict[str, object]:
    """Return the stored NPM configuration without mutating the entry."""
    value = entry.options.get(CONF_NPM, entry.data.get(CONF_NPM, {}))
    return dict(value) if isinstance(value, Mapping) else {}


def _runtime(hass: HomeAssistant) -> dict[str, object]:
    return hass.data.setdefault(KEY_NPM_RUNTIME, {})


def _stored_int(value: object) -> int:
    """Return a stored integer, or zero when legacy data is malformed."""
    try:
        return int(str(value or "0"))
    except ValueError:
        return 0


def _stored_list(value: object) -> list[object]:
    """Return a shallow copy of a stored list."""
    return list(value) if isinstance(value, list) else []


def npm_panel_status(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, object]:
    """Return non-sensitive NPM state for the panel."""
    config = entry_npm_config(entry)
    runtime = _runtime(hass)
    return {
        "configured": bool(config.get("base_url") and config.get("token")),
        "enabled": bool(config.get("enabled")),
        "base_url": str(config.get("base_url") or ""),
        "identity": str(config.get("identity") or ""),
        "external_hostname": external_hostname(hass),
        "proxy_host_id": _stored_int(config.get("proxy_host_id")),
        "exact_match_host_id": _stored_int(config.get("exact_match_host_id")),
        "access_list_id": _stored_int(config.get("access_list_id")),
        "mirror_default_deny": bool(config.get("mirror_default_deny")),
        "token_expires": str(config.get("token_expires") or ""),
        "matches": _stored_list(runtime.get("matches")),
        "hosts": _stored_list(runtime.get("hosts", config.get("hosts"))),
        "last_sync": runtime.get("last_sync"),
        "last_error": runtime.get("last_error"),
    }


class NpmClient:
    """Minimal client for the supported NPM 2.x API surface."""

    def __init__(self, hass: HomeAssistant, base_url: str, token: str = "") -> None:
        """Initialize the NGINX Proxy Manager client."""
        self._session = async_get_clientsession(hass)
        self.base_url = normalize_npm_url(base_url)
        self.token = token

    async def _json(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, object] | None = None,
        authenticated: bool = True,
    ) -> object:
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if authenticated:
            if not self.token:
                raise HomeAssistantError("Nginx Proxy Manager is not connected.")
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = await self._session.request(
                method,
                f"{self.base_url}/api/{path.lstrip('/')}",
                json=dict(payload) if payload is not None else None,
                headers=headers,
                timeout=NPM_REQUEST_TIMEOUT,
            )
        except (ClientError, TimeoutError) as err:
            raise HomeAssistantError(
                f"Could not connect to Nginx Proxy Manager: {err}"
            ) from err
        return await self._response_json(response)

    @staticmethod
    async def _response_json(response: ClientResponse) -> object:
        try:
            body = await response.json(content_type=None)
        except (ClientError, ValueError) as err:
            raise HomeAssistantError(
                f"Nginx Proxy Manager returned HTTP {response.status}."
            ) from err
        if response.status >= 400:
            message = body.get("message") if isinstance(body, Mapping) else None
            raise HomeAssistantError(
                str(message or f"Nginx Proxy Manager returned HTTP {response.status}.")
            )
        return body

    async def authenticate(self, identity: str, secret: str) -> dict[str, str]:
        """Authenticate and retain the returned access token."""
        result = await self._json(
            "POST",
            "tokens",
            payload={"identity": identity, "secret": secret},
            authenticated=False,
        )
        if not isinstance(result, Mapping) or not result.get("token"):
            raise HomeAssistantError(
                "Nginx Proxy Manager did not return an access token. "
                "Two-factor authentication is not supported yet."
            )
        self.token = str(result["token"])
        return {
            "token": self.token,
            "token_expires": str(result.get("expires") or ""),
        }

    async def refresh_token(self) -> dict[str, str]:
        """Refresh and retain the current access token."""
        result = await self._json("GET", "tokens")
        if not isinstance(result, Mapping) or not result.get("token"):
            raise HomeAssistantError("Could not refresh the Nginx Proxy Manager token.")
        self.token = str(result["token"])
        return {
            "token": self.token,
            "token_expires": str(result.get("expires") or ""),
        }

    async def proxy_hosts(self) -> list[object]:
        """Return the configured proxy hosts."""
        result = await self._json("GET", "nginx/proxy-hosts")
        if not isinstance(result, list):
            raise HomeAssistantError(
                "Nginx Proxy Manager returned an invalid host list."
            )
        return result

    async def access_lists(self) -> list[object]:
        """Return access lists with their client entries expanded."""
        result = await self._json("GET", "nginx/access-lists?expand=clients")
        if not isinstance(result, list):
            raise HomeAssistantError(
                "Nginx Proxy Manager returned an invalid access-list response."
            )
        return result

    async def update_proxy_host_policy(
        self,
        host_id: int,
        advanced_config: str,
        *,
        access_list_id: int | None = None,
    ) -> None:
        """Update only the fields owned by edge-policy synchronization."""
        payload: dict[str, object] = {"advanced_config": advanced_config}
        if access_list_id is not None:
            payload["access_list_id"] = access_list_id
        await self._json(
            "PUT",
            f"nginx/proxy-hosts/{host_id}",
            payload=payload,
        )

    async def delete_access_list(self, access_list_id: int) -> None:
        """Delete an integration-owned legacy access list."""
        await self._json("DELETE", f"nginx/access-lists/{access_list_id}")


def _persist_npm_config(hass: HomeAssistant, config: Mapping[str, object]) -> None:
    update_entry_options(hass, **{CONF_NPM: dict(config)})


async def async_connect_npm(
    hass: HomeAssistant, base_url: object, identity: object, secret: object
) -> None:
    """Authenticate and exact-match the HA external hostname in NPM."""
    email = str(identity or "").strip()
    password = str(secret or "")
    if not email or not password:
        raise HomeAssistantError("Enter the Nginx Proxy Manager email and password.")
    current = entry_npm_config(_config_entry(hass))
    if current.get("enabled"):
        await async_disable_npm(hass)
        current = entry_npm_config(_config_entry(hass))
    client = NpmClient(hass, normalize_npm_url(base_url))
    token = await client.authenticate(email, password)
    hosts = await client.proxy_hosts()
    hostname = external_hostname(hass)
    if not hostname:
        raise HomeAssistantError(
            "Set Home Assistant's external URL before connecting Nginx Proxy Manager."
        )
    matches = exact_external_url_matches(hosts, hostname)
    parsed_hosts = [host for item in hosts if (host := _proxy_host(item)) is not None]
    _runtime(hass)["matches"] = [host.panel_dict() for host in matches]
    _runtime(hass)["hosts"] = [host.panel_dict() for host in parsed_hosts]
    selected_id = matches[0].host_id if len(matches) == 1 else 0
    _persist_npm_config(
        hass,
        {
            **current,
            "base_url": client.base_url,
            "identity": email,
            **token,
            "proxy_host_id": selected_id,
            "exact_match_host_id": selected_id,
            "hosts": [host.panel_dict() for host in parsed_hosts],
            "access_list_id": 0,
            "enabled": False,
            "mirror_default_deny": False,
        },
    )
    _runtime(hass).update({"last_error": None, "last_sync": None})


async def async_select_npm_host(hass: HomeAssistant, host_id_value: object) -> None:
    """Select a proxy host after validating it still exists."""
    try:
        host_id = int(str(host_id_value))
    except (TypeError, ValueError) as err:
        raise HomeAssistantError(
            "Select a valid Nginx Proxy Manager proxy host."
        ) from err
    entry = _config_entry(hass)
    config = entry_npm_config(entry)
    if config.get("enabled"):
        await async_disable_npm(hass)
        config = entry_npm_config(entry)
    client = NpmClient(
        hass, str(config.get("base_url") or ""), str(config.get("token") or "")
    )
    hosts = [host for item in await client.proxy_hosts() if (host := _proxy_host(item))]
    if not any(host.host_id == host_id for host in hosts):
        raise HomeAssistantError(
            "That Nginx Proxy Manager proxy host no longer exists."
        )
    _persist_npm_config(
        hass,
        {
            **config,
            "proxy_host_id": host_id,
            "exact_match_host_id": 0,
            "hosts": [host.panel_dict() for host in hosts],
            "access_list_id": 0,
            "enabled": False,
        },
    )


def _policy_rules(
    hass: HomeAssistant, entry: ConfigEntry, default_deny: bool
) -> list[str]:
    """Build ordered NGINX access rules without changing NGINX's default."""
    allows = [
        f"allow {parse_allowlist_network(value)};"
        for value in entry_ip_addresses(entry)
    ]
    ban_manager = hass.http.app.get(KEY_BAN_MANAGER)
    exact_bans = (
        [f"deny {ban.ip_address};" for ban in chronological_ip_bans(ban_manager)]
        if ban_manager is not None
        else []
    )
    network_bans = [
        f"deny {parse_allowlist_network(value)};"
        for value in entry_blocked_networks(entry)
    ]
    denies = [*exact_bans, *network_bans]
    rules = (
        [*denies, *allows]
        if entry_allowlisted_logins_can_ban(entry)
        else [*allows, *denies]
    )
    if default_deny:
        rules.append("deny all;")
    if entry_callback_route_protection_enabled(entry):
        rules.extend(
            _callback_location_rules(exact_bans, frozenset(hass.config.components))
        )
    return rules


def _callback_location_rules(
    exact_bans: list[str], component_domains: frozenset[str] = frozenset()
) -> list[str]:
    """Build callback locations that bypass non-exact managed restrictions."""
    access_rules = [*exact_bans, "allow all;"]
    locations = [("=", path) for path in sorted(CALLBACK_ROUTE_EXACT_PATHS)] + [
        ("^~", prefix) for prefix in CALLBACK_ROUTE_PREFIXES
    ]
    for domain in sorted(component_domains):
        locations.extend(
            ("=", path)
            for path in sorted(INTEGRATION_CALLBACK_EXACT_PATHS.get(domain, ()))
        )
        locations.extend(
            ("^~", prefix) for prefix in INTEGRATION_CALLBACK_PREFIXES.get(domain, ())
        )
    rules: list[str] = []
    for modifier, path in locations:
        rules.extend(
            [
                f"location {modifier} {path} {{",
                *(f"    {rule}" for rule in access_rules),
                "    include conf.d/include/proxy.conf;",
                "}",
            ]
        )
    return rules


def _without_managed_config(advanced_config: str) -> str:
    """Remove only IP Ban Manager's complete marked configuration block."""
    has_begin = NPM_CONFIG_BEGIN in advanced_config
    has_end = NPM_CONFIG_END in advanced_config
    if has_begin != has_end:
        raise HomeAssistantError(
            "The Nginx Proxy Manager host contains an incomplete IP Ban Manager configuration block."
        )
    cleaned, count = _MANAGED_CONFIG_PATTERN.subn("", advanced_config)
    if has_begin and count != 1:
        raise HomeAssistantError(
            "The Nginx Proxy Manager host contains an invalid IP Ban Manager configuration block."
        )
    return cleaned.rstrip()


def _with_managed_config(advanced_config: str, rules: list[str]) -> str:
    """Replace IP Ban Manager's marked configuration block."""
    existing = _without_managed_config(advanced_config)
    block = "\n".join((NPM_CONFIG_BEGIN, *rules, NPM_CONFIG_END))
    return f"{existing}\n\n{block}\n" if existing else f"{block}\n"


async def _legacy_access_list(
    client: NpmClient, managed_id: int
) -> Mapping[str, object] | None:
    if not managed_id:
        return None
    managed = next(
        (
            item
            for item in await client.access_lists()
            if isinstance(item, Mapping) and _stored_int(item.get("id")) == managed_id
        ),
        None,
    )
    if managed is not None and _access_list_name(managed) != NPM_ACCESS_LIST_NAME:
        raise HomeAssistantError(
            "The previously managed Nginx Proxy Manager access list was renamed."
        )
    return managed


async def _apply_proxy_policy(
    client: NpmClient,
    host: NpmProxyHost,
    managed_id: int,
    rules: list[str] | None,
) -> None:
    """Apply or remove managed rules and migrate the legacy access list."""
    managed = await _legacy_access_list(client, managed_id)
    advanced_config = (
        _with_managed_config(host.advanced_config, rules)
        if rules is not None
        else _without_managed_config(host.advanced_config)
    )
    detach_legacy = bool(managed_id and host.access_list_id == managed_id)
    await client.update_proxy_host_policy(
        host.host_id,
        advanced_config,
        access_list_id=0 if detach_legacy else None,
    )
    if managed is not None:
        await client.delete_access_list(managed_id)


def _access_list_name(item: object) -> str:
    return str(item.get("name") or "") if isinstance(item, Mapping) else ""


async def async_enable_npm(
    hass: HomeAssistant, *, mirror_default_deny: bool | None = None
) -> None:
    """Enable edge-policy synchronization on the selected proxy host."""
    entry = _config_entry(hass)
    config = entry_npm_config(entry)
    # The main default-deny option is authoritative; keep the keyword for live reloads.
    mirror_default_deny = entry_default_deny_enabled(entry)
    host_id = _stored_int(config.get("proxy_host_id"))
    if not host_id:
        raise HomeAssistantError("Select the Home Assistant proxy host first.")
    client = NpmClient(
        hass, str(config.get("base_url") or ""), str(config.get("token") or "")
    )
    hosts = [host for item in await client.proxy_hosts() if (host := _proxy_host(item))]
    host = next((item for item in hosts if item.host_id == host_id), None)
    if host is None:
        raise HomeAssistantError(
            "The selected Nginx Proxy Manager host no longer exists."
        )

    managed_id = _stored_int(config.get("access_list_id"))
    await _apply_proxy_policy(
        client,
        host,
        managed_id,
        _policy_rules(hass, entry, mirror_default_deny),
    )
    _persist_npm_config(
        hass,
        {
            **config,
            "access_list_id": 0,
            "enabled": True,
            "mirror_default_deny": bool(mirror_default_deny),
        },
    )
    _runtime(hass).update(
        {"last_sync": dt_util.utcnow().isoformat(), "last_error": None}
    )


async def async_sync_npm(hass: HomeAssistant) -> None:
    """Synchronize current IP/network rules to the selected proxy host."""
    entry = _config_entry(hass)
    config = entry_npm_config(entry)
    if not config.get("enabled"):
        return
    managed_id = _stored_int(config.get("access_list_id"))
    client = NpmClient(
        hass, str(config.get("base_url") or ""), str(config.get("token") or "")
    )
    refreshed = await client.refresh_token()
    host_id = _stored_int(config.get("proxy_host_id"))
    hosts = [host for item in await client.proxy_hosts() if (host := _proxy_host(item))]
    host = next((item for item in hosts if item.host_id == host_id), None)
    if host is None:
        raise HomeAssistantError(
            "The selected Nginx Proxy Manager host no longer exists."
        )
    mirror_default_deny = entry_default_deny_enabled(entry)
    config = {
        **config,
        **refreshed,
        "access_list_id": 0,
        "mirror_default_deny": mirror_default_deny,
    }
    await _apply_proxy_policy(
        client,
        host,
        managed_id,
        _policy_rules(hass, entry, mirror_default_deny),
    )
    _persist_npm_config(hass, config)
    _runtime(hass).update(
        {"last_sync": dt_util.utcnow().isoformat(), "last_error": None}
    )


async def async_disable_npm(hass: HomeAssistant) -> None:
    """Detach edge protection while keeping the NPM connection available."""
    entry = _config_entry(hass)
    config = entry_npm_config(entry)
    host_id = _stored_int(config.get("proxy_host_id"))
    managed_id = _stored_int(config.get("access_list_id"))
    if host_id and config.get("base_url") and config.get("token"):
        client = NpmClient(
            hass,
            str(config["base_url"]),
            str(config["token"]),
        )
        refreshed = await client.refresh_token()
        config = {**config, **refreshed}
        hosts = [
            host
            for item in await client.proxy_hosts()
            if (host := _proxy_host(item)) is not None
        ]
        host = next((item for item in hosts if item.host_id == host_id), None)
        if host is not None:
            await _apply_proxy_policy(client, host, managed_id, None)
    _persist_npm_config(
        hass,
        {
            **config,
            "access_list_id": 0,
            "enabled": False,
            "mirror_default_deny": False,
        },
    )
    _runtime(hass).update({"last_sync": None, "last_error": None})


async def async_disconnect_npm(hass: HomeAssistant) -> None:
    """Remove the managed proxy rules and forget NPM credentials."""
    config = entry_npm_config(_config_entry(hass))
    host_id = _stored_int(config.get("proxy_host_id"))
    managed_id = _stored_int(config.get("access_list_id"))
    if host_id and config.get("base_url") and config.get("token"):
        client = NpmClient(
            hass,
            str(config["base_url"]),
            str(config["token"]),
        )
        await client.refresh_token()
        hosts = [
            host
            for item in await client.proxy_hosts()
            if (host := _proxy_host(item)) is not None
        ]
        host = next((item for item in hosts if item.host_id == host_id), None)
        if host is not None:
            await _apply_proxy_policy(client, host, managed_id, None)
    _persist_npm_config(hass, {})
    _runtime(hass).clear()


async def _async_debounced_sync(hass: HomeAssistant) -> None:
    try:
        await asyncio.sleep(NPM_SYNC_DEBOUNCE_SECONDS)
        await async_sync_npm(hass)
    except asyncio.CancelledError:
        raise
    except (HomeAssistantError, ClientError, TimeoutError) as err:
        _runtime(hass)["last_error"] = str(err)
    finally:
        hass.data.pop(KEY_NPM_SYNC_TASK, None)


@callback
def schedule_npm_sync(hass: HomeAssistant, _event: Event | None = None) -> None:
    """Debounce NPM writes after local policy events."""
    task = hass.data.get(KEY_NPM_SYNC_TASK)
    if task is not None and not task.done():
        task.cancel()
    hass.data[KEY_NPM_SYNC_TASK] = hass.async_create_task(
        _async_debounced_sync(hass), "IP Ban Manager NPM sync"
    )


def setup_npm_sync(hass: HomeAssistant) -> None:
    """Listen for policy changes without delaying Home Assistant startup."""
    if hass.data.get(KEY_NPM_UNSUBSCRIBERS):
        return
    events = (
        EVENT_COMPONENT_LOADED,
        EVENT_IP_BANNED,
        EVENT_IP_UNBANNED,
        EVENT_ALLOWLIST_NETWORK_ADDED,
        EVENT_ALLOWLIST_NETWORK_REMOVED,
        EVENT_BLOCKED_NETWORK_ADDED,
        EVENT_BLOCKED_NETWORK_REMOVED,
    )

    @callback
    def _schedule_sync(event: Event) -> None:
        schedule_npm_sync(hass, event)

    hass.data[KEY_NPM_UNSUBSCRIBERS] = [
        hass.bus.async_listen(event_type, _schedule_sync) for event_type in events
    ]
    if entry_npm_config(_config_entry(hass)).get("enabled"):
        schedule_npm_sync(hass)


def unload_npm_sync(hass: HomeAssistant) -> None:
    """Remove NPM listeners and cancel a pending sync."""
    for unsubscribe in hass.data.pop(KEY_NPM_UNSUBSCRIBERS, []):
        unsubscribe()
    task = hass.data.pop(KEY_NPM_SYNC_TASK, None)
    if task is not None and not task.done():
        task.cancel()
    hass.data.pop(KEY_NPM_RUNTIME, None)
