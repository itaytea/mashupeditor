"""Unit tests for the SQLite layer (app.db) and the job queue (app.jobs)."""

import json
import sqlite3
import threading
import time

import pytest

from app import db, jobs
from tests.conftest import make_track


def wait_status(job_id, statuses=("done", "failed", "cancelled"), timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        job = db.get_job(job_id)
        if job["status"] in statuses:
            return job
        time.sleep(0.01)
    raise AssertionError("job did not reach " + str(statuses))


# --- db: schema / init -----------------------------------------------------

def test_init_creates_tables(temp_db):
    names = {r["name"] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"tracks", "playlist", "playlist_tracks", "lookups", "jobs"} <= names


def test_init_migrates_old_schema(data_dir):
    data_dir.mkdir(parents=True)
    path = data_dir / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE,"
        " mtime REAL NOT NULL, size INTEGER NOT NULL, title TEXT, artist TEXT, duration REAL,"
        " bpm REAL, key_pc INTEGER, mode TEXT, key_name TEXT, camelot TEXT,"
        " key_confidence REAL, error TEXT, analyzed_at REAL);"
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,"
        " progress REAL NOT NULL DEFAULT 0, message TEXT, params TEXT, result TEXT,"
        " error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);"
    )
    conn.commit()
    conn.close()
    try:
        db.init(path)
        assert "version" in {r["name"] for r in db._conn.execute("PRAGMA table_info(tracks)")}
        assert "cancel" in {r["name"] for r in db._conn.execute("PRAGMA table_info(jobs)")}
    finally:
        db._conn.close()
        db._conn = None


def test_recover_jobs_only_when_requested(data_dir):
    path = data_dir / "r.sqlite3"
    try:
        db.init(path)
        db.create_job("a", "scan", {})
        db.update_job("a", status="running")
        db.create_job("b", "scan", {})
        db.update_job("b", status="done")
        db.init(path)  # no recovery
        assert db.get_job("a")["status"] == "running"
        db.init(path, recover_jobs=True)
        a, b = db.get_job("a"), db.get_job("b")
        assert a["status"] == "failed" and a["error"] == "server restarted"
        assert b["status"] == "done"
    finally:
        db._conn.close()
        db._conn = None


def test_cursor_rolls_back_on_error(temp_db):
    with pytest.raises(RuntimeError):
        with db.cursor() as c:
            c.execute("INSERT INTO lookups (lookup_key, status, fetched_at) VALUES ('k','found',1)")
            raise RuntimeError("boom")
    assert db.get_lookups(["k"]) == {}


# --- db: tracks ------------------------------------------------------------

def test_upsert_track_updates_in_place(temp_db, tmp_path):
    p = tmp_path / "a.mp3"
    first = db.upsert_track(make_track(p, bpm=100.0))
    second = db.upsert_track(make_track(p, bpm=130.0, title="New"))
    assert first == second
    t = db.get_track(first)
    assert t["bpm"] == 130.0 and t["title"] == "New"
    assert len(db.list_tracks()) == 1


def test_get_track_missing(temp_db):
    assert db.get_track(12345) is None


def test_cached_track_checks_mtime_size_error_version(temp_db, tmp_path):
    p = str(tmp_path / "a.mp3")
    db.upsert_track(make_track(p, mtime=5.0, size=10, version=2))
    assert db.get_cached_track(p, 5.0, 10, 2) is not None
    assert db.get_cached_track(p, 5.0, 10, 1) is not None  # newer version is fine
    assert db.get_cached_track(p, 5.0, 10, 3) is None       # analysis is outdated
    assert db.get_cached_track(p, 6.0, 10, 1) is None       # file modified
    assert db.get_cached_track(p, 5.0, 11, 1) is None       # file size changed
    db.upsert_track(make_track(p, mtime=5.0, size=10, error="bad", analyzed_at=time.time()))
    cached = db.get_cached_track(p, 5.0, 10, 1)             # known failures are cached
    assert cached is not None and cached["error"] == "bad"


def test_list_tracks_order_and_folder_filter(temp_db, tmp_path):
    sub = tmp_path / "sub"
    other = tmp_path / "sub_other"
    sub.mkdir()
    other.mkdir()
    db.upsert_track(make_track(sub / "1.mp3", title="B", artist="Zed"))
    db.upsert_track(make_track(sub / "2.mp3", title="A", artist="Zed"))
    db.upsert_track(make_track(sub / "3.mp3", title="C", artist="Abe"))
    db.upsert_track(make_track(other / "4.mp3", title="D", artist="Abe"))
    assert [t["title"] for t in db.list_tracks()] == ["C", "D", "A", "B"]
    in_sub = db.list_tracks(str(sub))
    assert [t["title"] for t in in_sub] == ["C", "A", "B"]  # not the sibling "sub_other"


