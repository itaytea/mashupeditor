"""Background jobs: cancel requests stop jobs at their next progress report."""

import threading

import pytest

from app import config, db, jobs


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    db.init(tmp_path / "test.sqlite3")
    yield


def _wait(job_id, timeout=5.0):
    """Block until the job leaves queued/running."""
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = db.get_job(job_id)
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} still {job['status']}")


def test_progress_raises_once_cancel_is_requested(temp_db):
    db.create_job("j1", "scan", {})
    ctx = jobs.JobContext("j1")
    ctx.progress(0.1, "first")  # no cancel yet
    db.update_job("j1", cancel=1)
    ctx._last_cancel_check = float("-inf")  # don't wait out the throttle
    with pytest.raises(jobs.Cancelled):
        ctx.progress(0.2, "second")


def test_job_that_only_reports_progress_ends_cancelled(temp_db):
    started, release = threading.Event(), threading.Event()
    calls = []

    def work(ctx):
        ctx.progress(0.0, "starting")
        started.set()
        release.wait(5)
        for i in range(1000):  # like scan_folder: progress per file, no explicit check
            ctx._last_cancel_check = float("-inf")
            ctx.progress(i / 1000, f"file {i}")
            calls.append(i)
        return {"ok": True}

    job_id = jobs.submit("scan", {}, work)
    assert started.wait(5)
    assert jobs.cancel(job_id)
    release.set()
    job = _wait(job_id)
    assert job["status"] == "cancelled"
    assert calls == []  # stopped at the first report after the cancel


def _throttle_setup(monkeypatch, job_id):
    db.create_job(job_id, "scan", {})
    ctx = jobs.JobContext(job_id)
    reads = []
    real_get = db.get_job
    monkeypatch.setattr(db, "get_job", lambda jid: reads.append(jid) or real_get(jid))
    now = [1000.0]
    monkeypatch.setattr(jobs.time, "monotonic", lambda: now[0])
    return ctx, reads, now


def test_progress_cancel_checks_are_throttled(temp_db, monkeypatch):
    ctx, reads, _ = _throttle_setup(monkeypatch, "j2")
    for i in range(100):
        ctx.progress(i / 100)
    assert len(reads) == 1


def test_progress_rechecks_cancel_after_interval(temp_db, monkeypatch):
    ctx, reads, now = _throttle_setup(monkeypatch, "j3")
    for i in range(50):
        ctx.progress(i / 100)
    assert len(reads) == 1
    now[0] += ctx.CANCEL_CHECK_INTERVAL
    for i in range(50):
        ctx.progress(i / 100)
    assert len(reads) == 2
