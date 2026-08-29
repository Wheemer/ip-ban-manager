"""Tests for panel translation loading."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable, cast

from homeassistant.core import HomeAssistant

from custom_components.ip_ban_manager.i18n import (
    SUPPORTED_LOCALES,
    async_load_health_issue_strings,
    async_load_panel_translations,
    async_normalize_language,
    format_health_issue_message,
    load_health_issue_strings,
    load_panel_translations,
    normalize_language,
    resolve_translation_language,
)


class FakeHass:
    """Small executor-job recorder for async i18n wrapper tests."""

    def __init__(self) -> None:
        """Initialize the call recorder."""
        self.executor_calls: list[tuple[Callable[..., object], tuple[object, ...]]] = []

    async def async_add_executor_job(
        self, target: Callable[..., object], *args: object
    ) -> Any:
        """Run an executor target while recording that the wrapper used it."""
        self.executor_calls.append((target, args))
        return target(*args)


def run_async_test(coro: Any) -> Any:
    """Run a coroutine without leaving pytest's event loop unset."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


def test_normalize_language_uses_primary_subtag() -> None:
    """Test locale tags resolve to shipped translation files."""
    assert normalize_language("de-DE") == "de"
    assert normalize_language(None) == "en"
    assert resolve_translation_language("zh-Hans") == "zh-Hans"
    assert resolve_translation_language("zh-Hant") == "zh-Hant"
    assert resolve_translation_language("pt-BR") == "pt-BR"
    assert resolve_translation_language("pt") == "pt"


def test_async_normalize_language_uses_executor_job() -> None:
    """Test locale resolution is kept outside the event loop."""
    hass = FakeHass()

    language = run_async_test(
        async_normalize_language(cast(HomeAssistant, hass), "de-DE")
    )

    assert language == "de"
    assert len(hass.executor_calls) == 1
    assert hass.executor_calls[0][0].__name__ == normalize_language.__name__
    assert hass.executor_calls[0][1] == ("de-DE",)


def test_supported_locales_match_translation_files() -> None:
    """Test the supported locale registry matches shipped panel files."""
    panel_translations_dir = (
        Path(__file__).resolve().parents[2]
        / "custom_components"
        / "ip_ban_manager"
        / "panel_translations"
    )
    for language in SUPPORTED_LOCALES:
        assert (panel_translations_dir / f"{language}.json").is_file()


def test_load_panel_translations_falls_back_to_english() -> None:
    """Test unknown locales still return English panel strings."""
    translations = load_panel_translations("zz")
    assert translations["title"] == "IP Ban Manager"
    assert translations["allowed_ips.title"] == "Allowed IPs"


def test_async_load_panel_translations_uses_executor_job() -> None:
    """Test panel translations are loaded outside the event loop."""
    hass = FakeHass()

    translations = run_async_test(
        async_load_panel_translations(cast(HomeAssistant, hass), "de")
    )

    assert translations["remove"] == "Entfernen"
    assert len(hass.executor_calls) == 1
    assert hass.executor_calls[0][0].__name__ == load_panel_translations.__name__
    assert hass.executor_calls[0][1] == ("de",)


def test_load_panel_translations_overlays_localized_strings() -> None:
    """Test localized panel strings override English defaults."""
    translations = load_panel_translations("de")
    assert translations["title"] == "IP Ban Manager"
    assert translations["remove"] == "Entfernen"
    assert translations["allowed_ips.title"] == "Erlaubte IPs"
    assert (
        translations["health.issues.panel_not_registered"]
        == "Das IP-Ban-Manager-Panel ist nicht registriert."
    )


def test_load_panel_translations_supports_new_locales() -> None:
    """Test newly added locales provide localized panel strings."""
    italian = load_panel_translations("it")
    assert italian["add"] == "Aggiungi"
    chinese = load_panel_translations("zh-Hans")
    assert chinese["loading"] == "加载中..."
    traditional = load_panel_translations("zh-Hant")
    assert traditional["loading"] == "載入中..."
    assert traditional["apply"] == "套用"
    brazilian = load_panel_translations("pt-BR")
    assert brazilian["apply"] == "Aplicar"


def test_format_health_issue_message_uses_locale_and_placeholders() -> None:
    """Test repair health summaries use localized issue templates."""
    issue_strings = load_health_issue_strings("de")
    assert (
        format_health_issue_message("panel_not_registered", None, issue_strings)
        == "Das IP-Ban-Manager-Panel ist nicht registriert."
    )
    assert (
        format_health_issue_message(
            "ban_file_parent_not_writable",
            {"path": "/config"},
            issue_strings,
        )
        == "/config ist nicht beschreibbar."
    )


def test_async_load_health_issue_strings_uses_executor_job() -> None:
    """Test health strings are loaded outside the event loop."""
    hass = FakeHass()

    issue_strings = run_async_test(
        async_load_health_issue_strings(cast(HomeAssistant, hass), "de")
    )

    assert (
        issue_strings["panel_not_registered"]
        == "Das IP-Ban-Manager-Panel ist nicht registriert."
    )
    assert len(hass.executor_calls) == 1
    assert hass.executor_calls[0][0].__name__ == load_health_issue_strings.__name__
    assert hass.executor_calls[0][1] == ("de",)