def test_list_tracks_folder_escapes_like_wildcards(temp_db, tmp_path):
    odd = tmp_path / "100%_real"
    plain = tmp_path / "100xxreal"
    odd.mkdir()
    plain.mkdir()
    db.upsert_track(make_track(odd / "a.mp3", title="odd"))
    db.upsert_track(make_track(plain / "a.mp3", title="plain"))
    assert [t["title"] for t in db.list_tracks(str(odd))] == ["odd"]


# --- db: playlist ----------------------------------------------------------

def test_playlist_roundtrip_and_replace(temp_db):
    assert db.get_playlist() is None
    db.save_playlist("p1", "One", "url1", [
        {"spotify_id": "s1", "title": "T1", "artists": ["A", "B"], "duration_ms": 1000},
        {"title": "T2", "artists": ["C"]},
    ])
    pl = db.get_playlist()
    assert (pl["id"], pl["name"], pl["url"]) == ("p1", "One", "url1")
    assert [t["title"] for t in pl["tracks"]] == ["T1", "T2"]
    assert pl["tracks"][0]["artists"] == ["A", "B"]
    assert pl["tracks"][1]["spotify_id"] is None and pl["tracks"][1]["track_id"] is None

    db.save_playlist("p2", "Two", "url2", [{"title": "X", "artists": ["Y"]}])
    pl = db.get_playlist()
    assert pl["id"] == "p2" and len(pl["tracks"]) == 1


def test_set_playlist_matches(temp_db, tmp_path):
    tid = db.upsert_track(make_track(tmp_path / "a.mp3"))
    db.save_playlist("p", "P", "u", [
        {"title": "T0", "artists": ["A"]}, {"title": "T1", "artists": ["A"]}])
    db.set_playlist_matches("p", {0: (tid, 0.93), 1: (None, None)})
    rows = db.get_playlist()["tracks"]
    assert rows[0]["track_id"] == tid and rows[0]["match_score"] == pytest.approx(0.93)
    assert rows[1]["track_id"] is None and rows[1]["match_score"] is None


def test_deleting_track_nulls_playlist_match(temp_db, tmp_path):
    tid = db.upsert_track(make_track(tmp_path / "a.mp3"))
    db.save_playlist("p", "P", "u", [{"title": "T", "artists": ["A"]}])
    db.set_playlist_matches("p", {0: (tid, 1.0)})
    with db.cursor() as c:
        c.execute("DELETE FROM tracks WHERE id=?", (tid,))
    assert db.get_playlist()["tracks"][0]["track_id"] is None


# --- db: lookups -----------------------------------------------------------

def test_lookups_save_update_and_get(temp_db):
    db.save_lookup({"lookup_key": "k1", "status": "found", "bpm": 100.0, "fetched_at": 1.0})
    db.save_lookup({"lookup_key": "k2", "status": "not_found", "fetched_at": 2.0})
    db.save_lookup({"lookup_key": "k1", "status": "found", "bpm": 111.0, "fetched_at": 3.0})
    got = db.get_lookups(["k1", "k2", "missing"])
    assert set(got) == {"k1", "k2"}
    assert got["k1"]["bpm"] == 111.0 and got["k2"]["status"] == "not_found"
    assert db.get_lookups([]) == {}


def test_get_lookups_handles_more_than_one_chunk(temp_db):
    keys = [f"key{i}" for i in range(1200)]
    with db.cursor() as c:
        c.executemany("INSERT INTO lookups (lookup_key, status, fetched_at) VALUES (?, 'found', 1)",
                      [(k,) for k in keys])
    assert len(db.get_lookups(keys)) == 1200


# --- db: jobs --------------------------------------------------------------

def test_job_create_update_get(temp_db):
    db.create_job("j", "scan", {"folder": "/x"})
    job = db.get_job("j")
    assert job["status"] == "queued" and job["progress"] == 0 and job["cancel"] == 0
    assert job["params"] == {"folder": "/x"} and job["result"] is None
    db.update_job("j", status="done", progress=1.0, result={"n": 3})
    job = db.get_job("j")
    assert job["status"] == "done" and job["result"] == {"n": 3}
    assert job["updated_at"] >= job["created_at"]
    assert db.get_job("nope") is None


def test_list_jobs_filter_order_limit(temp_db):
    for i, kind in enumerate(["scan", "draft", "scan"]):
        db.create_job(f"j{i}", kind, {})
        db.update_job(f"j{i}", message=str(i))
        with db.cursor() as c:  # make ordering deterministic
            c.execute("UPDATE jobs SET created_at=? WHERE id=?", (100 + i, f"j{i}"))
    assert [j["id"] for j in db.list_jobs()] == ["j2", "j1", "j0"]
    assert [j["id"] for j in db.list_jobs("scan")] == ["j2", "j0"]
    assert [j["id"] for j in db.list_jobs(limit=1)] == ["j2"]


def test_concurrent_job_updates_are_serialized(temp_db):
    db.create_job("j", "scan", {})
    errors = []

    def worker(n):
        try:
            for i in range(25):
                db.update_job("j", message=f"{n}-{i}")
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert db.get_job("j")["message"].split("-")[1] == "24"


