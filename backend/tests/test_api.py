"""HTTP-level tests of the FastAPI app with Spotify and audio analysis stubbed."""

import time

import pytest

from app import analysis, config, db, main, spotify
from tests.conftest import make_track


def wait_job(client, job_id, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish")


def add_track(tmp_path, name, **over):
    f = tmp_path / f"{name}.mp3"
    f.write_bytes(b"x")
    return db.upsert_track(make_track(f, title=name, **over))


# --- config ----------------------------------------------------------------

def test_config_defaults(client):
    body = client.get("/api/config").json()
    assert body["spotify_configured"] is False
    assert body["spotify_connected"] is False
    assert body["lookup_configured"] is False
    assert body["demucs_model"] == config.DEMUCS_MODEL
    assert body["watch"] is False


def test_watch_toggle_persists(client):
    assert client.post("/api/find/watch", json={"watch": True}).json() == {"watch": True}
    assert client.get("/api/config").json()["watch"] is True


# --- library ---------------------------------------------------------------

def test_library_empty(client):
    assert client.get("/api/library").json() == []


def test_library_lists_only_existing_files(client, tmp_path):
    add_track(tmp_path, "Alpha")
    gone = tmp_path / "gone.mp3"
    db.upsert_track(make_track(gone, title="Gone"))
    titles = [t["title"] for t in client.get("/api/library").json()]
    assert titles == ["Alpha"]


def test_scan_rejects_missing_folder(client, tmp_path):
    r = client.post("/api/library/scan", json={"folder": str(tmp_path / "nope")})
    assert r.status_code == 400


def test_scan_job_analyses_files(client, tmp_path, monkeypatch):
    music = tmp_path / "music"
    music.mkdir()
    for n in ("a", "b"):
        (music / f"Artist - {n}.mp3").write_bytes(b"x")
    monkeypatch.setattr(analysis, "analyze_file", lambda path: {
        "duration": 100.0, "bpm": 124.0, "key_pc": 9, "mode": "minor",
        "key_name": "A minor", "camelot": "8A", "key_confidence": 0.8,
    })
    r = client.post("/api/library/scan", json={"folder": str(music)})
    assert r.status_code == 200
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["analyzed"] == 2
    assert job["progress"] == 1.0
    lib = client.get("/api/library").json()
    assert len(lib) == 2 and {t["bpm"] for t in lib} == {124.0}
    assert client.get("/api/config").json()["music_dir"] == str(music.resolve())

    # A second scan reuses the cache.
    job2 = wait_job(client, client.post("/api/library/scan",
                                        json={"folder": str(music)}).json()["job_id"])
    assert job2["result"]["cached"] == 2


def test_scan_records_analysis_failure(client, tmp_path, monkeypatch):
    music = tmp_path / "music"
    music.mkdir()
    (music / "Bad - Song.mp3").write_bytes(b"x")

    def boom(path):
        raise RuntimeError("corrupt audio")

    monkeypatch.setattr(analysis, "analyze_file", boom)
    job = wait_job(client, client.post("/api/library/scan",
                                       json={"folder": str(music)}).json()["job_id"])
    assert job["status"] == "done"
    assert job["result"]["failed"] == 1
    assert db.list_tracks()[0]["error"] == "corrupt audio"


# --- find ------------------------------------------------------------------

def test_find_requires_folder_and_playlist(client, tmp_path):
    assert client.post("/api/find", json={"folder": str(tmp_path / "nope")}).status_code == 400
    r = client.post("/api/find", json={"folder": str(tmp_path)})
    assert r.status_code == 400
    assert "playlist" in r.json()["detail"].lower()


# --- spotify / playlist ----------------------------------------------------

def test_spotify_login_without_client_id(client):
    r = client.get("/api/spotify/login", follow_redirects=False)
    assert r.status_code == 400


def test_spotify_callback_without_code_redirects_with_error(client):
    r = client.get("/callback", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "spotify_error=" in r.headers["location"]


def test_spotify_logout(client):
    assert client.post("/api/spotify/logout").json() == {"ok": True}


def test_playlist_none_before_load(client):
    r = client.get("/api/playlist")
    assert r.status_code == 200 and r.json() is None


def test_playlist_error_is_400(client, monkeypatch):
    def fail(url):
        raise spotify.SpotifyError("no access")

    monkeypatch.setattr(spotify, "fetch_playlist", fail)
    r = client.post("/api/playlist", json={"url": "x"})
    assert r.status_code == 400 and r.json()["detail"] == "no access"


@pytest.fixture
def playlist(client, tmp_path, monkeypatch):
    """Two owned tracks + one unowned track in a stubbed Spotify playlist."""
    add_track(tmp_path, "Alpha", artist="Ann", bpm=120.0, key_pc=0, mode="major",
              key_name="C major", camelot="8B")
    add_track(tmp_path, "Beta", artist="Bob", bpm=122.0, key_pc=7, mode="major",
              key_name="G major", camelot="9B")
    monkeypatch.setattr(spotify, "fetch_playlist", lambda url: {
        "id": "pl1", "name": "Mix",
        "tracks": [
            {"spotify_id": "s1", "title": "Alpha", "artists": ["Ann"], "duration_ms": 1000},
            {"spotify_id": "s2", "title": "Beta", "artists": ["Bob"], "duration_ms": 1000},
            {"spotify_id": "s3", "title": "Zzzz Unknown", "artists": ["Nobody"],
             "duration_ms": 1000},
        ],
    })
    r = client.post("/api/playlist", json={"url": "https://open.spotify.com/playlist/pl1"})
    assert r.status_code == 200
    return r.json()


def test_playlist_matches_local_tracks(client, playlist):
    assert playlist["name"] == "Mix"
    rows = playlist["tracks"]
    assert [r["title"] for r in rows] == ["Alpha", "Beta", "Zzzz Unknown"]
    assert rows[0]["local"]["title"] == "Alpha"
    assert rows[1]["local"]["title"] == "Beta"
    assert rows[2]["local"] is None and rows[2]["entity"] is None
    assert rows[0]["entity"]["source"] == "analysis"
    assert client.get("/api/playlist").json()["tracks"][0]["track_id"] == rows[0]["track_id"]


def test_find_with_all_tracks_owned_is_rejected(client, tmp_path, monkeypatch):
    add_track(tmp_path, "Alpha", artist="Ann")
    monkeypatch.setattr(spotify, "fetch_playlist", lambda url: {
        "id": "p", "name": "P",
        "tracks": [{"spotify_id": "s1", "title": "Alpha", "artists": ["Ann"],
                    "duration_ms": 1}],
    })
    client.post("/api/playlist", json={"url": "p"})
    r = client.post("/api/find", json={"folder": str(tmp_path)})
    assert r.status_code == 400
    assert "every playlist track" in r.json()["detail"]


# --- pairs -----------------------------------------------------------------

def test_pairs_invalid_scope(client):
    assert client.get("/api/pairs", params={"scope": "bogus"}).status_code == 400


def test_pairs_empty(client):
    body = client.get("/api/pairs").json()
    assert body == {"tracks": {}, "pairs": [], "total_tracks": 0}


def test_pairs_playlist_scope(client, playlist):
    body = client.get("/api/pairs").json()
    assert body["total_tracks"] == 2
    assert len(body["pairs"]) == 1
    pair = body["pairs"][0]
    assert {pair["a"], pair["b"]} <= set(body["tracks"])
    assert pair["score"] > 0


def test_pairs_library_scope_and_filters(client, tmp_path):
    a = add_track(tmp_path, "A", bpm=120.0, key_pc=0, mode="major", camelot="8B")
    b = add_track(tmp_path, "B", bpm=121.0, key_pc=0, mode="major", camelot="8B")
    add_track(tmp_path, "C", bpm=90.0, key_pc=6, mode="minor", camelot="2A")
    add_track(tmp_path, "Broken", error="bad", bpm=None)
    body = client.get("/api/pairs", params={"scope": "library"}).json()
    assert body["total_tracks"] == 3
    assert len(body["pairs"]) == 3
    assert body["pairs"] == sorted(body["pairs"], key=lambda p: -p["score"])

    limited = client.get("/api/pairs", params={"scope": "library", "limit": 1}).json()
    assert len(limited["pairs"]) == 1
    assert {limited["pairs"][0]["a"], limited["pairs"][0]["b"]} == {f"t{a}", f"t{b}"}

    focused = client.get("/api/pairs", params={"scope": "library", "focus": f"t{a}"}).json()
    assert focused["pairs"] and all(f"t{a}" in (p["a"], p["b"]) for p in focused["pairs"])

    high = client.get("/api/pairs", params={"scope": "library", "min_score": 10000}).json()
    assert high["pairs"] == []


# --- lookup ----------------------------------------------------------------

def test_lookup_requires_api_key(client):
    r = client.post("/api/lookup")
    assert r.status_code == 400 and "GETSONGBPM_API_KEY" in r.json()["detail"]


def test_lookup_requires_playlist(client, monkeypatch):
    monkeypatch.setattr(config, "GETSONGBPM_API_KEY", "k")
    r = client.post("/api/lookup")
    assert r.status_code == 400 and "playlist" in r.json()["detail"].lower()


# --- drafts ----------------------------------------------------------------

def test_draft_validation(client, tmp_path):
    a = add_track(tmp_path, "A")
    assert client.post("/api/drafts", json={"vocals_id": a, "instrumental_id": a}
                       ).status_code == 400
    assert client.post("/api/drafts", json={"vocals_id": a, "instrumental_id": 999}
                       ).status_code == 404
    broken = db.upsert_track(make_track(tmp_path / "x.mp3", error="bad", bpm=None))
    assert client.post("/api/drafts", json={"vocals_id": a, "instrumental_id": broken}
                       ).status_code == 400
    assert client.post("/api/drafts", json={"vocals_id": a, "instrumental_id": a + 1,
                                            "duration": 5}).status_code == 422


def test_draft_job_with_stubbed_render(client, tmp_path, monkeypatch):
    a = add_track(tmp_path, "A")
    b = add_track(tmp_path, "B")
    calls = {}

    def fake_render(ta, tb, **kw):
        calls.update(a=ta["title"], b=tb["title"], **{k: v for k, v in kw.items()
                                                       if k != "progress"})
        kw["progress"](0.5, "halfway")
        return {"id": "d1", "title": "A x B"}

    monkeypatch.setattr(main.render, "render_draft", fake_render)
    r = client.post("/api/drafts", json={"vocals_id": a, "instrumental_id": b,
                                         "duration": 30})
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"] == {"id": "d1", "title": "A x B"}
    assert job["params"]["queue"] == "render"
    assert calls["a"] == "A" and calls["b"] == "B" and calls["duration"] == 30
    assert client.get("/api/drafts").json() == [{"id": "d1", "title": "A x B"}]


def test_draft_file_serving(client, data_dir):
    d = config.DRAFTS_DIR / "abc"
    d.mkdir(parents=True)
    (d / "draft.wav").write_bytes(b"RIFFdata")
    r = client.get("/api/drafts/abc/draft.wav")
    assert r.status_code == 200 and r.content == b"RIFFdata"
    assert r.headers["content-type"] == "audio/wav"
    assert client.get("/api/drafts/abc/draft.mp3").status_code == 404
    assert client.get("/api/drafts/abc/other.txt").status_code == 404
    assert client.get("/api/drafts/nope/draft.wav").status_code == 404


# --- jobs ------------------------------------------------------------------

def test_job_not_found(client):
    assert client.get("/api/jobs/missing").status_code == 404


def test_cancel_unknown_job(client):
    assert client.post("/api/jobs/missing/cancel").json() == {"cancelled": False}


def test_jobs_listing_and_kind_filter(client):
    db.create_job("j1", "scan", {"title": "s"})
    db.create_job("j2", "draft", {"title": "d"})
    assert {j["id"] for j in client.get("/api/jobs").json()} == {"j1", "j2"}
    only = client.get("/api/jobs", params={"kind": "draft"}).json()
    assert [j["id"] for j in only] == ["j2"]
    assert client.get("/api/jobs/j1").json()["params"] == {"title": "s"}


def test_cancel_queued_job_via_api(client):
    db.create_job("q1", "scan", {})
    assert client.post("/api/jobs/q1/cancel").json() == {"cancelled": True}
    job = client.get("/api/jobs/q1").json()
    assert job["status"] == "cancelled" and job["cancel"] == 1
    assert client.post("/api/jobs/q1/cancel").json() == {"cancelled": False}


def test_startup_marks_stale_jobs_failed(data_dir):
    from fastapi.testclient import TestClient

    db.init()
    db.create_job("old", "scan", {})
    db.update_job("old", status="running")
    db.create_job("fin", "scan", {})
    db.update_job("fin", status="done")
    db._conn.close()
    db._conn = None
    with TestClient(main.app) as c:
        old = c.get("/api/jobs/old").json()
        assert old["status"] == "failed" and old["error"] == "server restarted"
        assert c.get("/api/jobs/fin").json()["status"] == "done"
    db._conn.close()
    db._conn = None
