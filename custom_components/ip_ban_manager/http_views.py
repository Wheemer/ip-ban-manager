"""HTTP API views used by the bundled IP Ban Manager panel."""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from ipaddress import ip_address
from pathlib import Path
from types import ModuleType
from typing import Any

from aiohttp.web import Request, Response
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.const import KEY_HASS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .audit import mutation_source, record_geoip_updated
from .backup import (
    async_export_config,
    async_import_config,
    async_import_config_from_yaml,
    config_download_payload,
)
from .ban_ops import async_add_ip_ban, async_remove_ip_ban
from .const import (
    ATTR_IP_ADDRESS,
    CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED,
    DOMAIN,
    SOURCE_PANEL,
)
from .entry_helpers import update_entry_options
from .geoip import async_download_geoip_database
from .health import async_update_health_issue
from .metrics import metric_increment
from .network_policy import (
    async_add_allowlist_network,
    async_add_blocked_network,
    async_remove_allowlist_network,
    async_remove_blocked_network,
)
from .nginx_proxy_manager import (
    async_connect_npm,
    async_disconnect_npm,
    async_enable_npm,
    async_select_npm_host,
    async_sync_npm,
    schedule_npm_sync,
    setup_npm_sync,
    unload_npm_sync,
)
from .notifications import (
    ALLOWLISTED_LOGIN_SILENCE_URL,
    ATTR_NOTIFICATION_ID,
    PANEL_ACTION_SILENCE_ALLOWLISTED_LOGIN,
    PANEL_ACTION_UNSILENCE_ALLOWLISTED_LOGIN,
    dismiss_allowlisted_login_notifications,
    notification_action_response,
    silence_allowlisted_login_notifications,
)
from .panel import (
    async_panel_payload,
    async_panel_set_options,
    panel_silence_allowlisted_login_notification,
    panel_unsilence_allowlisted_login_notification,
)
from .panel_assets import PANEL_JS_PATH, async_panel_js_response
from .storage_keys import (
    KEY_CONFIG_ENTRY,
    KEY_HTTP_VIEW_HANDLERS,
    KEY_HTTP_VIEWS,
    KEY_LOCAL_GEOIP_REGION_CACHE,
)

_LOGGER = logging.getLogger(__name__)

