"""Config flow for the IP Ban Manager integration."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_interface
from typing import Any, cast

import voluptuous as vol
from homeassistant import config_entries, data_entry_flow
from homeassistant.components.http.ban import ATTR_BANNED_AT, KEY_LOGIN_THRESHOLD
from homeassistant.components.network import async_get_adapters
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util
from voluptuous.schema_builder import Optional as vol_optional

from .const import (
    ATTR_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED,
    ATTR_ALLOWLISTED_LOGINS_CAN_BAN,
    ATTR_AUTO_BAN_ENABLED,
    ATTR_BAN_NOTIFICATIONS_ENABLED,
    ATTR_BANNED_IPS,
    ATTR_BLOCKED_NETWORKS,
    ATTR_DEFAULT_DENY_ENABLED,
    ATTR_FAILED_LOGIN_ATTEMPTS,
    ATTR_GEOIP_ENABLED,
    ATTR_IP_ADDRESS,
    ATTR_LOGIN_ATTEMPTS_THRESHOLD,
    ATTR_NATIVE_IP_BAN_ENABLED,
    ATTR_NETWORKS,
    CONF_ALLOWED_IPS,
    CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED,
    CONF_ALLOWLISTED_LOGINS_CAN_BAN,
    CONF_AUTO_BAN_ENABLED,
    CONF_BAN_NOTIFICATIONS_ENABLED,
    CONF_BANNED_IPS,
    CONF_BLOCKED_NETWORKS,
    CONF_DEFAULT_DENY_ENABLED,
    CONF_GEOIP_ENABLED,
    CONF_IP_ADDRESSES,
    CONF_LEGACY_ENTRY_ID,
    CONF_LOGIN_ATTEMPTS_THRESHOLD,
    CONF_SIDEBAR_PANEL_ENABLED,
    DEFAULT_LOGIN_ATTEMPTS_THRESHOLD,
    DOMAIN,
    LEGACY_DOMAIN,
    MAX_LOGIN_ATTEMPTS_THRESHOLD,
)
from .ip_utils import normalize_allowlist_network, parse_allowlist_network

SECTION_ALLOWED_IPS = "allowed_ips"
SECTION_BANNED_IPS = "banned_ips"
DEFAULT_ALLOWED_IPS = ["127.0.0.1"]
CONF_QUICK_ALLOWLIST = "quick_allowlist"
CONF_BANNED_IPS_HELP = "banned_ips_help"
CONF_BLOCKED_NETWORKS_HELP = "blocked_networks_help"
CONF_ALLOWED_IPS_HELP = "allowed_ips_help"
CONF_BAN_OPTIONS = "ban_options"
CONF_ADVANCED_BAN_OPTIONS = "advanced_ban_options"
CONF_AUTO_BAN_CHECKBOX = "auto_ban"
CONF_BAN_NOTIFICATIONS_CHECKBOX = "ban_notifications"
CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_CHECKBOX = "allowlisted_login_notifications"
CONF_ALLOWLISTED_LOGINS_CAN_BAN_CHECKBOX = "allowlisted_logins_can_ban"
CONF_DEFAULT_DENY_CHECKBOX = "default_deny"
CONF_GEOIP_CHECKBOX = "geoip"
CONF_SIDEBAR_PANEL_CHECKBOX = "sidebar_panel"
CONF_CONFIRM_CLEAR_BANS = "confirm_clear_bans"
QUICK_ALLOW_LOCALHOST = "localhost"
QUICK_ALLOW_LOCAL_NETWORK = "local_network"

IPNetwork = IPv4Network | IPv6Network
SUPERVISOR_DOCKER_PARENT_NETWORK = IPv4Network("172.30.0.0/16")


class UnsafeAllowlistError(ValueError):
    """Raised when an allowlist entry would effectively disable IP bans."""


class BannedAllowlistedIPError(ValueError):
    """Raised when an IP is both allowlisted and banned."""


class UnsafeBlockedNetworkError(ValueError):
    """Raised when a blocked network would block every IP address."""


class UnprotectedLocalBlockError(ValueError):
    """Raised when a local access path has no matching allowed entry."""


def _normalize_list(value: str | Iterable[str]) -> list[str]:
    """Normalize multiline, comma-separated, or YAML-imported values."""
    raw_values: Iterable[str]
    if isinstance(value, str):
        raw_values = value.replace(",", "\n").splitlines()
    else:
        raw_values = value

    return [item.strip() for item in raw_values if item.strip()]


def _dedupe_items(items: Iterable[str]) -> list[str]:
    """Return items without duplicates while preserving input order."""
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _validate_ip_addresses(value: str | Iterable[str]) -> list[str]:
    """Validate and normalize configured IP addresses and networks."""
    ip_addresses = _dedupe_items(
        normalize_allowlist_network(address) for address in _normalize_list(value)
    )
    for address in ip_addresses:
        network = parse_allowlist_network(address)
        if network.prefixlen == 0:
            raise UnsafeAllowlistError

    return ip_addresses


def _validate_banned_ips(value: str | Iterable[str]) -> list[str]:
    """Validate and normalize configured banned IP addresses."""
    return _dedupe_items(
        str(ip_address(banned_ip.split(" - ", 1)[0].strip()))
        for banned_ip in _normalize_list(value)
    )


def _validate_blocked_networks(value: str | Iterable[str]) -> list[str]:
    """Validate and normalize configured blocked network entries."""
    blocked_networks: list[str] = []

    for raw_entry in _normalize_list(value):
        network = parse_allowlist_network(raw_entry)
        if network.prefixlen == 0:
            raise UnsafeBlockedNetworkError
        blocked_networks.append(str(network))

    return _dedupe_items(blocked_networks)


def _validate_ban_safety(
    allowlist: Iterable[str],
    banned_ips: Iterable[str],
) -> None:
    """Validate cross-list edits that could lock users out or hide mistakes."""
    allowlist_values = list(allowlist)

    allowlist_networks: list[IPNetwork] = [
        parse_allowlist_network(network) for network in allowlist_values
    ]
    banned_ip_values = [ip_address(banned_ip) for banned_ip in banned_ips]

    if any(
        banned_ip in allowlist_network
        for banned_ip in banned_ip_values
        for allowlist_network in allowlist_networks
    ):
        raise BannedAllowlistedIPError


def _validate_local_block_safety(
    allowlist: Iterable[str],
    blocked_networks: Iterable[str],
    detected_subnets: Iterable[str],
    default_deny_enabled: bool = False,
) -> None:
    """Reject local network blocks that have no local allowlist path back in."""
    allowlist_networks = [parse_allowlist_network(network) for network in allowlist]
    blocked = [parse_allowlist_network(network) for network in blocked_networks]
    detected = [
        network
        for network in (
            parse_allowlist_network(network) for network in detected_subnets
        )
        if not _is_supervisor_internal_network(network)
    ]

    def _covers_detected_local_network(
        allowed_network: IPNetwork, detected_network: IPNetwork
    ) -> bool:
        """Return whether an allowlist network keeps a detected local network open."""
        if isinstance(allowed_network, IPv4Network) and isinstance(
            detected_network, IPv4Network
        ):
            return detected_network.subnet_of(allowed_network)
        if isinstance(allowed_network, IPv6Network) and isinstance(
            detected_network, IPv6Network
        ):
            return detected_network.subnet_of(allowed_network)
        return False

    local_network_is_allowed = False
    for detected_network in detected:
        detected_network_is_allowed = any(
            _covers_detected_local_network(allowed_network, detected_network)
            for allowed_network in allowlist_networks
        )
        local_network_is_allowed = (
            local_network_is_allowed or detected_network_is_allowed
        )

        for blocked_network in blocked:
            if blocked_network.version != detected_network.version:
                continue
            if not blocked_network.overlaps(detected_network):
                continue
            if detected_network_is_allowed:
                continue
            raise UnprotectedLocalBlockError(
                "A blocked network overlaps a detected local access path. "
                "Add that local network to Allowed IPs first, or narrow the "
                "blocked network."
            )

    if default_deny_enabled and detected and not local_network_is_allowed:
        raise UnprotectedLocalBlockError(
            "Block everything outside Allowed IPs would block every detected "
            "local access path. Add one current local network to Allowed IPs "
            "before enabling it."
        )


def _is_supervisor_internal_network(network: IPNetwork) -> bool:
    """Return whether a detected subnet is Home Assistant's internal Supervisor LAN."""
    return isinstance(network, IPv4Network) and network.subnet_of(
        SUPERVISOR_DOCKER_PARENT_NETWORK
    )


