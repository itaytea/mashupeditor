import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { ProgressBar } from "./Badges.jsx";
import PlaylistView from "./PlaylistView.jsx";
import MatchList from "./MatchList.jsx";
import Drafts from "./Drafts.jsx";

const ACTIVE = new Set(["queued", "running"]);

export default function App() {
  const [config, setConfig] = useState(null);
  const [folder, setFolder] = useState("");
  const [playlistUrl, setPlaylistUrl] = useState("");
  const [library, setLibrary] = useState([]);
  const [playlist, setPlaylist] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [drafts, setDrafts] = useState([]);
  const [error, setError] = useState("");
  const [loadingPlaylist, setLoadingPlaylist] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0); // bump to re-rank pairs
  const [selectedDraft, setSelectedDraft] = useState(null);
  const finished = useRef(new Set());

  const refreshData = useCallback(async () => {
    const [lib, pl, dr] = await Promise.all([api.library(), api.playlist(), api.drafts()]);
    setLibrary(lib);
    setPlaylist(pl);
    setDrafts(dr);
    setRefreshKey((k) => k + 1);
  }, []);

  useEffect(() => {
    // Returning from the Spotify login redirect.
    const params = new URLSearchParams(window.location.search);
    if (params.has("spotify_error")) setError(params.get("spotify_error"));
    if (params.has("spotify") || params.has("spotify_error")) {
      window.history.replaceState(null, "", window.location.pathname);
    }
    api.config().then((c) => {
      setConfig(c);
      setFolder(c.music_dir || "");
    });
    api.jobs().then((js) => {
      js.forEach((j) => !ACTIVE.has(j.status) && finished.current.add(j.id));
      setJobs(js);
    });
    refreshData().catch((e) => setError(e.message));
  }, [refreshData]);

  // Poll jobs while any are active; refresh data when one finishes.
  const anyActive = jobs.some((j) => ACTIVE.has(j.status));
  useEffect(() => {
    if (!anyActive) return;
    const timer = setInterval(async () => {
      const js = await api.jobs();
      setJobs(js);
      const newlyDone = js.filter((j) => !ACTIVE.has(j.status) && !finished.current.has(j.id));
      if (newlyDone.length) {
        newlyDone.forEach((j) => finished.current.add(j.id));
        await refreshData();
        const draft = newlyDone.find((j) => j.kind === "draft" && j.status === "done");
        if (draft) setSelectedDraft(draft.result.draft_id);
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [anyActive, refreshData]);

  const startJob = async (fn) => {
    setError("");
    try {
      await fn();
      setJobs(await api.jobs());
    } catch (e) {
      setError(e.message);
    }
  };

  const scan = (e) => {
    e.preventDefault();
    startJob(() => api.scan(folder));
  };

  const loadPlaylist = async (e) => {
    e.preventDefault();
    setError("");
    setLoadingPlaylist(true);
    try {
      setPlaylist(await api.loadPlaylist(playlistUrl));
      setRefreshKey((k) => k + 1);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoadingPlaylist(false);
    }
  };

  const disconnectSpotify = async () => {
    await api.spotifyLogout();
    setConfig(await api.config());
  };

  const makeDraft = (vocalsId, instrumentalId) =>
    startJob(() => api.createDraft({ vocals_id: vocalsId, instrumental_id: instrumentalId }));

  const scanJob = jobs.find((j) => j.kind === "scan" && ACTIVE.has(j.status));
  const draftJobs = jobs.filter((j) => j.kind === "draft");

  return (
    <div className="app">
      <header>
        <h1>Mashup Maker</h1>
        <form onSubmit={scan} className="field">
          <label htmlFor="folder">Your music folder</label>
          <div className="row">
            <input
              id="folder"
              value={folder}
              onChange={(e) => setFolder(e.target.value)}
              placeholder="/Users/you/Music/stems-ok"
            />
            <button disabled={!folder || !!scanJob}>{scanJob ? "Analysing…" : "Analyse"}</button>
          </div>
          {scanJob && (
            <div className="job-inline">
              <ProgressBar value={scanJob.progress} />
              <span>{scanJob.message}</span>
            </div>
          )}
        </form>
        <form onSubmit={loadPlaylist} className="field">
          <label htmlFor="playlist">Spotify playlist URL</label>
          <div className="row">
            <input
              id="playlist"
              value={playlistUrl}
              onChange={(e) => setPlaylistUrl(e.target.value)}
              placeholder="https://open.spotify.com/playlist/…"
            />
            <button disabled={!playlistUrl || loadingPlaylist}>
              {loadingPlaylist ? "Loading…" : "Load"}
            </button>
          </div>
          {config && !config.spotify_configured && (
            <p className="hint">Spotify credentials not set — see README. You can still rank your whole library.</p>
          )}
          {config?.spotify_configured && (
            <p className="hint">
              {config.spotify_connected ? (
                <>
                  Connected to Spotify{config.spotify_user ? ` as ${config.spotify_user}` : ""} ·{" "}
                  <button type="button" className="link" onClick={disconnectSpotify}>
                    disconnect
                  </button>
                </>
              ) : (
                <>
                  Spotify only shares playlist tracks with a logged-in user.{" "}
                  <a href="/api/spotify/login">Connect Spotify</a>
                </>
              )}
            </p>
          )}
        </form>
      </header>

      {error && (
        <div className="error" role="alert">
          {error} <button className="link" onClick={() => setError("")}>dismiss</button>
        </div>
      )}
      {config && !config.rubberband && (
        <div className="notice">
          Rubber Band isn't installed, so drafts use librosa's rougher stretcher. Install it with{" "}
          <code>brew install rubberband</code> and restart the backend.
        </div>
      )}

      <main>
        <section className="panel playlist-panel">
          <PlaylistView playlist={playlist} library={library} />
        </section>
        <section className="panel matches-panel">
          <MatchList
            refreshKey={refreshKey}
            hasPlaylist={!!playlist}
            onDraft={makeDraft}
            busy={draftJobs.some((j) => ACTIVE.has(j.status))}
          />
        </section>
        <section className="panel drafts-panel">
          <Drafts
            jobs={draftJobs}
            drafts={drafts}
            selected={selectedDraft}
            onSelect={setSelectedDraft}
          />
        </section>
      </main>
    </div>
  );
}
