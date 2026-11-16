"""Repair issues and health reporting for IP Ban Manager."""

from __future__ import annotations

import os
from pathlib import Path
from time import monotonic
from typing import cast

from homeassistant.components.http.ban import KEY_BAN_MANAGER
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import ATTR_HEALTH_ISSUES, DOMAIN, LEGACY_DOMAIN
from .entry_helpers import (
    entry_allowed_region_mode,
    entry_geoip_enabled,
    entry_regional_login_thresholds,
    native_ip_banning_enabled,
)
from .file_store import geoip_database_path, path_is_file
from .geoip import geoip_reader
from .i18n import async_load_health_issue_strings, format_health_issue_message
from .region_rules import entry_public_region_settings
from .storage_keys import (
    KEY_CONFIG_ENTRY,
    KEY_HEALTH,
    KEY_LEGACY_FOLDER_CLEANED,
    KEY_PANEL_REGISTERED,
    KEY_SETUP_IN_PROGRESS,
)

IP_BAN_DISABLED_ISSUE_ID = "ip_ban_disabled"
INTEGRATION_DISABLED_BY_YAML_ISSUE_ID = "integration_disabled_by_yaml"
LEGACY_YAML_PRESENT_ISSUE_ID = "legacy_yaml_present"
LEGACY_FOLDER_CLEANUP_FAILED_ISSUE_ID = "legacy_folder_cleanup_failed"
HEALTH_CHECK_FAILED_ISSUE_ID = "health_check_failed"
TRANSIENT_HEALTH_ISSUE_KEYS = frozenset(
    {
        "legacy_cleanup_pending",
        "geoip_reader_not_ready",
    }
)
HTTP_IP_BAN_DOCS_URL = (
    "https://www.home-assistant.io/integrations/http/#ip-filtering-and-banning"
)
_UNSET = object()
_HEALTH_GRACE_STATE = "ip_ban_manager_health_grace"
_HEALTH_LIFETIME = "ip_ban_manager_health_lifetime"
HEALTH_GRACE_SECONDS = 30


def clear_health_grace(hass: HomeAssistant, *, invalidate: bool = False) -> None:
    """Cancel the integration-owned deferred health check on unload."""
    state = hass.data.pop(_HEALTH_GRACE_STATE, None)
    if invalidate:
        hass.data.pop(_HEALTH_LIFETIME, None)
    if state is not None and (remove := state.get("remove")):
        remove()