# --- jobs: redact ----------------------------------------------------------

@pytest.mark.parametrize("raw, masked", [
    ("GET https://x/?api_key=SECRET123&q=1", "GET https://x/?api_key=***&q=1"),
    ("failed access_token=abc.def", "failed access_token=***"),
    ("API_KEY=Upper", "API_KEY=***"),
    ("nothing sensitive", "nothing sensitive"),
])
def test_redact(raw, masked):
    assert jobs.redact(raw) == masked


# --- jobs: JobContext ------------------------------------------------------

def test_progress_clamps_and_dedupes(temp_db):
    db.create_job("j", "scan", {})
    ctx = jobs.JobContext("j")
    ctx.progress(1.7, "end")
    job = db.get_job("j")
    assert job["progress"] == 1.0 and job["message"] == "end"
    ctx.progress(-3)
    assert db.get_job("j")["progress"] == 0.0
    before = db.get_job("j")["updated_at"]
    time.sleep(0.01)
    ctx.progress(0.001)  # < 0.005 change and same (None) message: skipped
    assert db.get_job("j")["updated_at"] == before
    assert db.get_job("j")["progress"] == 0.0


def test_progress_keeps_message_when_none(temp_db):
    db.create_job("j", "scan", {})
    ctx = jobs.JobContext("j")
    ctx.progress(0.2, "hello")
    ctx.progress(0.6)
    job = db.get_job("j")
    assert job["progress"] == 0.6 and job["message"] == "hello"


def test_check_cancelled(temp_db):
    db.create_job("j", "scan", {})
    ctx = jobs.JobContext("j")
    ctx.check_cancelled()
    db.update_job("j", cancel=1)
    with pytest.raises(jobs.Cancelled):
        ctx.check_cancelled()


# --- jobs: submit / cancel -------------------------------------------------

def test_submit_success(temp_db):
    def work(ctx):
        ctx.progress(0.5, "half")
        return {"answer": 42}

    job_id = jobs.submit("test", {"title": "t"}, work)
    job = wait_status(job_id)
    assert job["status"] == "done" and job["progress"] == 1.0
    assert job["result"] == {"answer": 42} and job["message"] == "Done"
    assert job["params"] == {"title": "t", "queue": "heavy"}


def test_submit_failure_is_redacted_and_truncated(temp_db):
    def work(ctx):
        raise ValueError("bad url https://x?api_key=TOPSECRET " + "x" * 2000)

    job = wait_status(jobs.submit("test", {}, work, queue="net"))
    assert job["status"] == "failed" and job["message"] == "Failed"
    assert "TOPSECRET" not in job["error"] and "api_key=***" in job["error"]
    assert len(job["error"]) <= 1000
    assert job["params"]["queue"] == "net"


def test_cancel_running_job(temp_db):
    started, release = threading.Event(), threading.Event()

    def work(ctx):
        started.set()
        release.wait(5)
        ctx.check_cancelled()
        return "unreachable"

    job_id = jobs.submit("test", {}, work, queue="render")
    assert started.wait(5)
    assert jobs.cancel(job_id) is True
    assert db.get_job(job_id)["message"] == "Cancelling…"
    release.set()
    job = wait_status(job_id)
    assert job["status"] == "cancelled" and job["result"] is None
    assert jobs.cancel(job_id) is False  # already finished


def test_cancel_queued_job_never_runs(temp_db):
    started, release = threading.Event(), threading.Event()
    ran = []

    def blocker(ctx):
        started.set()
        release.wait(5)

    jobs.submit("test", {}, blocker, queue="heavy")
    assert started.wait(5)
    queued = jobs.submit("test", {}, lambda ctx: ran.append(1), queue="heavy")
    assert db.get_job(queued)["status"] == "queued"
    assert jobs.cancel(queued) is True
    assert db.get_job(queued)["status"] == "cancelled"
    release.set()
    # The queue is FIFO with one worker; once a follow-up job is done the
    # cancelled one has been processed too.
    wait_status(jobs.submit("test", {}, lambda ctx: None, queue="heavy"))
    assert ran == []
    assert db.get_job(queued)["status"] == "cancelled"


def test_cancel_unknown_job(temp_db):
    assert jobs.cancel("nope") is False


def test_queue_runs_jobs_in_order(temp_db):
    order = []
    ids = [jobs.submit("test", {}, lambda ctx, i=i: order.append(i), queue="net")
           for i in range(5)]
    for i in ids:
        wait_status(i)
    assert order == [0, 1, 2, 3, 4]


def test_params_json_stored(temp_db):
    job_id = jobs.submit("test", {"a": [1, 2]}, lambda ctx: None)
    wait_status(job_id)
    with db.cursor() as c:
        c.execute("SELECT params FROM jobs WHERE id=?", (job_id,))
        assert json.loads(c.fetchone()["params"])["a"] == [1, 2]
