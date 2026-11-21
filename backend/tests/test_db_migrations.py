"""Schema migrations tracked by PRAGMA user_version."""

import sqlite3

from app import config, db

OLD_SCHEMA = """
CREATE TABLE tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE,
    mtime REAL NOT NULL, size INTEGER NOT NULL, title TEXT, artist TEXT,
    duration REAL, bpm REAL, key_pc INTEGER, mode TEXT, key_name TEXT,
    camelot TEXT, key_confidence REAL, error TEXT, analyzed_at REAL
);
CREATE TABLE jobs (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0, message TEXT, params TEXT, result TEXT,
    error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
"""


def _close():
    if db._conn is not None:
        db._conn.close()
    db._conn = None


def _cols(table):
    return {r["name"] for r in db._conn.execute(f"PRAGMA table_info({table})")}


def _version():
    return db._conn.execute("PRAGMA user_version").fetchone()[0]


def test_old_database_is_upgraded(data_dir):
    config.ensure_dirs()
    raw = sqlite3.connect(config.DB_PATH)
    raw.executescript(OLD_SCHEMA)
    raw.execute("INSERT INTO tracks (path, mtime, size) VALUES ('a.mp3', 1, 2)")
    raw.execute("INSERT INTO jobs VALUES ('j','render','done',1,NULL,NULL,NULL,NULL,1,1)")
    raw.commit()
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 0
    raw.close()
    try:
        db.init()
        assert "version" in _cols("tracks")
        assert "cancel" in _cols("jobs")
        assert _version() == len(db.MIGRATIONS)
        assert db._conn.execute("SELECT version FROM tracks").fetchone()[0] == 1
        assert db._conn.execute("SELECT cancel FROM jobs").fetchone()[0] == 0
    finally:
        _close()


def test_fresh_database_and_repeat_init_are_noops(data_dir):
    try:
        db.init()
        assert _version() == len(db.MIGRATIONS)
        before = (_cols("tracks"), _cols("jobs"))
        _close()
        db.init()
        assert _version() == len(db.MIGRATIONS)
        assert (_cols("tracks"), _cols("jobs")) == before
    finally:
        _close()


def test_unversioned_database_with_columns_is_stamped(data_dir):
    """Existing DBs already upgraded by the old checks have user_version 0."""
    try:
        db.init()
        db._conn.execute("PRAGMA user_version = 0")
        _close()
        db.init()
        assert _version() == len(db.MIGRATIONS)
    finally:
        _close()


def test_recover_jobs_still_works(data_dir):
    try:
        db.init()
        db._conn.execute(
            "INSERT INTO jobs (id, kind, status, created_at, updated_at) "
            "VALUES ('j','render','running',1,1)")
        db._conn.commit()
        _close()
        db.init(recover_jobs=True)
        row = db._conn.execute("SELECT status, error FROM jobs").fetchone()
        assert (row["status"], row["error"]) == ("failed", "server restarted")
    finally:
        _close()
