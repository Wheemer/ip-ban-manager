"""Home Assistant service registration for IP Ban Manager."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .audit import mutation_source, record_geoip_updated
from .backup import async_export_config, async_import_config
from .ban_ops import async_add_ip_ban, async_remove_all_ip_bans, async_remove_ip_ban
from .const import (
    ATTR_CONFIRM,
    ATTR_IP_ADDRESS,
    ATTR_NETWORK,
    DOMAIN,
    SERVICE_ADD_ALLOWLIST_NETWORK,
    SERVICE_ADD_BLOCKED_NETWORK,
    SERVICE_ADD_IP_BAN,
    SERVICE_EXPORT_CONFIG,
    SERVICE_IMPORT_CONFIG,
    SERVICE_REMOVE_ALL_IP_BANS,
    SERVICE_REMOVE_ALLOWLIST_NETWORK,
    SERVICE_REMOVE_BLOCKED_NETWORK,
    SERVICE_REMOVE_IP_BAN,
    SERVICE_UPDATE_GEOIP,
    SOURCE_SERVICE,
)
from .geoip import async_download_geoip_database
from .network_policy import (
    async_add_allowlist_network,
    async_add_blocked_network,
    async_remove_allowlist_network,
    async_remove_blocked_network,
)

IP_ADDRESS_SCHEMA = vol.Schema({vol.Required(ATTR_IP_ADDRESS): cv.string})
NETWORK_SCHEMA = vol.Schema({vol.Required(ATTR_NETWORK): cv.string})
REMOVE_ALL_IP_BANS_SCHEMA = vol.Schema(
    {vol.Required(ATTR_CONFIRM, default=False): cv.boolean}
)

REGISTERED_SERVICES = (
    SERVICE_ADD_IP_BAN,
    SERVICE_REMOVE_IP_BAN,
    SERVICE_REMOVE_ALL_IP_BANS,
    SERVICE_ADD_ALLOWLIST_NETWORK,
    SERVICE_REMOVE_ALLOWLIST_NETWORK,
    SERVICE_ADD_BLOCKED_NETWORK,
    SERVICE_REMOVE_BLOCKED_NETWORK,
    SERVICE_EXPORT_CONFIG,
    SERVICE_IMPORT_CONFIG,
    SERVICE_UPDATE_GEOIP,
)


def register_services(hass: HomeAssistant) -> None:  # noqa: D202
    """Register live ban and allowlist management services."""

    async def add_ip_ban(call: ServiceCall) -> None:
        with mutation_source(SOURCE_SERVICE):
            await async_add_ip_ban(hass, call.data[ATTR_IP_ADDRESS])

    async def remove_ip_ban(call: ServiceCall) -> None:
        await async_remove_ip_ban(hass, call.data[ATTR_IP_ADDRESS])

    async def remove_all_ip_bans(call: ServiceCall) -> None:
        if not call.data[ATTR_CONFIRM]:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="clear_all_ip_bans_confirmation_required",
            )
        await async_remove_all_ip_bans(hass)

    async def add_allowlist_network(call: ServiceCall) -> None:
        await async_add_allowlist_network(hass, call.data[ATTR_NETWORK])

    async def remove_allowlist_network(call: ServiceCall) -> None:
        await async_remove_allowlist_network(hass, call.data[ATTR_NETWORK])

    async def add_blocked_network(call: ServiceCall) -> None:
        await async_add_blocked_network(hass, call.data[ATTR_NETWORK])

    async def remove_blocked_network(call: ServiceCall) -> None:
        await async_remove_blocked_network(hass, call.data[ATTR_NETWORK])

    async def export_config(call: ServiceCall) -> None:
        await async_export_config(hass)

    async def import_config(call: ServiceCall) -> None:
        await async_import_config(hass)

    async def update_geoip(call: ServiceCall) -> None:
        await async_download_geoip_database(hass)
        record_geoip_updated(hass, SOURCE_SERVICE)

    hass.services.async_register(
        DOMAIN, SERVICE_ADD_IP_BAN, add_ip_ban, schema=IP_ADDRESS_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REMOVE_IP_BAN, remove_ip_ban, schema=IP_ADDRESS_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_ALL_IP_BANS,
        remove_all_ip_bans,
        schema=REMOVE_ALL_IP_BANS_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_ALLOWLIST_NETWORK,
        add_allowlist_network,
        schema=NETWORK_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_ALLOWLIST_NETWORK,
        remove_allowlist_network,
        schema=NETWORK_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_BLOCKED_NETWORK,
        add_blocked_network,
        schema=NETWORK_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_BLOCKED_NETWORK,
        remove_blocked_network,
        schema=NETWORK_SCHEMA,
    )
    hass.services.async_register(DOMAIN, SERVICE_EXPORT_CONFIG, export_config)
    hass.services.async_register(DOMAIN, SERVICE_IMPORT_CONFIG, import_config)
    hass.services.async_register(DOMAIN, SERVICE_UPDATE_GEOIP, update_geoip)
