"""Extract overlay JSON files from existing full locale translations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TRANSLATIONS_DIR = ROOT / "custom_components" / "ip_ban_manager" / "translations"
OVERLAY_DIR = Path(__file__).resolve().parent / "translation_overlays"


def extract_overlay(en: dict[str, Any], locale: dict[str, Any]) -> dict[str, Any]:
    """Return only branches that differ from the English reference."""
    overlay: dict[str, Any] = {}
    for key, value in locale.items():
        if key not in en:
            overlay[key] = value
            continue
        english_value = en[key]
        if isinstance(value, dict) and isinstance(english_value, dict):
            nested = extract_overlay(english_value, value)
            if nested:
                overlay[key] = nested
        elif value != english_value:
            overlay[key] = value
    return overlay


def main() -> None:
    """Write overlay files for locales that already exist on disk."""
    OVERLAY_DIR.mkdir(parents=True, exist_ok=True)
    en = json.loads((TRANSLATIONS_DIR / "en.json").read_text(encoding="utf-8"))
    for path in sorted(TRANSLATIONS_DIR.glob("*.json")):
        language = path.stem
        if language == "en":
            continue
        locale = json.loads(path.read_text(encoding="utf-8"))
        overlay = extract_overlay(en, locale)
        (OVERLAY_DIR / f"{language}.json").write_text(
            json.dumps(overlay, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"extracted {language}.json")


if __name__ == "__main__":
    main()