def async_create_ip_ban_disabled_issue(hass: HomeAssistant) -> None:
    """Create a repair issue when Home Assistant IP banning is disabled."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        IP_BAN_DISABLED_ISSUE_ID,
        is_fixable=False,
        is_persistent=True,
        learn_more_url=HTTP_IP_BAN_DOCS_URL,
        severity=ir.IssueSeverity.WARNING,
        translation_key=IP_BAN_DISABLED_ISSUE_ID,
    )


def async_delete_ip_ban_disabled_issue(hass: HomeAssistant) -> None:
    """Delete the disabled-IP-ban repair issue when setup is healthy."""
    ir.async_delete_issue(hass, DOMAIN, IP_BAN_DISABLED_ISSUE_ID)


def async_update_emergency_disabled_issue(
    hass: HomeAssistant, emergency_disabled: bool
) -> None:
    """Create or clear the Repair for the emergency kill switch."""
    if emergency_disabled:
        ir.async_create_issue(
            hass,
            DOMAIN,
            INTEGRATION_DISABLED_BY_YAML_ISSUE_ID,
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=INTEGRATION_DISABLED_BY_YAML_ISSUE_ID,
        )
        return

    ir.async_delete_issue(hass, DOMAIN, INTEGRATION_DISABLED_BY_YAML_ISSUE_ID)


def async_update_legacy_yaml_issue(hass: HomeAssistant, config: ConfigType) -> None:
    """Create a repair when old YAML remains after migration."""
    if LEGACY_DOMAIN in config and hass.config_entries.async_entries(DOMAIN):
        ir.async_create_issue(
            hass,
            DOMAIN,
            LEGACY_YAML_PRESENT_ISSUE_ID,
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=LEGACY_YAML_PRESENT_ISSUE_ID,
        )
        return

    if LEGACY_DOMAIN not in config:
        ir.async_delete_issue(hass, DOMAIN, LEGACY_YAML_PRESENT_ISSUE_ID)


def async_update_legacy_folder_cleanup_issue(
    hass: HomeAssistant, failures: list[str]
) -> None:
    """Create or clear the repair for failed legacy folder cleanup."""
    if failures:
        ir.async_create_issue(
            hass,
            DOMAIN,
            LEGACY_FOLDER_CLEANUP_FAILED_ISSUE_ID,
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=LEGACY_FOLDER_CLEANUP_FAILED_ISSUE_ID,
            translation_placeholders={
                "paths": "\n".join(f"- `{path}`" for path in failures)
            },
        )
        return

    ir.async_delete_issue(hass, DOMAIN, LEGACY_FOLDER_CLEANUP_FAILED_ISSUE_ID)


def _ban_file_access_issue_for_path(path: Path) -> dict[str, object]:
    """Return a structured ip_bans.yaml access issue for a concrete path."""
    if path.exists():
        if not path.is_file():
            return {
                "key": "ban_file_not_regular_file",
                "placeholders": {"path": str(path)},
            }
        if not os.access(path, os.R_OK | os.W_OK):
            return {
                "key": "ban_file_not_readable_writable",
                "placeholders": {"path": str(path)},
            }
        return {}

    parent = path.parent
    if not parent.exists():
        return {"key": "ban_file_parent_missing", "placeholders": {"path": str(parent)}}
    if not os.access(parent, os.W_OK):
        return {
            "key": "ban_file_parent_not_writable",
            "placeholders": {"path": str(parent)},
        }
    return {}


def ban_file_access_issue(hass: HomeAssistant) -> dict[str, object] | None:
    """Return a structured ip_bans.yaml access issue, if one is visible."""
    ban_manager = hass.http.app.get(KEY_BAN_MANAGER)
    if ban_manager is None:
        return {"key": "ban_file_not_loaded"}

    return _ban_file_access_issue_for_path(Path(ban_manager.path)) or None


def health_status(
    hass: HomeAssistant,
    *,
    geoip_database_present: bool | None = None,
    ban_file_issue: dict[str, object] | None | object = _UNSET,
) -> dict[str, object]:
    """Return the latest lightweight integration health summary."""
    issues: list[dict[str, object]] = []

    if not native_ip_banning_enabled(hass):
        issues.append({"key": "native_ip_ban_disabled"})

    if ban_file_issue is _UNSET:
        ban_file_issue = ban_file_access_issue(hass)
    if ban_file_issue:
        issues.append(cast(dict[str, object], ban_file_issue))

    if not hass.data.get(KEY_PANEL_REGISTERED, False):
        issues.append({"key": "panel_not_registered"})

    if not hass.data.get(KEY_LEGACY_FOLDER_CLEANED, False):
        issues.append({"key": "legacy_cleanup_pending"})

    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    if entry is not None:
        database_present = (
            geoip_database_present
            if geoip_database_present is not None
            else geoip_database_path(hass).is_file()
        )
        region_enabled = (
            entry_public_region_settings(hass, entry)["public_region_enabled"]
            or entry_allowed_region_mode(entry) != "anywhere"
            or bool(entry_regional_login_thresholds(entry))
        )
        if (region_enabled and not entry_geoip_enabled(entry)) or (
            geoip_reader(hass) is None
            and (region_enabled or (entry_geoip_enabled(entry) and database_present))
        ):
            issues.append({"key": "geoip_reader_not_ready"})

    return {
        "ok": not issues,
        ATTR_HEALTH_ISSUES: issues,
        "checked_at": dt_util.utcnow().isoformat(),
    }


async def async_health_status(hass: HomeAssistant) -> dict[str, object]:
    """Return health status without blocking the event loop on file checks."""
    geoip_database_present = await hass.async_add_executor_job(
        path_is_file, geoip_database_path(hass)
    )
    ban_manager = hass.http.app.get(KEY_BAN_MANAGER)
    ban_file_issue: dict[str, object]
    if ban_manager is None:
        ban_file_issue = {"key": "ban_file_not_loaded"}
    else:
        ban_file_issue = await hass.async_add_executor_job(
            _ban_file_access_issue_for_path, Path(ban_manager.path)
        )
    return health_status(
        hass,
        geoip_database_present=geoip_database_present,
        ban_file_issue=ban_file_issue,
    )


async def async_update_health_issue(hass: HomeAssistant) -> None:
    """Refresh the lightweight health status and matching Repair issue."""
    entry = hass.http.app.get(KEY_CONFIG_ENTRY)
    lifetime = hass.data.setdefault(_HEALTH_LIFETIME, object())
    if entry is None or hass.data.get(KEY_SETUP_IN_PROGRESS):
        return
    health = await async_health_status(hass)
    if (
        hass.data.get(_HEALTH_LIFETIME) is not lifetime
        or hass.http.app.get(KEY_CONFIG_ENTRY) is not entry
        or hass.data.get(KEY_SETUP_IN_PROGRESS)
    ):
        return
    hass.data[KEY_HEALTH] = health
    issues = cast(list[dict[str, object]], health[ATTR_HEALTH_ISSUES])
    transient = any(issue["key"] in TRANSIENT_HEALTH_ISSUE_KEYS for issue in issues)
    state = hass.data.get(_HEALTH_GRACE_STATE)
    if transient and state is None:
        state = {"deadline": monotonic() + HEALTH_GRACE_SECONDS}
        hass.data[_HEALTH_GRACE_STATE] = state

        async def refresh(_now: object) -> None:
            if (
                state is None
                or hass.data.get(_HEALTH_GRACE_STATE) is not state
                or hass.data.get(_HEALTH_LIFETIME) is not lifetime
            ):
                return
            if hass.data.get(KEY_SETUP_IN_PROGRESS):
                cast(dict[str, object], state)["remove"] = async_call_later(
                    hass, 1, refresh
                )
                return
            if state is not None:
                state.pop("remove", None)
            await async_update_health_issue(hass)

        state["remove"] = async_call_later(hass, HEALTH_GRACE_SECONDS, refresh)
    elif not transient:
        clear_health_grace(hass)
        state = None
    actionable = [
        issue
        for issue in issues
        if cast(str, issue["key"]) not in TRANSIENT_HEALTH_ISSUE_KEYS
        or (state is not None and monotonic() >= state["deadline"])
    ]
    if actionable:
        issue_strings = await async_load_health_issue_strings(
            hass, hass.config.language
        )
        if (
            hass.data.get(_HEALTH_LIFETIME) is not lifetime
            or hass.http.app.get(KEY_CONFIG_ENTRY) is not entry
            or hass.data.get(KEY_SETUP_IN_PROGRESS)
        ):
            return
        ir.async_create_issue(
            hass,
            DOMAIN,
            HEALTH_CHECK_FAILED_ISSUE_ID,
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=HEALTH_CHECK_FAILED_ISSUE_ID,
            translation_placeholders={
                "issues": "\n".join(f"- {format_health_issue_message(
                        cast(str, issue['key']),
                        cast(dict[str, str], issue.get('placeholders')),
                        issue_strings,
                    )}" for issue in actionable)
            },
        )
        return

    ir.async_delete_issue(hass, DOMAIN, HEALTH_CHECK_FAILED_ISSUE_ID)