def _items_to_text(items: Iterable[str]) -> str:
    """Convert stored items to the multiline UI representation."""
    text = "\n".join(items)
    return f"{text}\n" if text else ""


def _format_banned_ip_details(banned_ips: list[dict[str, str]]) -> str:
    """Return a readable banned-IP detail list."""
    if not banned_ips:
        return "None"

    return "\n".join(
        f"{ban[ATTR_IP_ADDRESS]} - {_format_banned_at(ban[ATTR_BANNED_AT])}"
        for ban in banned_ips
    )


def _format_banned_at(banned_at: str) -> str:
    """Return a friendly local timestamp for the options UI."""
    try:
        parsed_banned_at = datetime.fromisoformat(banned_at)
    except ValueError:
        return banned_at

    if parsed_banned_at.tzinfo is None:
        parsed_banned_at = dt_util.as_utc(parsed_banned_at)

    local_banned_at = dt_util.as_local(parsed_banned_at)
    return local_banned_at.strftime("%Y-%m-%d %H:%M")


def _text_selector() -> selector.TextSelector:
    """Return a multiline text selector."""
    return selector.TextSelector(
        selector.TextSelectorConfig(
            multiline=True,
            type=selector.TextSelectorType.TEXT,
        )
    )


def _allowed_ips_help_text() -> str:
    """Return static guidance for the allowed entries textarea."""
    return (
        "Trusted IPv4/IPv6 addresses and networks that should never be "
        "banned. Use one entry per line. CIDR networks and IPv4 wildcards "
        "like 192.168.1.* are supported."
    )


def _allowed_ips_help_selector() -> selector.ConstantSelector:
    """Return static guidance for the allowed entries textarea."""
    return selector.ConstantSelector(
        selector.ConstantSelectorConfig(value=_allowed_ips_help_text())
    )


def _banned_ips_help_text() -> str:
    """Return static guidance for the banned entries textarea."""
    return (
        "Currently blocked exact IPv4/IPv6 addresses. Existing rows show the "
        "block time for review; new rows can be just an IP. Leave this empty "
        "to clear all blocked IPs."
    )


def _banned_ips_help_selector() -> selector.ConstantSelector:
    """Return static guidance for the banned entries textarea."""
    return selector.ConstantSelector(
        selector.ConstantSelectorConfig(value=_banned_ips_help_text())
    )


def _blocked_networks_help_text() -> str:
    """Return static guidance for the blocked network textarea."""
    return (
        "IPv4/IPv6 CIDR networks or IPv4 wildcard networks to block without "
        "writing them to Home Assistant's ip_bans.yaml. Allowed entries still "
        "win."
    )


