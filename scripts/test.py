"""Create the local test environment and run pytest."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / (".venv-win" if os.name == "nt" else ".venv")
UV_CACHE = ROOT / ".uv-cache"
VENV_PYTHON = (
    VENV / "Scripts" / "python.exe"
    if os.name == "nt"
    else VENV / "bin" / "python"
)


def run(command: list[str], env: dict[str, str] | None = None) -> None:
    """Run a command from the repository root."""
    subprocess.run(command, cwd=ROOT, check=True, env=env)


def create_venv(env: dict[str, str]) -> None:
    """Create a fresh local virtual environment."""
    run(["uv", "venv", "--clear", "--python", "3.13", str(VENV)], env=env)


def venv_can_import() -> bool:
    """Return whether the existing venv can import packages from site-packages."""
    if not VENV_PYTHON.exists():
        return False

    result = subprocess.run(
        [str(VENV_PYTHON), "-c", "import multidict"],
        cwd=ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def main() -> int:
    """Ensure the test environment exists, then run pytest."""
    uv_env = os.environ.copy()
    uv_env.setdefault("UV_CACHE_DIR", str(UV_CACHE))

    args = sys.argv[1:]
    recreate = "--recreate" in args
    args = [arg for arg in args if arg != "--recreate"]

    if recreate or not venv_can_import():
        create_venv(uv_env)

    run(
        [
            "uv",
            "pip",
            "sync",
            "--python",
            str(VENV_PYTHON),
            "--strict",
            "requirements.test",
        ],
        env=uv_env,
    )
    env = os.environ.copy()
    python_path = [str(ROOT)]
    if os.name == "nt":
        python_path.insert(0, str(ROOT / "scripts" / "windows_compat"))
    if existing_python_path := env.get("PYTHONPATH"):
        python_path.append(existing_python_path)
    env["PYTHONPATH"] = os.pathsep.join(python_path)

    run([str(VENV_PYTHON), "-m", "pytest", *(args or ["-vvv"])], env=env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
