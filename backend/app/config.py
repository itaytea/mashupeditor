"""Settings read from the environment (and an optional backend/.env file)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")

DATA_DIR = Path(os.environ.get("MASHUP_DATA_DIR", BACKEND_DIR / "data")).expanduser()
DB_PATH = DATA_DIR / "mashup.sqlite3"
STEMS_DIR = DATA_DIR / "stems"
DRAFTS_DIR = DATA_DIR / "drafts"

MUSIC_DIR = os.environ.get("MUSIC_DIR", "")

SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
# Optional user access token; skips the "Connect Spotify" login when set.
SPOTIFY_ACCESS_TOKEN = os.environ.get("SPOTIFY_ACCESS_TOKEN", "")
# Must exactly match a Redirect URI registered in the Spotify dashboard.
SPOTIFY_REDIRECT_URI = os.environ.get(
    "SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8000/callback"
)
# Where to send the browser after the Spotify login completes.
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173/")

# GetSongBPM (https://getsongbpm.com/api): key/BPM for playlist tracks you
# don't have locally. Free key; their terms require a visible backlink.
GETSONGBPM_API_KEY = os.environ.get("GETSONGBPM_API_KEY", "")
GETSONGBPM_API = os.environ.get("GETSONGBPM_API", "https://api.getsong.co")
# Requests per second (the free tier allows roughly 3000 per hour).
GETSONGBPM_RPS = float(os.environ.get("GETSONGBPM_RPS", "0.8"))

DEMUCS_MODEL = os.environ.get("DEMUCS_MODEL", "htdemucs")
DEMUCS_DEVICE = os.environ.get("DEMUCS_DEVICE", "cpu")

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".aiff", ".aif"}


def has_rubberband() -> bool:
    """pyrubberband shells out to the `rubberband` CLI."""
    return shutil.which("rubberband") is not None


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def ensure_dirs() -> None:
    for d in (DATA_DIR, STEMS_DIR, DRAFTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
