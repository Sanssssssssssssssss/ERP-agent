"""Package version helpers."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

_DISTRIBUTION_NAME = "erp-harness"
_UNKNOWN_VERSION = "0+unknown"


def current_version() -> str:
    """Return Pi's installed package version from package metadata."""
    try:
        return version(_DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return _UNKNOWN_VERSION
