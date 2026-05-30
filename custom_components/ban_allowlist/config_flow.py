"""Config flow for the Ban Allowlist integration."""

from __future__ import annotations

from collections.abc import Iterable
from ipaddress import ip_network
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import CONF_IP_ADDRESSES, DOMAIN


def _normalize_ip_addresses(value: str | Iterable[str]) -> list[str]:
    """Normalize multiline, comma-separated, or YAML-imported addresses."""
    if isinstance(value, str):
        raw_addresses = value.replace(",", "\n").splitlines()
    else:
        raw_addresses = value

    return [address.strip() for address in raw_addresses if address.strip()]


def _validate_ip_addresses(value: str | Iterable[str]) -> list[str]:
    """Validate and normalize configured IP addresses and networks."""
    ip_addresses = _normalize_ip_addresses(value)
    for address in ip_addresses:
        ip_network(address)

    return ip_addresses


def _addresses_to_text(ip_addresses: Iterable[str]) -> str:
    """Convert stored addresses to the multiline UI representation."""
    return "\n".join(ip_addresses)


def _data_schema(ip_addresses: Iterable[str] | None = None) -> vol.Schema:
    """Return the config flow data schema."""
    return vol.Schema(
        {
            vol.Required(
                CONF_IP_ADDRESSES,
                default=_addresses_to_text(ip_addresses or []),
            ): selector.TextSelector(
                selector.TextSelectorConfig(
                    multiline=True,
                    type=selector.TextSelectorType.TEXT,
                )
            )
        }
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
                    title="IP Ban Allowlist",
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
            title="IP Ban Allowlist",
            data={CONF_IP_ADDRESSES: ip_addresses},
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return OptionsFlow(config_entry)


class OptionsFlow(config_entries.OptionsFlow):
    """Handle options for Ban Allowlist."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage Ban Allowlist options."""
        errors: dict[str, str] = {}
        current_addresses = self._config_entry.options.get(
            CONF_IP_ADDRESSES,
            self._config_entry.data.get(CONF_IP_ADDRESSES, []),
        )

        if user_input is not None:
            try:
                ip_addresses = _validate_ip_addresses(user_input[CONF_IP_ADDRESSES])
            except ValueError:
                errors[CONF_IP_ADDRESSES] = "invalid_ip_address"
            else:
                return self.async_create_entry(
                    title="",
                    data={CONF_IP_ADDRESSES: ip_addresses},
                )

        return self.async_show_form(
            step_id="init",
            data_schema=_data_schema(current_addresses),
            errors=errors,
        )
