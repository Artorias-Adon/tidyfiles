# SPDX-License-Identifier: GPL-3.0-or-later
"""Small, dependency-free persistence for the desktop app."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .categories import CATEGORIES
from .organizer import ConflictPolicy, Operation
from .paths import settings_path

DEFAULT_SETTINGS: dict[str, Any] = {
    "source": "",
    "destination": "",
    "recursive": True,
    "operation": Operation.COPY.value,
    "conflict": ConflictPolicy.RENAME.value,
    "categorized": True,
    "selected_categories": [category.key for category in CATEGORIES],
    "geometry": "",
}


def load_settings(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else settings_path()
    values = dict(DEFAULT_SETTINGS)
    values["selected_categories"] = list(DEFAULT_SETTINGS["selected_categories"])
    try:
        raw = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return values
    if not isinstance(raw, dict):
        return values

    for key in ("source", "destination", "geometry"):
        value = raw.get(key)
        if isinstance(value, str):
            values[key] = value
    for key in ("recursive", "categorized"):
        value = raw.get(key)
        if isinstance(value, bool):
            values[key] = value
    if raw.get("operation") in {item.value for item in Operation}:
        values["operation"] = raw["operation"]
    if raw.get("conflict") in {item.value for item in ConflictPolicy}:
        values["conflict"] = raw["conflict"]

    selected = raw.get("selected_categories")
    if isinstance(selected, list):
        known = {category.key for category in CATEGORIES}
        values["selected_categories"] = [item for item in selected if item in known]
    return values


def save_settings(values: dict[str, Any], path: str | Path | None = None) -> bool:
    target = Path(path) if path is not None else settings_path()
    payload = {
        "source": str(values.get("source", "")),
        "destination": str(values.get("destination", "")),
        "recursive": bool(values.get("recursive", True)),
        "operation": str(values.get("operation", Operation.COPY.value)),
        "conflict": str(values.get("conflict", ConflictPolicy.RENAME.value)),
        "categorized": bool(values.get("categorized", True)),
        "selected_categories": [
            item
            for item in values.get("selected_categories", [])
            if item in {category.key for category in CATEGORIES}
        ],
        "geometry": str(values.get("geometry", "")),
    }
    temporary = target.with_suffix(".json.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, target)
        return True
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False
