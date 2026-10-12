"""SQLite storage: analysis cache, the loaded playlist, and background jobs."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    path           TEXT NOT NULL UNIQUE,
    mtime          REAL NOT NULL,
    size           INTEGER NOT NULL,
    title          TEXT,
    artist         TEXT,
    duration       REAL,
    bpm            REAL,
    key_pc         INTEGER,
    mode           TEXT,
    key_name       TEXT,
    camelot        TEXT,
    key_confidence REAL,
    error          TEXT,
    analyzed_at    REAL
);

CREATE TABLE IF NOT EXISTS playlist (
    id          TEXT PRIMARY KEY,
    name        TEXT,
    url         TEXT,
    fetched_at  REAL
);

CREATE TABLE IF NOT EXISTS playlist_tracks (
    playlist_id  TEXT NOT NULL,
    position     INTEGER NOT NULL,
    spotify_id   TEXT,
    title        TEXT NOT NULL,
    artists      TEXT NOT NULL,
    duration_ms  INTEGER,
    track_id     INTEGER REFERENCES tracks(id) ON DELETE SET NULL,
    match_score  REAL,
    PRIMARY KEY (playlist_id, position)
);

CREATE TABLE IF NOT EXISTS lookups (
    lookup_key     TEXT PRIMARY KEY,   -- Spotify track id, or normalized "title|artist"
    status         TEXT NOT NULL,      -- found / not_found
    bpm            REAL,
    key_pc         INTEGER,
    mode           TEXT,
    key_name       TEXT,
    camelot        TEXT,
    source_id      TEXT,
    matched_title  TEXT,
    matched_artist TEXT,
    match_score    REAL,
    fetched_at     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL,
    progress    REAL NOT NULL DEFAULT 0,
    message     TEXT,
    params      TEXT,
    result      TEXT,
    error       TEXT,
    cancel      INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
"""

_lock = threading.Lock()


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or config.DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_conn: sqlite3.Connection | None = None


def init(path: Path | None = None) -> None:
    global _conn
    config.ensure_dirs()
    _conn = connect(path)
    _conn.executescript(SCHEMA)
    cols = {r["name"] for r in _conn.execute("PRAGMA table_info(jobs)")}
    if "cancel" not in cols:  # databases created before cancellation existed
        _conn.execute("ALTER TABLE jobs ADD COLUMN cancel INTEGER NOT NULL DEFAULT 0")
    # Jobs that were running when the server stopped will never finish.
    now = time.time()
    _conn.execute(
        "UPDATE jobs SET status='failed', error='server restarted', updated_at=? "
        "WHERE status IN ('queued', 'running')",
        (now,),
    )
    _conn.commit()


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    """Serialized access to the shared connection (worker threads write too)."""
    if _conn is None:
        init()
    assert _conn is not None
    with _lock:
        cur = _conn.cursor()
        try:
            yield cur
            _conn.commit()
        except Exception:
            _conn.rollback()
            raise
        finally:
            cur.close()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


# --- tracks -----------------------------------------------------------------

def get_cached_track(path: str, mtime: float, size: int) -> dict | None:
    with cursor() as c:
        c.execute(
            "SELECT * FROM tracks WHERE path=? AND mtime=? AND size=? AND error IS NULL",
            (path, mtime, size),
        )
        return row_to_dict(c.fetchone())


def upsert_track(rec: dict[str, Any]) -> int:
    cols = list(rec)
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "path")
    with cursor() as c:
        c.execute(
            f"INSERT INTO tracks ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(path) DO UPDATE SET {updates}",
            [rec[k] for k in cols],
        )
        c.execute("SELECT id FROM tracks WHERE path=?", (rec["path"],))
        return c.fetchone()["id"]


def list_tracks(folder: str | None = None) -> list[dict]:
    with cursor() as c:
        if folder:
            prefix = str(Path(folder).expanduser().resolve()).rstrip("/") + "/"
            c.execute(
                "SELECT * FROM tracks WHERE path LIKE ? ESCAPE '\\' ORDER BY artist, title",
                (_like_escape(prefix) + "%",),
            )
        else:
            c.execute("SELECT * FROM tracks ORDER BY artist, title")
        return [dict(r) for r in c.fetchall()]


