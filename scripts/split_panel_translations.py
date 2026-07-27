#!/usr/bin/env python3
"""Extract panel strings into panel_translations/ for hassfest compliance."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_DIR = ROOT / "custom_components" / "ip_ban_manager"
TRANSLATIONS_DIR = INTEGRATION_DIR / "translations"
PANEL_TRANSLATIONS_DIR = INTEGRATION_DIR / "panel_translations"
STRINGS_PATH = INTEGRATION_DIR / "strings.json"


def extract_panel(path: Path, language: str) -> bool:
    """Move the panel section into panel_translations and return True if changed."""
    data = json.loads(path.read_text(encoding="utf-8"))
    panel = data.pop("panel", None)
    if panel is None:
        return False
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    panel_path = PANEL_TRANSLATIONS_DIR / f"{language}.json"
    panel_path.write_text(
        json.dumps(panel, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def main() -> int:
    """Split panel strings out of Home Assistant translation files."""
    PANEL_TRANSLATIONS_DIR.mkdir(parents=True, exist_ok=True)
    changed = 0
    if extract_panel(TRANSLATIONS_DIR / "en.json", "en"):
        changed += 1
        print("split translations/en.json")
    if STRINGS_PATH.is_file() and extract_panel(STRINGS_PATH, "en"):
        changed += 1
        print("split strings.json")
    for path in sorted(TRANSLATIONS_DIR.glob("*.json")):
        if path.name == "en.json":
            continue
        language = path.stem
        if extract_panel(path, language):
            changed += 1
            print(f"split translations/{path.name}")
    print(f"updated {changed} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
