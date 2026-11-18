"""Skip runtime suites only for an explicitly recognized documentation-only diff."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterable
from pathlib import PurePosixPath

_ROOT_DOCS = frozenset(
    {
        "README.md",
        "CHANGELOG.md",
        "RELEASES.md",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "CODE_OF_CONDUCT.md",
        "LICENSE",
    }
)
_IMAGES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"})


def documentation_only(paths: Iterable[str]) -> bool:
    """Recognize only documentation paths; empty or uncertain changes run tests."""
    changed = list(paths)
    if not changed:
        return False
    for value in changed:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            return False
        if value in _ROOT_DOCS:
            continue
        if (
            len(path.parts) > 1
            and path.parts[0] == "docs"
            and path.suffix.lower() == ".md"
        ):
            continue
        if path.parts[:2] == ("docs", "images") and path.suffix.lower() in _IMAGES:
            continue
        return False
    return True


def runtime_required(event: str, base: str, head: str) -> bool:
    """Run tests by default, including schedules, missing history, and renames."""
    if event not in {"pull_request", "push"}:
        return True
    if any(
        not re.fullmatch(r"[0-9a-f]{40,64}", sha) or not sha.strip("0")
        for sha in (base, head)
    ):
        return True
    try:
        result = subprocess.run(
            ["git", "diff", "--no-renames", "--name-only", "-z", base, head, "--"],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return True
    paths = os.fsdecode(result.stdout).split("\0")
    return not documentation_only(path for path in paths if path)


def main() -> None:
    """Emit one GitHub Actions output; unknown inputs cannot skip runtime checks."""
    required = runtime_required(
        os.getenv("GITHUB_EVENT_NAME", ""),
        os.getenv("CI_BASE_SHA", ""),
        os.getenv("CI_HEAD_SHA", ""),
    )
    print(f"runtime={str(required).lower()}")


if __name__ == "__main__":
    main()
