"""Polls the FIND folder and looks for wanted playlist tracks in new files."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from . import analysis, db, settings

log = logging.getLogger(__name__)

POLL_SECONDS = 30
BACKOFF_START = 1 * 60  # seconds; doubles per failure
BACKOFF_MAX = 30 * 60


class FolderWatcher:
    def __init__(self, start_find: Callable[[Path, list[Path]], str | None]):
        self._start_find = start_find
        self._checked: dict[str, float] = {}  # path -> mtime already looked at
        self._watch_key: tuple[str, str | None] | None = None  # (folder, playlist id)
        self._last_job: str | None = None  # id of the find job we started
        self._failures = 0
        self._retry_at = 0.0  # time.monotonic() before which we don't retry
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

    def _current_key(self, folder: Path) -> tuple[str, str | None]:
        pl = db.get_playlist()
        return (str(folder), pl["id"] if pl else None)

    def _reset(self) -> None:
        self._checked.clear()
        self._failures = 0
        self._retry_at = 0.0
        self._last_job = None

    def _note_job_result(self) -> None:
        """Update backoff from the outcome of the find job this watcher started."""
        if not self._last_job:
            return
        job = db.get_job(self._last_job)
        if not job or job["status"] in ("queued", "running"):
            return
        self._last_job = None
        if job["status"] == "failed":
            self._failures += 1
            delay = min(BACKOFF_START * 2 ** (self._failures - 1), BACKOFF_MAX)
            self._retry_at = time.monotonic() + delay
            log.warning("watch: find job failed, backing off %ds", delay)
        elif job["status"] == "done":
            self._failures = 0
            self._retry_at = 0.0

    def tick(self) -> str | None:
        s = settings.load()
        folder = Path(s["find_dir"]).expanduser()
        if not s["watch"] or not folder.is_dir():
            return None
        key = self._current_key(folder)
        if key != self._watch_key:
            if self._watch_key is not None:
                log.info("watch: playlist or folder changed, resetting checked files")
                self._reset()
            self._watch_key = key
        self._note_job_result()
        if time.monotonic() < self._retry_at:
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
        job_id = self._start_find(folder, new)
        self._last_job = job_id
        return job_id
