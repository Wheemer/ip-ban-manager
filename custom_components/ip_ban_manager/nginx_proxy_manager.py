"""Nginx Proxy Manager edge-policy integration."""

from __future__ import annotations

import asyncio
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from ipaddress import IPv6Address, ip_address
from urllib.parse import urlsplit, urlunsplit

from aiohttp import ClientError, ClientResponse, ClientTimeout
from homeassistant.components.http.ban import KEY_BAN_MANAGER
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_COMPONENT_LOADED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .activity import (
    KEY_ACTIVITY_NPM_CACHE,
    KEY_ACTIVITY_NPM_LOCK,
    NPM_ACTIVITY_CACHE_SECONDS,
    parse_npm_access_line,
    record_history_events,
)
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
from .region_rules import entry_public_region_settings
from .runtime_options import entry_callback_route_protection_enabled
from .storage_keys import KEY_CONFIG_ENTRY

NPM_ACCESS_LIST_NAME = "IP Ban Manager"
NPM_CONFIG_BEGIN = "# BEGIN IP BAN MANAGER"
NPM_CONFIG_END = "# END IP BAN MANAGER"
NPM_REQUEST_TIMEOUT = ClientTimeout(total=15)
NPM_DISCOVERY_TIMEOUT = ClientTimeout(total=5)
NPM_SYNC_DEBOUNCE_SECONDS = 1.0
NPM_REGION_AUTH_PATH = "/api/ip_ban_manager/npm-region-auth"
NPM_REGION_AUTH_SECRET_KEY = "region_auth_secret"
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
KEY_NPM_TOKEN_TIMER = "ip_ban_manager_npm_token_timer"
KEY_NPM_TOKEN_TASK = "ip_ban_manager_npm_token_task"
KEY_NPM_DISCOVERY_CACHE = "ip_ban_manager_npm_discovery_cache"


class NpmAuthenticationError(HomeAssistantError):
    """NPM requires a fresh sign-in, not another proxy configuration update."""


class NpmConfigurationError(HomeAssistantError):
    """NPM accepted a write but could not activate the generated host."""


@dataclass(frozen=True, slots=True)
class NpmProxyHost:
    """Small, validated proxy-host record used by the panel."""

    host_id: int
    domain_names: tuple[str, ...]
    access_list_id: int
    enabled: bool
    advanced_config: str
    locations: tuple[str, ...] = ()

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


def suggested_npm_url(hass: HomeAssistant) -> str:
    """Return an NPM origin suggestion using Home Assistant's local API IP."""
    api = getattr(hass.config, "api", None)
    raw_address = str(getattr(api, "local_ip", "") or "").strip()
    try:
        address = ip_address(raw_address)
    except ValueError:
        return ""
    if address.is_unspecified:
        return ""
    host = (
        f"[{address.compressed}]"
        if isinstance(address, IPv6Address)
        else address.compressed
    )
    return f"http://{host}:81"


def _supervisor_origin() -> str:
    """Return the Supervisor origin when this is an OS or Supervised install."""
    value = os.environ.get("SUPERVISOR", "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = f"http://{value}"
    return value.rstrip("/")


def _installed_addon(value: object) -> bool:
    """Interpret Supervisor's installed flag without treating 'false' as true."""
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "none", "null"}
    return bool(value)


def _is_npm_addon(value: object) -> bool:
    """Match the official/community Nginx Proxy Manager add-on identifiers."""
    if not isinstance(value, Mapping):
        return False
    name = re.sub(r"[^a-z0-9]", "", str(value.get("name") or "").lower())
    slug = re.sub(r"[^a-z0-9]", "", str(value.get("slug") or "").lower())
    return name == "nginxproxymanager" or slug.endswith("nginxproxymanager")


