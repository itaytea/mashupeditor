"""Small filesystem helpers."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: str | Path, text: str, mode: int = 0o600) -> None:
    """Write ``text`` to ``path`` atomically.

    The data goes to a temp file in the same directory (so ``os.replace`` stays
    on one filesystem), is fsynced, then swapped into place. A crash or error
    never leaves a truncated ``path``; the temp file is removed on failure.
    """
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
