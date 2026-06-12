"""Config flow for the Ban Allowlist integration."""

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
    ATTR_AUTO_BAN_ENABLED,
    ATTR_BANNED_IPS,
    ATTR_FAILED_LOGIN_ATTEMPTS,
    ATTR_IP_ADDRESS,
    ATTR_LOGIN_ATTEMPTS_THRESHOLD,
    ATTR_NATIVE_IP_BAN_ENABLED,
    ATTR_NETWORKS,
    CONF_ALLOWED_IPS,
    CONF_AUTO_BAN_ENABLED,
    CONF_BANNED_IPS,
    CONF_IP_ADDRESSES,
    CONF_LOGIN_ATTEMPTS_THRESHOLD,
    DEFAULT_LOGIN_ATTEMPTS_THRESHOLD,
    DOMAIN,
)
from .ip_utils import normalize_allowlist_network, parse_allowlist_network

SECTION_ALLOWED_IPS = "allowed_ips"
SECTION_BANNED_IPS = "banned_ips"
SECTION_BAN_SETTINGS = "ban_settings"
DEFAULT_ALLOWED_IPS = ["127.0.0.1"]
CONF_QUICK_ALLOWLIST = "quick_allowlist"
QUICK_ALLOW_LOCALHOST = "localhost"
QUICK_ALLOW_LOCAL_NETWORK = "local_network"

IPNetwork = IPv4Network | IPv6Network


class UnsafeAllowlistError(ValueError):
    """Raised when an allowlist entry would effectively disable IP bans."""


class BannedAllowlistedIPError(ValueError):
    """Raised when an IP is both allowlisted and banned."""


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


def _login_attempts_threshold_selector() -> selector.NumberSelector:
    """Return the login-attempt threshold selector."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0,
            max=100,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _current_login_threshold(hass: HomeAssistant) -> int:
    """Return Home Assistant's current live login-attempt threshold."""
    if hass.http is None:
        return DEFAULT_LOGIN_ATTEMPTS_THRESHOLD
    return max(
        0, int(hass.http.app.get(KEY_LOGIN_THRESHOLD, DEFAULT_LOGIN_ATTEMPTS_THRESHOLD))
    )


def _ban_settings_fields(auto_ban_enabled: bool, threshold: int) -> dict[Any, Any]:
    """Return the auto-ban settings fields."""
    return {
        vol_optional(
            CONF_AUTO_BAN_ENABLED,
            default=auto_ban_enabled,
        ): bool,
        vol.Required(
            CONF_LOGIN_ATTEMPTS_THRESHOLD,
            default=threshold,
        ): _login_attempts_threshold_selector(),
    }


def _ban_settings_schema(auto_ban_enabled: bool, threshold: int) -> vol.Schema:
    """Return the auto-ban settings schema."""
    return vol.Schema(_ban_settings_fields(auto_ban_enabled, threshold))


def _local_network_option_label(detected_subnets: list[str]) -> str:
    """Return a readable dynamic label for the local-network checkbox."""
    if len(detected_subnets) == 1:
        return f"Allow local network {detected_subnets[0]}"

    return f"Allow local networks {', '.join(detected_subnets)}"


