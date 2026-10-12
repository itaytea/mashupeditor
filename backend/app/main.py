"""FastAPI app: library analysis, playlist matching, pair ranking, drafts."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from . import analysis, config, db, fuzzy, jobs, matching, render, spotify

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

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
    for row in pl["tracks"]:
        row["local"] = by_id.get(row["track_id"]) if row["track_id"] else None
    return pl


# --- pairs ------------------------------------------------------------------

@app.get("/api/pairs")
def get_pairs(scope: str = "playlist", limit: int = 100, min_score: float = 0.0) -> dict:
    """Rank pairs among playlist-matched files (scope=playlist) or the whole
    analysed library (scope=library)."""
    tracks = [t for t in _library_tracks() if not t["error"] and t["bpm"]]
    if scope == "playlist":
        pl = db.get_playlist()
        ids = {r["track_id"] for r in pl["tracks"] if r["track_id"]} if pl else set()
        tracks = [t for t in tracks if t["id"] in ids]
    elif scope != "library":
        raise HTTPException(400, "scope must be 'playlist' or 'library'")
    by_id = {t["id"]: t for t in tracks}
    pairs = matching.rank_pairs(tracks, limit=limit, min_score=min_score)
    return {"tracks": by_id, "pairs": pairs}


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


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404)
    return job