def _blocked_networks_help_selector() -> selector.ConstantSelector:
    """Return static guidance for the blocked network textarea."""
    return selector.ConstantSelector(
        selector.ConstantSelectorConfig(value=_blocked_networks_help_text())
    )


def _ban_option_values(
    auto_ban_enabled: bool,
    notifications_enabled: bool,
    allowlisted_login_notifications_enabled: bool,
    sidebar_panel_enabled: bool = True,
    geoip_enabled: bool = False,
) -> list[str]:
    """Return selected standard option values."""
    values: list[str] = []
    if auto_ban_enabled:
        values.append(CONF_AUTO_BAN_CHECKBOX)
    if notifications_enabled:
        values.append(CONF_BAN_NOTIFICATIONS_CHECKBOX)
    if allowlisted_login_notifications_enabled:
        values.append(CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_CHECKBOX)
    if sidebar_panel_enabled:
        values.append(CONF_SIDEBAR_PANEL_CHECKBOX)
    if geoip_enabled:
        values.append(CONF_GEOIP_CHECKBOX)
    return values


def _advanced_ban_option_values(
    allowlisted_logins_can_ban: bool,
    default_deny_enabled: bool,
) -> list[str]:
    """Return selected advanced ban option values."""
    values: list[str] = []
    if allowlisted_logins_can_ban:
        values.append(CONF_ALLOWLISTED_LOGINS_CAN_BAN_CHECKBOX)
    if default_deny_enabled:
        values.append(CONF_DEFAULT_DENY_CHECKBOX)
    return values


def _ban_options_selector() -> selector.SelectSelector:
    """Return the compact standard option checkbox group."""
    options: list[selector.SelectOptionDict] = [
        {
            "value": CONF_AUTO_BAN_CHECKBOX,
            "label": "Automatic bans - block failed login sources",
        },
        {
            "value": CONF_BAN_NOTIFICATIONS_CHECKBOX,
            "label": "Automatic ban notifications - show alerts when IPs are blocked",
        },
        {
            "value": CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_CHECKBOX,
            "label": "Allowlisted login notifications - alert on failed trusted logins",
        },
        {
            "value": CONF_SIDEBAR_PANEL_CHECKBOX,
            "label": "Show in sidebar - add the left menu page",
        },
        {
            "value": CONF_GEOIP_CHECKBOX,
            "label": "GeoIP location labels - download a local DB-IP database",
        },
    ]
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            multiple=True,
            mode=selector.SelectSelectorMode.LIST,
        )
    )


def _advanced_ban_options_selector() -> selector.SelectSelector:
    """Return the advanced option checkbox group."""
    options: list[selector.SelectOptionDict] = [
        {
            "value": CONF_ALLOWLISTED_LOGINS_CAN_BAN_CHECKBOX,
            "label": "Advanced: Bans inside Allowed IPs - trusted IPs can be blocked",
        },
        {
            "value": CONF_DEFAULT_DENY_CHECKBOX,
            "label": "Advanced: Block everything outside Allowed IPs - be careful",
        },
    ]
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            multiple=True,
            mode=selector.SelectSelectorMode.LIST,
        )
    )


def _initial_ban_options_selector() -> selector.SelectSelector:
    """Return the first-run automatic-ban checkbox group."""
    options: list[selector.SelectOptionDict] = [
        {
            "value": CONF_AUTO_BAN_CHECKBOX,
            "label": "Automatic bans - block failed login sources",
        }
    ]
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            multiple=True,
            mode=selector.SelectSelectorMode.LIST,
        )
    )


def _login_attempts_threshold_selector() -> selector.NumberSelector:
    """Return the login-attempt threshold selector."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0,
            max=100,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _ban_option_enabled(
    user_input: dict[str, Any],
    legacy_key: str,
    checkbox: str,
    default: bool,
) -> bool:
    """Return whether a ban option is selected from new or legacy form data."""
    if CONF_BAN_OPTIONS in user_input:
        standard_options = cast(list[str], user_input.get(CONF_BAN_OPTIONS, []))
        advanced_options = cast(
            list[str], user_input.get(CONF_ADVANCED_BAN_OPTIONS, [])
        )
        return checkbox in standard_options or checkbox in advanced_options
    return checkbox in cast(
        list[str], user_input.get(legacy_key, [checkbox] if default else [])
    )


def _current_login_threshold(hass: HomeAssistant) -> int:
    """Return Home Assistant's current live login-attempt threshold."""
    if hass.http is None:
        return DEFAULT_LOGIN_ATTEMPTS_THRESHOLD
    return _normalize_login_attempts_threshold(
        hass.http.app.get(KEY_LOGIN_THRESHOLD, DEFAULT_LOGIN_ATTEMPTS_THRESHOLD)
    )


def _normalize_login_attempts_threshold(value: Any) -> int:
    """Return a login-attempt threshold inside the supported backend range."""
    return min(MAX_LOGIN_ATTEMPTS_THRESHOLD, max(0, int(value)))


def _ban_settings_fields(
    auto_ban_enabled: bool,
    threshold: int,
    notifications_enabled: bool = True,
    allowlisted_login_notifications_enabled: bool = True,
    allowlisted_logins_can_ban: bool = False,
    default_deny_enabled: bool = False,
    sidebar_panel_enabled: bool = True,
    geoip_enabled: bool = False,
) -> dict[Any, Any]:
    """Return the auto-ban settings fields."""
    return {
        vol_optional(
            CONF_BAN_OPTIONS,
            default=_ban_option_values(
                auto_ban_enabled,
                notifications_enabled,
                allowlisted_login_notifications_enabled,
                sidebar_panel_enabled,
                geoip_enabled,
            ),
        ): _ban_options_selector(),
        vol_optional(
            CONF_ADVANCED_BAN_OPTIONS,
            default=_advanced_ban_option_values(
                allowlisted_logins_can_ban,
                default_deny_enabled,
            ),
        ): _advanced_ban_options_selector(),
        vol.Required(
            CONF_LOGIN_ATTEMPTS_THRESHOLD,
            default=threshold,
        ): _login_attempts_threshold_selector(),
    }


