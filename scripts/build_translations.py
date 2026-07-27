"""Build full locale files from en.json and per-language overlay JSON files."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TRANSLATIONS_DIR = ROOT / "custom_components" / "ip_ban_manager" / "translations"
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


def apply_ban_file_health(locale: dict[str, Any], language: str) -> None:
    """Merge localized ban-file health strings and drop legacy placeholder key."""
    issues = (
        locale.setdefault("panel", {}).setdefault("health", {}).setdefault("issues", {})
    )
    issues.pop("ban_file_access", None)
    translations = load_ban_file_health_translations()
    if language in translations:
        issues.update(translations[language])


def build_locale(en: dict[str, Any], language: str) -> dict[str, Any]:
    """Return one fully merged locale file."""
    merged = deep_merge(en, load_overlay(language))
    apply_ban_file_health(merged, language)
    validate_locale(en, merged, language)
    return merged


def main() -> int:
    """Generate all supported locale files."""
    en = json.loads((TRANSLATIONS_DIR / "en.json").read_text(encoding="utf-8"))
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

    for language in SUPPORTED_LOCALES:
        locale = build_locale(en, language)
        (TRANSLATIONS_DIR / f"{language}.json").write_text(
            json.dumps(locale, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"updated {language}.json")

    print(f"built {len(SUPPORTED_LOCALES)} locales")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
