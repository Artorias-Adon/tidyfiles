"""Platform paths and small display helpers."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "TidyFiles"


def data_dir() -> Path:
    """Return the per-user directory used for settings and reports."""

    if os.name == "nt":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME")
        root = Path(base) if base else Path.home() / ".local" / "share"
    return root / APP_NAME


def reports_dir() -> Path:
    return data_dir() / "reports"


def settings_path() -> Path:
    return data_dir() / "settings.json"


def human_size(size: int) -> str:
    """Format a byte count using compact binary units."""

    value = float(max(0, size))
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            if value >= 100:
                return f"{value:.0f} {unit}"
            if value >= 10:
                return f"{value:.1f} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def middle_ellipsis(text: str, limit: int = 54) -> str:
    """Shorten a path-like string while keeping both ends visible."""

    if len(text) <= limit:
        return text
    if limit < 7:
        return text[:limit]
    left = (limit - 1) // 2
    right = limit - left - 1
    return f"{text[:left]}…{text[-right:]}"


def tail_ellipsis(text: str, limit: int = 54) -> str:
    """Shorten a path while preserving its most specific trailing part."""

    if len(text) <= limit:
        return text
    if limit < 2:
        return text[-limit:] if limit else ""
    return "…" + text[-(limit - 1) :]


def resolve_path(value: str | os.PathLike[str]) -> Path:
    """Expand ``~`` and return an absolute path without requiring existence."""

    path = Path(value).expanduser()
    try:
        return path.resolve(strict=False)
    except OSError:
        return path.absolute()
