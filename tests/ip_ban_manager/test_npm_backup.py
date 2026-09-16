"""NPM backup round trips and failure boundaries."""

from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.ip_ban_manager import backup
from custom_components.ip_ban_manager import nginx_proxy_manager as npm
from custom_components.ip_ban_manager.const import CONF_NPM, DOMAIN
from custom_components.ip_ban_manager.entry_helpers import update_entry_options

from .test_setup import setup_ip_ban_manager


def npm_config(enabled: bool = True) -> dict[str, object]:
    """Return a complete connection with non-sensitive test credentials."""
    return {
        "base_url": "http://npm.example.test:81",
        "identity": "admin@example.test",
        "token": "test-token",
        "token_expires": "2030-01-01T00:00:00Z",
        "proxy_host_id": 4,
        "exact_match_host_id": 4,
        "access_list_id": 0,
        "enabled": enabled,
        "mirror_default_deny": False,
    }


@pytest.mark.asyncio
async def test_npm_download_round_trip_and_repeat(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Downloads restore credentials/settings, not caches; repeating is safe."""
    await setup_ip_ban_manager(hass)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    config = npm_config()
    update_entry_options(hass, **{CONF_NPM: {**config, "hosts": [{"id": 999}]}})
    downloaded = backup.config_download_payload(hass)["content"]
    payload = yaml.safe_load(downloaded)
    assert payload["format_version"] == 3
    assert payload["settings"][CONF_NPM] == config
    assert downloaded.startswith("# Private backup:")
    assert "hosts:" not in downloaded
    # Exact-ban file persistence is covered by the existing backup tests.
    payload.pop("banned_ips")
    update_entry_options(hass, **{CONF_NPM: {}})
    sync = Mock()
    cleanup = AsyncMock()
    monkeypatch.setattr(backup, "schedule_npm_sync", sync)
    monkeypatch.setattr(backup, "async_disable_npm", cleanup)

    for _ in range(2):
        await backup.async_apply_config_backup_payload(hass, payload)
        assert npm.entry_npm_config(entry) == config
    assert sync.call_count == 2
    cleanup.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_backup_leaves_npm_unchanged(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Old backup files cannot implicitly disconnect an existing proxy."""
    await setup_ip_ban_manager(hass)
    entry = update_entry_options(hass, **{CONF_NPM: npm_config()})
    cleanup = AsyncMock()
    monkeypatch.setattr(backup, "async_disable_npm", cleanup)
    monkeypatch.setattr(backup, "schedule_npm_sync", Mock())

    await backup.async_apply_config_backup_payload(
        hass, {"format_version": 1, "settings": {}}
    )

    assert npm.entry_npm_config(entry) == npm_config()
    cleanup.assert_not_awaited()


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {"enabledd": False},
        {"enabled": "not-a-bool"},
        {"proxy_host_id": True},
        {"proxy_host_id": -1},
        {"proxy_host_id": 2.5},
        {"token": "token-without-url"},
        {"base_url": "http://npm.example.test"},
        {"enabled": True},
        {"token": "secret\nheader"},
        {"identity": ["admin"]},
        {"base_url": "http://user:password@npm.example.test", "token": "secret"},
        {"base_url": "file:///etc/passwd", "token": "secret"},
        {"base_url": "http://npm.example.test:bad", "token": "secret"},
        {"base_url": "http://npm.example.test:0", "token": "secret"},
    ],
)
def test_reject_malformed_npm_backup(value: object) -> None:
    """Reject malformed fields without exposing credential values in errors."""
    with pytest.raises(HomeAssistantError) as error:
        backup._npm_from_import({CONF_NPM: value})
    assert "secret" not in str(error.value)
    assert "password@" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect", [False, True])
