"""Minimal Windows test stub for Home Assistant's imported resource module."""

RLIMIT_NOFILE = 0
_LIMITS = (2048, 2048)


def getrlimit(_resource: int) -> tuple[int, int]:
    """Return a harmless file descriptor limit for local Windows tests."""
    return _LIMITS


def setrlimit(_resource: int, limits: tuple[int, int]) -> None:
    """Store a harmless file descriptor limit for local Windows tests."""
    global _LIMITS
    _LIMITS = limits
