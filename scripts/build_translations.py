"""Build full locale files from en.json and per-language overlay JSON files."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_DIR = ROOT / "custom_components" / "ip_ban_manager"
TRANSLATIONS_DIR = INTEGRATION_DIR / "translations"
PANEL_TRANSLATIONS_DIR = INTEGRATION_DIR / "panel_translations"
OVERLAY_DIR = Path(__file__).resolve().parent / "translation_overlays"
BAN_FILE_HEALTH_PATH = (
    Path(__file__).resolve().parent / "ban_file_health_translations.json"
)

# English reference plus every non-English locale we ship.
SUPPORTED_LOCALES: tuple[str, ...] = (
    "de",
    "fr",
    "nl",
    "es",
    "it",
    "pl",
    "pt",
    "pt-BR",
    "sv",
    "cs",
    "ru",
    "uk",
    "tr",
    "zh-Hans",
    "zh-Hant",
    "ja",
    "ko",
    "nb",
    "da",
    "fi",
    "ca",
    "hu",
    "sk",
    "ro",
    "el",
)


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge overlay values into base."""
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def flatten_leaf_keys(value: object, prefix: str = "") -> set[str]:
    """Return dotted paths for every string leaf in a translation tree."""
    if not isinstance(value, dict):
        return set()
    keys: set[str] = set()
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, dict):
            keys.update(flatten_leaf_keys(item, path))
        elif isinstance(item, str):
            keys.add(path)
    return keys


def validate_locale(en: dict[str, Any], locale: dict[str, Any], language: str) -> None:
    """Ensure a generated locale covers every translatable leaf in en.json."""
    missing = flatten_leaf_keys(en) - flatten_leaf_keys(locale)
    if missing:
        sample = ", ".join(sorted(missing)[:8])
        raise ValueError(
            f"{language}.json is missing {len(missing)} translation key(s), "
            f"including: {sample}"
        )


def load_overlay(language: str) -> dict[str, Any]:
    """Load one language overlay JSON file."""
    path = OVERLAY_DIR / f"{language}.json"
    if not path.is_file():
        raise FileNotFoundError(f"missing overlay file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_ban_file_health_translations() -> dict[str, dict[str, str]]:
    """Load per-locale ban-file health issue strings."""
    return json.loads(BAN_FILE_HEALTH_PATH.read_text(encoding="utf-8"))


def split_panel_overlay(
    overlay: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return overlay copies with and without the custom panel section."""
    ha_overlay = copy.deepcopy(overlay)
    panel_overlay = ha_overlay.pop("panel", None)
    if not isinstance(panel_overlay, dict):
        panel_overlay = {}
    return ha_overlay, panel_overlay


def apply_ban_file_health(panel: dict[str, Any], language: str) -> None:
    """Merge localized ban-file health strings and drop legacy placeholder key."""
    issues = panel.setdefault("health", {}).setdefault("issues", {})
    issues.pop("ban_file_access", None)
    translations = load_ban_file_health_translations()
    if language in translations:
        issues.update(translations[language])


def build_locale(
    en_ha: dict[str, Any],
    en_panel: dict[str, Any],
    language: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return Home Assistant and panel locale files for one language."""
    ha_overlay, panel_overlay = split_panel_overlay(load_overlay(language))
    ha_locale = deep_merge(en_ha, ha_overlay)
    panel_locale = deep_merge(en_panel, panel_overlay)
    apply_ban_file_health(panel_locale, language)
    validate_locale(en_ha, ha_locale, language)
    validate_locale(en_panel, panel_locale, f"panel/{language}")
    return ha_locale, panel_locale


def main() -> int:
    """Generate all supported locale files."""
    en_ha = json.loads((TRANSLATIONS_DIR / "en.json").read_text(encoding="utf-8"))
    en_panel_path = PANEL_TRANSLATIONS_DIR / "en.json"
    if en_panel_path.is_file():
        en_panel = json.loads(en_panel_path.read_text(encoding="utf-8"))
    else:
        en_panel = en_ha.pop("panel", {})
        if not isinstance(en_panel, dict):
            en_panel = {}
    if "panel" in en_ha:
        raise ValueError(
            "translations/en.json must not contain a top-level panel section; "
            "run scripts/split_panel_translations.py first"
        )
    missing_overlays = [
        language
        for language in SUPPORTED_LOCALES
        if not (OVERLAY_DIR / f"{language}.json").is_file()
    ]
    if missing_overlays:
        print(
            "missing overlay files: " + ", ".join(missing_overlays),
            file=sys.stderr,
        )
        return 1

    PANEL_TRANSLATIONS_DIR.mkdir(parents=True, exist_ok=True)

    for language in SUPPORTED_LOCALES:
        ha_locale, panel_locale = build_locale(en_ha, en_panel, language)
        (TRANSLATIONS_DIR / f"{language}.json").write_text(
            json.dumps(ha_locale, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (PANEL_TRANSLATIONS_DIR / f"{language}.json").write_text(
            json.dumps(panel_locale, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"updated {language}.json and panel_translations/{language}.json")

    print(f"built {len(SUPPORTED_LOCALES)} locales")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
