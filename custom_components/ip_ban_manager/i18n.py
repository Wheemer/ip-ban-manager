"""Panel translation loading for IP Ban Manager."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_TRANSLATIONS_DIR = Path(__file__).parent / "translations"

# Non-English locale files shipped with the integration (see scripts/build_translations.py).
SUPPORTED_LOCALES: frozenset[str] = frozenset(
    {
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
    }
)


def _flatten_strings(value: object, prefix: str = "") -> dict[str, str]:
    """Flatten nested translation mappings into dotted lookup keys."""
    if not isinstance(value, dict):
        return {}
    flattened: dict[str, str] = {}
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, dict):
            flattened.update(_flatten_strings(item, path))
        elif isinstance(item, str):
            flattened[path] = item
    return flattened


def _load_translation_file(language: str) -> dict[str, Any]:
    """Load one locale JSON file when present."""
    path = _TRANSLATIONS_DIR / f"{language}.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_panel_language(language: str) -> dict[str, str]:
    """Return flattened panel strings for one language file."""
    data = _load_translation_file(language)
    panel = data.get("panel")
    if not isinstance(panel, dict):
        return {}
    return _flatten_strings(panel)


def normalize_language(language: str | None) -> str:
    """Return the translation file code for a Home Assistant locale tag."""
    return resolve_translation_language(language)


def resolve_translation_language(language: str | None) -> str:
    """Resolve a locale tag to an on-disk translation file, falling back to English."""
    if not language:
        return "en"
    tag = language.strip().replace("_", "-")
    if not tag:
        return "en"

    candidates: list[str] = []

    def add(code: str) -> None:
        if code and code not in candidates:
            candidates.append(code)

    add(tag)
    add(tag.lower())
    if "-" in tag:
        primary, region = tag.split("-", 1)
        add(f"{primary.lower()}-{region}")
        if len(region) == 2:
            add(f"{primary.lower()}-{region.upper()}")
        add(primary.lower())
    else:
        add(tag.lower())

    for code in candidates:
        if code in SUPPORTED_LOCALES and (_TRANSLATIONS_DIR / f"{code}.json").is_file():
            return code
    return "en"


def _load_panel_health_issues(language: str) -> dict[str, str]:
    """Return health issue templates for one language file."""
    data = _load_translation_file(language)
    panel = data.get("panel")
    if not isinstance(panel, dict):
        return {}
    health = panel.get("health")
    if not isinstance(health, dict):
        return {}
    issues = health.get("issues")
    if not isinstance(issues, dict):
        return {}
    return {
        key: value
        for key, value in issues.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def load_health_issue_strings(language: str | None) -> dict[str, str]:
    """Return health issue templates for a locale, falling back to English."""
    english = _load_panel_health_issues("en")
    normalized = normalize_language(language)
    if normalized == "en":
        return english
    localized = _load_panel_health_issues(normalized)
    return {**english, **localized}


def format_health_issue_message(
    issue_key: str,
    placeholders: dict[str, str] | None,
    issue_strings: dict[str, str],
) -> str:
    """Return one formatted health issue line for repair summaries."""
    template = issue_strings.get(issue_key, issue_key)
    if not placeholders:
        return template
    message = template
    for name, value in placeholders.items():
        message = message.replace(f"{{{name}}}", value)
    return message


def load_panel_translations(language: str | None) -> dict[str, str]:
    """Return panel strings for a locale, falling back to English."""
    english = _load_panel_language("en")
    normalized = normalize_language(language)
    if normalized == "en":
        return english
    localized = _load_panel_language(normalized)
    return {**english, **localized}