def _ban_settings_schema(
    auto_ban_enabled: bool,
    threshold: int,
    notifications_enabled: bool = True,
    allowlisted_login_notifications_enabled: bool = True,
    allowlisted_logins_can_ban: bool = False,
    default_deny_enabled: bool = False,
    sidebar_panel_enabled: bool = True,
    geoip_enabled: bool = False,
) -> vol.Schema:
    """Return the auto-ban settings schema."""
    return vol.Schema(
        _ban_settings_fields(
            auto_ban_enabled,
            threshold,
            notifications_enabled,
            allowlisted_login_notifications_enabled,
            allowlisted_logins_can_ban,
            default_deny_enabled,
            sidebar_panel_enabled,
            geoip_enabled,
        )
    )


def _local_network_option_label(detected_subnets: list[str]) -> str:
    """Return a readable dynamic label for the local-network checkbox."""
    if len(detected_subnets) == 1:
        return f"Local network {detected_subnets[0]}"

    return f"Local networks {', '.join(detected_subnets)}"


def _initial_setup_schema(detected_subnets: list[str], threshold: int) -> vol.Schema:
    """Return the first-run setup schema."""
    fields: dict[Any, Any] = {
        vol_optional(
            CONF_BAN_OPTIONS,
            default=[CONF_AUTO_BAN_CHECKBOX],
        ): _initial_ban_options_selector(),
        vol.Required(
            CONF_LOGIN_ATTEMPTS_THRESHOLD,
            default=threshold,
        ): _login_attempts_threshold_selector(),
    }
    default_quick_allowlist = [
        QUICK_ALLOW_LOCALHOST,
        *([QUICK_ALLOW_LOCAL_NETWORK] if detected_subnets else []),
    ]
    fields[
        vol_optional(
            CONF_QUICK_ALLOWLIST,
            default=default_quick_allowlist,
        )
    ] = _quick_allowlist_selector(
        [
            QUICK_ALLOW_LOCALHOST,
            *([QUICK_ALLOW_LOCAL_NETWORK] if detected_subnets else []),
        ],
        detected_subnets,
    )
    return vol.Schema(fields)


def _allowlist_management_schema(
    current_addresses: list[str], detected_subnets: list[str]
) -> vol.Schema:
    """Return the compact allowlist management schema."""
    quick_options = _available_quick_allowlist_options(detected_subnets)
    fields: dict[Any, Any] = {}
    if quick_options:
        fields[
            vol_optional(
                CONF_QUICK_ALLOWLIST,
                default=_current_quick_allowlist_options(
                    current_addresses, detected_subnets
                ),
            )
        ] = _quick_allowlist_selector(quick_options, detected_subnets)
    fields[
        vol_optional(
            CONF_ALLOWED_IPS_HELP,
            default=_allowed_ips_help_text(),
        )
    ] = _allowed_ips_help_selector()
    fields[
        vol.Required(
            CONF_ALLOWED_IPS,
            default=_items_to_text(current_addresses),
        )
    ] = _text_selector()

    return vol.Schema(fields)


def _quick_allowlist_selector(
    quick_options: list[str], detected_subnets: list[str]
) -> selector.SelectSelector:
    """Return a compact checkbox list for common allowlist entries."""
    options: list[selector.SelectOptionDict] = []
    if QUICK_ALLOW_LOCALHOST in quick_options:
        options.append(
            {
                "value": QUICK_ALLOW_LOCALHOST,
                "label": "Localhost 127.0.0.1",
            }
        )
    if QUICK_ALLOW_LOCAL_NETWORK in quick_options:
        options.append(
            {
                "value": QUICK_ALLOW_LOCAL_NETWORK,
                "label": _local_network_option_label(detected_subnets),
            }
        )

    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            multiple=True,
            mode=selector.SelectSelectorMode.LIST,
        )
    )


def _available_quick_allowlist_options(detected_subnets: list[str]) -> list[str]:
    """Return common allowlist checkbox options available for this host."""
    return [
        QUICK_ALLOW_LOCALHOST,
        *([QUICK_ALLOW_LOCAL_NETWORK] if detected_subnets else []),
    ]


def _current_quick_allowlist_options(
    current_addresses: list[str], detected_subnets: list[str]
) -> list[str]:
    """Return common allowlist checkbox options already active."""
    current = set(current_addresses)
    selected: list[str] = []
    if DEFAULT_ALLOWED_IPS[0] in current:
        selected.append(QUICK_ALLOW_LOCALHOST)
    if detected_subnets and any(subnet in current for subnet in detected_subnets):
        selected.append(QUICK_ALLOW_LOCAL_NETWORK)
    return selected


def _apply_quick_allowlist_options(
    ip_addresses: list[str], quick_input: list[str], detected_subnets: list[str]
) -> list[str]:
    """Sync convenience allowlist entries with the checkbox state."""
    quick_managed_entries = {*DEFAULT_ALLOWED_IPS, *detected_subnets}
    updated = [ip for ip in ip_addresses if ip not in quick_managed_entries]

    if QUICK_ALLOW_LOCALHOST in quick_input:
        updated.extend(DEFAULT_ALLOWED_IPS)
    if QUICK_ALLOW_LOCAL_NETWORK in quick_input:
        updated.extend(detected_subnets)

    return _dedupe_items(updated)


