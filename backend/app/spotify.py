"""Read playlist track names from the Spotify Web API.

Only metadata (title, artists, duration) is read; no audio is fetched from
Spotify.

Spotify only returns a playlist's items to a *logged-in user*, so the app
uses the Authorization Code flow with PKCE: the user clicks "Connect Spotify",
approves, and Spotify redirects to SPOTIFY_REDIRECT_URI (default
http://127.0.0.1:8000/callback) with a code we exchange for tokens. Tokens are
stored in the local data dir and refreshed automatically.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from urllib.parse import urlencode

import httpx

from . import config

API = "https://api.spotify.com/v1"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SCOPES = "playlist-read-private playlist-read-collaborative"

_PLAYLIST_ID = re.compile(r"(?:playlist[/:])([A-Za-z0-9]{22})")
_BARE_ID = re.compile(r"^[A-Za-z0-9]{22}$")

_pending: dict[str, tuple[str, float]] = {}  # state -> (code_verifier, created)


class SpotifyError(RuntimeError):
    pass


def parse_playlist_id(url_or_id: str) -> str:
    s = url_or_id.strip()
    if _BARE_ID.match(s):
        return s
    m = _PLAYLIST_ID.search(s)
    if not m:
        raise SpotifyError("Could not find a playlist ID in that URL")
    return m.group(1)


def is_configured() -> bool:
    return bool(config.SPOTIFY_ACCESS_TOKEN or config.SPOTIFY_CLIENT_ID)


# --- user login (Authorization Code + PKCE) ----------------------------------

def _token_file():
    return config.DATA_DIR / "spotify_token.json"


def _load_user_token() -> dict | None:
    try:
        return json.loads(_token_file().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_user_token(tok: dict) -> None:
    config.ensure_dirs()
    path = _token_file()
    path.write_text(json.dumps(tok))
    path.chmod(0o600)


def is_connected() -> bool:
    return bool(config.SPOTIFY_ACCESS_TOKEN or _load_user_token())


def connected_user() -> str | None:
    tok = _load_user_token()
    return tok.get("display_name") if tok else None


def disconnect() -> None:
    _token_file().unlink(missing_ok=True)


def login_url() -> str:
    if not config.SPOTIFY_CLIENT_ID:
        raise SpotifyError("Set SPOTIFY_CLIENT_ID in backend/.env first")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    now = time.time()
    for s, (_, created) in list(_pending.items()):
        if now - created > 600:
            del _pending[s]
    _pending[state] = (verifier, now)
    return AUTH_URL + "?" + urlencode({
        "client_id": config.SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": config.SPOTIFY_REDIRECT_URI,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "scope": SCOPES,
        "state": state,
    })


def _token_request(client: httpx.Client, data: dict) -> dict:
    data = {**data, "client_id": config.SPOTIFY_CLIENT_ID}
    headers = {}
    if config.SPOTIFY_CLIENT_SECRET:
        basic = base64.b64encode(
            f"{config.SPOTIFY_CLIENT_ID}:{config.SPOTIFY_CLIENT_SECRET}".encode()
        ).decode()
        headers["Authorization"] = f"Basic {basic}"
    r = client.post(TOKEN_URL, data=data, headers=headers)
    if r.status_code != 200:
        raise SpotifyError(f"Spotify auth failed ({r.status_code}): {r.text[:200]}")
    return r.json()


def finish_login(code: str, state: str) -> str:
    """Exchange the callback code for tokens. Returns the user's display name."""
    pending = _pending.pop(state, None)
    if not pending:
        raise SpotifyError("Login expired or was started elsewhere; try again")
    with httpx.Client(timeout=20) as client:
        body = _token_request(client, {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.SPOTIFY_REDIRECT_URI,
            "code_verifier": pending[0],
        })
        me = client.get(
            f"{API}/me", headers={"Authorization": f"Bearer {body['access_token']}"}
        )
        name = me.json().get("display_name") or me.json().get("id") if me.status_code == 200 else None
    _save_user_token({
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token"),
        "expires_at": time.time() + body.get("expires_in", 3600),
        "display_name": name,
    })
    return name or "Spotify user"


def _access_token(client: httpx.Client) -> str:
    if config.SPOTIFY_ACCESS_TOKEN:
        return config.SPOTIFY_ACCESS_TOKEN
    tok = _load_user_token()
    if not tok:
        raise SpotifyError("Connect your Spotify account first (Connect Spotify button)")
    if tok["expires_at"] > time.time() + 30:
        return tok["access_token"]
    if not tok.get("refresh_token"):
        disconnect()
        raise SpotifyError("Spotify session expired; connect again")
    body = _token_request(client, {
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
    })
    tok.update(
        access_token=body["access_token"],
        expires_at=time.time() + body.get("expires_in", 3600),
        refresh_token=body.get("refresh_token", tok["refresh_token"]),
    )
    _save_user_token(tok)
    return tok["access_token"]


# --- playlist fetch ----------------------------------------------------------

def _get(client: httpx.Client, url: str, params: dict | None = None) -> dict:
    r = client.get(
        url, params=params, headers={"Authorization": f"Bearer {_access_token(client)}"}
    )
    if r.status_code == 429:
        time.sleep(min(int(r.headers.get("Retry-After", "2")), 30))
        return _get(client, url, params)
    if r.status_code == 401:
        raise SpotifyError("Spotify rejected the login; click Connect Spotify again")
    if r.status_code == 403:
        raise SpotifyError(
            "Spotify refused access to this playlist's tracks (403). In development "
            "mode Spotify only exposes playlists you own or collaborate on, and only "
            "to users added under User Management in your app's dashboard."
        )
    if r.status_code == 404:
        raise SpotifyError(
            "Playlist not found. Spotify-owned editorial playlists aren't available "
            "to new apps; copy the tracks into your own playlist."
        )
    if r.status_code != 200:
        raise SpotifyError(f"Spotify API error {r.status_code}: {r.text[:200]}")
    return r.json()


def _parse_item(item: dict) -> dict | None:
    # The playlist-items endpoint calls the entry "item"; older responses "track".
    t = item.get("item") or item.get("track")
    if not t or t.get("type", "track") != "track" or not t.get("name"):
        return None  # podcast episodes, removed tracks
    return {
        "spotify_id": t.get("id"),
        "title": t["name"],
        "artists": [a["name"] for a in t.get("artists", []) if a.get("name")],
        "duration_ms": t.get("duration_ms"),
    }


def fetch_playlist(url_or_id: str) -> dict:
    """Return {id, name, tracks: [{spotify_id, title, artists, duration_ms}]}."""
    pl_id = parse_playlist_id(url_or_id)
    with httpx.Client(timeout=20) as client:
        meta = _get(client, f"{API}/playlists/{pl_id}", {"fields": "name"})
        tracks: list[dict] = []
        url: str | None = f"{API}/playlists/{pl_id}/items"
        params: dict | None = {"limit": 50, "additional_types": "track"}
        while url:
            page = _get(client, url, params)
            tracks.extend(t for t in map(_parse_item, page.get("items", [])) if t)
            url, params = page.get("next"), None  # `next` already has the query
    return {"id": pl_id, "name": meta.get("name", pl_id), "tracks": tracks}
