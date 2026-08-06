"""Allowlist, blocked-network, and default-deny policy operations."""

from __future__ import annotations

from homeassistant.components.http.ban import (
    KEY_BAN_MANAGER,
    KEY_LOGIN_THRESHOLD,
    NOTIFICATION_ID_BAN,
    NOTIFICATION_ID_LOGIN,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .audit import (
    record_allowlist_network_added,
    record_allowlist_network_removed,
    record_blocked_network_added,
    record_blocked_network_removed,
)
from .ban_lookup import (
    NetworkAwareBanLookup,
    _supervisor_internal_networks,
)
from .ban_ops import ban_manager
from .const import (
    ATTR_NETWORK,
    CONF_ALLOWLIST_ENTRY_META,
    CONF_BLOCKED_NETWORK_ENTRY_META,
    CONF_BLOCKED_NETWORKS,
    CONF_IP_ADDRESSES,
    DOMAIN,
    SOURCE_PANEL,
    SOURCE_SERVICE,
)
from .entry_helpers import (
    effective_login_threshold,
    entry_ban_notifications_enabled,
    entry_blocked_networks,
    entry_default_deny_enabled,
    entry_ip_addresses,
    native_ip_banning_enabled,
    parse_allowlist,
    parse_blocked_networks,
    update_entry_options,
)
from .entry_meta import (
    entry_allowlist_meta,
    entry_blocked_network_meta,
    sync_network_list_meta,
)
from .internal_networks import async_home_assistant_self_networks
from .ip_utils import parse_allowlist_network
from .storage_keys import (
    KEY_ALLOWLIST,
    KEY_BLOCKED_NETWORKS,
    KEY_CONFIG_ENTRY,
    KEY_DEFAULT_DENY,
    KEY_INTERNAL_BYPASS_NETWORKS,
)


async def async_update_internal_bypass_networks(hass: HomeAssistant) -> None:
    """Refresh exact Home Assistant self-addresses protected from managed rules."""
    networks = await async_home_assistant_self_networks(hass)
    hass.http.app[KEY_INTERNAL_BYPASS_NETWORKS] = networks

    try:
        lookup = hass.http.app[KEY_BAN_MANAGER].ip_bans_lookup
    except KeyError:
        return

    if isinstance(lookup, NetworkAwareBanLookup):
        lookup.internal_bypass_networks = networks


def dismiss_http_notifications(hass: HomeAssistant) -> None:
    """Dismiss Home Assistant HTTP ban/login notifications."""
    from homeassistant.components import persistent_notification

    persistent_notification.async_dismiss(hass, NOTIFICATION_ID_LOGIN)
    persistent_notification.async_dismiss(hass, NOTIFICATION_ID_BAN)


def apply_ban_settings(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply integration-owned ban settings to Home Assistant's live app."""
    if native_ip_banning_enabled(hass):
        hass.http.app[KEY_LOGIN_THRESHOLD] = effective_login_threshold(entry, hass)
    if not entry_ban_notifications_enabled(entry):
        dismiss_http_notifications(hass)


def apply_blocked_networks(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply blocked network settings to Home Assistant's live ban lookup."""
    blocked_networks = parse_blocked_networks(entry_blocked_networks(entry))
    default_deny_enabled = entry_default_deny_enabled(entry)
    allowlist = hass.http.app.get(KEY_ALLOWLIST, ())
    hass.http.app[KEY_BLOCKED_NETWORKS] = blocked_networks
    hass.http.app[KEY_DEFAULT_DENY] = default_deny_enabled

    if not native_ip_banning_enabled(hass):
        return

    ban_manager_ = hass.http.app[KEY_BAN_MANAGER]
    lookup = ban_manager_.ip_bans_lookup
    if isinstance(lookup, NetworkAwareBanLookup):
        lookup.blocked_networks = blocked_networks
        lookup.allowlist = allowlist
        lookup.default_deny_enabled = default_deny_enabled
        lookup.internal_bypass_networks = hass.http.app.get(
            KEY_INTERNAL_BYPASS_NETWORKS, _supervisor_internal_networks()
        )
        return

    ban_manager_.ip_bans_lookup = NetworkAwareBanLookup(
        dict(lookup),
        blocked_networks,
        allowlist,
        default_deny_enabled,
        hass.http.app.get(
            KEY_INTERNAL_BYPASS_NETWORKS, _supervisor_internal_networks()
        ),
    )


def update_allowlist_entry(
    hass: HomeAssistant,
    ip_addresses: list[str],
    *,
    meta_source: str | None = None,
) -> None:
    """Persist and apply the current allowlist without a Home Assistant restart."""
    entry = hass.http.app[KEY_CONFIG_ENTRY]
    allowlist_meta = entry_allowlist_meta(entry)
    if meta_source is None:
        allowlist_meta = {
            network: allowlist_meta[network]
            for network in ip_addresses
            if network in allowlist_meta
        }
    else:
        allowlist_meta = sync_network_list_meta(
            allowlist_meta, ip_addresses, source=meta_source
        )
    update_entry_options(
        hass,
        **{
            CONF_IP_ADDRESSES: ip_addresses,
            CONF_ALLOWLIST_ENTRY_META: allowlist_meta,
        },
    )
    hass.http.app[KEY_ALLOWLIST] = parse_allowlist(ip_addresses)
    apply_blocked_networks(hass, hass.http.app[KEY_CONFIG_ENTRY])


def update_blocked_networks_entry(
    hass: HomeAssistant,
    networks: list[str],
    *,
    meta_source: str | None = None,
) -> None:
    """Persist and apply blocked networks without a Home Assistant restart."""
    entry = hass.http.app[KEY_CONFIG_ENTRY]
    blocked_meta = entry_blocked_network_meta(entry)
    if meta_source is None:
        blocked_meta = {
            network: blocked_meta[network]
            for network in networks
            if network in blocked_meta
        }
    else:
        blocked_meta = sync_network_list_meta(
            blocked_meta, networks, source=meta_source
        )
    update_entry_options(
        hass,
        **{
            CONF_BLOCKED_NETWORKS: networks,
            CONF_BLOCKED_NETWORK_ENTRY_META: blocked_meta,
        },
    )
    apply_blocked_networks(hass, hass.http.app[KEY_CONFIG_ENTRY])


def current_allowlist_strings(hass: HomeAssistant) -> list[str]:
    """Return the persisted allowlist strings."""
    return entry_ip_addresses(hass.http.app[KEY_CONFIG_ENTRY])


def current_blocked_network_strings(hass: HomeAssistant) -> list[str]:
    """Return the persisted blocked network strings."""
    return entry_blocked_networks(hass.http.app[KEY_CONFIG_ENTRY])


async def async_validate_panel_network_safety(
    hass: HomeAssistant,
    allowlist: list[str],
    blocked_networks: list[str],
    default_deny_enabled: bool,
) -> None:
    """Validate panel network edits against detected local access paths."""
    await async_update_internal_bypass_networks(hass)

    from .config_flow import (
        UnprotectedLocalBlockError,
        _async_detect_home_assistant_subnets,
        _validate_local_block_safety,
    )

    try:
        _validate_local_block_safety(
            allowlist,
            blocked_networks,
            await _async_detect_home_assistant_subnets(hass),
            default_deny_enabled,
        )
    except UnprotectedLocalBlockError as err:
        raise HomeAssistantError(str(err)) from err


async def async_add_allowlist_network(
    hass: HomeAssistant, network_value: str, source: str = SOURCE_SERVICE
) -> None:
    """Add an allowlist network immediately."""
    try:
        network = parse_allowlist_network(network_value)
    except ValueError as err:
        if source == SOURCE_PANEL:
            raise HomeAssistantError("Invalid IP address or network.") from err
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_network",
            translation_placeholders={ATTR_NETWORK: network_value},
        ) from err

    if network.prefixlen == 0:
        if source == SOURCE_PANEL:
            raise HomeAssistantError(
                "Allowing every address belongs outside the allowlist."
            )
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unsafe_allowlist_network",
            translation_placeholders={ATTR_NETWORK: str(network)},
        )

    banned_ips = ban_manager(hass).ip_bans_lookup
    if any(banned_ip in network for banned_ip in banned_ips):
        message = (
            "An allowlist network cannot include an exact banned IP. "
            "Remove the ban first and try again."
        )
        if source == SOURCE_PANEL:
            raise HomeAssistantError(message)
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="network_contains_banned_ip",
            translation_placeholders={ATTR_NETWORK: str(network)},
        )

    current = current_allowlist_strings(hass)
    normalized_network = str(network)
    current_networks = {
        parse_allowlist_network(current_network) for current_network in current
    }
    if network in current_networks:
        return

    updated = [*current, normalized_network]
    try:
        await async_validate_panel_network_safety(
            hass,
            updated,
            current_blocked_network_strings(hass),
            bool(hass.http.app.get(KEY_DEFAULT_DENY, False)),
        )
    except HomeAssistantError as err:
        if source == SOURCE_PANEL:
            raise
        raise ServiceValidationError(str(err)) from err
    update_allowlist_entry(hass, updated, meta_source=source)
    record_allowlist_network_added(hass, normalized_network, source)