async def async_detect_npm_addon(hass: HomeAssistant) -> dict[str, object]:
    """Detect a Supervisor-managed NPM add-on without reading credentials."""
    cached = hass.data.get(KEY_NPM_DISCOVERY_CACHE)
    if isinstance(cached, Mapping):
        try:
            if (
                dt_util.utcnow().timestamp() - float(cached.get("fetched_at", 0))
                < 60
            ):
                result = cached.get("result")
                if isinstance(result, Mapping):
                    return dict(result)
        except (TypeError, ValueError):
            pass

    origin = _supervisor_origin()
    token = os.environ.get("SUPERVISOR_TOKEN", "").strip()
    if not origin or not token:
        return {"addon_detected": False}

    result: dict[str, object] = {"addon_detected": False}
    try:
        session = async_get_clientsession(hass)
        async with session.request(
            "GET",
            f"{origin}/addons",
            headers={"Authorization": f"Bearer {token}"},
            timeout=NPM_DISCOVERY_TIMEOUT,
        ) as response:
            body = await response.json(content_type=None)
            addons = body.get("addons") if isinstance(body, Mapping) else None
            if response.status >= 400 or not isinstance(addons, list):
                return result
        addon = next(
            (
                item
                for item in addons
                if _is_npm_addon(item)
                and isinstance(item, Mapping)
                and _installed_addon(item.get("installed"))
            ),
            None,
        )
        if isinstance(addon, Mapping):
            result = {
                "addon_detected": True,
                "addon_name": str(addon.get("name") or "Nginx Proxy Manager"),
                "addon_slug": str(addon.get("slug") or ""),
                "addon_state": str(addon.get("state") or ""),
                "detected_url": suggested_npm_url(hass),
            }
    except (ClientError, TimeoutError, ValueError):
        return result
    finally:
        hass.data[KEY_NPM_DISCOVERY_CACHE] = {
            "fetched_at": dt_util.utcnow().timestamp(),
            "result": result,
        }
    return result


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
        locations=tuple(
            str(location.get("path") or "")
            for location in value.get("locations", []) or []
            if isinstance(location, Mapping)
        ),
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


def _stored_host_ids(value: object) -> set[int]:
    """Return valid unique proxy-host ids from persistent state."""
    if not isinstance(value, list):
        return set()
    return {host_id for item in value if (host_id := _stored_int(item)) > 0}


def _cached_npm_activity(hass: HomeAssistant) -> list[dict[str, object]] | None:
    """Return a fresh activity cache, treating malformed data as stale."""
    cached = hass.data.get(KEY_ACTIVITY_NPM_CACHE)
    if not isinstance(cached, Mapping):
        return None
    try:
        age = dt_util.utcnow().timestamp() - float(cached.get("fetched_at", 0))
    except (TypeError, ValueError):
        return None
    if age < 0 or age >= NPM_ACTIVITY_CACHE_SECONDS:
        return None
    rows = cached.get("events")
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def npm_panel_status(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, object]:
    """Return non-sensitive NPM state for the panel."""
    config = entry_npm_config(entry)
    runtime = _runtime(hass)
    return {
        "configured": bool(config.get("base_url") and config.get("token")),
        "enabled": bool(config.get("enabled")),
        "base_url": str(config.get("base_url") or ""),
        "suggested_url": suggested_npm_url(hass),
        "identity": str(config.get("identity") or ""),
        "external_hostname": external_hostname(hass),
        "proxy_host_id": _stored_int(config.get("proxy_host_id")),
        "exact_match_host_id": _stored_int(config.get("exact_match_host_id")),
        "access_list_id": _stored_int(config.get("access_list_id")),
        "mirror_default_deny": bool(config.get("mirror_default_deny")),
        "protect_all_domains": bool(config.get("protect_all_domains")),
        "managed_host_ids": sorted(_stored_host_ids(config.get("managed_host_ids"))),
        "token_expires": str(config.get("token_expires") or ""),
        "matches": _stored_list(runtime.get("matches")),
        "hosts": _stored_list(runtime.get("hosts", config.get("hosts"))),
        "last_sync": runtime.get("last_sync"),
        "last_error": runtime.get("last_error"),
        "activity_error": runtime.get("activity_error"),
        "reauth_required": bool(runtime.get("reauth_required")),
    }