async def test_import_can_disable_or_disconnect_after_manual_cleanup(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, disconnect: bool
) -> None:
    """An edited backup disables safely without writing already removed rules."""
    await setup_ip_ban_manager(hass)
    entry = update_entry_options(hass, **{CONF_NPM: npm_config()})
    client = AsyncMock()
    client.refresh_token.return_value = {"token": "renewed-token"}
    client.proxy_hosts.return_value = [
        {
            "id": 4,
            "domain_names": ["ha.example.test"],
            "advanced_config": "keep-user-config;",
            "access_list_id": 0,
        }
    ]
    monkeypatch.setattr(npm, "NpmClient", lambda *args: client)
    monkeypatch.setattr(backup, "schedule_npm_sync", Mock())
    imported = {} if disconnect else npm_config(False)
    payload = {"format_version": 2, "settings": {CONF_NPM: imported}}

    await backup.async_apply_config_backup_payload(hass, payload)
    await backup.async_apply_config_backup_payload(hass, payload)

    result = npm.entry_npm_config(entry)
    assert not result.get("enabled")
    if disconnect:
        assert result == {}
    else:
        assert result["base_url"] == imported["base_url"]
        assert result["token"] == "renewed-token"
    client.update_proxy_host_policy.assert_not_awaited()
    client.refresh_token.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_apply_backup(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failed remote removal retains the original options and connection."""
    await setup_ip_ban_manager(hass)
    entry = update_entry_options(hass, **{CONF_NPM: npm_config()})
    before = dict(entry.options)
    cleanup = AsyncMock(side_effect=HomeAssistantError("NPM rejected cleanup"))
    sync = Mock()
    monkeypatch.setattr(backup, "async_disable_npm", cleanup)
    monkeypatch.setattr(backup, "schedule_npm_sync", sync)

    with pytest.raises(HomeAssistantError, match="NPM rejected cleanup"):
        await backup.async_apply_config_backup_payload(
            hass,
            {
                "format_version": 2,
                "settings": {
                    CONF_NPM: npm_config(False),
                    "login_attempts_threshold": 9,
                },
            },
        )

    assert dict(entry.options) == before
    sync.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_other_settings_prevent_remote_cleanup(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validate the whole backup before changing remote edge protection."""
    await setup_ip_ban_manager(hass)
    entry = update_entry_options(hass, **{CONF_NPM: npm_config()})
    before = dict(entry.options)
    cleanup = AsyncMock()
    monkeypatch.setattr(backup, "async_disable_npm", cleanup)

    with pytest.raises(HomeAssistantError):
        await backup.async_apply_config_backup_payload(
            hass,
            {
                "format_version": 2,
                "settings": {CONF_NPM: {}, "login_attempts_threshold": "invalid"},
            },
        )

    assert dict(entry.options) == before
    cleanup.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_backup_includes_npm_credentials(hass: HomeAssistant) -> None:
    """The on-disk backup includes the same NPM settings as the download."""
    await setup_ip_ban_manager(hass)
    update_entry_options(hass, **{CONF_NPM: npm_config()})

    saved = await backup.async_export_config(hass)

    assert isinstance(saved, Path)
    payload = yaml.safe_load(saved.read_text(encoding="utf8"))
    assert payload["settings"][CONF_NPM] == npm_config()
    assert "test-token" in saved.read_text(encoding="utf8")


@pytest.mark.asyncio
async def test_changing_npm_server_cleans_previous_host_first(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Moving a connection cannot abandon enabled rules or reuse its token."""
    await setup_ip_ban_manager(hass)
    entry = update_entry_options(hass, **{CONF_NPM: npm_config()})
    target = {
        **npm_config(),
        "base_url": "http://other.example.test:81",
        "token": "other-token",
    }

    async def cleanup(target_hass: HomeAssistant) -> None:
        assert target_hass is hass
        assert npm.entry_npm_config(entry) == npm_config()
        update_entry_options(
            hass, **{CONF_NPM: {**npm_config(False), "token": "renewed-old-token"}}
        )

    remove = AsyncMock(side_effect=cleanup)
    sync = Mock()
    monkeypatch.setattr(backup, "async_disable_npm", remove)
    monkeypatch.setattr(backup, "schedule_npm_sync", sync)

    await backup.async_apply_config_backup_payload(
        hass, {"format_version": 2, "settings": {CONF_NPM: target}}
    )

    remove.assert_awaited_once()
    assert npm.entry_npm_config(entry) == target
    sync.assert_called_once_with(hass)
