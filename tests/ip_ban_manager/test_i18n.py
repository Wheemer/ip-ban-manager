"""Tests for panel translation loading."""

from __future__ import annotations

from pathlib import Path

from custom_components.ip_ban_manager.i18n import (
    SUPPORTED_LOCALES,
    format_health_issue_message,
    load_health_issue_strings,
    load_panel_translations,
    normalize_language,
    resolve_translation_language,
)


def test_normalize_language_uses_primary_subtag() -> None:
    """Test locale tags resolve to shipped translation files."""
    assert normalize_language("de-DE") == "de"
    assert normalize_language(None) == "en"
    assert resolve_translation_language("zh-Hans") == "zh-Hans"
    assert resolve_translation_language("zh-Hant") == "zh-Hant"
    assert resolve_translation_language("pt-BR") == "pt-BR"
    assert resolve_translation_language("pt") == "pt"


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