KEY_RUNTIME_MODULE_MTIMES = "ip_ban_manager_runtime_module_mtimes"
RUNTIME_MODULE_NAMES = (
    "custom_components.ip_ban_manager.const",
    "custom_components.ip_ban_manager.file_store",
    "custom_components.ip_ban_manager.ip_utils",
    "custom_components.ip_ban_manager.entry_meta",
    "custom_components.ip_ban_manager.entry_helpers",
    "custom_components.ip_ban_manager.metrics",
    "custom_components.ip_ban_manager.audit",
    "custom_components.ip_ban_manager.ban_lookup",
    "custom_components.ip_ban_manager.runtime_options",
    "custom_components.ip_ban_manager.backup",
    "custom_components.ip_ban_manager.ban_ops",
    "custom_components.ip_ban_manager.geoip",
    "custom_components.ip_ban_manager.health",
    "custom_components.ip_ban_manager.i18n",
    "custom_components.ip_ban_manager.network_policy",
    "custom_components.ip_ban_manager.nginx_proxy_manager",
    "custom_components.ip_ban_manager.notifications",
    "custom_components.ip_ban_manager.panel_assets",
    "custom_components.ip_ban_manager.panel",
)
RUNTIME_BINDINGS: dict[str, tuple[str, str]] = {
    "async_export_config": (
        "custom_components.ip_ban_manager.backup",
        "async_export_config",
    ),
    "async_import_config": (
        "custom_components.ip_ban_manager.backup",
        "async_import_config",
    ),
    "async_import_config_from_yaml": (
        "custom_components.ip_ban_manager.backup",
        "async_import_config_from_yaml",
    ),
    "config_download_payload": (
        "custom_components.ip_ban_manager.backup",
        "config_download_payload",
    ),
    "async_add_ip_ban": (
        "custom_components.ip_ban_manager.ban_ops",
        "async_add_ip_ban",
    ),
    "async_remove_ip_ban": (
        "custom_components.ip_ban_manager.ban_ops",
        "async_remove_ip_ban",
    ),
    "async_download_geoip_database": (
        "custom_components.ip_ban_manager.geoip",
        "async_download_geoip_database",
    ),
    "async_update_health_issue": (
        "custom_components.ip_ban_manager.health",
        "async_update_health_issue",
    ),
    "async_add_allowlist_network": (
        "custom_components.ip_ban_manager.network_policy",
        "async_add_allowlist_network",
    ),
    "async_add_blocked_network": (
        "custom_components.ip_ban_manager.network_policy",
        "async_add_blocked_network",
    ),
    "async_remove_allowlist_network": (
        "custom_components.ip_ban_manager.network_policy",
        "async_remove_allowlist_network",
    ),
    "async_remove_blocked_network": (
        "custom_components.ip_ban_manager.network_policy",
        "async_remove_blocked_network",
    ),
    "async_connect_npm": (
        "custom_components.ip_ban_manager.nginx_proxy_manager",
        "async_connect_npm",
    ),
    "async_disconnect_npm": (
        "custom_components.ip_ban_manager.nginx_proxy_manager",
        "async_disconnect_npm",
    ),
    "async_enable_npm": (
        "custom_components.ip_ban_manager.nginx_proxy_manager",
        "async_enable_npm",
    ),
    "async_select_npm_host": (
        "custom_components.ip_ban_manager.nginx_proxy_manager",
        "async_select_npm_host",
    ),
    "async_sync_npm": (
        "custom_components.ip_ban_manager.nginx_proxy_manager",
        "async_sync_npm",
    ),
    "schedule_npm_sync": (
        "custom_components.ip_ban_manager.nginx_proxy_manager",
        "schedule_npm_sync",
    ),
    "setup_npm_sync": (
        "custom_components.ip_ban_manager.nginx_proxy_manager",
        "setup_npm_sync",
    ),
    "unload_npm_sync": (
        "custom_components.ip_ban_manager.nginx_proxy_manager",
        "unload_npm_sync",
    ),
    "dismiss_allowlisted_login_notifications": (
        "custom_components.ip_ban_manager.notifications",
        "dismiss_allowlisted_login_notifications",
    ),
    "notification_action_response": (
        "custom_components.ip_ban_manager.notifications",
        "notification_action_response",
    ),
    "silence_allowlisted_login_notifications": (
        "custom_components.ip_ban_manager.notifications",
        "silence_allowlisted_login_notifications",
    ),
    "async_panel_payload": (
        "custom_components.ip_ban_manager.panel",
        "async_panel_payload",
    ),
    "async_panel_set_options": (
        "custom_components.ip_ban_manager.panel",
        "async_panel_set_options",
    ),
    "panel_silence_allowlisted_login_notification": (
        "custom_components.ip_ban_manager.panel",
        "panel_silence_allowlisted_login_notification",
    ),
    "panel_unsilence_allowlisted_login_notification": (
        "custom_components.ip_ban_manager.panel",
        "panel_unsilence_allowlisted_login_notification",
    ),
    "async_panel_js_response": (
        "custom_components.ip_ban_manager.panel_assets",
        "async_panel_js_response",
    ),
}


async def async_dispatch_http_view(
    view: HomeAssistantView, request: Request, handler_name: str
) -> Response:
    """Dispatch to the setup-installed handler, or refuse when unloaded."""
    hass = request.app[KEY_HASS]
    await async_refresh_runtime_imports(hass)
    handlers = hass.data.get(KEY_HTTP_VIEW_HANDLERS)
    if not isinstance(handlers, dict):
        return http_view_not_loaded_response(view, handler_name)
    handler = handlers.get(handler_name)
    if not callable(handler):
        return http_view_not_loaded_response(view, handler_name)
    return await handler(view, request)


def http_view_not_loaded_response(
    view: HomeAssistantView, handler_name: str
) -> Response:
    """Return a not-loaded response that matches each endpoint's style."""
    if handler_name.startswith("silence_"):
        return Response(text="IP Ban Manager is not loaded.", status=404)
    return view.json(
        {"ok": False, "error": "IP Ban Manager is not loaded."},
        status_code=404,
    )