class NpmClient:
    """Minimal client for the supported NPM 2.x API surface."""

    def __init__(self, hass: HomeAssistant, base_url: str, token: str = "") -> None:
        """Initialize the NGINX Proxy Manager client."""
        self._session = async_get_clientsession(hass)
        self._hass = hass
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
            async with self._session.request(
                method,
                f"{self.base_url}/api/{path.lstrip('/')}",
                json=dict(payload) if payload is not None else None,
                headers=headers,
                timeout=NPM_REQUEST_TIMEOUT,
            ) as response:
                return await self._response_json(response)
        except (ClientError, TimeoutError) as err:
            raise HomeAssistantError(
                f"Could not connect to Nginx Proxy Manager: {err}"
            ) from err
        except NpmAuthenticationError as err:
            if authenticated and isinstance(self._hass, HomeAssistant):
                _runtime(self._hass).update(
                    {"reauth_required": True, "last_error": str(err)}
                )
            raise

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
            error = body.get("error") if isinstance(body, Mapping) else None
            if isinstance(error, Mapping):
                message = error.get("message") or message
            if not isinstance(message, str):
                message = None
            if message in {
                "Token has expired",
                "Empty token",
                "invalid token",
                "invalid signature",
                "jwt malformed",
            }:
                raise NpmAuthenticationError(
                    "Nginx Proxy Manager authentication has expired or is invalid. "
                    "Sign in again to restore the connection. Existing proxy rules have not been removed."
                )
            raise HomeAssistantError(
                f"Nginx Proxy Manager returned HTTP {response.status}."
                + (f" {message}" if message else "")
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

    async def log_sources(self) -> object:
        """Return NPM's available log sources."""
        return await self._json("GET", "logs/sources")

    async def log_tail(
        self,
        *,
        host_id: int,
        lines: int = 100,
    ) -> object:
        """Return recent access-log lines for one proxy host."""
        return await self._json(
            "GET",
            f"logs/tail?type=host&host_type=proxy&host_id={host_id}"
            f"&channel=access&lines={max(1, min(lines, 1000))}",
        )

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
        result = await self._json(
            "PUT",
            f"nginx/proxy-hosts/{host_id}",
            payload=payload,
        )
        meta = result.get("meta") if isinstance(result, Mapping) else None
        if isinstance(meta, Mapping) and meta.get("nginx_online") is False:
            detail = str(meta.get("nginx_err") or "NGINX configuration test failed.")
            raise NpmConfigurationError(
                f"Nginx Proxy Manager could not activate the proxy host: {detail}"
            )

    async def delete_access_list(self, access_list_id: int) -> None:
        """Delete an integration-owned legacy access list."""
        await self._json("DELETE", f"nginx/access-lists/{access_list_id}")


def _persist_npm_config(hass: HomeAssistant, config: Mapping[str, object]) -> None:
    update_entry_options(hass, **{CONF_NPM: dict(config)})
    _schedule_token_refresh(hass, config)


async def async_npm_activity(
    hass: HomeAssistant, entry: ConfigEntry
) -> list[dict[str, object]]:
    """Read and parse recent NPM access logs without blocking the panel."""
    config = entry_npm_config(entry)
    if not config.get("enabled") or not config.get("base_url") or not config.get("token"):
        return []

    runtime = _runtime(hass)
    if (cached_events := _cached_npm_activity(hass)) is not None:
        return cached_events

    lock = hass.data.get(KEY_ACTIVITY_NPM_LOCK)
    if lock is None:
        lock = asyncio.Lock()
        hass.data[KEY_ACTIVITY_NPM_LOCK] = lock
    async with lock:
        if (cached_events := _cached_npm_activity(hass)) is not None:
            return cached_events

        try:
            client = NpmClient(
                hass, str(config["base_url"]), str(config["token"])
            )
            sources = await client.log_sources()
            host_labels: dict[int, str] = {}
            if isinstance(sources, Mapping):
                hosts = sources.get("hosts")
                proxy_hosts = hosts.get("proxy") if isinstance(hosts, Mapping) else None
                if isinstance(proxy_hosts, list):
                    for item in proxy_hosts:
                        if isinstance(item, Mapping):
                            try:
                                host_id = int(item["id"])
                            except (KeyError, TypeError, ValueError):
                                continue
                            host_labels[host_id] = str(item.get("label") or host_id)
            host_ids = _stored_host_ids(config.get("managed_host_ids"))
            if not host_ids:
                host_ids = {_stored_int(config.get("proxy_host_id"))}
            events: list[dict[str, object]] = []
            for host_id in sorted(host_ids):
                if host_id < 1:
                    continue
                result = await client.log_tail(host_id=host_id)
                lines = result.get("lines") if isinstance(result, Mapping) else None
                if not isinstance(lines, list):
                    continue
                for line in lines:
                    if isinstance(line, str):
                        if parsed := parse_npm_access_line(
                            line, host=host_labels.get(host_id, str(host_id))
                        ):
                            events.append(parsed)
            events = events[-100:]
            record_history_events(hass, events)
            hass.data[KEY_ACTIVITY_NPM_CACHE] = {
                "fetched_at": dt_util.utcnow().timestamp(),
                "events": events,
            }
            runtime["activity_error"] = None
            return events
        except (HomeAssistantError, ClientError, TimeoutError) as err:
            runtime["activity_error"] = str(err)
            return []


@callback
def _schedule_token_refresh(hass: HomeAssistant, config: Mapping[str, object]) -> None:
    """Renew before expiry even when no bans or settings have changed."""
    if remove := hass.data.pop(KEY_NPM_TOKEN_TIMER, None):
        remove()
    if not config.get("base_url") or not config.get("token"):
        return
    try:
        expires = dt_util.parse_datetime(str(config.get("token_expires") or ""))
    except ValueError:
        return
    if expires is None:
        return
    when = max(
        dt_util.as_utc(expires) - timedelta(hours=1),
        dt_util.utcnow() + timedelta(minutes=1),
    )

    @callback
    def renew(_now: object) -> None:
        hass.data.pop(KEY_NPM_TOKEN_TIMER, None)
        hass.data[KEY_NPM_TOKEN_TASK] = hass.async_create_task(
            _async_refresh_token(hass), "IP Ban Manager NPM token renewal"
        )

    hass.data[KEY_NPM_TOKEN_TIMER] = async_track_point_in_utc_time(hass, renew, when)


async def _async_refresh_token(hass: HomeAssistant) -> None:
    config: dict[str, object] = {}
    try:
        config = entry_npm_config(_config_entry(hass))
        if not config.get("base_url") or not config.get("token"):
            return
        client = NpmClient(hass, str(config["base_url"]), str(config["token"]))
        token = await client.refresh_token()
        current = entry_npm_config(_config_entry(hass))
        if (current.get("base_url"), current.get("token")) == (
            config.get("base_url"),
            config.get("token"),
        ):
            _persist_npm_config(hass, {**current, **token})
    except (HomeAssistantError, ClientError, TimeoutError) as err:
        entry = hass.http.app.get(KEY_CONFIG_ENTRY)
        if not isinstance(entry, ConfigEntry):
            return
        current = entry_npm_config(entry)
        if (current.get("base_url"), current.get("token")) != (
            config.get("base_url"),
            config.get("token"),
        ):
            return
        _runtime(hass)["last_error"] = str(err)
        if not isinstance(err, NpmAuthenticationError):
            # Retry transient failures without writing anything to the proxy host.
            _schedule_token_refresh(
                hass,
                {
                    **config,
                    "token_expires": (
                        dt_util.utcnow() + timedelta(hours=1, minutes=5)
                    ).isoformat(),
                },
            )
    finally:
        if hass.data.get(KEY_NPM_TOKEN_TASK) is asyncio.current_task():
            hass.data.pop(KEY_NPM_TOKEN_TASK, None)


async def async_connect_npm(
    hass: HomeAssistant, base_url: object, identity: object, secret: object
) -> None:
    """Authenticate and exact-match the HA external hostname in NPM."""
    email = str(identity or "").strip()
    password = str(secret or "")
    if not email or not password:
        raise HomeAssistantError("Enter the Nginx Proxy Manager email and password.")
    client = NpmClient(hass, normalize_npm_url(base_url))
    token = await client.authenticate(email, password)
    hosts = await client.proxy_hosts()
    current = entry_npm_config(_config_entry(hass))
    if current.get("base_url") == client.base_url and current.get("identity") == email:
        host_id = _stored_int(current.get("proxy_host_id"))
        if host_id and not any(
            isinstance(host, Mapping) and host.get("id") == host_id for host in hosts
        ):
            raise HomeAssistantError(
                "The selected Nginx Proxy Manager host is not accessible with these credentials."
            )
        _persist_npm_config(hass, {**current, **token})
        _runtime(hass).update(
            {"reauth_required": False, "last_error": None, "activity_error": None}
        )
        if current.get("enabled"):
            schedule_npm_sync(hass)
        return
    if current.get("enabled"):
        await async_disable_npm(hass)
        current = entry_npm_config(_config_entry(hass))
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
            "protect_all_domains": False,
            "managed_host_ids": [],
        },
    )
    _runtime(hass).update(
        {
            "last_error": None,
            "last_sync": None,
            "reauth_required": False,
            "activity_error": None,
        }
    )


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


