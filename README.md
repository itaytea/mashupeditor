# Mashup Maker

> 🚧 **Work in progress.** This is an early personal project. It runs, but expect
> rough edges: drafts can still sound off, APIs and the data format may change
> without notice, and there is no packaged release. See [Status](#status) below.

A local assistant for making mashup drafts from **your own audio files**. Give it
a Spotify playlist URL (used only for track names) and a folder of audio you own.
It matches the two, detects BPM and key, ranks which pairs will mix well, and
renders a 60-second rough draft: vocals of song A over the instrumental of song B.

Song key and BPM data for tracks you don't own is provided by [GetSongBPM](https://getsongbpm.com).

> **Only audio you own is processed.** Nothing is downloaded from Spotify or
> anywhere else. The Spotify Web API is used solely to read playlist metadata
> (titles, artists, durations). All analysis, stem separation and rendering run
> locally on files in the folder you choose. Stems and drafts stay in
> `backend/data/` on your machine. Make sure you have the rights to any audio you
> process, and to anything you do with the drafts afterwards.

## How it works

| Milestone | What it does | Code |
|---|---|---|
| **M1: Analysis** | Scans the folder, reads title/artist from tags (or `Artist - Title` filenames), detects BPM (librosa beat tracker) and key (chroma + Krumhansl-Kessler profiles). Results are cached in SQLite, keyed on path + size + mtime, so rescans only analyse new or changed files. | `app/analysis.py`, `app/db.py` |
| **M2: Pair ranking** | Scores every pair by key compatibility on the Camelot wheel (same key, ±1, relative major/minor; otherwise the smallest pitch shift that would fix it) and tempo closeness (including half/double time). Gives a ranked list with the reasons. | `app/camelot.py`, `app/tempo.py`, `app/matching.py` |
| **M3: Draft render** | Demucs (`htdemucs`) splits both songs into vocals and instrumental (cached). A's vocals are time-stretched and pitch-shifted with Rubber Band to fit B, beat-aligned (beat grid, then refined by onset cross-correlation), level-matched and mixed. Exports WAV and MP3. Runs as a background job with progress. | `app/stems.py`, `app/render.py`, `app/jobs.py` |
| **M4: UI** | A Winamp-style skin. The player window plays drafts through Web Audio, with a live spectrum analyser, volume and balance. The playlist window shows Camelot key and BPM for each track. Matches and Drafts are playlist-style windows with live job progress and WAV/MP3 downloads. | `frontend/src/` |

**Key/BPM lookup (optional).** Spotify no longer gives new apps key/BPM data
(its audio-features endpoint returns 403), so for playlist tracks you *don't*
own, the app can look the numbers up by title and artist from
[GetSongBPM](https://getsongbpm.com). Press the magnifier in the playlist
window. Matches then ranks the whole playlist, marking each track ● (your file,
analysed locally) or ○ (looked up). You can only render a pair once you have
both files. Local analysis always wins over looked-up values. The lookup is
throttled to stay under the free tier's limit (about 3000 requests/hour), so a
4000-track playlist takes roughly 1.5 hours the first time. It runs on its own
worker, can be cancelled, and results are cached (misses are retried after 30
days). The code is in `app/songlookup.py`.

**FIND folder.** The FIND row in the playlist window searches another folder
(default `~/Downloads`) for playlist tracks you don't own yet. It reads only
names and tags for every file, and runs the slow audio analysis only on files
that match a wanted track, so a folder full of unrelated downloads stays quick.
With **WATCH** on, it re-checks every 30 seconds and picks up new files
automatically. Names like `Artist - Title (Official Video) [videoId].mp3` are
cleaned before matching. Select a track you don't own and press ▶ (or use the
▶ buttons in Matches) to search YouTube for it. The code is in
`app/analysis.py` (`find_in_folder`) and `app/watcher.py`.

**Phrases and quantize.** Before rendering, both songs get a beat grid, downbeats
(the "1" of each bar, found from kicks and chord changes) and 8-bar phrases (the
bar where the music changes most), each labelled intro / build / drop /
breakdown / outro by energy. B's window is a run of whole phrases, preferring
one with a drop or a build into one. A's vocals start on one of A's phrase
boundaries, so the two songs' phrases line up. If one constant stretch leaves
A's beats drifting against B's (over 40 ms), **QUANT AUTO** warps A beat by beat
onto B's actual beats with Rubber Band, and keeps that only if it measures
tighter. Beats are placed using a kick-weighted onset envelope (off-beat
hi-hats otherwise pull trackers half a beat off), and the tempo is counted from
the full-band one. The code is in `app/structure.py`, `app/sync.py` and `app/render.py`.

Playlist rows are matched to local files with fuzzy matching (rapidfuzz). Before
comparing, it strips "feat.", "Remastered 2011", "Radio Edit" and similar suffixes,
then assigns matches one-to-one from best score down (`app/fuzzy.py`).

### Scoring

* **Key** (`camelot.py`): same code = 1.0, relative major/minor (8A↔8B) = 0.9,
  adjacent (±1 same letter, wrapping 12→1) = 0.85. Pairs that clash get 0.5 if
  a ±1 semitone shift of A makes them compatible, 0.3 for ±2, otherwise 0. The
  render applies that shift automatically.
* **Tempo** (`tempo.py`): tries A×1, A×2 and A×½ against B. The score falls
  linearly from 1.0 at 0% apart to 0 at 8% apart. Half/double-time matches
  get ×0.9 so a 1:1 match ranks first.
* **Pair** = 0.55 × key + 0.45 × tempo.

## Status

Work in progress. What works and what doesn't yet:

**Works**
- Library analysis with cached, versioned results; precise BPM at the strongest beat level
- Spotify playlist import (needs a logged-in Spotify account), fuzzy matching to local files
- Key/BPM lookup for tracks you don't own (GetSongBPM), with a strict title + artist match
- Pair ranking over thousands of tracks; duplicate copies of a song are never paired
- Draft rendering that measures tempo per section and locks A's rhythm onto B's,
  with a sync rating (ms of drift) on every draft
- Downbeats and 8-bar phrases with intro / build / drop / breakdown / outro labels;
  drafts start on a phrase boundary and are cut to whole phrases
- Quantize (AUTO / ON / OFF): warps a loose song beat-by-beat onto the other's grid
- FIND/WATCH folder matching, YouTube search links, a JOBS view with cancel

**Known issues / not done yet**
- Key detection is weak on dense or electronic mixes. When it's unsure, drafts are
  left unshifted, so vocals can clash harmonically
- Drafts are rough: one window of whole phrases, basic level balancing, no EQ or effects
- Section labels are energy heuristics; they assume 4/4 and 8-bar phrasing
- Songs whose beat level is ambiguous (the tracker can't settle on one tempo) can't be
  quantized; those drafts are flagged LOOSE instead of being warped
- Songs that change tempo work only if one of their sections locks onto the other song
- Spotify's development mode may refuse playlists you don't own
- Single-user and local only: no auth, no packaging, and the UI is desktop-first

## Setup

Requirements: Python 3.11+ (3.12 tested), Node 20+, `ffmpeg`, and ideally
Rubber Band:

```bash
brew install ffmpeg rubberband
```

Without the `rubberband` CLI, drafts fall back to librosa's phase vocoder,
which sounds much rougher, and the UI shows a warning.

### Backend

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # then add your Spotify credentials
.venv/bin/uvicorn app.main:app --reload --port 8000
```

The first draft downloads the Demucs model (~80 MB) to `~/.cache/torch`.

### Spotify credentials

1. Create an app at <https://developer.spotify.com/dashboard> with **Web API**
   enabled, and add the Redirect URI `http://127.0.0.1:8000/callback`.
2. Put its Client ID and Client Secret in `backend/.env`.
3. In the UI, click **Connect Spotify** under the playlist field and approve.
   Tokens are stored in `backend/data/spotify_token.json` and refreshed
   automatically.

Spotify only returns a playlist's tracks to a logged-in user, so app-only
(client-credentials) access gets a 403. In development mode, Spotify may also
limit this to playlists you own or collaborate on, and to accounts listed under
**User Management** in the dashboard. Spotify's own editorial playlists aren't
available to new apps. To use one, copy its tracks into your own playlist.
Without Spotify you can still rank pairs across your whole library.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. Vite proxies `/api` to the backend on port 8000.

## Usage

1. Enter your music folder and click **Analyse**. Progress shows live, and
   rescans are quick because results are cached.
2. Paste a playlist URL and click **Load**. Each row shows the local file it
   matched (with a match %) plus Camelot key and BPM badges.
3. In **Matches**, use **PL** / **LIB** to rank pairs from the playlist or the whole
   library. Click a track in the playlist to show only pairs that include it.
   Select a pair and press **A VOX + B INST** (or **B VOX + A INST**).
4. The draft renders in the background. Demucs takes roughly 0.5–1× track
   length on CPU, and the stems are cached afterwards. With **AUTO** on in the player
   window, the draft plays in the player as soon as it finishes. Download it
   from the Drafts window as WAV or MP3.

## Configuration (`backend/.env`)

| Variable | Default | |
|---|---|---|
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | – | From the Spotify dashboard |
| `SPOTIFY_ACCESS_TOKEN` | – | Optional user token (skips Connect Spotify) |
| `SPOTIFY_REDIRECT_URI` | `http://127.0.0.1:8000/callback` | Must match the dashboard |
| `MUSIC_DIR` | – | Pre-fills the folder field |
| `GETSONGBPM_API_KEY` | – | Enables key/BPM lookup for tracks you don't own |
| `GETSONGBPM_RPS` | `0.8` | Lookup requests per second |
| `MASHUP_DATA_DIR` | `backend/data` | SQLite DB, stems, drafts |
| `DEMUCS_MODEL` | `htdemucs` | e.g. `htdemucs_ft` (better, 4× slower) |
| `DEMUCS_DEVICE` | `cpu` | `cuda` or `mps` if supported |

## API

| Method | Path | |
|---|---|---|
| GET | `/api/config` | Feature flags (Spotify, rubberband, ffmpeg) |
| POST | `/api/library/scan` `{folder}` | Starts an analysis job → `{job_id}` |
| GET | `/api/library` | Analysed tracks |
| POST/GET | `/api/playlist` `{url}` | Fetch + match / current playlist |
| GET | `/api/pairs?scope=playlist\|library&limit=100&focus=&owned=` | Ranked pairs with reasons |
| POST | `/api/drafts` `{vocals_id, instrumental_id, duration?, semitones?, vocal_gain_db?, quantize?}` | Starts a render job (`quantize`: auto/on/off) |
| GET | `/api/drafts`, `/api/drafts/{id}/draft.wav\|mp3` | Finished drafts |
| POST | `/api/find` `{folder, watch?}` | Look for unowned playlist tracks in a folder |
| POST | `/api/find/watch` `{watch}` | Toggle watching the FIND folder |
| POST | `/api/lookup` | Starts the GetSongBPM key/BPM lookup for the playlist |
| GET | `/api/jobs`, `/api/jobs/{id}` | Job status and progress |
| POST | `/api/jobs/{id}/cancel` | Cancel a queued or running job |

## Tests

```bash
cd backend
.venv/bin/python -m pytest            # all tests
.venv/bin/python -m pytest -m "not slow"   # skip the librosa synthetic-audio test
```

The tests cover Camelot mapping and key compatibility (same, relative, ±1 with
wrap-around, suggested pitch shifts), tempo scoring (closeness, half and double
time, stretch math), pair ranking, fuzzy title/artist matching, and key/BPM
detection on synthetic audio.

## Limitations

* Drafts are rough. The tempo is assumed constant, downbeats are approximated as
  every 4th beat, and the render picks the most energetic window of each song
  automatically.
* Key detection is a profile-matching heuristic. It can confuse relative
  major/minor or a fifth, so check the confidence (hover a key badge).
* One job runs at a time, so separations don't compete for the CPU.
