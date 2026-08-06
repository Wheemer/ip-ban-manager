"""Integration-owned file and path helpers."""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN

GEOIP_DIR = "geoip"
GEOIP_FILENAME = "dbip-city-lite.mmdb"
CONFIG_EXPORT_FILENAME = "ip-ban-manager-backup.yaml"
SNAPSHOT_DIR = "snapshots"
SNAPSHOT_KEEP = 3


def atomic_write_text(path: str, content: str) -> None:
    """Write text to a file using an atomic same-directory replacement."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: str | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = temp_file.name
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, target)
    finally:
        if temp_path is not None and os.path.exists(temp_path):
            os.unlink(temp_path)


def snapshot_dir(hass: HomeAssistant) -> Path:
    """Return the integration-owned snapshot directory."""
    return Path(hass.config.path(DOMAIN, SNAPSHOT_DIR))


def snapshot_existing_file(path: Path, snapshots: Path) -> bool:
    """Keep a small local snapshot before replacing or deleting a managed file."""
    if not path.is_file():
        return False

    snapshots.mkdir(parents=True, exist_ok=True)
    timestamp = dt_util.utcnow().strftime("%Y%m%d%H%M%S%f")
    snapshot_path = snapshots / f"{path.name}.{timestamp}.bak"
    shutil.copy2(path, snapshot_path)

    existing_snapshots = sorted(
        snapshots.glob(f"{path.name}.*.bak"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for stale_snapshot in existing_snapshots[SNAPSHOT_KEEP:]:
        stale_snapshot.unlink(missing_ok=True)
    return True


def geoip_database_path(hass: HomeAssistant) -> Path:
    """Return the local GeoIP database path owned by this integration."""
    return Path(hass.config.path(DOMAIN, GEOIP_DIR, GEOIP_FILENAME))


def config_export_path(hass: HomeAssistant) -> Path:
    """Return the manual config export path owned by this integration."""
    return Path(hass.config.path(DOMAIN, CONFIG_EXPORT_FILENAME))


def ha_config_relative_path(path: Path) -> str:
    """Return a Home Assistant-style display path for a file under /config."""
    return f"/config/{path.parent.name}/{path.name}"


def path_is_file(path: Path) -> bool:
    """Return whether a path is a file."""
    return path.is_file()


def file_updated(path: Path) -> str | None:
    """Return the file modification time for status surfaces."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, dt_util.UTC).isoformat()
    except OSError:
        return None
