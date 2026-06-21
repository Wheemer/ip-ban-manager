"""Legacy config flow placeholder for ban_allowlist migration."""

from __future__ import annotations

from homeassistant import config_entries

from . import DOMAIN


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Prevent new ban_allowlist setup while old entries migrate."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, object] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Abort manual setup for the legacy domain."""
        return self.async_abort(reason="legacy_domain")
