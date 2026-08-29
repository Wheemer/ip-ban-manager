"""Tests for translation locale coverage and parity."""

from __future__ import annotations

import json
import re
from pathlib import Path

from custom_components.ip_ban_manager.i18n import SUPPORTED_LOCALES

INTEGRATION_DIR = (
    Path(__file__).resolve().parents[2] / "custom_components" / "ip_ban_manager"
)
TRANSLATIONS_DIR = INTEGRATION_DIR / "translations"
PANEL_TRANSLATIONS_DIR = INTEGRATION_DIR / "panel_translations"
PLACEHOLDER_PATTERN = re.compile(r"\{([a-zA-Z0-9_]+)\}")

ALLOWED_ENGLISH_HA_PATHS = {
    "config.error.unknown",
    "config.step.user.data.ban_options",
    "options.step.init.sections.banned_ips.data.ban_options",
    "selector.quick_allowlist.options.localhost",
}
ALLOWED_ENGLISH_PANEL_PATHS = {
    "backup.download",
    "backup.transfer_title",
    "backup.upload",
    "geoip.download",
    "npm.identity",
    "npm.secret",
    "npm.title",
    "options",
    "sources.panel",
    "sources.service",
    "sources.yaml",
    "title",
}


def _flatten_leaf_keys(value: object, prefix: str = "") -> set[str]:
    if not isinstance(value, dict):
        return set()
    keys: set[str] = set()
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, dict):
            keys.update(_flatten_leaf_keys(item, path))
        elif isinstance(item, str):
            keys.add(path)
    return keys


def _flatten_strings(value: object, prefix: str = "") -> dict[str, str]:
    """Return dotted paths and values for every translated string leaf."""
    if not isinstance(value, dict):
        return {}
    strings: dict[str, str] = {}
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, dict):
            strings.update(_flatten_strings(item, path))
        elif isinstance(item, str):
            strings[path] = item
    return strings


def _assert_placeholder_parity(
    english: dict[str, str], locale: dict[str, str], language: str
) -> None:
    """Assert translated strings preserve every English placeholder name."""
    for path, english_value in english.items():
        expected = set(PLACEHOLDER_PATTERN.findall(english_value))
        actual = set(PLACEHOLDER_PATTERN.findall(locale[path]))
        assert actual == expected, (
            f"{language} placeholder mismatch at {path}: "
            f"expected {sorted(expected)}, got {sorted(actual)}"
        )


def test_supported_locales_have_full_translation_files() -> None:
    """Test every supported locale ships a complete translation file."""
    english = json.loads((TRANSLATIONS_DIR / "en.json").read_text(encoding="utf-8"))
    english_keys = _flatten_leaf_keys(english)

    for language in sorted(SUPPORTED_LOCALES):
        path = TRANSLATIONS_DIR / f"{language}.json"
        assert path.is_file(), f"missing locale file: {language}.json"
        locale = json.loads(path.read_text(encoding="utf-8"))
        locale_keys = _flatten_leaf_keys(locale)
        missing = english_keys - locale_keys
        assert not missing, f"{language}.json missing keys: {sorted(missing)[:5]}"


def test_supported_locales_preserve_ha_placeholders_and_translate_prose() -> None:
    """Test HA locale values preserve placeholders and localize feature prose."""
    english = _flatten_strings(
        json.loads((TRANSLATIONS_DIR / "en.json").read_text(encoding="utf-8"))
    )

    for language in sorted(SUPPORTED_LOCALES):
        locale = _flatten_strings(
            json.loads(
                (TRANSLATIONS_DIR / f"{language}.json").read_text(encoding="utf-8")
            )
        )
        _assert_placeholder_parity(english, locale, language)
        untranslated = {
            path
            for path, value in locale.items()
            if value == english[path] and path not in ALLOWED_ENGLISH_HA_PATHS
        }
        assert not untranslated, (
            f"{language}.json contains unexpected English values: "
            f"{sorted(untranslated)[:5]}"
        )


def test_supported_locales_have_full_panel_translation_files() -> None:
    """Test every supported locale ships a complete panel translation file."""
    english = json.loads(
        (PANEL_TRANSLATIONS_DIR / "en.json").read_text(encoding="utf-8")
    )
    english_keys = _flatten_leaf_keys(english)

    for language in sorted(SUPPORTED_LOCALES):
        path = PANEL_TRANSLATIONS_DIR / f"{language}.json"
        assert path.is_file(), f"missing panel locale file: {language}.json"
        locale = json.loads(path.read_text(encoding="utf-8"))
        locale_keys = _flatten_leaf_keys(locale)
        missing = english_keys - locale_keys
        assert not missing, f"panel/{language}.json missing keys: {sorted(missing)[:5]}"


def test_supported_panel_locales_preserve_placeholders_and_translate_prose() -> None:
    """Test panel locales preserve placeholders and localize feature prose."""
    english = _flatten_strings(
        json.loads((PANEL_TRANSLATIONS_DIR / "en.json").read_text(encoding="utf-8"))
    )

    for language in sorted(SUPPORTED_LOCALES):
        locale = _flatten_strings(
            json.loads(
                (PANEL_TRANSLATIONS_DIR / f"{language}.json").read_text(
                    encoding="utf-8"
                )
            )
        )
        _assert_placeholder_parity(english, locale, f"panel/{language}")
        untranslated = {
            path
            for path, value in locale.items()
            if value == english[path] and path not in ALLOWED_ENGLISH_PANEL_PATHS
        }
        assert not untranslated, (
            f"panel/{language}.json contains unexpected English values: "
            f"{sorted(untranslated)[:5]}"
        )


def test_ha_translation_files_exclude_panel_section() -> None:
    """Test Home Assistant locale files do not include custom panel strings."""
    for path in sorted(TRANSLATIONS_DIR.glob("*.json")):
        locale = json.loads(path.read_text(encoding="utf-8"))
        assert "panel" not in locale, f"{path.name} must not contain a panel section"


def test_supported_locale_count() -> None:
    """Test we ship the expected number of non-English locales."""
    assert len(SUPPORTED_LOCALES) == 25
