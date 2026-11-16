"""Validate the HACS release zip structure."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from zipfile import ZipFile

REPO_ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_DIR = REPO_ROOT / "custom_components" / "ip_ban_manager"


def expected_integration_files() -> set[str]:
    """Return files that must be present in the flat HACS release zip."""
    return {
        path.relative_to(INTEGRATION_DIR).as_posix()
        for path in INTEGRATION_DIR.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }


def main() -> int:
    """Return 0 when the release zip has the HACS integration layout."""
    if len(sys.argv) != 2:
        print("usage: validate_hacs_zip.py <zip-path>", file=sys.stderr)
        return 2

    zip_path = Path(sys.argv[1])
    with ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        files = [item.filename for item in archive.infolist() if not item.is_dir()]
        unexpected = set(files) - expected_integration_files()
        if unexpected or len(files) != len(set(files)):
            print("zip contains unexpected or duplicate files", file=sys.stderr)
            return 1
        if any(
            archive.read(name) != (INTEGRATION_DIR / name).read_bytes()
            for name in files
        ):
            print("zip content differs from the checked-out source", file=sys.stderr)
            return 1
        if "manifest.json" in names:
            packaged = json.loads(archive.read("manifest.json"))
            expected = json.loads((INTEGRATION_DIR / "manifest.json").read_text())
            if packaged.get("version") != expected["version"]:
                print("zip manifest version differs from the source", file=sys.stderr)
                return 1

    missing = sorted(expected_integration_files() - names)
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