async def async_remove_allowlist_network(
    hass: HomeAssistant, network_value: str, source: str = SOURCE_SERVICE
) -> None:
    """Remove an allowlist network immediately."""
    try:
        network = parse_allowlist_network(network_value)
    except ValueError as err:
        if source == SOURCE_PANEL:
            raise HomeAssistantError("Invalid IP address or network.") from err
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_network",
            translation_placeholders={ATTR_NETWORK: network_value},
        ) from err

    current = current_allowlist_strings(hass)
    remaining_networks = [
        current_network
        for current_network in current
        if parse_allowlist_network(current_network) != network
    ]
    if len(remaining_networks) == len(current):
        return

    try:
        await async_validate_panel_network_safety(
            hass,
            remaining_networks,
            current_blocked_network_strings(hass),
            bool(hass.http.app.get(KEY_DEFAULT_DENY, False)),
        )
    except HomeAssistantError as err:
        if source == SOURCE_PANEL:
            raise
        raise ServiceValidationError(str(err)) from err
    update_allowlist_entry(hass, remaining_networks)
    record_allowlist_network_removed(hass, str(network), source)


async def async_add_blocked_network(
    hass: HomeAssistant, network_value: str, source: str = SOURCE_SERVICE
) -> None:
    """Add a managed blocked network immediately."""
    try:
        network = parse_allowlist_network(network_value)
    except ValueError as err:
        if source == SOURCE_PANEL:
            raise HomeAssistantError("Invalid IP address or network.") from err
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_network",
            translation_placeholders={ATTR_NETWORK: network_value},
        ) from err

    if network.prefixlen == 0:
        message = "Blocking every address belongs in default-deny mode."
        if source == SOURCE_PANEL:
            raise HomeAssistantError(message)
        raise ServiceValidationError(message)

    current = current_blocked_network_strings(hass)
    normalized_network = str(network)
    current_networks = {
        parse_allowlist_network(current_network) for current_network in current
    }
    if network in current_networks:
        return

    updated = [*current, normalized_network]
    try:
        await async_validate_panel_network_safety(
            hass,
            current_allowlist_strings(hass),
            updated,
            bool(hass.http.app.get(KEY_DEFAULT_DENY, False)),
        )
    except HomeAssistantError as err:
        if source == SOURCE_PANEL:
            raise
        raise ServiceValidationError(str(err)) from err
    update_blocked_networks_entry(hass, updated, meta_source=source)
    record_blocked_network_added(hass, normalized_network, source)


async def async_remove_blocked_network(
    hass: HomeAssistant, network_value: str, source: str = SOURCE_SERVICE
) -> None:
    """Remove a managed blocked network immediately."""
    try:
        network = parse_allowlist_network(network_value)
    except ValueError as err:
        if source == SOURCE_PANEL:
            raise HomeAssistantError("Invalid IP address or network.") from err
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_network",
            translation_placeholders={ATTR_NETWORK: network_value},
        ) from err

    current = current_blocked_network_strings(hass)
    remaining_networks = [
        current_network
        for current_network in current
        if parse_allowlist_network(current_network) != network
    ]
    if len(remaining_networks) == len(current):
        return

    update_blocked_networks_entry(hass, remaining_networks)
    record_blocked_network_removed(hass, str(network), source)