async def async_handle_silence_get(
    view: HomeAssistantView, request: Request
) -> Response:
    """Reject GET to avoid CSRF via notification or cross-site image requests."""
    return view.json_message(
        "Use POST with an administrator session for this action.",
        405,
    )


async def async_handle_silence_post(
    view: HomeAssistantView, request: Request
) -> Response:
    """Silence allowlisted failed-login notifications for one IP or globally."""
    hass = request.app[KEY_HASS]
    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    if entry is None:
        return Response(text="IP Ban Manager is not loaded.", status=404)

    user = request.get("hass_user")
    if user is None or not user.is_admin:
        return view.json_message("Administrator access is required.", 403)

    try:
        data = await request.json()
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}

    query = getattr(request, "query", {})
    ip_address_value = data.get(ATTR_IP_ADDRESS) or query.get(ATTR_IP_ADDRESS)
    if ip_address_value:
        try:
            remote_addr = ip_address(str(ip_address_value))
        except ValueError:
            return Response(text="Invalid IP address.", status=400)

        notification_id = data.get(ATTR_NOTIFICATION_ID) or query.get(
            ATTR_NOTIFICATION_ID
        )
        silence_allowlisted_login_notifications(
            hass,
            entry,
            remote_addr,
            notification_id if isinstance(notification_id, str) else None,
        )
        return notification_action_response()

    update_entry_options(hass, **{CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED: False})
    dismiss_allowlisted_login_notifications(hass)
    return notification_action_response()


async def async_handle_status_get(
    view: HomeAssistantView, request: Request
) -> Response:
    """Return live status and persisted editable values."""
    hass = request.app[KEY_HASS]
    metric_increment(hass, "panel_api_calls")
    user = request.get("hass_user")
    if user is None or not user.is_admin:
        metric_increment(hass, "panel_api_errors")
        return view.json(
            {"ok": False, "error": "Administrator access is required."},
            status_code=403,
        )

    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    if entry is None:
        metric_increment(hass, "panel_api_errors")
        return view.json(
            {"ok": False, "error": "IP Ban Manager is not loaded."},
            status_code=404,
        )

    language = request.query.get("language")
    return view.json(await async_panel_payload(hass, entry, language=language))


