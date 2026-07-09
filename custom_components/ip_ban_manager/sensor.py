"""Sensors for the IP Ban Manager integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import current_status
from .const import (
    ATTR_BANNED_IPS,
    ATTR_BLOCKED_NETWORKS,
    ATTR_DEFAULT_DENY_ENABLED,
    ATTR_FAILED_LOGIN_ATTEMPTS,
    ATTR_NETWORKS,
)


@dataclass(frozen=True, kw_only=True)
class IPBanManagerSensorDescription(SensorEntityDescription):
    """Describe an IP Ban Manager diagnostic sensor."""

    key: str
    name: str
    icon: str
    value_fn: Callable[[dict[str, Any]], int]
    attributes_fn: Callable[[dict[str, Any]], dict[str, Any]]


SENSOR_DESCRIPTIONS = (
    IPBanManagerSensorDescription(
        key="active_bans",
        name="IP Ban Manager Active Bans",
        icon="mdi:shield-lock-outline",
        value_fn=lambda status: len(cast(list[object], status[ATTR_BANNED_IPS])),
        attributes_fn=lambda status: {
            ATTR_BANNED_IPS: status[ATTR_BANNED_IPS],
        },
    ),
    IPBanManagerSensorDescription(
        key="allowlisted_networks",
        name="IP Ban Manager Allowlisted Networks",
        icon="mdi:shield-check-outline",
        value_fn=lambda status: len(cast(list[object], status[ATTR_NETWORKS])),
        attributes_fn=lambda status: {
            ATTR_NETWORKS: status[ATTR_NETWORKS],
        },
    ),
    IPBanManagerSensorDescription(
        key="blocked_networks",
        name="IP Ban Manager Blocked Networks",
        icon="mdi:shield-alert-outline",
        value_fn=lambda status: len(cast(list[object], status[ATTR_BLOCKED_NETWORKS])),
        attributes_fn=lambda status: {
            ATTR_BLOCKED_NETWORKS: status[ATTR_BLOCKED_NETWORKS],
            ATTR_DEFAULT_DENY_ENABLED: status[ATTR_DEFAULT_DENY_ENABLED],
        },
    ),
    IPBanManagerSensorDescription(
        key="failed_login_sources",
        name="IP Ban Manager Failed Login Sources",
        icon="mdi:account-alert-outline",
        value_fn=lambda status: len(
            cast(dict[str, int], status[ATTR_FAILED_LOGIN_ATTEMPTS])
        ),
        attributes_fn=lambda status: {
            ATTR_FAILED_LOGIN_ATTEMPTS: status[ATTR_FAILED_LOGIN_ATTEMPTS],
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up IP Ban Manager sensors."""
    async_add_entities(
        [
            IPBanManagerSensor(hass, entry, description)
            for description in SENSOR_DESCRIPTIONS
        ]
    )


class IPBanManagerSensor(SensorEntity):
    """Expose one live IP Ban Manager diagnostic count."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = ""
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        description: IPBanManagerSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        self.hass = hass
        self._description = description
        self.entity_description = description
        self._attr_icon = description.icon
        self._attr_name = description.name
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._status = cast(dict[str, Any], current_status(hass))

    async def async_update(self) -> None:
        """Refresh the cached diagnostic status."""
        self._status = cast(dict[str, Any], current_status(self.hass))

    @property
    def native_value(self) -> int:
        """Return the diagnostic count."""
        return self._description.value_fn(self._status)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return details behind the diagnostic count."""
        return self._description.attributes_fn(self._status)
