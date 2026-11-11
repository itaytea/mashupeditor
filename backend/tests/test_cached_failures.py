"""Files whose analysis failed are cached too, so rescans don't retry them."""

import pytest

from app import analysis, db


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
                     "error": "boom", "version": 3})
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
