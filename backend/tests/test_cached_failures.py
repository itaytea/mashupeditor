"""Files whose analysis failed are cached too, so rescans don't retry them."""

import time

import pytest

from app import analysis, config, db


@pytest.fixture()
def tmp_db(tmp_path):
    db.init(tmp_path / "test.db")
    yield
    db._conn.close()
    db._conn = None


def _bad_file(tmp_path):
    f = tmp_path / "broken.mp3"
    f.write_bytes(b"not audio")
    return f


def test_get_cached_track_returns_errored_row(tmp_db, tmp_path):
    f = _bad_file(tmp_path)
    st = f.stat()
    db.upsert_track({"path": str(f), "mtime": st.st_mtime, "size": st.st_size,
                     "error": "boom", "version": 3, "analyzed_at": time.time()})
    assert db.get_cached_track(str(f), st.st_mtime, st.st_size, 3)["error"] == "boom"
    # changed file or newer analysis version is a miss
    assert db.get_cached_track(str(f), st.st_mtime + 1, st.st_size, 3) is None
    assert db.get_cached_track(str(f), st.st_mtime, st.st_size + 1, 3) is None
    assert db.get_cached_track(str(f), st.st_mtime, st.st_size, 4) is None


def test_scan_counts_known_bad_file_as_failed_without_retry(tmp_db, tmp_path, monkeypatch):
    f = _bad_file(tmp_path)
    calls = []

    def boom(path):
        calls.append(path)
        raise RuntimeError("cannot decode")

    monkeypatch.setattr(analysis, "analyze_file", boom)
    assert analysis.scan_folder(tmp_path)["failed"] == 1
    assert len(calls) == 1
    stats = analysis.scan_folder(tmp_path)
    assert stats["failed"] == 1 and stats["cached"] == 0
    assert len(calls) == 1  # not decoded again

    # a modified file is retried
    f.write_bytes(b"different bytes now")
    assert analysis.scan_folder(tmp_path)["failed"] == 1
    assert len(calls) == 2


# --- failure TTL ------------------------------------------------------------

def _store(f, **over):
    st = f.stat()
    rec = {"path": str(f), "mtime": st.st_mtime, "size": st.st_size,
           "version": analysis.ANALYSIS_VERSION}
    rec.update(over)
    db.upsert_track(rec)
    return st


def test_fresh_failure_is_skipped(tmp_db, tmp_path, monkeypatch):
    f = _bad_file(tmp_path)
    _store(f, error="boom", analyzed_at=time.time() - 3600)
    monkeypatch.setattr(analysis, "analyze_file",
                        lambda p: pytest.fail("fresh failure must not be retried"))
    assert analysis.analyze_path(f, "t", "a") == "failed"


def test_expired_failure_is_retried(tmp_db, tmp_path, monkeypatch):
    f = _bad_file(tmp_path)
    st = _store(f, error="boom", analyzed_at=time.time() - 25 * 3600)
    assert db.get_cached_track(str(f), st.st_mtime, st.st_size,
                               analysis.ANALYSIS_VERSION) is None
    calls = []
    monkeypatch.setattr(analysis, "analyze_file",
                        lambda p: calls.append(p) or {"bpm": 120.0})
    assert analysis.analyze_path(f, "t", "a") == "analyzed"
    assert len(calls) == 1
    row = db.get_cached_track(str(f), st.st_mtime, st.st_size, analysis.ANALYSIS_VERSION)
    assert row["error"] is None and row["bpm"] == 120.0


def test_failure_ttl_is_configurable(tmp_db, tmp_path, monkeypatch):
    f = _bad_file(tmp_path)
    st = _store(f, error="boom", analyzed_at=time.time() - 3600)
    monkeypatch.setattr(config, "ANALYSIS_FAILURE_TTL_HOURS", 0.5)
    assert db.get_cached_track(str(f), st.st_mtime, st.st_size,
                               analysis.ANALYSIS_VERSION) is None


def test_old_success_is_still_a_cache_hit(tmp_db, tmp_path, monkeypatch):
    f = _bad_file(tmp_path)
    _store(f, error=None, bpm=100.0, analyzed_at=time.time() - 365 * 24 * 3600)
    monkeypatch.setattr(analysis, "analyze_file",
                        lambda p: pytest.fail("success must stay cached"))
    assert analysis.analyze_path(f, "t", "a") == "cached"