def _ha_local_api_url(hass: HomeAssistant) -> str:
    """Return the local HTTP origin NPM can use for its auth subrequest."""
    api = getattr(hass.config, "api", None)
    raw_address = str(getattr(api, "local_ip", "") or "").strip()
    try:
        address = ip_address(raw_address)
    except ValueError as err:
        raise HomeAssistantError(
            "Home Assistant's local API address is unavailable for NPM region protection."
        ) from err
    if address.is_unspecified:
        raise HomeAssistantError(
            "Home Assistant's local API address is unavailable for NPM region protection."
        )
    host = (
        f"[{address.compressed}]"
        if isinstance(address, IPv6Address)
        else address.compressed
    )
    port = int(getattr(hass.http, "server_port", 8123))
    return f"http://{host}:{port}"


def _ensure_region_auth_secret(config: Mapping[str, object]) -> dict[str, object]:
    """Return NPM settings with a persistent secret for the auth subrequest."""
    secret = str(config.get(NPM_REGION_AUTH_SECRET_KEY) or "").strip()
    if secret:
        return dict(config)
    return {**config, NPM_REGION_AUTH_SECRET_KEY: secrets.token_urlsafe(32)}


def _region_auth_rules(
    hass: HomeAssistant, config: Mapping[str, object]
) -> list[str]:
    """Build the compact NPM gate used for public-region enforcement."""
    secret = str(config.get(NPM_REGION_AUTH_SECRET_KEY) or "").strip()
    if not secret:
        raise HomeAssistantError(
            "NPM region protection is missing its authorization secret. Disable and re-enable NPM protection."
        )
    origin = _ha_local_api_url(hass)
    return [
        f"auth_request {NPM_REGION_AUTH_PATH};",
        f"location = {NPM_REGION_AUTH_PATH} {{",
        "    internal;",
        "    auth_request off;",
        "    proxy_pass_request_body off;",
        '    proxy_set_header Content-Length "";',
        f'    proxy_set_header X-IP-Ban-Manager-Secret "{secret}";',
        "    proxy_set_header X-IP-Ban-Manager-Client-IP $remote_addr;",
        f"    proxy_pass {origin}{NPM_REGION_AUTH_PATH};",
        "}",
    ]


