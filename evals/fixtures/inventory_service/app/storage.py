"""Filesystem helpers for exported reports and other downloadable files."""

from __future__ import annotations

from pathlib import Path

EXPORT_DIR = Path("/var/lib/inventory/exports")


def safe_join(base: Path, name: str) -> Path:
    """Join `name` onto `base`, refusing anything that escapes `base`."""
    candidate = (base / name).resolve()
    if not candidate.is_relative_to(base.resolve()):
        raise ValueError(f"path escapes {base}: {name!r}")
    return candidate
