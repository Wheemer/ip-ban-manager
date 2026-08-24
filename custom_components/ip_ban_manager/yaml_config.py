"""YAML configuration helpers for IP Ban Manager."""

from __future__ import annotations

from pathlib import Path

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from voluptuous.schema_builder import Optional as vol_optional
from voluptuous.validators import Any as vol_any

from .const import (
    CONF_DISABLE_BAN_MANAGER,
    CONF_DISABLED,
    CONF_IP_ADDRESSES,
    DOMAIN,
    LEGACY_DOMAIN,
)

EMERGENCY_DISABLE_FILENAME = "ip_ban_manager.disabled"

CONFIG_SCHEMA = vol.Schema(
    {
        vol_optional(DOMAIN): vol_any(
            CONF_DISABLED,
            vol.Schema(
                {
                    vol_optional(CONF_DISABLE_BAN_MANAGER, default=False): cv.boolean,
                    vol_optional(CONF_IP_ADDRESSES): vol.All(
                        cv.ensure_list, [cv.string]
                    ),
                }
            ),
        ),
        vol_optional(LEGACY_DOMAIN): vol_any(
            CONF_DISABLED,
            vol.Schema(
                {
                    vol_optional(CONF_DISABLE_BAN_MANAGER, default=False): cv.boolean,
                    vol_optional(CONF_IP_ADDRESSES): vol.All(
                        cv.ensure_list, [cv.string]
                    ),
                }
            ),
        ),
    },
    extra=vol.ALLOW_EXTRA,
)


def yaml_disable_ban_manager(config: ConfigType) -> bool:
    """Return whether YAML requested the emergency integration kill switch."""
    for domain in (DOMAIN, LEGACY_DOMAIN):
        domain_config = config.get(domain)
        if domain_config == CONF_DISABLED:
            return True
        if not isinstance(domain_config, dict):
            continue
        if domain_config.get(CONF_DISABLE_BAN_MANAGER):
            return True

    return False


def emergency_disable_file_exists(hass: HomeAssistant) -> bool:
    """Return whether the emergency disable file exists."""
    return Path(hass.config.path(EMERGENCY_DISABLE_FILENAME)).is_file()


def emergency_disable_requested(hass: HomeAssistant, config: ConfigType) -> bool:
    """Return whether any supported emergency disable path is active."""
    return yaml_disable_ban_manager(config) or emergency_disable_file_exists(hass)


async def async_emergency_disable_requested(
    hass: HomeAssistant, config: ConfigType
) -> bool:
    """Return whether the emergency disable path is active without blocking."""
    if yaml_disable_ban_manager(config):
        return True

    return await hass.async_add_executor_job(emergency_disable_file_exists, hass)
