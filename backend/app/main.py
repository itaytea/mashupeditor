"""FastAPI app: library analysis, playlist matching, pair ranking, drafts."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from . import analysis, config, db, fuzzy, jobs, matching, render, songlookup, spotify

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
# httpx logs every request URL at INFO, which would print API keys.
logging.getLogger("httpx").setLevel(logging.WARNING)

_state: dict[str, str] = {"music_dir": config.MUSIC_DIR}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    yield


app = FastAPI(title="Mashup Maker", lifespan=lifespan)


# --- helpers ----------------------------------------------------------------

def _library_tracks() -> list[dict]:
    folder = _state.get("music_dir") or None
    return db.list_tracks(folder)


def _rematch_playlist() -> None:
    pl = db.get_playlist()
    if not pl:
        return
    local = [t for t in _library_tracks() if not t["error"]]
    matches = fuzzy.match_playlist(pl["tracks"], local)
    db.set_playlist_matches({m.position: (m.track_id, m.score) for m in matches})


def _track_or_404(track_id: int) -> dict:
    t = db.get_track(track_id)
    if not t:
        raise HTTPException(404, f"track {track_id} not found")
    if t["error"] or t["bpm"] is None:
        raise HTTPException(400, f"track {track_id} has no analysis")
    return t


# --- config / library -------------------------------------------------------

@app.get("/api/config")
def get_config() -> dict:
    return {
        "music_dir": _state.get("music_dir", ""),
        "spotify_configured": spotify.is_configured(),
        "spotify_connected": spotify.is_connected(),
        "spotify_user": spotify.connected_user(),
        "rubberband": config.has_rubberband(),
        "ffmpeg": config.has_ffmpeg(),
        "demucs_model": config.DEMUCS_MODEL,
        "lookup_configured": bool(config.GETSONGBPM_API_KEY),
    }


class ScanRequest(BaseModel):
    folder: str


@app.post("/api/library/scan")
def scan_library(req: ScanRequest) -> dict:
    folder = Path(req.folder).expanduser()
    if not folder.is_dir():
        raise HTTPException(400, f"Not a folder: {folder}")
    _state["music_dir"] = str(folder.resolve())

    def work(ctx: jobs.JobContext) -> dict:
        stats = analysis.scan_folder(folder, ctx.progress)
        _rematch_playlist()
        return stats

    return {"job_id": jobs.submit("scan", {"folder": str(folder)}, work)}


@app.get("/api/library")
def get_library() -> list[dict]:
    return _library_tracks()


# --- spotify login ----------------------------------------------------------

@app.get("/api/spotify/login")
def spotify_login() -> RedirectResponse:
    try:
        return RedirectResponse(spotify.login_url())
    except spotify.SpotifyError as e:
        raise HTTPException(400, str(e))


@app.get("/callback")
def spotify_callback(code: str | None = None, state: str = "", error: str | None = None):
    """Spotify redirects here (SPOTIFY_REDIRECT_URI) after the user approves."""
    if error or not code:
        msg = f"Spotify login cancelled ({error or 'no code'})"
    else:
        try:
            spotify.finish_login(code, state)
            msg = ""
        except spotify.SpotifyError as e:
            msg = str(e)
    query = urlencode({"spotify_error": msg}) if msg else "spotify=connected"
    return RedirectResponse(f"{config.FRONTEND_URL}?{query}")


@app.post("/api/spotify/logout")
def spotify_logout() -> dict:
    spotify.disconnect()
    return {"ok": True}


# --- playlist ---------------------------------------------------------------

class PlaylistRequest(BaseModel):
    url: str


@app.post("/api/playlist")
def load_playlist(req: PlaylistRequest) -> dict:
    try:
        pl = spotify.fetch_playlist(req.url)
    except spotify.SpotifyError as e:
        raise HTTPException(400, str(e))
    db.save_playlist(pl["id"], pl["name"], req.url, pl["tracks"])
    _rematch_playlist()
    return get_playlist()


@app.get("/api/playlist")
def get_playlist() -> dict | None:
    pl = db.get_playlist()
    if not pl:
        return None
    by_id = {t["id"]: t for t in _library_tracks()}
    lookups = db.get_lookups([songlookup.lookup_key(r) for r in pl["tracks"]])
    for row in pl["tracks"]:
        row["local"] = by_id.get(row["track_id"]) if row["track_id"] else None
        hit = lookups.get(songlookup.lookup_key(row))
        row["lookup"] = hit if hit and hit["status"] == "found" else None
        row["lookup_status"] = hit["status"] if hit else None
        row["entity"] = _row_entity(row)
    pl["lookup_configured"] = bool(config.GETSONGBPM_API_KEY)
    return pl


# --- pairs ------------------------------------------------------------------

def _local_entity(t: dict) -> dict:
    return {
        "id": f"t{t['id']}", "local_id": t["id"], "source": "analysis",
        "title": t["title"], "artist": t["artist"], "bpm": t["bpm"],
        "key_pc": t["key_pc"], "mode": t["mode"], "key_name": t["key_name"],
        "camelot": t["camelot"],
    }


def _row_entity(row: dict) -> dict | None:
    """What a playlist row contributes to ranking: your analysed file if you
    have one, otherwise looked-up key/BPM, otherwise nothing."""
    local = row.get("local")
    if local and not local["error"] and local["bpm"]:
        return _local_entity(local)
    hit = row.get("lookup")
    if hit:
        return {
            "id": f"s{songlookup.lookup_key(row)}", "local_id": None, "source": "getsongbpm",
            "title": row["title"], "artist": ", ".join(row["artists"]), "bpm": hit["bpm"],
            "key_pc": hit["key_pc"], "mode": hit["mode"], "key_name": hit["key_name"],
            "camelot": hit["camelot"],
        }
    return None


@app.get("/api/pairs")
def get_pairs(scope: str = "playlist", limit: int = 100, min_score: float = 0.0,
              focus: str | None = None, owned: bool = False) -> dict:
    """Rank pairs across the playlist (your files plus looked-up tracks) or
    across your whole analysed library. `focus` limits to pairs with one track;
    `owned` keeps only tracks you have a file for (pairs you can render)."""
    if scope == "playlist":
        pl = get_playlist()
        seen: dict[str, dict] = {}
        for row in pl["tracks"] if pl else []:
            if row["entity"]:
                seen.setdefault(row["entity"]["id"], row["entity"])
        tracks = list(seen.values())
    elif scope == "library":
        tracks = [_local_entity(t) for t in _library_tracks() if not t["error"] and t["bpm"]]
    else:
        raise HTTPException(400, "scope must be 'playlist' or 'library'")
    if owned:
        tracks = [t for t in tracks if t["local_id"]]
    pairs = matching.rank_pairs(tracks, limit=min(limit, 1000), min_score=min_score,
                                focus=focus)
    ids = {p["a"] for p in pairs} | {p["b"] for p in pairs}
    by_id = {t["id"]: t for t in tracks if t["id"] in ids or t["id"] == focus}
    return {"tracks": by_id, "pairs": pairs, "total_tracks": len(tracks)}


# --- key/BPM lookup ---------------------------------------------------------

@app.post("/api/lookup")
def start_lookup() -> dict:
    if not config.GETSONGBPM_API_KEY:
        raise HTTPException(
            400, "Set GETSONGBPM_API_KEY in backend/.env (free key from getsongbpm.com/api), "
                 "then restart the backend."
        )
    pl = db.get_playlist()
    if not pl:
        raise HTTPException(400, "Load a Spotify playlist first")
    running = [j for j in db.list_jobs("lookup", 5) if j["status"] in ("queued", "running")]
    if running:
        return {"job_id": running[0]["id"]}
    rows = pl["tracks"]
    return {"job_id": jobs.submit(
        "lookup", {"title": f"Key/BPM lookup: {pl['name']}"},
        lambda ctx: songlookup.run_lookup(rows, ctx), queue="net",
    )}


# --- drafts -----------------------------------------------------------------

class DraftRequest(BaseModel):
    vocals_id: int = Field(description="track whose vocals are used (A)")
    instrumental_id: int = Field(description="track whose instrumental is used (B)")
    duration: float = Field(60.0, ge=10, le=180)
    semitones: int | None = Field(None, ge=-6, le=6)
    vocal_gain_db: float = Field(0.0, ge=-12, le=12)


@app.post("/api/drafts")
def create_draft(req: DraftRequest) -> dict:
    if req.vocals_id == req.instrumental_id:
        raise HTTPException(400, "pick two different tracks")
    a, b = _track_or_404(req.vocals_id), _track_or_404(req.instrumental_id)

    def work(ctx: jobs.JobContext) -> dict:
        return render.render_draft(
            a, b, duration=req.duration, semitones=req.semitones,
            vocal_gain_db=req.vocal_gain_db, progress=ctx.progress,
        )

    params = req.model_dump() | {"title": f"{a['title']} × {b['title']}"}
    return {"job_id": jobs.submit("draft", params, work)}


@app.get("/api/drafts")
def list_drafts() -> list[dict]:
    return [j["result"] for j in db.list_jobs("draft", limit=100)
            if j["status"] == "done" and j["result"]]


@app.get("/api/drafts/{draft_id}/{filename}")
def draft_file(draft_id: str, filename: str) -> FileResponse:
    if filename not in ("draft.wav", "draft.mp3"):
        raise HTTPException(404)
    path = (config.DRAFTS_DIR / draft_id / filename).resolve()
    if config.DRAFTS_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404)
    media = "audio/wav" if filename.endswith(".wav") else "audio/mpeg"
    return FileResponse(path, media_type=media, filename=f"mashup-{draft_id}{path.suffix}")


# --- jobs -------------------------------------------------------------------

@app.get("/api/jobs")
def list_jobs(kind: str | None = None) -> list[dict]:
    return db.list_jobs(kind)


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    return {"cancelled": jobs.cancel(job_id)}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404)
    return job
