"""Background jobs with progress, persisted to SQLite so the UI can poll them.

Heavy work (folder analysis, Demucs, rendering) runs on a single "heavy"
worker so two separations never compete for the CPU/GPU at once. Slow,
network-bound work (metadata lookups) gets its own "net" worker so it never
holds up a draft.
"""

from __future__ import annotations

import logging
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from . import db

log = logging.getLogger(__name__)

_executors = {
    "heavy": ThreadPoolExecutor(max_workers=1, thread_name_prefix="mashup-heavy"),
    "net": ThreadPoolExecutor(max_workers=1, thread_name_prefix="mashup-net"),
}


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
    db.create_job(job_id, kind, params)

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
            log.error("job %s failed:\n%s", job_id, traceback.format_exc())
            db.update_job(job_id, status="failed", error=str(e)[:1000],
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
