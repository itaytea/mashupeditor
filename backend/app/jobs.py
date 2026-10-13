"""Background jobs with progress, persisted to SQLite so the UI can poll them.

Each queue has one worker:
  "heavy"  - folder analysis (SCAN / FIND)
  "render" - Demucs + draft rendering, so a draft never waits behind a
             long library scan
  "net"    - metadata lookups (network-bound)
"""

from __future__ import annotations

import logging
import re
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from . import db

log = logging.getLogger(__name__)

_executors = {
    "heavy": ThreadPoolExecutor(max_workers=1, thread_name_prefix="mashup-heavy"),
    "render": ThreadPoolExecutor(max_workers=1, thread_name_prefix="mashup-render"),
    "net": ThreadPoolExecutor(max_workers=1, thread_name_prefix="mashup-net"),
}


_SECRET = re.compile(r"((?:api_key|access_token|client_secret)=)[^&\s'\"]+", re.IGNORECASE)


def redact(text: str) -> str:
    """Mask credentials that libraries put into error messages via URLs."""
    return _SECRET.sub(r"\1***", text)


class Cancelled(Exception):
    pass


class JobContext:
    """Handed to job functions so they can report progress and notice cancels."""

    def __init__(self, job_id: str):
        self.job_id = job_id
        self._last: tuple[float, str | None] = (-1.0, None)

    def progress(self, fraction: float, message: str | None = None) -> None:
        fraction = max(0.0, min(1.0, fraction))
        # Skip writes for changes too small to see in a progress bar.
        if abs(fraction - self._last[0]) < 0.005 and message == self._last[1]:
            return
        self._last = (fraction, message)
        fields: dict[str, Any] = {"progress": round(fraction, 4)}
        if message is not None:
            fields["message"] = message
        db.update_job(self.job_id, **fields)

    def check_cancelled(self) -> None:
        job = db.get_job(self.job_id)
        if job and job.get("cancel"):
            raise Cancelled()


def submit(kind: str, params: dict, fn: Callable[[JobContext], Any], queue: str = "heavy") -> str:
    job_id = uuid.uuid4().hex[:12]
    db.create_job(job_id, kind, {**params, "queue": queue})

    def run() -> None:
        ctx = JobContext(job_id)
        try:
            ctx.check_cancelled()  # cancelled while still queued
            db.update_job(job_id, status="running", message="Starting…")
            result = fn(ctx)
            db.update_job(job_id, status="done", progress=1.0, result=result,
                          message="Done")
        except Cancelled:
            db.update_job(job_id, status="cancelled", message="Cancelled")
        except Exception as e:
            log.error("job %s failed:\n%s", job_id, redact(traceback.format_exc()))
            db.update_job(job_id, status="failed", error=redact(str(e))[:1000],
                          message="Failed")

    _executors[queue].submit(run)
    return job_id


def cancel(job_id: str) -> bool:
    job = db.get_job(job_id)
    if not job or job["status"] not in ("queued", "running"):
        return False
    if job["status"] == "queued":  # never started: done right away
        db.update_job(job_id, cancel=1, status="cancelled", message="Cancelled")
    else:  # the running job notices at its next check
        db.update_job(job_id, cancel=1, message="Cancelling…")
    return True