async def async_handle_manage_post(
    view: HomeAssistantView, request: Request
) -> Response:
    """Apply one validated panel action."""
    hass = request.app[KEY_HASS]
    metric_increment(hass, "panel_api_calls")
    user = request.get("hass_user")
    if user is None or not user.is_admin:
        metric_increment(hass, "panel_api_errors")
        return view.json(
            {"ok": False, "error": "Administrator access is required."},
            status_code=403,
        )

    try:
        data = await request.json()
    except ValueError:
        metric_increment(hass, "panel_api_errors")
        return view.json(
            {"ok": False, "error": "Expected JSON request body."},
            status_code=400,
        )

    action = data.get("action")
    value = str(data.get("value", "")).strip()
    download: dict[str, str] | None = None

    try:
        if action == "add_allowlist":
            await async_add_allowlist_network(hass, value, SOURCE_PANEL)
        elif action == "remove_allowlist":
            await async_remove_allowlist_network(hass, value, SOURCE_PANEL)
        elif action == "add_ban":
            with mutation_source(SOURCE_PANEL):
                await async_add_ip_ban(hass, value)
        elif action == "remove_ban":
            await async_remove_ip_ban(hass, value, SOURCE_PANEL)
        elif action == "add_blocked_network":
            await async_add_blocked_network(hass, value, SOURCE_PANEL)
        elif action == "remove_blocked_network":
            await async_remove_blocked_network(hass, value, SOURCE_PANEL)
        elif action == "set_options":
            await async_panel_set_options(hass, data.get("options", {}))
        elif action == "update_geoip":
            await async_download_geoip_database(hass)
            record_geoip_updated(hass, SOURCE_PANEL)
        elif action == "export_config":
            await async_export_config(hass)
        elif action == "import_config":
            await async_import_config(hass)
        elif action == "download_config":
            download = config_download_payload(hass)
        elif action == "upload_config":
            content = data.get("content")
            if not isinstance(content, str) or not content.strip():
                raise HomeAssistantError(
                    "Backup upload must include YAML file content."
                )
            await async_import_config_from_yaml(hass, content)
        elif action == "npm_connect":
            await async_connect_npm(
                hass,
                data.get("base_url"),
                data.get("identity"),
                data.get("secret"),
            )
        elif action == "npm_select_host":
            await async_select_npm_host(hass, data.get("host_id"))
        elif action == "npm_enable":
            await async_enable_npm(hass)
        elif action == "npm_sync":
            await async_sync_npm(hass)
        elif action == "npm_disconnect":
            await async_disconnect_npm(hass)
        elif action == PANEL_ACTION_SILENCE_ALLOWLISTED_LOGIN:
            panel_silence_allowlisted_login_notification(
                hass, value, data.get(ATTR_NOTIFICATION_ID)
            )
        elif action == PANEL_ACTION_UNSILENCE_ALLOWLISTED_LOGIN:
            panel_unsilence_allowlisted_login_notification(hass, value)
        else:
            metric_increment(hass, "panel_api_errors")
            return view.json(
                {"ok": False, "error": "Unknown action."},
                status_code=400,
            )
    except (HomeAssistantError, ValueError) as err:
        metric_increment(hass, "panel_api_errors")
        return view.json({"ok": False, "error": str(err)}, status_code=400)

    if action in {"import_config", "upload_config"}:
        schedule_npm_sync(hass)
    await async_update_health_issue(hass)
    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    if entry is None:
        metric_increment(hass, "panel_api_errors")
        return view.json(
            {"ok": False, "error": "IP Ban Manager is not loaded."},
            status_code=404,
        )
    language = data.get("language") or request.query.get("language")
    payload = await async_panel_payload(hass, entry, language=language)
    if download is not None:
        payload["download"] = download
    return view.json(payload)


class SilenceAllowlistedLoginNotificationsView(HomeAssistantView):
    """Admin-only POST endpoint for allowlisted-login notification silence actions."""

    name = "api:ip_ban_manager:silence_allowlisted_login_notifications"
    url = ALLOWLISTED_LOGIN_SILENCE_URL
    requires_auth = True

    async def get(self, request: Request) -> Response:
        """Dispatch silence GET through the reloadable handler table."""
        return await async_dispatch_http_view(self, request, "silence_get")

    async def post(self, request: Request) -> Response:
        """Dispatch silence POST through the reloadable handler table."""
        return await async_dispatch_http_view(self, request, "silence_post")


class IPBanManagerPanelView(HomeAssistantView):
    """Serve the bundled panel script with the installed version injected."""

    name = "api:ip_ban_manager:panel_js"
    url = PANEL_JS_PATH
    requires_auth = False

    async def get(self, request: Request) -> Response:
        """Return panel.js with the manifest version baked into the header."""
        hass = request.app[KEY_HASS]
        await async_refresh_runtime_imports(hass)
        return await async_panel_js_response(hass)


class IPBanManagerStatusView(HomeAssistantView):
    """Return live IP Ban Manager state for the bundled panel."""

    name = "api:ip_ban_manager:status"
    url = f"/api/{DOMAIN}/status"

    async def get(self, request: Request) -> Response:
        """Dispatch status GET through the reloadable handler table."""
        return await async_dispatch_http_view(self, request, "status_get")


class IPBanManagerManageView(HomeAssistantView):
    """Apply live IP Ban Manager changes from the bundled panel."""

    name = "api:ip_ban_manager:manage"
    url = f"/api/{DOMAIN}/manage"

    async def post(self, request: Request) -> Response:
        """Dispatch manage POST through the reloadable handler table."""
        return await async_dispatch_http_view(self, request, "manage_post")


def integration_view_urls() -> set[str]:
    """Return the HTTP paths owned by IP Ban Manager."""
    return {
        url
        for url in (
            SilenceAllowlistedLoginNotificationsView.url,
            IPBanManagerPanelView.url,
            IPBanManagerStatusView.url,
            IPBanManagerManageView.url,
        )
        if url
    }


