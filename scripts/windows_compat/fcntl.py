"""Minimal Windows test stub for Home Assistant's imported fcntl module."""

LOCK_EX = 2
LOCK_NB = 4
LOCK_UN = 8


def flock(_file_descriptor: int, _operation: int) -> None:
    """Pretend to acquire or release a file lock during local Windows tests."""