def _initial_setup_schema(detected_subnets: list[str], threshold: int) -> vol.Schema:
    """Return the first-run setup schema."""
    fields = _ban_settings_fields(True, threshold)
    fields[
        vol_optional(
            CONF_QUICK_ALLOWLIST,
            default=[QUICK_ALLOW_LOCALHOST],
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
    fields: dict[Any, Any] = {}
    missing_quick_options = _missing_quick_allowlist_options(
        current_addresses, detected_subnets
    )
    if missing_quick_options:
        fields[
            vol_optional(
                CONF_QUICK_ALLOWLIST,
                default=[],
            )
        ] = _quick_allowlist_selector(missing_quick_options, detected_subnets)
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
                "label": "Allow localhost 127.0.0.1",
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


def _missing_quick_allowlist_options(
    current_addresses: list[str], detected_subnets: list[str]
) -> list[str]:
    """Return common allowlist checkboxes that are not already configured."""
    missing: list[str] = []
    if DEFAULT_ALLOWED_IPS[0] not in current_addresses:
        missing.append(QUICK_ALLOW_LOCALHOST)
    if detected_subnets and not any(
        subnet in current_addresses for subnet in detected_subnets
    ):
        missing.append(QUICK_ALLOW_LOCAL_NETWORK)
    return missing


def _apply_quick_allowlist_options(
    ip_addresses: list[str], quick_input: list[str], detected_subnets: list[str]
) -> list[str]:
    """Add selected convenience allowlist entries to the editable allowlist."""
    updated = list(ip_addresses)

    if QUICK_ALLOW_LOCALHOST in quick_input:
        updated.extend(DEFAULT_ALLOWED_IPS)
    if QUICK_ALLOW_LOCAL_NETWORK in quick_input:
        updated.extend(detected_subnets)

    return _dedupe_items(updated)


async def _async_detect_home_assistant_subnets(hass: HomeAssistant) -> list[str]:
    """Detect local IPv4 networks from Home Assistant's enabled adapters."""
    adapters = await async_get_adapters(hass)
    enabled_adapters = [adapter for adapter in adapters if adapter["enabled"]]
    default_adapters = [
        adapter
        for adapter in enabled_adapters
        if adapter["default"] and adapter["ipv4"]
    ]
    candidate_adapters = default_adapters or enabled_adapters
    networks: list[str] = []
    seen: set[str] = set()

    for adapter in candidate_adapters:
        for address in adapter["ipv4"]:
            interface = ip_interface(
                f"{address['address']}/{address['network_prefix']}"
            )
            network = interface.network
            if (
                network.is_loopback
                or network.is_link_local
                or network.is_multicast
                or network.is_unspecified
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


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ban Allowlist."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        detected_subnets = await _async_detect_home_assistant_subnets(self.hass)

        if user_input is not None:
            ip_addresses = []
            quick_input = cast(list[str], user_input.get(CONF_QUICK_ALLOWLIST, []))
            if QUICK_ALLOW_LOCALHOST in quick_input:
                ip_addresses.extend(DEFAULT_ALLOWED_IPS)
            if QUICK_ALLOW_LOCAL_NETWORK in quick_input:
                ip_addresses.extend(detected_subnets)

            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title="IP Ban Manager",
                data={
                    CONF_IP_ADDRESSES: _dedupe_items(ip_addresses),
                    CONF_AUTO_BAN_ENABLED: bool(
                        user_input.get(CONF_AUTO_BAN_ENABLED, True)
                    ),
                    CONF_LOGIN_ATTEMPTS_THRESHOLD: int(
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
        self._abort_if_unique_id_configured(updates={CONF_IP_ADDRESSES: ip_addresses})
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

    def _management_schema(self) -> vol.Schema:
        """Return the live management form schema."""
        from . import current_status

        status = current_status(self.hass)
        banned_ips = [
            f"{ban[ATTR_IP_ADDRESS]} - {_format_banned_at(ban[ATTR_BANNED_AT])}"
            for ban in cast(list[dict[str, str]], status[ATTR_BANNED_IPS])
        ]
        current_addresses = _current_addresses(self._config_entry)
        return vol.Schema(
            {
                vol_optional(
                    SECTION_BAN_SETTINGS,
                    default={},
                ): data_entry_flow.section(
                    _ban_settings_schema(
                        bool(status[ATTR_AUTO_BAN_ENABLED]),
                        cast(int, status[ATTR_LOGIN_ATTEMPTS_THRESHOLD]),
                    ),
                    {"collapsed": True},
                ),
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
                            vol.Required(
                                CONF_BANNED_IPS,
                                default=_items_to_text(banned_ips),
                            ): _text_selector(),
                        }
                    ),
                    {"collapsed": True},
                ),
            }
        )

    def _description_placeholders(self) -> dict[str, str]:
        """Return current live status details for the management form."""
        from . import current_status

        status = current_status(self.hass)
        banned_ips = cast(list[dict[str, str]], status[ATTR_BANNED_IPS])
        failed_login_attempts = cast(dict[str, int], status[ATTR_FAILED_LOGIN_ATTEMPTS])
        return {
            ATTR_NATIVE_IP_BAN_ENABLED: (
                "Enabled" if status[ATTR_NATIVE_IP_BAN_ENABLED] else "Disabled"
            ),
            ATTR_AUTO_BAN_ENABLED: (
                "Enabled" if status[ATTR_AUTO_BAN_ENABLED] else "Disabled"
            ),
            ATTR_LOGIN_ATTEMPTS_THRESHOLD: str(status[ATTR_LOGIN_ATTEMPTS_THRESHOLD]),
            ATTR_NETWORKS: "\n".join(cast(list[str], status[ATTR_NETWORKS])) or "None",
            ATTR_BANNED_IPS: _format_banned_ip_details(banned_ips),
            ATTR_FAILED_LOGIN_ATTEMPTS: "\n".join(
                f"{ip}: {count}" for ip, count in failed_login_attempts.items()
            )
            or "None",
        }

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage allowlisted and banned IP entries."""
        from . import (
            _apply_ban_settings,
            _async_replace_ip_bans,
            _update_allowlist_entry,
        )

        errors: dict[str, str] = {}
        self._detected_subnets = await _async_detect_home_assistant_subnets(self.hass)

        if user_input is not None:
            ban_settings_input = cast(
                dict[str, Any], user_input.get(SECTION_BAN_SETTINGS, {})
            )
            auto_ban_enabled = bool(
                ban_settings_input.get(
                    CONF_AUTO_BAN_ENABLED,
                    self._config_entry.options.get(
                        CONF_AUTO_BAN_ENABLED,
                        self._config_entry.data.get(CONF_AUTO_BAN_ENABLED, True),
                    ),
                )
            )
            login_attempts_threshold = int(
                ban_settings_input.get(
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
            allowed_input = cast(dict[str, Any], user_input[SECTION_ALLOWED_IPS])
            banned_input = cast(dict[str, str], user_input[SECTION_BANNED_IPS])
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
                banned_ips = _validate_banned_ips(banned_input[CONF_BANNED_IPS])
            except ValueError:
                errors[CONF_BANNED_IPS] = "invalid_banned_ip"

            if not errors:
                try:
                    _validate_ban_safety(
                        ip_addresses,
                        banned_ips,
                    )
                except BannedAllowlistedIPError:
                    errors[CONF_BANNED_IPS] = "banned_ip_allowlisted"

            if not errors:
                self.hass.config_entries.async_update_entry(
                    self._config_entry,
                    options={
                        **self._config_entry.options,
                        CONF_AUTO_BAN_ENABLED: auto_ban_enabled,
                        CONF_LOGIN_ATTEMPTS_THRESHOLD: login_attempts_threshold,
                    },
                )
                _apply_ban_settings(self.hass, self._config_entry)
                _update_allowlist_entry(self.hass, ip_addresses)
                await _async_replace_ip_bans(self.hass, banned_ips)
                return self.async_create_entry(
                    title="",
                    data={CONF_IP_ADDRESSES: ip_addresses},
                )

        return self.async_show_form(
            step_id="init",
            data_schema=self._management_schema(),
            description_placeholders=self._description_placeholders(),
            errors=errors,
        )
