"""Locations of Pi's packaged self-documentation and examples."""

from __future__ import annotations

from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent
_DATA_ROOT = _PACKAGE_ROOT / "data"


def pi_readme_path() -> Path:
    """Return the installed overview document for Pi-aware tasks."""
    return _DATA_ROOT / "docs" / "README.md"


def pi_docs_path() -> Path:
    """Return the installed Pi self-documentation directory."""
    return _DATA_ROOT / "docs"


def pi_examples_path() -> Path:
    """Return the installed Pi example directory."""
    return _DATA_ROOT / "examples"
