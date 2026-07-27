"""Tests for translation locale coverage and parity."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.ip_ban_manager.i18n import SUPPORTED_LOCALES

INTEGRATION_DIR = (
    Path(__file__).resolve().parents[2] / "custom_components" / "ip_ban_manager"
)
TRANSLATIONS_DIR = INTEGRATION_DIR / "translations"
PANEL_TRANSLATIONS_DIR = INTEGRATION_DIR / "panel_translations"


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


def test_ha_translation_files_exclude_panel_section() -> None:
    """Test Home Assistant locale files do not include custom panel strings."""
    for path in sorted(TRANSLATIONS_DIR.glob("*.json")):
        locale = json.loads(path.read_text(encoding="utf-8"))
        assert "panel" not in locale, f"{path.name} must not contain a panel section"


def test_supported_locale_count() -> None:
    """Test we ship the expected number of non-English locales."""
    assert len(SUPPORTED_LOCALES) == 25
