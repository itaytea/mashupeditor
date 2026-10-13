"""Key/BPM for playlist tracks you don't own, from the GetSongBPM API.

This is metadata only (numbers looked up by title and artist); no audio is
fetched. Local analysis always takes priority when you have the file.
GetSongBPM's terms require a visible link to getsongbpm.com, which the UI shows.
"""

from __future__ import annotations

import re
import time
from typing import Callable

import httpx

from . import camelot, config, db
from .fuzzy import artist_score, normalize, title_artist_score, title_score

# A looked-up song must clearly be the same title by the same artist: wrong
# numbers are worse than no numbers.
MIN_TITLE = 85.0
MIN_ARTIST = 75.0
# Re-try tracks that weren't found after this long (new songs get added).
NOT_FOUND_TTL = 30 * 24 * 3600

_PITCH = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_KEY_RE = re.compile(r"^\s*([A-Ga-g])\s*([#♯b♭]?)\s*(m|min|minor|maj|major)?\s*$")
_OPEN_KEY_RE = re.compile(r"^\s*(\d{1,2})\s*([dm])\s*$", re.IGNORECASE)


class SongLookupError(RuntimeError):
    pass


def parse_key(key_of: str | None, open_key: str | None = None) -> tuple[int, str] | None:
    """(pitch_class, mode) from strings like "Em", "C♯", "Bb minor", "F#/Gb",
    falling back to Open Key notation ("1d" = C major, "1m" = A minor)."""
    if key_of:
        first = key_of.split("/")[0]  # enharmonic spellings: "F♯/G♭" -> "F♯"
        m = _KEY_RE.match(first)
        if m:
            letter, acc, quality = m.groups()
            pc = _PITCH[letter.upper()]
            if acc in ("#", "♯"):
                pc += 1
            elif acc in ("b", "♭"):
                pc -= 1
            mode = "minor" if quality in ("m", "min", "minor") else "major"
            return pc % 12, mode
    if open_key:
        m = _OPEN_KEY_RE.match(open_key)
        if m and 1 <= int(m.group(1)) <= 12:
            step = int(m.group(1)) - 1  # each step clockwise is a fifth (+7)
            if m.group(2).lower() == "d":
                return (7 * step) % 12, "major"
            return (9 + 7 * step) % 12, "minor"
    return None


def lookup_key(track: dict) -> str:
    """Cache key for a playlist row."""
    if track.get("spotify_id"):
        return track["spotify_id"]
    return f"{normalize(track['title'])}|{normalize(', '.join(track['artists']))}"


def _record(song: dict, title: str, artists: list[str]) -> dict | None:
    """Turn an API song object into a lookups row, or None if it lacks data."""
    try:
        bpm = float(song.get("tempo") or 0)
    except (TypeError, ValueError):
        bpm = 0.0
    key = parse_key(song.get("key_of"), song.get("open_key"))
    if bpm <= 0 or key is None:
        return None
    pc, mode = key
    artist = song.get("artist") or {}
    return {
        "status": "found",
        "bpm": round(bpm, 2),
        "key_pc": pc,
        "mode": mode,
        "key_name": camelot.key_name(pc, mode),
        "camelot": str(camelot.to_camelot(pc, mode)),
        "source_id": str(song.get("id") or ""),
        "matched_title": song.get("title"),
        "matched_artist": artist.get("name") if isinstance(artist, dict) else str(artist),
        "match_score": round(
            title_artist_score(title, artists, song.get("title") or "",
                               artist.get("name", "") if isinstance(artist, dict) else ""),
            1,
        ),
    }


Getter = Callable[[str, dict], dict]


def _accept(title: str, artists: list[str], result: dict) -> float | None:
    a = result.get("artist") or {}
    name = a.get("name", "") if isinstance(a, dict) else str(a)
    t = title_score(title, result.get("title") or "")
    ar = artist_score(artists, name) if artists else 100.0
    if t < MIN_TITLE or ar is None or ar < MIN_ARTIST:
        return None
    return 0.7 * t + 0.3 * ar


def _best_from(results, title: str, artists: list[str], get: Getter) -> dict | None:
    if not isinstance(results, list):
        return None  # {"search": {"error": "no result"}}
    scored = [(s, r) for r in results if isinstance(r, dict)
              for s in [_accept(title, artists, r)] if s is not None]
    for _, r in sorted(scored, key=lambda x: -x[0]):
        rec = _record(r, title, artists)
        if rec is None and r.get("id"):
            song = get("/song/", {"id": r["id"]}).get("song")
            rec = _record(song, title, artists) if isinstance(song, dict) else None
        if rec:
            return rec
    return None