async def _async_detect_home_assistant_subnets(hass: HomeAssistant) -> list[str]:
    """Detect useful local networks from Home Assistant's enabled adapters."""
    adapters = await async_get_adapters(hass)
    enabled_adapters = [adapter for adapter in adapters if adapter["enabled"]]
    default_adapters = [
        adapter
        for adapter in enabled_adapters
        if adapter["default"] and (adapter["ipv4"] or adapter["ipv6"])
    ]
    candidate_adapters = default_adapters or enabled_adapters
    networks: list[str] = []
    seen: set[str] = set()

    for adapter in candidate_adapters:
        for address in (*adapter["ipv4"], *adapter["ipv6"]):
            interface = ip_interface(
                f"{address['address']}/{address['network_prefix']}"
            )
            network = interface.network
            if (
                network.is_loopback
                or (network.is_link_local and not isinstance(network, IPv6Network))
                or network.is_multicast
                or network.is_unspecified
                or _is_supervisor_internal_network(network)
            ):
                continue

            normalized = str(network)
            if normalized not in seen:
                seen.add(normalized)
                networks.append(normalized)

    return networks


def _current_addresses(config_entry: config_entries.ConfigEntry) -> list[str]:
    """Return the current allowlist strings from data or options."""
    return config_entry.options.get(
        CONF_ALLOWED_IPS,
        config_entry.options.get(
            CONF_IP_ADDRESSES,
            config_entry.data.get(CONF_IP_ADDRESSES, []),
        ),
    )


