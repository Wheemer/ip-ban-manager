#!/usr/bin/env python3
"""Move options.init data_description keys under section blocks for hassfest."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    ROOT / "custom_components" / "ip_ban_manager" / "strings.json",
    ROOT / "custom_components" / "ip_ban_manager" / "translations" / "en.json",
    *sorted((Path(__file__).resolve().parent / "translation_overlays").glob("*.json")),
]


def fix_file(path: Path) -> bool:
    """Return True when the file was modified."""
    data = json.loads(path.read_text(encoding="utf-8"))
    init = data.get("options", {}).get("step", {}).get("init")
    if not isinstance(init, dict):
        return False
    descriptions = init.pop("data_description", None)
    if not isinstance(descriptions, dict):
        return False
    sections = init.get("sections")
    if not isinstance(sections, dict):
        raise ValueError(f"{path}: options.step.init is missing sections")

    allowed = sections.setdefault("allowed_ips", {})
    banned = sections.setdefault("banned_ips", {})
    if not isinstance(allowed, dict) or not isinstance(banned, dict):
        raise ValueError(f"{path}: invalid section structure")

    allowed_desc = allowed.setdefault("data_description", {})
    banned_desc = banned.setdefault("data_description", {})
    if not isinstance(allowed_desc, dict) or not isinstance(banned_desc, dict):
        raise ValueError(f"{path}: invalid section data_description")

    if "allowed_ips" in descriptions:
        allowed_desc["allowed_ips"] = descriptions["allowed_ips"]
    if "banned_ips" in descriptions:
        banned_desc["banned_ips"] = descriptions["banned_ips"]
    if "blocked_networks" in descriptions:
        banned_desc["blocked_networks"] = descriptions["blocked_networks"]

    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def main() -> int:
    changed = 0
    for path in TARGETS:
        if fix_file(path):
            print(f"fixed {path.relative_to(ROOT)}")
            changed += 1
    print(f"updated {changed} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
