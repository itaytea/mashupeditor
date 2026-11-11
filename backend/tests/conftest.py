"""Shared fixtures: an isolated data dir / SQLite DB and a FastAPI TestClient."""

import pytest

from app import config, db


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point every config path at a temp dir and blank out credentials."""
    root = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", root)
    monkeypatch.setattr(config, "DB_PATH", root / "mashup.sqlite3")
    monkeypatch.setattr(config, "STEMS_DIR", root / "stems")
    monkeypatch.setattr(config, "DRAFTS_DIR", root / "drafts")
    for name in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_ACCESS_TOKEN",
                 "GETSONGBPM_API_KEY"):
        monkeypatch.setattr(config, name, "")
    return root


@pytest.fixture
def temp_db(data_dir):
    """A fresh database in the temp data dir, closed afterwards."""
    db.init()
    yield db
    if db._conn is not None:
        db._conn.close()
    db._conn = None


@pytest.fixture
def client(data_dir):
    """TestClient running the app lifespan against the temp data dir."""
    from fastapi.testclient import TestClient

    from app import main

    with TestClient(main.app) as c:
        yield c
    if db._conn is not None:
        db._conn.close()
    db._conn = None


def make_track(path, **over):
    """A complete analysed-track row for db.upsert_track."""
    rec = {
        "path": str(path), "mtime": 1.0, "size": 10, "title": "Song", "artist": "Artist",
        "duration": 180.0, "bpm": 120.0, "key_pc": 0, "mode": "major",
        "key_name": "C major", "camelot": "8B", "key_confidence": 0.9, "error": None,
        "analyzed_at": 1.0, "version": 1,
    }
    rec.update(over)
    return rec
