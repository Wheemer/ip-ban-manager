"""Stubs for pytest_homeassistant_custom_component.common."""

from typing import Any

class MockConfigEntry:
    entry_id: str

    def __init__(self, *args: Any, **kwargs: Any) -> None: ...
    def add_to_hass(self, hass: Any) -> None: ...
