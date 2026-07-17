"""Canary tests for Home Assistant ban surfaces IP Ban Manager patches."""

from custom_components.ip_ban_manager.ha_compat import assert_http_ban_hooks_available


def test_http_ban_hooks_still_available() -> None:
    """Fail early when a Home Assistant upgrade removes patched ban hooks."""
    assert_http_ban_hooks_available()
