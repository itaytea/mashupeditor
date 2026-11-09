"""Small user settings (folders, watch toggle) persisted next to the database."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from . import config

_lock = threading.Lock()
DEFAULTS: dict[str, Any] = {
    "music_dir": config.MUSIC_DIR,
    "find_dir": str(Path("~/Downloads").expanduser()),
    "watch": False,
}


def _path() -> Path:
    return config.DATA_DIR / "settings.json"


def load() -> dict[str, Any]:
    with _lock:
        try:
            saved = json.loads(_path().read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            saved = {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def update(**changes: Any) -> dict[str, Any]:
    current = load()
    current.update({k: v for k, v in changes.items() if k in DEFAULTS})
    with _lock:
        config.ensure_dirs()
        path = _path()
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(current, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    return current