def registered_integration_view_urls(hass: HomeAssistant) -> set[str]:
    """Return integration view paths already registered on the HTTP router."""
    owned_urls = integration_view_urls()
    registered_urls = set()
    for route in hass.http.app.router.routes():
        resource = route.resource
        if resource is None:
            continue
        canonical = resource.canonical
        if canonical in owned_urls:
            registered_urls.add(canonical)
    return registered_urls


def http_route_registered(hass: HomeAssistant, url: str) -> bool:
    """Return whether a URL path already has an HTTP route."""
    for route in hass.http.app.router.routes():
        resource = route.resource
        if resource is not None and resource.canonical == url:
            return True
    return False


def install_http_view_handlers(hass: HomeAssistant) -> None:
    """Install the live HTTP handlers used by process-lifetime view routes."""
    hass.data[KEY_HTTP_VIEW_HANDLERS] = {
        "silence_get": async_handle_silence_get,
        "silence_post": async_handle_silence_post,
        "status_get": async_handle_status_get,
        "manage_post": async_handle_manage_post,
    }


async def async_refresh_runtime_imports(hass: HomeAssistant) -> None:
    """Refresh panel/status helpers after source files change on disk."""
    previous = hass.data.get(KEY_RUNTIME_MODULE_MTIMES)
    result = await hass.async_add_executor_job(
        refresh_runtime_imports_sync, previous if isinstance(previous, dict) else None
    )
    if result is None:
        return

    mtimes, bindings = result
    hass.data[KEY_RUNTIME_MODULE_MTIMES] = mtimes
    if not bindings:
        return

    globals().update(bindings)
    hass.data.pop(KEY_HTTP_VIEW_HANDLERS, None)
    install_http_view_handlers(hass)
    hass.http.app.pop(KEY_LOCAL_GEOIP_REGION_CACHE, None)
    _LOGGER.debug("Reloaded IP Ban Manager runtime modules after source update")


def refresh_runtime_imports_sync(
    previous: dict[str, float] | None,
) -> tuple[dict[str, float], dict[str, Callable[..., Any]]] | None:
    """Reload selected runtime modules when their source files changed."""
    modules = {
        module_name: importlib.import_module(module_name)
        for module_name in RUNTIME_MODULE_NAMES
    }
    mtimes = runtime_module_mtimes(modules)
    if mtimes == previous:
        return None

    importlib.invalidate_caches()
    for module_name in RUNTIME_MODULE_NAMES:
        modules[module_name] = importlib.reload(modules[module_name])

    return mtimes, {
        binding: getattr(modules[module_name], attribute)
        for binding, (module_name, attribute) in RUNTIME_BINDINGS.items()
    }


def runtime_module_mtimes(modules: dict[str, ModuleType]) -> dict[str, float]:
    """Return source-file mtimes for modules used by sticky HTTP views."""
    mtimes: dict[str, float] = {}
    for module_name, module in modules.items():
        module_file = getattr(module, "__file__", None)
        if module_file:
            mtimes[module_name] = Path(module_file).stat().st_mtime
    return mtimes


def register_http_views(hass: HomeAssistant) -> None:
    """Register HTTP API views once and bind reloadable handlers on each setup."""
    install_http_view_handlers(hass)
    setup_npm_sync(hass)
    if hass.data.get(KEY_HTTP_VIEWS):
        return

    views = (
        SilenceAllowlistedLoginNotificationsView(),
        IPBanManagerPanelView(),
        IPBanManagerStatusView(),
        IPBanManagerManageView(),
    )
    registered_urls = registered_integration_view_urls(hass)
    for view in views:
        if view.url in registered_urls:
            continue
        hass.http.register_view(view)
    hass.data[KEY_HTTP_VIEWS] = views


def unregister_http_views(hass: HomeAssistant) -> None:
    """Detach live handlers; sticky HA routes stay but refuse requests."""
    unload_npm_sync(hass)
    hass.data.pop(KEY_HTTP_VIEW_HANDLERS, None)
    hass.data.pop(KEY_HTTP_VIEWS, None)
