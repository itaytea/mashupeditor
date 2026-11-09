"""A rematch that started before a new playlist was loaded must not touch it."""

from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from app import config, db, main  # noqa: E402


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "STEMS_DIR", tmp_path / "data" / "stems")
    monkeypatch.setattr(config, "DRAFTS_DIR", tmp_path / "data" / "drafts")
    db.init(tmp_path / "test.sqlite3")
    yield
    db._conn.close()
    db._conn = None


def _tracks(n):
    return [{"spotify_id": f"s{i}", "title": f"T{i}", "artists": ["A"], "duration_ms": 1}
            for i in range(n)]


def test_set_playlist_matches_is_scoped_to_playlist_id(temp_db):
    db.save_playlist("new", "New", "u", _tracks(2))
    db.set_playlist_matches("old", {0: (None, 0.9), 1: (None, 0.9)})
    assert [t["match_score"] for t in db.get_playlist()["tracks"]] == [None, None]
    db.set_playlist_matches("new", {0: (None, 0.5)})
    assert db.get_playlist()["tracks"][0]["match_score"] == 0.5


def test_stale_rematch_leaves_new_playlist_untouched(temp_db, monkeypatch):
    db.save_playlist("old", "Old", "u", _tracks(2))

    def fake_match(tracks, local):
        # while matching runs, the user loads another playlist
        db.save_playlist("new", "New", "u", _tracks(2))
        return [SimpleNamespace(position=0, track_id=None, score=0.99)]

    monkeypatch.setattr(main.fuzzy, "match_playlist", fake_match)
    monkeypatch.setattr(main, "_library_tracks", lambda: [])
    main._rematch_playlist()
    pl = db.get_playlist()
    assert pl["id"] == "new"
    assert all(t["match_score"] is None for t in pl["tracks"])
