"""Config flow for the Ban Allowlist integration."""

from __future__ import annotations

from collections.abc import Iterable
from ipaddress import ip_address, ip_network
from typing import Any, cast

import voluptuous as vol
from homeassistant import config_entries, data_entry_flow
from homeassistant.components.http.ban import ATTR_BANNED_AT
from homeassistant.helpers import selector

from .const import (
    ATTR_BANNED_IPS,
    ATTR_FAILED_LOGIN_ATTEMPTS,
    ATTR_IP_ADDRESS,
    ATTR_NETWORKS,
    CONF_ALLOWED_IPS,
    CONF_BANNED_IPS,
    CONF_IP_ADDRESSES,
    DOMAIN,
)

SECTION_ALLOWED_IPS = "allowed_ips"
SECTION_BANNED_IPS = "banned_ips"


def _normalize_list(value: str | Iterable[str]) -> list[str]:
    """Normalize multiline, comma-separated, or YAML-imported values."""
    raw_values: Iterable[str]
    if isinstance(value, str):
        raw_values = value.replace(",", "\n").splitlines()
    else:
        raw_values = value

    return [item.strip() for item in raw_values if item.strip()]


def _validate_ip_addresses(value: str | Iterable[str]) -> list[str]:
    """Validate and normalize configured IP addresses and networks."""
    ip_addresses = _normalize_list(value)
    for address in ip_addresses:
        ip_network(address)

    return ip_addresses


def _validate_banned_ips(value: str | Iterable[str]) -> list[str]:
    """Validate and normalize configured banned IP addresses."""
    banned_ips = [
        banned_ip.split(" - ", 1)[0].strip() for banned_ip in _normalize_list(value)
    ]
    for banned_ip in banned_ips:
        ip_address(banned_ip)

    return banned_ips


def _items_to_text(items: Iterable[str]) -> str:
    """Convert stored items to the multiline UI representation."""
    return "\n".join(items)


def _format_banned_ip_details(banned_ips: list[dict[str, str]]) -> str:
    """Return a readable banned-IP detail list."""
    if not banned_ips:
        return "None"

    return "\n".join(
        f"{ban[ATTR_IP_ADDRESS]} - {ban[ATTR_BANNED_AT]}" for ban in banned_ips
    )


def _text_selector() -> selector.TextSelector:
    """Return a multiline text selector."""
    return selector.TextSelector(
        selector.TextSelectorConfig(
            multiline=True,
            type=selector.TextSelectorType.TEXT,
        )
    )


def _data_schema(ip_addresses: Iterable[str] | None = None) -> vol.Schema:
    """Return the config flow data schema."""
    return vol.Schema(
        {
            vol.Required(
                CONF_IP_ADDRESSES,
                default=_items_to_text(ip_addresses or []),
            ): _text_selector()
        }
    )


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
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                ip_addresses = _validate_ip_addresses(user_input[CONF_IP_ADDRESSES])
            except ValueError:
                errors[CONF_IP_ADDRESSES] = "invalid_ip_address"
            else:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="IP Ban Manager",
                    data={CONF_IP_ADDRESSES: ip_addresses},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_data_schema(),
            errors=errors,
        )

    async def async_step_import(
        self, user_input: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Import YAML configuration."""
        ip_addresses = _validate_ip_addresses(user_input[CONF_IP_ADDRESSES])
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

    def _management_schema(self) -> vol.Schema:
        """Return the live management form schema."""
        from . import current_status

        status = current_status(self.hass)
        banned_ips = [
            f"{ban[ATTR_IP_ADDRESS]} - {ban[ATTR_BANNED_AT]}"
            for ban in cast(list[dict[str, str]], status[ATTR_BANNED_IPS])
        ]
        return vol.Schema(
            {
                vol.Required(
                    SECTION_ALLOWED_IPS,
                ): data_entry_flow.section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_ALLOWED_IPS,
                                default=_items_to_text(
                                    _current_addresses(self._config_entry)
                                ),
                            ): _text_selector(),
                        }
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
        from . import _async_replace_ip_bans, _update_allowlist_entry

        errors: dict[str, str] = {}

        if user_input is not None:
            allowed_input = cast(dict[str, str], user_input[SECTION_ALLOWED_IPS])
            banned_input = cast(dict[str, str], user_input[SECTION_BANNED_IPS])
            try:
                ip_addresses = _validate_ip_addresses(allowed_input[CONF_ALLOWED_IPS])
            except ValueError:
                errors[CONF_ALLOWED_IPS] = "invalid_ip_address"

            try:
                banned_ips = _validate_banned_ips(banned_input[CONF_BANNED_IPS])
            except ValueError:
                errors[CONF_BANNED_IPS] = "invalid_banned_ip"

            if not errors:
                _update_allowlist_entry(self.hass, ip_addresses)
                await _async_replace_ip_bans(self.hass, banned_ips)
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_IP_ADDRESSES: ip_addresses,
                        CONF_BANNED_IPS: banned_ips,
                    },
                )

        return self.async_show_form(
            step_id="init",
            data_schema=self._management_schema(),
            description_placeholders=self._description_placeholders(),
            errors=errors,
        )
