"""Small user settings (folders, watch toggle) persisted next to the database."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from . import config
from .fsutil import atomic_write_text

_lock = threading.Lock()
DEFAULTS: dict[str, Any] = {
    "music_dir": config.MUSIC_DIR,
    "find_dir": str(Path("~/Downloads").expanduser()),
    "watch": False,
}


def _path() -> Path:
    return config.DATA_DIR / "settings.json"


def _read() -> dict[str, Any]:
    try:
        saved = json.loads(_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        saved = {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def load() -> dict[str, Any]:
    with _lock:
        return _read()


def update(**changes: Any) -> dict[str, Any]:
    # Hold the lock across read-modify-write so concurrent updates don't lose changes.
    with _lock:
        current = _read()
        current.update({k: v for k, v in changes.items() if k in DEFAULTS})
        config.ensure_dirs()
        atomic_write_text(_path(), json.dumps(current, indent=2), mode=0o600)
    return current
