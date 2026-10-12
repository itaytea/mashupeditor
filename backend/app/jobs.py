"""Background jobs with progress, persisted to SQLite so the UI can poll them.

Heavy work (folder analysis, Demucs, rendering) runs on a single worker thread
so two separations never compete for the CPU/GPU at once.
"""

from __future__ import annotations

import logging
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from . import db

log = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mashup-job")


class JobContext:
    """Handed to job functions so they can report progress."""

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


def submit(kind: str, params: dict, fn: Callable[[JobContext], Any]) -> str:
    job_id = uuid.uuid4().hex[:12]
    db.create_job(job_id, kind, params)

    def run() -> None:
        ctx = JobContext(job_id)
        db.update_job(job_id, status="running", message="Starting…")
        try:
            result = fn(ctx)
            db.update_job(job_id, status="done", progress=1.0, result=result,
                          message="Done")
        except Exception as e:
            log.error("job %s failed:\n%s", job_id, traceback.format_exc())
            db.update_job(job_id, status="failed", error=str(e)[:1000],
                          message="Failed")

    _executor.submit(run)
    return job_id
