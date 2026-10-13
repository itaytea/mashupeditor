"""Polls the FIND folder and looks for wanted playlist tracks in new files."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from . import analysis, db, settings

log = logging.getLogger(__name__)

POLL_SECONDS = 30


class FolderWatcher:
    def __init__(self, start_find: Callable[[Path, list[Path]], str | None]):
        self._start_find = start_find
        self._checked: dict[str, float] = {}  # path -> mtime already looked at
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def mark_checked(self, paths: list[str]) -> None:
        for p in paths:
            try:
                self._checked[p] = Path(p).stat().st_mtime
            except OSError:
                pass

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mashup-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(POLL_SECONDS):
            try:
                self.tick()
            except Exception:
                log.exception("folder watch failed")

    def tick(self) -> str | None:
        s = settings.load()
        folder = Path(s["find_dir"]).expanduser()
        if not s["watch"] or not folder.is_dir():
            return None
        if any(j["status"] in ("queued", "running") for j in db.list_jobs("find", 3)):
            return None
        new = []
        for p in analysis.find_audio_files(folder):
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if self._checked.get(str(p)) != mtime:
                new.append(p)
        if not new:
            return None
        log.info("watch: %d new/changed file(s) in %s", len(new), folder)
        return self._start_find(folder, new)