def _policy_rules(
    hass: HomeAssistant,
    entry: ConfigEntry,
    default_deny: bool,
    *,
    include_callbacks: bool = True,
    region_auth_rules: tuple[str, ...] = (),
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
    rules.extend(region_auth_rules)
    if default_deny:
        rules.append("deny all;")
    if include_callbacks and entry_callback_route_protection_enabled(entry):
        rules.extend(
            _callback_location_rules(
                exact_bans,
                frozenset(hass.config.components),
                region_auth_enabled=bool(region_auth_rules),
            )
        )
    return rules


def _callback_location_rules(
    exact_bans: list[str],
    component_domains: frozenset[str] = frozenset(),
    *,
    region_auth_enabled: bool = False,
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
                *(("    auth_request off;",) if region_auth_enabled else ()),
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
    if rules is not None:
        generated = {
            (match[1] == "=", match[2])
            for line in rules
            if (match := re.fullmatch(r"location (=|\^~) (\S+) \{", line))
        }
        for location in host.locations:
            parts = location.strip().split()
            key = (parts[0] == "=", parts[-1]) if parts else None
            if key in generated:
                raise HomeAssistantError(
                    f"NPM Custom Location {location!r} conflicts with a protected callback route. "
                    "No proxy settings were changed."
                )
    # Do not rewrite a host merely to disconnect after manual rule removal.
    if advanced_config.rstrip() != host.advanced_config.rstrip() or detach_legacy:
        try:
            await client.update_proxy_host_policy(
                host.host_id,
                advanced_config,
                access_list_id=0 if detach_legacy else None,
            )
        except NpmConfigurationError as err:
            # NPM persists failed writes. Restore only if nobody changed the host meanwhile.
            latest = next(
                (
                    row
                    for row in await client.proxy_hosts()
                    if isinstance(row, Mapping) and row.get("id") == host.host_id
                ),
                None,
            )
            if (
                latest is not None
                and latest.get("advanced_config") == advanced_config
                and _stored_int(latest.get("access_list_id"))
                == (0 if detach_legacy else host.access_list_id)
            ):
                try:
                    await client.update_proxy_host_policy(
                        host.host_id,
                        host.advanced_config,
                        access_list_id=host.access_list_id if detach_legacy else None,
                    )
                except HomeAssistantError as rollback_error:
                    raise HomeAssistantError(
                        f"{err} Restoring the previous policy also failed: {rollback_error}"
                    ) from err
                raise HomeAssistantError(
                    f"{err} The previous policy was restored."
                ) from err
            raise
    if managed is not None:
        await client.delete_access_list(managed_id)


async def _reconcile_proxy_policies(
    client: NpmClient,
    hosts: list[NpmProxyHost],
    *,
    selected_host_id: int,
    managed_id: int,
    previous_host_ids: set[int],
    desired_rules: Mapping[int, list[str]],
) -> list[int]:
    """Apply one desired policy set and roll back earlier hosts on failure."""
    by_id = {host.host_id: host for host in hosts}
    discovered = {
        host.host_id
        for host in hosts
        if NPM_CONFIG_BEGIN in host.advanced_config
        or NPM_CONFIG_END in host.advanced_config
    }
    operation_ids = (previous_host_ids | discovered | set(desired_rules)) & set(by_id)
    # The selected host owns any legacy access list. Process it last so deleting
    # that list cannot be followed by another host failure.
    ordered_ids = sorted(
        operation_ids, key=lambda host_id: (host_id == selected_host_id, host_id)
    )
    changed: list[NpmProxyHost] = []
    try:
        for host_id in ordered_ids:
            host = by_id[host_id]
            rules = desired_rules.get(host_id)
            proposed = (
                _with_managed_config(host.advanced_config, rules)
                if rules is not None
                else _without_managed_config(host.advanced_config)
            )
            legacy_id = managed_id if host_id == selected_host_id else 0
            detach_legacy = bool(legacy_id and host.access_list_id == legacy_id)
            if proposed.rstrip() != host.advanced_config.rstrip() or detach_legacy:
                await _apply_proxy_policy(client, host, legacy_id, rules)
                changed.append(host)
    except (HomeAssistantError, ClientError, TimeoutError) as err:
        rollback_errors: list[str] = []
        for host in reversed(changed):
            try:
                await client.update_proxy_host_policy(
                    host.host_id,
                    host.advanced_config,
                    access_list_id=host.access_list_id,
                )
            except (HomeAssistantError, ClientError, TimeoutError) as rollback_error:
                rollback_errors.append(f"host {host.host_id}: {rollback_error}")
        if rollback_errors:
            raise HomeAssistantError(
                f"{err} Restoring earlier proxy hosts also failed: "
                + "; ".join(rollback_errors)
            ) from err
        if changed:
            raise HomeAssistantError(
                f"{err} Earlier proxy-host changes were restored."
            ) from err
        raise
    return sorted(desired_rules)


async def _reconcile_npm_policy(
    hass: HomeAssistant,
    entry: ConfigEntry,
    config: Mapping[str, object],
    client: NpmClient,
    hosts: list[NpmProxyHost],
    *,
    enabled: bool,
    protect_all_domains: bool,
) -> list[int]:
    """Reconcile the selected host or every active proxy host."""
    selected_host_id = _stored_int(config.get("proxy_host_id"))
    if enabled and not any(host.host_id == selected_host_id for host in hosts):
        raise HomeAssistantError(
            "The selected Nginx Proxy Manager host no longer exists."
        )
    desired_rules: dict[int, list[str]] = {}
    if enabled:
        default_deny = entry_default_deny_enabled(entry)
        region_settings = entry_public_region_settings(hass, entry)
        region_auth_rules = tuple(
            _region_auth_rules(hass, config)
            if region_settings["public_region_enabled"]
            else ()
        )
        targets = (
            [host for host in hosts if host.enabled]
            if protect_all_domains
            else [host for host in hosts if host.host_id == selected_host_id]
        )
        if not targets:
            raise HomeAssistantError(
                "Nginx Proxy Manager has no active proxy hosts to protect."
            )
        for host in targets:
            desired_rules[host.host_id] = _policy_rules(
                hass,
                entry,
                default_deny,
                include_callbacks=host.host_id == selected_host_id,
                region_auth_rules=region_auth_rules,
            )
    previous_host_ids = _stored_host_ids(config.get("managed_host_ids"))
    if config.get("enabled") and selected_host_id:
        previous_host_ids.add(selected_host_id)
    return await _reconcile_proxy_policies(
        client,
        hosts,
        selected_host_id=selected_host_id,
        managed_id=_stored_int(config.get("access_list_id")),
        previous_host_ids=previous_host_ids,
        desired_rules=desired_rules,
    )


def _access_list_name(item: object) -> str:
    return str(item.get("name") or "") if isinstance(item, Mapping) else ""


async def async_enable_npm(
    hass: HomeAssistant,
    *,
    mirror_default_deny: bool | None = None,
    protect_all_domains: bool | None = None,
) -> None:
    """Enable edge-policy synchronization on the configured proxy hosts."""
    entry = _config_entry(hass)
    config = entry_npm_config(entry)
    config = _ensure_region_auth_secret(config)
    # The main default-deny option is authoritative; keep the keyword for live reloads.
    mirror_default_deny = entry_default_deny_enabled(entry)
    host_id = _stored_int(config.get("proxy_host_id"))
    if not host_id:
        raise HomeAssistantError("Select the Home Assistant proxy host first.")
    client = NpmClient(
        hass, str(config.get("base_url") or ""), str(config.get("token") or "")
    )
    hosts = [host for item in await client.proxy_hosts() if (host := _proxy_host(item))]
    protect_all_domains = (
        bool(config.get("protect_all_domains"))
        if protect_all_domains is None
        else bool(protect_all_domains)
    )
    managed_host_ids = await _reconcile_npm_policy(
        hass,
        entry,
        config,
        client,
        hosts,
        enabled=True,
        protect_all_domains=protect_all_domains,
    )
    _persist_npm_config(
        hass,
        {
            **config,
            "access_list_id": 0,
            "enabled": True,
            "mirror_default_deny": bool(mirror_default_deny),
            "protect_all_domains": protect_all_domains,
            "managed_host_ids": managed_host_ids,
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
    config = _ensure_region_auth_secret(config)
    client = NpmClient(
        hass, str(config.get("base_url") or ""), str(config.get("token") or "")
    )
    refreshed = await client.refresh_token()
    hosts = [host for item in await client.proxy_hosts() if (host := _proxy_host(item))]
    mirror_default_deny = entry_default_deny_enabled(entry)
    protect_all_domains = bool(config.get("protect_all_domains"))
    config = {
        **config,
        **refreshed,
        "access_list_id": 0,
        "mirror_default_deny": mirror_default_deny,
    }
    managed_host_ids = await _reconcile_npm_policy(
        hass,
        entry,
        config,
        client,
        hosts,
        enabled=True,
        protect_all_domains=protect_all_domains,
    )
    config["managed_host_ids"] = managed_host_ids
    _persist_npm_config(hass, config)
    _runtime(hass).update(
        {"last_sync": dt_util.utcnow().isoformat(), "last_error": None}
    )


async def async_disable_npm(
    hass: HomeAssistant, *, protect_all_domains: bool | None = None
) -> None:
    """Detach edge protection while keeping the NPM connection available."""
    entry = _config_entry(hass)
    config = entry_npm_config(entry)
    host_id = _stored_int(config.get("proxy_host_id"))
    managed_host_ids = _stored_host_ids(config.get("managed_host_ids"))
    if (host_id or managed_host_ids) and config.get("base_url") and config.get("token"):
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
        if hosts:
            await _reconcile_npm_policy(
                hass,
                entry,
                config,
                client,
                hosts,
                enabled=False,
                protect_all_domains=False,
            )
    _persist_npm_config(
        hass,
        {
            **config,
            "access_list_id": 0,
            "enabled": False,
            "mirror_default_deny": False,
            "protect_all_domains": (
                bool(config.get("protect_all_domains"))
                if protect_all_domains is None
                else bool(protect_all_domains)
            ),
            "managed_host_ids": [],
        },
    )
    _runtime(hass).update(
        {"last_sync": None, "last_error": None, "activity_error": None}
    )


async def async_disconnect_npm(hass: HomeAssistant) -> None:
    """Remove the managed proxy rules and forget NPM credentials."""
    config = entry_npm_config(_config_entry(hass))
    host_id = _stored_int(config.get("proxy_host_id"))
    managed_host_ids = _stored_host_ids(config.get("managed_host_ids"))
    if (host_id or managed_host_ids) and config.get("base_url") and config.get("token"):
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
        if hosts:
            await _reconcile_npm_policy(
                hass,
                _config_entry(hass),
                config,
                client,
                hosts,
                enabled=False,
                protect_all_domains=False,
            )
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
        if hass.data.get(KEY_NPM_SYNC_TASK) is asyncio.current_task():
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
    _schedule_token_refresh(hass, entry_npm_config(_config_entry(hass)))
    if entry_npm_config(_config_entry(hass)).get("enabled"):
        schedule_npm_sync(hass)


async def unload_npm_sync(hass: HomeAssistant) -> None:
    """Remove NPM listeners and cancel a pending sync."""
    for unsubscribe in hass.data.pop(KEY_NPM_UNSUBSCRIBERS, []):
        unsubscribe()
    if remove := hass.data.pop(KEY_NPM_TOKEN_TIMER, None):
        remove()
    tasks: list[asyncio.Task[object]] = []
    if refresh_task := hass.data.pop(KEY_NPM_TOKEN_TASK, None):
        if not refresh_task.done():
            refresh_task.cancel()
        tasks.append(refresh_task)
    task = hass.data.pop(KEY_NPM_SYNC_TASK, None)
    if task is not None:
        if not task.done():
            task.cancel()
        tasks.append(task)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    hass.data.pop(KEY_NPM_RUNTIME, None)
