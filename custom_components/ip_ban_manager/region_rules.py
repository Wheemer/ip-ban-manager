"""Validated public-region lists with a read-only legacy migration path."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    ALLOWED_REGION_ANYWHERE,
    ALLOWED_REGION_COUNTRY,
    CONF_PUBLIC_REGION_ENABLED,
    CONF_PUBLIC_REGION_RULES,
)
from .entry_helpers import (
    entry_allowed_region_country,
    entry_allowed_region_mode,
    entry_allowed_region_subdivision,
    entry_login_threshold,
    entry_regional_login_thresholds,
    normalize_regional_login_thresholds,
)


def normalize_public_region_settings(enabled: object, rules: object) -> dict[str, Any]:
    """Reject invalid counts and empty enabled lists before any mutation."""
    if type(enabled) is not bool:
        raise HomeAssistantError("Public Region Lock enabled must be true or false.")
    if not isinstance(rules, dict) or any(
        type(value) is not int or not 0 <= value <= 100 for value in rules.values()
    ):
        raise HomeAssistantError(
            "Region thresholds must be whole numbers from 0 to 100."
        )
    if any(not isinstance(key, str) or not key.isascii() for key in rules):
        raise HomeAssistantError("Use ISO region codes such as CA or CA-NL.")
    normalized = normalize_regional_login_thresholds(rules)
    if len(normalized) != len(rules):
        raise HomeAssistantError("Duplicate region codes are not allowed.")
    if enabled and not normalized:
        raise HomeAssistantError(
            "Add at least one region before enabling Public Region Lock."
        )
    return {CONF_PUBLIC_REGION_ENABLED: enabled, CONF_PUBLIC_REGION_RULES: normalized}


def has_public_region_settings(entry: ConfigEntry) -> bool:
    """Identify entries that use the new list format."""
    return (
        CONF_PUBLIC_REGION_RULES in entry.options
        or CONF_PUBLIC_REGION_RULES in entry.data
    )


def entry_public_region_settings(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Keep the old access boundary when displaying a legacy single-region rule."""
    if has_public_region_settings(entry):
        return normalize_public_region_settings(
            entry.options.get(
                CONF_PUBLIC_REGION_ENABLED,
                entry.data.get(CONF_PUBLIC_REGION_ENABLED, False),
            ),
            entry.options.get(
                CONF_PUBLIC_REGION_RULES, entry.data.get(CONF_PUBLIC_REGION_RULES, {})
            ),
        )
    mode = entry_allowed_region_mode(entry)
    country = entry_allowed_region_country(entry)
    return legacy_public_region_settings(
        mode,
        country,
        entry_allowed_region_subdivision(entry),
        entry_regional_login_thresholds(entry),
        entry_login_threshold(entry, hass),
    )


def legacy_public_region_settings(
    mode: str, country: str, subdivision: str, thresholds: dict[str, int], default: int
) -> dict[str, Any]:
    """Convert one old access boundary without widening it."""
    code = country if mode == ALLOWED_REGION_COUNTRY else subdivision
    rules = {}
    if mode != ALLOWED_REGION_ANYWHERE and code:
        rules[code] = thresholds.get(code, thresholds.get(country, default))
        # Keep narrower overrides without allowing any additional country.
        if mode == ALLOWED_REGION_COUNTRY:
            rules.update(
                {
                    key: value
                    for key, value in thresholds.items()
                    if key.startswith(country + "-")
                }
            )
    elif mode == ALLOWED_REGION_ANYWHERE:
        rules = thresholds
    return normalize_public_region_settings(mode != ALLOWED_REGION_ANYWHERE, rules)
