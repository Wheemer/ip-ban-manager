"""Manual backup import/export helpers for IP Ban Manager."""

from __future__ import annotations

from datetime import datetime
from ipaddress import ip_address
from pathlib import Path

import yaml
from homeassistant.components.http.ban import (
    ATTR_BANNED_AT,
    KEY_BAN_MANAGER,
    KEY_FAILED_LOGIN_ATTEMPTS,
    IpBan,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .ban_ops import (
    async_rewrite_ip_bans_file,
    ban_manager,
    dismiss_ban_notification_for_ips,
    ip_ban_chronological_key,
    ip_ban_file_payload,
)
from .const import (
    ATTR_BANNED_IPS,
    CONF_ALLOWLIST_ENTRY_META,
    CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED,
    CONF_ALLOWLISTED_LOGINS_CAN_BAN,
    CONF_AUTO_BAN_ENABLED,
    CONF_BAN_NOTIFICATIONS_ENABLED,
    CONF_BLOCKED_NETWORK_ENTRY_META,
    CONF_BLOCKED_NETWORKS,
    CONF_DEFAULT_DENY_ENABLED,
    CONF_GEOIP_ENABLED,
    CONF_IP_ADDRESSES,
    CONF_LOGIN_ATTEMPTS_THRESHOLD,
    CONF_SIDEBAR_PANEL_ENABLED,
    CONF_SILENCED_ALLOWLISTED_LOGIN_IPS,
    DOMAIN,
    SOURCE_IMPORT,
)
from .entry_helpers import (
    entry_allowlisted_login_notifications_enabled,
    entry_allowlisted_logins_can_ban,
    entry_auto_ban_enabled,
    entry_ban_notifications_enabled,
    entry_blocked_networks,
    entry_default_deny_enabled,
    entry_geoip_enabled,
    entry_ip_addresses,
    entry_login_threshold,
    entry_sidebar_panel_enabled,
    normalize_login_attempts_threshold,
    parse_allowlist,
    update_entry_options,
)
from .entry_meta import (
    entry_allowlist_meta,
    entry_blocked_network_meta,
    merge_imported_meta,
)
from .file_store import (
    CONFIG_EXPORT_FILENAME,
    atomic_write_text,
    config_export_path,
    geoip_database_path,
)
from .geoip import async_prepare_geoip_reader, close_geoip_reader
from .network_policy import (
    apply_ban_settings,
    apply_blocked_networks,
    async_validate_panel_network_safety,
)
from .notifications import entry_silenced_allowlisted_login_ip_strings
from .panel import async_register_panel
from .storage_keys import KEY_ALLOWLIST, KEY_CONFIG_ENTRY

CONFIG_EXPORT_FORMAT_VERSION = 1


def config_export_payload(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, object]:
    """Return a stable manual export payload for IP Ban Manager."""
    manager = hass.http.app.get(KEY_BAN_MANAGER)
    return {
        "domain": DOMAIN,
        "format_version": CONFIG_EXPORT_FORMAT_VERSION,
        "exported_at": dt_util.utcnow().isoformat(),
        "settings": {
            CONF_IP_ADDRESSES: entry_ip_addresses(entry),
            CONF_BLOCKED_NETWORKS: entry_blocked_networks(entry),
            CONF_ALLOWLIST_ENTRY_META: entry_allowlist_meta(entry),
            CONF_BLOCKED_NETWORK_ENTRY_META: entry_blocked_network_meta(entry),
            CONF_AUTO_BAN_ENABLED: entry_auto_ban_enabled(entry),
            CONF_BAN_NOTIFICATIONS_ENABLED: entry_ban_notifications_enabled(entry),
            CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED: (
                entry_allowlisted_login_notifications_enabled(entry)
            ),
            CONF_ALLOWLISTED_LOGINS_CAN_BAN: entry_allowlisted_logins_can_ban(entry),
            CONF_DEFAULT_DENY_ENABLED: entry_default_deny_enabled(entry),
            CONF_GEOIP_ENABLED: entry_geoip_enabled(entry),
            CONF_LOGIN_ATTEMPTS_THRESHOLD: entry_login_threshold(entry, hass),
            CONF_SIDEBAR_PANEL_ENABLED: entry_sidebar_panel_enabled(entry),
            CONF_SILENCED_ALLOWLISTED_LOGIN_IPS: (
                entry_silenced_allowlisted_login_ip_strings(entry)
            ),
        },
        ATTR_BANNED_IPS: ip_ban_file_payload(manager) if manager else {},
    }


async def async_export_config(hass: HomeAssistant) -> Path:
    """Export IP Ban Manager settings to a readable integration-owned file."""
    entry = hass.http.app[KEY_CONFIG_ENTRY]
    payload = config_export_payload(hass, entry)
    export_path = config_export_path(hass)
    content = yaml.safe_dump(payload, sort_keys=False)
    await hass.async_add_executor_job(atomic_write_text, str(export_path), content)
    return export_path


async def async_import_config(hass: HomeAssistant) -> Path:
    """Import IP Ban Manager settings from the on-disk backup file."""
    export_path = config_export_path(hass)

    def _read() -> str:
        if not export_path.is_file():
            raise HomeAssistantError(f"Backup file not found: {export_path}")
        return export_path.read_text(encoding="utf8")

    content = await hass.async_add_executor_job(_read)
    await async_import_config_from_yaml(hass, content)
    return export_path


def config_download_payload(hass: HomeAssistant) -> dict[str, str]:
    """Return the current settings as a browser downloadable YAML backup."""
    entry = hass.http.app[KEY_CONFIG_ENTRY]
    return {
        "filename": CONFIG_EXPORT_FILENAME,
        "content": yaml.safe_dump(config_export_payload(hass, entry), sort_keys=False),
    }


def _bool_from_import(settings: dict[str, object], key: str, default: bool) -> bool:
    """Read a boolean import value without treating arbitrary strings as true."""
    if key not in settings:
        return default
    value = settings[key]
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lower_value = value.lower()
        if lower_value in {"true", "yes", "on", "1"}:
            return True
        if lower_value in {"false", "no", "off", "0"}:
            return False
    raise HomeAssistantError(f"Invalid boolean value for {key}.")


def _list_from_import(settings: dict[str, object], key: str) -> list[str] | None:
    """Read an optional string list from an import settings object."""
    if key not in settings:
        return None
    value = settings[key]
    if value is None:
        return []
    if isinstance(value, str):
        return [
            line.strip()
            for line in value.replace(",", "\n").splitlines()
            if line.strip()
        ]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise HomeAssistantError(f"Invalid list value for {key}.")


def _parse_import_banned_at(value: object) -> datetime:
    """Parse an exported ban timestamp."""
    if not isinstance(value, str):
        raise HomeAssistantError("Invalid banned_at value in backup file.")
    try:
        banned_at = datetime.fromisoformat(value)
    except ValueError as err:
        raise HomeAssistantError("Invalid banned_at value in backup file.") from err
    if banned_at.tzinfo is None:
        banned_at = dt_util.as_utc(banned_at)
    return banned_at


def _imported_bans_from_payload(payload: object) -> list[IpBan]:
    """Return timestamp-preserving exact bans from an import payload."""
    if payload in (None, {}):
        return []

    bans: list[IpBan] = []
    try:
        if isinstance(payload, dict):
            for raw_ip, raw_detail in payload.items():
                remote_addr = ip_address(str(raw_ip).strip())
                banned_at = dt_util.utcnow()
                if isinstance(raw_detail, dict) and ATTR_BANNED_AT in raw_detail:
                    banned_at = _parse_import_banned_at(raw_detail[ATTR_BANNED_AT])
                bans.append(IpBan(remote_addr, banned_at))
            return bans

        if isinstance(payload, list):
            for raw_ip in payload:
                bans.append(IpBan(ip_address(str(raw_ip).split(" - ", 1)[0].strip())))
            return bans
    except ValueError as err:
        raise HomeAssistantError("Invalid IP address in banned_ips section.") from err

    raise HomeAssistantError("Invalid banned_ips section in backup file.")


async def async_restore_exact_bans(hass: HomeAssistant, bans: list[IpBan]) -> None:
    """Replace Home Assistant's exact ban list while preserving ban timestamps."""
    manager = ban_manager(hass)
    existing_bans = manager.ip_bans_lookup
    removed_addrs = set(existing_bans) - {ban.ip_address for ban in bans}

    existing_bans.clear()
    existing_bans.update(
        {
            ip_ban.ip_address: ip_ban
            for ip_ban in sorted(bans, key=ip_ban_chronological_key)
        }
    )

    failed_attempts = hass.http.app[KEY_FAILED_LOGIN_ATTEMPTS]
    for remote_addr in removed_addrs | set(existing_bans):
        failed_attempts.pop(remote_addr, None)

    await async_rewrite_ip_bans_file(hass, manager)
    dismiss_ban_notification_for_ips(hass, removed_addrs)


async def async_import_config_from_yaml(hass: HomeAssistant, content: str) -> None:
    """Import IP Ban Manager settings from uploaded YAML backup content."""
    try:
        payload = yaml.safe_load(content) or {}
    except yaml.YAMLError as err:
        raise HomeAssistantError("Backup file is not valid YAML.") from err

    if not isinstance(payload, dict):
        raise HomeAssistantError("Backup file must contain a YAML mapping.")
    await async_apply_config_backup_payload(hass, payload)


async def async_apply_config_backup_payload(
    hass: HomeAssistant, payload: dict[str, object]
) -> None:
    """Validate and apply an IP Ban Manager backup payload."""
    if payload.get("domain") not in (None, DOMAIN):
        raise HomeAssistantError("Backup file is not for IP Ban Manager.")
    if payload.get("format_version", CONFIG_EXPORT_FORMAT_VERSION) != (
        CONFIG_EXPORT_FORMAT_VERSION
    ):
        raise HomeAssistantError("Unsupported IP Ban Manager backup format.")

    settings = payload.get("settings", {})
    if not isinstance(settings, dict):
        raise HomeAssistantError("Backup file settings must be a YAML mapping.")

    entry = hass.http.app[KEY_CONFIG_ENTRY]
    from .config_flow import (
        BannedAllowlistedIPError,
        UnsafeAllowlistError,
        UnsafeBlockedNetworkError,
        _validate_ban_safety,
        _validate_blocked_networks,
        _validate_ip_addresses,
    )

    imported_allowlist = _list_from_import(settings, CONF_IP_ADDRESSES)
    imported_blocked_networks = _list_from_import(settings, CONF_BLOCKED_NETWORKS)
    imported_allowlist_meta = settings.get(CONF_ALLOWLIST_ENTRY_META)
    imported_blocked_meta = settings.get(CONF_BLOCKED_NETWORK_ENTRY_META)
    if imported_allowlist_meta is not None and not isinstance(
        imported_allowlist_meta, dict
    ):
        raise HomeAssistantError(
            "Backup file allowlist metadata must be a YAML mapping."
        )
    if imported_blocked_meta is not None and not isinstance(
        imported_blocked_meta, dict
    ):
        raise HomeAssistantError(
            "Backup file blocked-network metadata must be a YAML mapping."
        )
    imported_silenced_ips = _list_from_import(
        settings, CONF_SILENCED_ALLOWLISTED_LOGIN_IPS
    )

    try:
        allowlist = (
            _validate_ip_addresses(imported_allowlist)
            if imported_allowlist is not None
            else entry_ip_addresses(entry)
        )
        blocked_networks = (
            _validate_blocked_networks(imported_blocked_networks)
            if imported_blocked_networks is not None
            else entry_blocked_networks(entry)
        )
    except UnsafeAllowlistError as err:
        raise HomeAssistantError(
            "Backup file allowlist cannot allow every address."
        ) from err
    except UnsafeBlockedNetworkError as err:
        raise HomeAssistantError(
            "Backup file blocked networks cannot block every address."
        ) from err
    except ValueError as err:
        raise HomeAssistantError(
            "Backup file contains an invalid allowlist or blocked network entry."
        ) from err
    try:
        silenced_ips = (
            [str(ip_address(value)) for value in imported_silenced_ips]
            if imported_silenced_ips is not None
            else entry_silenced_allowlisted_login_ip_strings(entry)
        )
    except ValueError as err:
        raise HomeAssistantError(
            "Invalid IP address in silenced allowlisted login notifications."
        ) from err

    auto_ban_enabled = _bool_from_import(
        settings, CONF_AUTO_BAN_ENABLED, entry_auto_ban_enabled(entry)
    )
    ban_notifications_enabled = _bool_from_import(
        settings,
        CONF_BAN_NOTIFICATIONS_ENABLED,
        entry_ban_notifications_enabled(entry),
    )
    allowlisted_login_notifications_enabled = _bool_from_import(
        settings,
        CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED,
        entry_allowlisted_login_notifications_enabled(entry),
    )
    allowlisted_logins_can_ban = _bool_from_import(
        settings,
        CONF_ALLOWLISTED_LOGINS_CAN_BAN,
        entry_allowlisted_logins_can_ban(entry),
    )
    default_deny_enabled = _bool_from_import(
        settings, CONF_DEFAULT_DENY_ENABLED, entry_default_deny_enabled(entry)
    )
    geoip_enabled = _bool_from_import(
        settings, CONF_GEOIP_ENABLED, entry_geoip_enabled(entry)
    )
    sidebar_panel_enabled = _bool_from_import(
        settings, CONF_SIDEBAR_PANEL_ENABLED, entry_sidebar_panel_enabled(entry)
    )
    try:
        login_attempts_threshold = normalize_login_attempts_threshold(
            settings.get(
                CONF_LOGIN_ATTEMPTS_THRESHOLD,
                entry_login_threshold(entry, hass),
            )
        )
    except (TypeError, ValueError) as err:
        raise HomeAssistantError(
            "Backup file login attempts threshold must be a number."
        ) from err

    imported_bans = (
        _imported_bans_from_payload(payload[ATTR_BANNED_IPS])
        if ATTR_BANNED_IPS in payload
        else None
    )
    if imported_bans is not None:
        try:
            _validate_ban_safety(
                allowlist, [str(ban.ip_address) for ban in imported_bans]
            )
        except BannedAllowlistedIPError as err:
            raise HomeAssistantError(
                "Backup file contains an IP that is both allowed and banned."
            ) from err
    await async_validate_panel_network_safety(
        hass, allowlist, blocked_networks, default_deny_enabled
    )

    if imported_allowlist is not None:
        allowlist_meta = merge_imported_meta(
            {},
            allowlist,
            imported_allowlist_meta,
            source=SOURCE_IMPORT,
        )
    else:
        allowlist_meta = {
            network: entry_allowlist_meta(entry)[network]
            for network in allowlist
            if network in entry_allowlist_meta(entry)
        }

    if imported_blocked_networks is not None:
        blocked_meta = merge_imported_meta(
            {},
            blocked_networks,
            imported_blocked_meta,
            source=SOURCE_IMPORT,
        )
    else:
        blocked_meta = {
            network: entry_blocked_network_meta(entry)[network]
            for network in blocked_networks
            if network in entry_blocked_network_meta(entry)
        }

    updated_entry = update_entry_options(
        hass,
        **{
            CONF_IP_ADDRESSES: allowlist,
            CONF_BLOCKED_NETWORKS: blocked_networks,
            CONF_ALLOWLIST_ENTRY_META: allowlist_meta,
            CONF_BLOCKED_NETWORK_ENTRY_META: blocked_meta,
            CONF_AUTO_BAN_ENABLED: auto_ban_enabled,
            CONF_BAN_NOTIFICATIONS_ENABLED: ban_notifications_enabled,
            CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED: (
                allowlisted_login_notifications_enabled
            ),
            CONF_ALLOWLISTED_LOGINS_CAN_BAN: allowlisted_logins_can_ban,
            CONF_DEFAULT_DENY_ENABLED: default_deny_enabled,
            CONF_GEOIP_ENABLED: geoip_enabled,
            CONF_LOGIN_ATTEMPTS_THRESHOLD: login_attempts_threshold,
            CONF_SIDEBAR_PANEL_ENABLED: sidebar_panel_enabled,
            CONF_SILENCED_ALLOWLISTED_LOGIN_IPS: silenced_ips,
        },
    )
    hass.http.app[KEY_ALLOWLIST] = parse_allowlist(allowlist)
    apply_ban_settings(hass, updated_entry)
    apply_blocked_networks(hass, updated_entry)
    if geoip_enabled and geoip_database_path(hass).is_file():
        await async_prepare_geoip_reader(hass)
    elif not geoip_enabled:
        close_geoip_reader(hass)
    await async_register_panel(hass, sidebar_enabled=sidebar_panel_enabled)
    if imported_bans is not None:
        await async_restore_exact_bans(hass, imported_bans)