def _legacy_entry_data(config_entry: config_entries.ConfigEntry) -> dict[str, Any]:
    """Return a new-domain config payload from a legacy config entry."""
    data = dict(config_entry.data)
    data[CONF_IP_ADDRESSES] = _validate_ip_addresses(_current_addresses(config_entry))
    data[CONF_LEGACY_ENTRY_ID] = config_entry.entry_id
    return data


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for IP Ban Manager."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        if user_input is None and self.hass.config_entries.async_entries(DOMAIN):
            return self.async_abort(reason="already_configured")

        if user_input is None:
            legacy_entry = next(
                iter(self.hass.config_entries.async_entries(LEGACY_DOMAIN)),
                None,
            )
            if legacy_entry is not None:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="IP Ban Manager",
                    data=_legacy_entry_data(legacy_entry),
                )

        detected_subnets = await _async_detect_home_assistant_subnets(self.hass)

        if user_input is not None:
            errors: dict[str, str] = {}
            ip_addresses = []
            quick_input = cast(list[str], user_input.get(CONF_QUICK_ALLOWLIST, []))
            if QUICK_ALLOW_LOCALHOST in quick_input:
                ip_addresses.extend(DEFAULT_ALLOWED_IPS)
            if QUICK_ALLOW_LOCAL_NETWORK in quick_input:
                ip_addresses.extend(detected_subnets)
            ip_addresses = _dedupe_items(ip_addresses)
            default_deny_enabled = False
            try:
                _validate_local_block_safety(
                    ip_addresses,
                    [],
                    detected_subnets,
                    default_deny_enabled,
                )
            except UnprotectedLocalBlockError:
                errors[CONF_QUICK_ALLOWLIST] = "local_network_block_unprotected"

            if errors:
                return self.async_show_form(
                    step_id="user",
                    data_schema=_initial_setup_schema(
                        detected_subnets, _current_login_threshold(self.hass)
                    ),
                    description_placeholders={
                        "home_assistant_subnets": _items_to_text(detected_subnets)
                        or "None"
                    },
                    errors=errors,
                )

            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="IP Ban Manager",
                data={
                    CONF_IP_ADDRESSES: ip_addresses,
                    CONF_AUTO_BAN_ENABLED: _ban_option_enabled(
                        user_input,
                        CONF_AUTO_BAN_ENABLED,
                        CONF_AUTO_BAN_CHECKBOX,
                        True,
                    ),
                    CONF_BAN_NOTIFICATIONS_ENABLED: True,
                    CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED: True,
                    CONF_ALLOWLISTED_LOGINS_CAN_BAN: False,
                    CONF_DEFAULT_DENY_ENABLED: default_deny_enabled,
                    CONF_LOGIN_ATTEMPTS_THRESHOLD: _normalize_login_attempts_threshold(
                        user_input.get(
                            CONF_LOGIN_ATTEMPTS_THRESHOLD,
                            _current_login_threshold(self.hass),
                        )
                    ),
                },
            )

        return self.async_show_form(
            step_id="user",
            data_schema=_initial_setup_schema(
                detected_subnets, _current_login_threshold(self.hass)
            ),
            description_placeholders={
                "home_assistant_subnets": _items_to_text(detected_subnets) or "None"
            },
        )

    async def async_step_import(
        self, user_input: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Import YAML configuration."""
        try:
            ip_addresses = _validate_ip_addresses(user_input[CONF_IP_ADDRESSES])
        except UnsafeAllowlistError:
            return self.async_abort(reason="unsafe_allowlist_network")
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title="IP Ban Manager",
            data={CONF_IP_ADDRESSES: ip_addresses},
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return OptionsFlow(config_entry)


class OptionsFlow(config_entries.OptionsFlow):
    """Handle live ban and allowlist management."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self._detected_subnets: list[str] = []
        self._pending_clear_bans: dict[str, Any] | None = None

    def _management_schema(self) -> vol.Schema:
        """Return the live management form schema."""
        from . import current_status

        status = current_status(self.hass)
        banned_ips = [
            f"{ban[ATTR_IP_ADDRESS]} - {_format_banned_at(ban[ATTR_BANNED_AT])}"
            for ban in cast(list[dict[str, str]], status[ATTR_BANNED_IPS])
        ]
        blocked_networks = cast(list[str], status[ATTR_BLOCKED_NETWORKS])
        current_addresses = _current_addresses(self._config_entry)
        return vol.Schema(
            {
                vol.Required(
                    SECTION_ALLOWED_IPS,
                ): data_entry_flow.section(
                    _allowlist_management_schema(
                        current_addresses, self._detected_subnets
                    ),
                    {"collapsed": True},
                ),
                vol.Required(
                    SECTION_BANNED_IPS,
                ): data_entry_flow.section(
                    vol.Schema(
                        {
                            **_ban_settings_fields(
                                bool(status[ATTR_AUTO_BAN_ENABLED]),
                                cast(int, status[ATTR_LOGIN_ATTEMPTS_THRESHOLD]),
                                bool(status[ATTR_BAN_NOTIFICATIONS_ENABLED]),
                                bool(
                                    status[ATTR_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED]
                                ),
                                bool(status[ATTR_ALLOWLISTED_LOGINS_CAN_BAN]),
                                bool(status[ATTR_DEFAULT_DENY_ENABLED]),
                                bool(
                                    self._config_entry.options.get(
                                        CONF_SIDEBAR_PANEL_ENABLED,
                                        self._config_entry.data.get(
                                            CONF_SIDEBAR_PANEL_ENABLED, True
                                        ),
                                    )
                                ),
                                bool(status[ATTR_GEOIP_ENABLED]),
                            ),
                            vol_optional(
                                CONF_BANNED_IPS_HELP,
                                default=_banned_ips_help_text(),
                            ): _banned_ips_help_selector(),
                            vol_optional(
                                CONF_BANNED_IPS,
                                default=_items_to_text(banned_ips),
                            ): _text_selector(),
                            vol_optional(
                                CONF_BLOCKED_NETWORKS_HELP,
                                default=_blocked_networks_help_text(),
                            ): _blocked_networks_help_selector(),
                            vol_optional(
                                CONF_BLOCKED_NETWORKS,
                                default=_items_to_text(blocked_networks),
                            ): _text_selector(),
                        }
                    ),
                    {"collapsed": True},
                ),
            }
        )

    def _confirm_clear_bans_schema(self) -> vol.Schema:
        """Return the confirmation schema for clearing every exact IP ban."""
        return vol.Schema(
            {
                vol.Required(
                    CONF_CONFIRM_CLEAR_BANS,
                    default=False,
                ): selector.BooleanSelector()
            }
        )

    async def _async_save_management_changes(
        self,
        ip_addresses: list[str],
        banned_ips: list[str],
        blocked_networks: list[str],
        auto_ban_enabled: bool,
        ban_notifications_enabled: bool,
        allowlisted_login_notifications_enabled: bool,
        allowlisted_logins_can_ban: bool,
        default_deny_enabled: bool,
        sidebar_panel_enabled: bool,
        geoip_enabled: bool,
        login_attempts_threshold: int,
    ) -> config_entries.ConfigFlowResult:
        """Persist validated options and apply them immediately."""
        from . import (
            _apply_ban_settings,
            _async_download_geoip_database,
            _async_register_panel,
            _async_replace_ip_bans,
            _geoip_database_path,
            _update_allowlist_entry,
            _update_blocked_networks_entry,
        )

        if geoip_enabled and not _geoip_database_path(self.hass).is_file():
            await _async_download_geoip_database(self.hass)
        self.hass.config_entries.async_update_entry(
            self._config_entry,
            options={
                **self._config_entry.options,
                CONF_AUTO_BAN_ENABLED: auto_ban_enabled,
                CONF_BAN_NOTIFICATIONS_ENABLED: ban_notifications_enabled,
                CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED: (
                    allowlisted_login_notifications_enabled
                ),
                CONF_ALLOWLISTED_LOGINS_CAN_BAN: allowlisted_logins_can_ban,
                CONF_DEFAULT_DENY_ENABLED: default_deny_enabled,
                CONF_SIDEBAR_PANEL_ENABLED: sidebar_panel_enabled,
                CONF_GEOIP_ENABLED: geoip_enabled,
                CONF_LOGIN_ATTEMPTS_THRESHOLD: login_attempts_threshold,
                CONF_BLOCKED_NETWORKS: blocked_networks,
            },
        )
        _apply_ban_settings(self.hass, self._config_entry)
        await _async_register_panel(self.hass, sidebar_enabled=sidebar_panel_enabled)
        _update_allowlist_entry(self.hass, ip_addresses)
        _update_blocked_networks_entry(self.hass, blocked_networks)
        await _async_replace_ip_bans(self.hass, banned_ips)
        self._pending_clear_bans = None
        return self.async_create_entry(
            title="",
            data={
                CONF_IP_ADDRESSES: ip_addresses,
                CONF_AUTO_BAN_ENABLED: auto_ban_enabled,
                CONF_BAN_NOTIFICATIONS_ENABLED: ban_notifications_enabled,
                CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED: (
                    allowlisted_login_notifications_enabled
                ),
                CONF_ALLOWLISTED_LOGINS_CAN_BAN: allowlisted_logins_can_ban,
                CONF_DEFAULT_DENY_ENABLED: default_deny_enabled,
                CONF_SIDEBAR_PANEL_ENABLED: sidebar_panel_enabled,
                CONF_GEOIP_ENABLED: geoip_enabled,
                CONF_LOGIN_ATTEMPTS_THRESHOLD: login_attempts_threshold,
                CONF_BLOCKED_NETWORKS: blocked_networks,
            },
        )

    def _description_placeholders(self) -> dict[str, str]:
        """Return current live status details for the management form."""
        from . import current_status

        status = current_status(self.hass)
        banned_ips = cast(list[dict[str, str]], status[ATTR_BANNED_IPS])
        failed_login_attempts = cast(dict[str, int], status[ATTR_FAILED_LOGIN_ATTEMPTS])
        blocked_networks = cast(list[str], status[ATTR_BLOCKED_NETWORKS])
        return {
            ATTR_NATIVE_IP_BAN_ENABLED: (
                "Enabled" if status[ATTR_NATIVE_IP_BAN_ENABLED] else "Disabled"
            ),
            ATTR_AUTO_BAN_ENABLED: (
                "Enabled" if status[ATTR_AUTO_BAN_ENABLED] else "Disabled"
            ),
            ATTR_BAN_NOTIFICATIONS_ENABLED: (
                "Enabled" if status[ATTR_BAN_NOTIFICATIONS_ENABLED] else "Disabled"
            ),
            ATTR_LOGIN_ATTEMPTS_THRESHOLD: str(status[ATTR_LOGIN_ATTEMPTS_THRESHOLD]),
            ATTR_NETWORKS: "\n".join(cast(list[str], status[ATTR_NETWORKS])) or "None",
            ATTR_BANNED_IPS: _format_banned_ip_details(banned_ips),
            ATTR_BLOCKED_NETWORKS: "\n".join(blocked_networks) or "None",
            ATTR_DEFAULT_DENY_ENABLED: (
                "Enabled" if status[ATTR_DEFAULT_DENY_ENABLED] else "Disabled"
            ),
            ATTR_FAILED_LOGIN_ATTEMPTS: "\n".join(
                f"{ip}: {count}" for ip, count in failed_login_attempts.items()
            )
            or "None",
        }

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage allowlisted and banned IP entries."""
        from . import current_status

        errors: dict[str, str] = {}
        self._detected_subnets = await _async_detect_home_assistant_subnets(self.hass)

        if user_input is not None:
            allowed_input = cast(dict[str, Any], user_input[SECTION_ALLOWED_IPS])
            banned_input = cast(dict[str, Any], user_input[SECTION_BANNED_IPS])
            current_auto_ban_enabled = bool(
                self._config_entry.options.get(
                    CONF_AUTO_BAN_ENABLED,
                    self._config_entry.data.get(CONF_AUTO_BAN_ENABLED, True),
                )
            )
            auto_ban_enabled = _ban_option_enabled(
                banned_input,
                CONF_AUTO_BAN_ENABLED,
                CONF_AUTO_BAN_CHECKBOX,
                current_auto_ban_enabled,
            )
            current_ban_notifications_enabled = bool(
                self._config_entry.options.get(
                    CONF_BAN_NOTIFICATIONS_ENABLED,
                    self._config_entry.data.get(CONF_BAN_NOTIFICATIONS_ENABLED, True),
                )
            )
            ban_notifications_enabled = _ban_option_enabled(
                banned_input,
                CONF_BAN_NOTIFICATIONS_ENABLED,
                CONF_BAN_NOTIFICATIONS_CHECKBOX,
                current_ban_notifications_enabled,
            )
            current_allowlisted_login_notifications_enabled = bool(
                self._config_entry.options.get(
                    CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED,
                    self._config_entry.data.get(
                        CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED, True
                    ),
                )
            )
            allowlisted_login_notifications_enabled = _ban_option_enabled(
                banned_input,
                CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED,
                CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_CHECKBOX,
                current_allowlisted_login_notifications_enabled,
            )
            current_allowlisted_logins_can_ban = bool(
                self._config_entry.options.get(
                    CONF_ALLOWLISTED_LOGINS_CAN_BAN,
                    self._config_entry.data.get(CONF_ALLOWLISTED_LOGINS_CAN_BAN, False),
                )
            )
            allowlisted_logins_can_ban = _ban_option_enabled(
                banned_input,
                CONF_ALLOWLISTED_LOGINS_CAN_BAN,
                CONF_ALLOWLISTED_LOGINS_CAN_BAN_CHECKBOX,
                current_allowlisted_logins_can_ban,
            )
            current_default_deny_enabled = bool(
                self._config_entry.options.get(
                    CONF_DEFAULT_DENY_ENABLED,
                    self._config_entry.data.get(CONF_DEFAULT_DENY_ENABLED, False),
                )
            )
            default_deny_enabled = _ban_option_enabled(
                banned_input,
                CONF_DEFAULT_DENY_ENABLED,
                CONF_DEFAULT_DENY_CHECKBOX,
                current_default_deny_enabled,
            )
            current_sidebar_panel_enabled = bool(
                self._config_entry.options.get(
                    CONF_SIDEBAR_PANEL_ENABLED,
                    self._config_entry.data.get(CONF_SIDEBAR_PANEL_ENABLED, True),
                )
            )
            sidebar_panel_enabled = _ban_option_enabled(
                banned_input,
                CONF_SIDEBAR_PANEL_ENABLED,
                CONF_SIDEBAR_PANEL_CHECKBOX,
                current_sidebar_panel_enabled,
            )
            current_geoip_enabled = bool(
                self._config_entry.options.get(
                    CONF_GEOIP_ENABLED,
                    self._config_entry.data.get(CONF_GEOIP_ENABLED, False),
                )
            )
            geoip_enabled = _ban_option_enabled(
                banned_input,
                CONF_GEOIP_ENABLED,
                CONF_GEOIP_CHECKBOX,
                current_geoip_enabled,
            )
            login_attempts_threshold = _normalize_login_attempts_threshold(
                banned_input.get(
                    CONF_LOGIN_ATTEMPTS_THRESHOLD,
                    self._config_entry.options.get(
                        CONF_LOGIN_ATTEMPTS_THRESHOLD,
                        self._config_entry.data.get(
                            CONF_LOGIN_ATTEMPTS_THRESHOLD,
                            _current_login_threshold(self.hass),
                        ),
                    ),
                )
            )
            try:
                ip_addresses = _validate_ip_addresses(allowed_input[CONF_ALLOWED_IPS])
                if CONF_QUICK_ALLOWLIST in allowed_input:
                    ip_addresses = _apply_quick_allowlist_options(
                        ip_addresses,
                        cast(list[str], allowed_input[CONF_QUICK_ALLOWLIST]),
                        self._detected_subnets,
                    )
            except UnsafeAllowlistError:
                errors[CONF_ALLOWED_IPS] = "unsafe_allowlist_network"
            except ValueError:
                errors[CONF_ALLOWED_IPS] = "invalid_ip_address"

            try:
                banned_ips = _validate_banned_ips(banned_input.get(CONF_BANNED_IPS, ""))
            except ValueError:
                errors[CONF_BANNED_IPS] = "invalid_banned_ip"

            try:
                blocked_networks = _validate_blocked_networks(
                    banned_input.get(CONF_BLOCKED_NETWORKS, "")
                )
            except UnsafeBlockedNetworkError:
                errors[CONF_BLOCKED_NETWORKS] = "unsafe_blocked_network"
            except ValueError:
                errors[CONF_BLOCKED_NETWORKS] = "invalid_blocked_network"

            if not errors:
                try:
                    _validate_ban_safety(
                        ip_addresses,
                        banned_ips,
                    )
                except BannedAllowlistedIPError:
                    errors[CONF_BANNED_IPS] = "banned_ip_allowlisted"

            if not errors:
                try:
                    _validate_local_block_safety(
                        ip_addresses,
                        blocked_networks,
                        self._detected_subnets,
                        default_deny_enabled,
                    )
                except UnprotectedLocalBlockError:
                    errors[CONF_BLOCKED_NETWORKS] = "local_network_block_unprotected"

            if not errors:
                current_bans = cast(
                    list[dict[str, str]], current_status(self.hass)[ATTR_BANNED_IPS]
                )
                if len(current_bans) > 1 and not banned_ips:
                    self._pending_clear_bans = {
                        CONF_IP_ADDRESSES: ip_addresses,
                        CONF_BANNED_IPS: banned_ips,
                        CONF_BLOCKED_NETWORKS: blocked_networks,
                        CONF_AUTO_BAN_ENABLED: auto_ban_enabled,
                        CONF_BAN_NOTIFICATIONS_ENABLED: ban_notifications_enabled,
                        CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED: (
                            allowlisted_login_notifications_enabled
                        ),
                        CONF_ALLOWLISTED_LOGINS_CAN_BAN: allowlisted_logins_can_ban,
                        CONF_DEFAULT_DENY_ENABLED: default_deny_enabled,
                        CONF_SIDEBAR_PANEL_ENABLED: sidebar_panel_enabled,
                        CONF_GEOIP_ENABLED: geoip_enabled,
                        CONF_LOGIN_ATTEMPTS_THRESHOLD: login_attempts_threshold,
                        "ban_count": len(current_bans),
                    }
                    return self.async_show_form(
                        step_id="confirm_clear_bans",
                        data_schema=self._confirm_clear_bans_schema(),
                        description_placeholders={"ban_count": str(len(current_bans))},
                    )

                return await self._async_save_management_changes(
                    ip_addresses,
                    banned_ips,
                    blocked_networks,
                    auto_ban_enabled,
                    ban_notifications_enabled,
                    allowlisted_login_notifications_enabled,
                    allowlisted_logins_can_ban,
                    default_deny_enabled,
                    sidebar_panel_enabled,
                    geoip_enabled,
                    login_attempts_threshold,
                )

        return self.async_show_form(
            step_id="init",
            data_schema=self._management_schema(),
            description_placeholders=self._description_placeholders(),
            errors=errors,
        )

    async def async_step_confirm_clear_bans(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Confirm clearing every exact IP ban."""
        errors: dict[str, str] = {}
        pending = self._pending_clear_bans
        if pending is None:
            return self.async_abort(reason="no_pending_clear_bans")

        if user_input is not None:
            if not user_input.get(CONF_CONFIRM_CLEAR_BANS, False):
                errors["base"] = "confirmation_required"
            else:
                return await self._async_save_management_changes(
                    cast(list[str], pending[CONF_IP_ADDRESSES]),
                    cast(list[str], pending[CONF_BANNED_IPS]),
                    cast(list[str], pending[CONF_BLOCKED_NETWORKS]),
                    cast(bool, pending[CONF_AUTO_BAN_ENABLED]),
                    cast(bool, pending[CONF_BAN_NOTIFICATIONS_ENABLED]),
                    cast(bool, pending[CONF_ALLOWLISTED_LOGIN_NOTIFICATIONS_ENABLED]),
                    cast(bool, pending[CONF_ALLOWLISTED_LOGINS_CAN_BAN]),
                    cast(bool, pending[CONF_DEFAULT_DENY_ENABLED]),
                    cast(bool, pending[CONF_SIDEBAR_PANEL_ENABLED]),
                    cast(bool, pending[CONF_GEOIP_ENABLED]),
                    cast(int, pending[CONF_LOGIN_ATTEMPTS_THRESHOLD]),
                )

        return self.async_show_form(
            step_id="confirm_clear_bans",
            data_schema=self._confirm_clear_bans_schema(),
            description_placeholders={"ban_count": str(pending["ban_count"])},
            errors=errors,
        )
