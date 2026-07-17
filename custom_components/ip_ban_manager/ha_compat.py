"""Home Assistant HTTP ban surfaces this integration depends on."""

from __future__ import annotations

from homeassistant.components.http import ban as http_ban
from homeassistant.components.http.ban import (
    KEY_BAN_MANAGER,
    KEY_FAILED_LOGIN_ATTEMPTS,
    KEY_LOGIN_THRESHOLD,
    IpBan,
    IpBanManager,
)

# Symbols imported or patched by IP Ban Manager. A Home Assistant upgrade that
# removes or renames any of these should fail this canary before runtime.
REQUIRED_HTTP_BAN_MODULE_ATTRS = (
    "process_wrong_login",
)
REQUIRED_BAN_EXPORTS = (
    ("KEY_BAN_MANAGER", KEY_BAN_MANAGER),
    ("KEY_FAILED_LOGIN_ATTEMPTS", KEY_FAILED_LOGIN_ATTEMPTS),
    ("KEY_LOGIN_THRESHOLD", KEY_LOGIN_THRESHOLD),
    ("IpBan", IpBan),
    ("IpBanManager", IpBanManager),
)
REQUIRED_IP_BAN_MANAGER_METHODS = (
    "async_add_ban",
    "async_load",
)


def assert_http_ban_hooks_available() -> None:
    """Raise AssertionError when HA ban hooks this integration needs are missing."""
    missing: list[str] = []

    for attr_name in REQUIRED_HTTP_BAN_MODULE_ATTRS:
        if not hasattr(http_ban, attr_name):
            missing.append(f"homeassistant.components.http.ban.{attr_name}")

    for export_name, export_value in REQUIRED_BAN_EXPORTS:
        if export_value is None:
            missing.append(f"homeassistant.components.http.ban.{export_name}")

    for method_name in REQUIRED_IP_BAN_MANAGER_METHODS:
        if not callable(getattr(IpBanManager, method_name, None)):
            missing.append(f"IpBanManager.{method_name}")

    if missing:
        raise AssertionError(
            "Home Assistant HTTP ban surface changed; IP Ban Manager needs an update: "
            + ", ".join(missing)
        )