def find_song(title: str, artists: list[str], get: Getter) -> dict | None:
    """Search by title + first artist, then by title alone if that finds
    nothing (artist spellings differ, e.g. "Deee‐Lite" with a Unicode hyphen).
    Only results with a matching title *and* artist are accepted; the full
    song record is fetched if a search result lacks tempo/key."""
    clean_title = normalize(title)
    clean_artist = normalize(artists[0]) if artists else ""
    if len(clean_title.replace(" ", "")) < 2:
        return None  # titles like "0" or "( )": the API rejects them, and they'd match anything
    if clean_artist:
        data = get("/search/", {"type": "both", "lookup": f"song:{clean_title} artist:{clean_artist}"})
        rec = _best_from(data.get("search"), title, artists, get)
        if rec:
            return rec
    data = get("/search/", {"type": "song", "lookup": clean_title})
    return _best_from(data.get("search"), title, artists, get)


class Client:
    """Throttled GetSongBPM HTTP client."""

    def __init__(self, api_key: str, base: str, rps: float):
        if not api_key:
            raise SongLookupError(
                "Set GETSONGBPM_API_KEY in backend/.env (free key from getsongbpm.com/api)"
            )
        self.api_key, self.base = api_key, base.rstrip("/")
        self.min_gap = 1.0 / max(rps, 0.05)
        self._last = 0.0
        self.http = httpx.Client(timeout=20, headers={"User-Agent": "mashup-maker/0.1"})
        self.requests = 0

    def get(self, path: str, params: dict) -> dict:
        for attempt in range(4):
            wait = self._last + self.min_gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.requests += 1
            try:
                r = self.http.get(self.base + path, params={"api_key": self.api_key, **params})
            except httpx.HTTPError as e:  # network trouble: retry, without the URL in logs
                if attempt == 3:
                    raise SongLookupError(f"GetSongBPM unreachable: {type(e).__name__}") from None
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(60, int(r.headers.get("Retry-After", 0) or 10 * (attempt + 1))))
                continue
            if r.status_code in (401, 403):
                raise SongLookupError(f"GetSongBPM rejected the API key ({r.status_code}): {r.text[:150]}")
            if r.status_code in (400, 404):
                return {}  # bad/odd query for this one track: treat as no result
            if r.status_code >= 400:
                # Never let the URL (it carries the API key) into an error message.
                raise SongLookupError(f"GetSongBPM error {r.status_code} for {path}")
            try:
                return r.json()
            except ValueError:
                return {}
        raise SongLookupError("GetSongBPM is rate-limiting or unavailable; try again later")

    def close(self) -> None:
        self.http.close()


def pending_rows(rows: list[dict], cached: dict[str, dict], now: float) -> list[dict]:
    """Playlist rows that still need a lookup: not owned locally and not
    already looked up (not-found results are retried after a while)."""
    out = []
    for row in rows:
        if row.get("track_id"):
            continue  # you own it: local analysis wins
        hit = cached.get(lookup_key(row))
        if hit and (hit["status"] == "found" or now - hit["fetched_at"] < NOT_FOUND_TTL):
            continue
        out.append(row)
    return out


def run_lookup(rows: list[dict], ctx, client: Client | None = None) -> dict:
    """Look up every pending playlist row, saving results as it goes."""
    now = time.time()
    cached = db.get_lookups([lookup_key(r) for r in rows])
    todo = pending_rows(rows, cached, now)
    stats = {"total": len(rows), "todo": len(todo), "found": 0, "not_found": 0}
    if not todo:
        ctx.progress(1.0, "Everything already looked up")
        return stats

    client = client or Client(config.GETSONGBPM_API_KEY, config.GETSONGBPM_API,
                              config.GETSONGBPM_RPS)
    try:
        for i, row in enumerate(todo):
            ctx.check_cancelled()
            ctx.progress(i / len(todo), f"{i + 1}/{len(todo)}: {row['title']}")
            rec = find_song(row["title"], row["artists"], client.get)
            if rec:
                stats["found"] += 1
            else:
                rec = {"status": "not_found"}
                stats["not_found"] += 1
            db.save_lookup({"lookup_key": lookup_key(row), "fetched_at": time.time(), **rec})
    finally:
        client.close()
    ctx.progress(1.0, f"Found {stats['found']} of {stats['todo']}")
    return stats
