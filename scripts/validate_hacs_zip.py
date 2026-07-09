"""Validate the HACS release zip structure."""

from __future__ import annotations

import sys
from pathlib import Path
from zipfile import ZipFile

REQUIRED_ROOT_FILES = {
    "__init__.py",
    "config_flow.py",
    "manifest.json",
    "sensor.py",
    "services.yaml",
    "strings.json",
    "translations/en.json",
}


def main() -> int:
    """Return 0 when the release zip has the HACS integration layout."""
    if len(sys.argv) != 2:
        print("usage: validate_hacs_zip.py <zip-path>", file=sys.stderr)
        return 2

    zip_path = Path(sys.argv[1])
    with ZipFile(zip_path) as archive:
        names = set(archive.namelist())

    missing = sorted(REQUIRED_ROOT_FILES - names)
    nested = sorted(name for name in names if name.startswith("custom_components/"))
    caches = sorted(
        name for name in names if "__pycache__/" in name or name.endswith(".pyc")
    )

    if missing:
        print(f"missing required root files: {', '.join(missing)}", file=sys.stderr)
        return 1
    if nested:
        print("zip must not contain custom_components/ nesting", file=sys.stderr)
        return 1
    if caches:
        print("zip must not contain Python cache files", file=sys.stderr)
        return 1

    print(f"{zip_path} has a valid HACS integration zip layout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