def get_track(track_id: int) -> dict | None:
    with cursor() as c:
        c.execute("SELECT * FROM tracks WHERE id=?", (track_id,))
        return row_to_dict(c.fetchone())


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# --- playlist ---------------------------------------------------------------

def save_playlist(pl_id: str, name: str, url: str, tracks: list[dict]) -> None:
    with cursor() as c:
        c.execute("DELETE FROM playlist")
        c.execute("DELETE FROM playlist_tracks")
        c.execute(
            "INSERT INTO playlist (id, name, url, fetched_at) VALUES (?, ?, ?, ?)",
            (pl_id, name, url, time.time()),
        )
        c.executemany(
            "INSERT INTO playlist_tracks (playlist_id, position, spotify_id, title, "
            "artists, duration_ms) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (pl_id, i, t.get("spotify_id"), t["title"], json.dumps(t["artists"]),
                 t.get("duration_ms"))
                for i, t in enumerate(tracks)
            ],
        )


def set_playlist_matches(matches: dict[int, tuple[int | None, float | None]]) -> None:
    with cursor() as c:
        c.executemany(
            "UPDATE playlist_tracks SET track_id=?, match_score=? WHERE position=?",
            [(tid, score, pos) for pos, (tid, score) in matches.items()],
        )


def get_playlist() -> dict | None:
    with cursor() as c:
        c.execute("SELECT * FROM playlist LIMIT 1")
        pl = row_to_dict(c.fetchone())
        if pl is None:
            return None
        c.execute(
            "SELECT * FROM playlist_tracks WHERE playlist_id=? ORDER BY position",
            (pl["id"],),
        )
        rows = [dict(r) for r in c.fetchall()]
    for r in rows:
        r["artists"] = json.loads(r["artists"])
    pl["tracks"] = rows
    return pl


# --- lookups ----------------------------------------------------------------

def get_lookups(keys: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    with cursor() as c:
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            c.execute(
                f"SELECT * FROM lookups WHERE lookup_key IN ({','.join('?' * len(chunk))})",
                chunk,
            )
            out.update({r["lookup_key"]: dict(r) for r in c.fetchall()})
    return out


def save_lookup(rec: dict[str, Any]) -> None:
    cols = list(rec)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "lookup_key")
    with cursor() as c:
        c.execute(
            f"INSERT INTO lookups ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
            f"ON CONFLICT(lookup_key) DO UPDATE SET {updates}",
            [rec[k] for k in cols],
        )


# --- jobs -------------------------------------------------------------------

def create_job(job_id: str, kind: str, params: dict) -> None:
    now = time.time()
    with cursor() as c:
        c.execute(
            "INSERT INTO jobs (id, kind, status, progress, params, created_at, updated_at) "
            "VALUES (?, ?, 'queued', 0, ?, ?, ?)",
            (job_id, kind, json.dumps(params), now, now),
        )


def update_job(job_id: str, **fields: Any) -> None:
    if "result" in fields and fields["result"] is not None:
        fields["result"] = json.dumps(fields["result"])
    fields["updated_at"] = time.time()
    sets = ", ".join(f"{k}=?" for k in fields)
    with cursor() as c:
        c.execute(f"UPDATE jobs SET {sets} WHERE id=?", [*fields.values(), job_id])


def _job_row(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["params"] = json.loads(d["params"]) if d["params"] else {}
    d["result"] = json.loads(d["result"]) if d["result"] else None
    return d


def get_job(job_id: str) -> dict | None:
    with cursor() as c:
        c.execute("SELECT * FROM jobs WHERE id=?", (job_id,))
        row = c.fetchone()
    return _job_row(row) if row else None


def list_jobs(kind: str | None = None, limit: int = 50) -> list[dict]:
    with cursor() as c:
        if kind:
            c.execute(
                "SELECT * FROM jobs WHERE kind=? ORDER BY created_at DESC LIMIT ?",
                (kind, limit),
            )
        else:
            c.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
        return [_job_row(r) for r in c.fetchall()]
