"""Legacy config flow shim for IP Ban Manager migration."""

from __future__ import annotations

from homeassistant import config_entries

DOMAIN = "ban_allowlist"


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Abort new legacy flows; users should add IP Ban Manager."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, object] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Direct users to the new integration domain."""
        return self.async_abort(reason="migrated")
