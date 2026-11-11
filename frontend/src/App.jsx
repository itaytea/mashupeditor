import { useCallback, useEffect, useState } from "react";
import { api, draftUrl } from "./api.js";
import { useAudioEngine } from "./audio.js";
import MainWindow from "./windows/MainWindow.jsx";
import JobsWindow from "./windows/JobsWindow.jsx";
import PlaylistWindow from "./windows/PlaylistWindow.jsx";
import MatchesWindow from "./windows/MatchesWindow.jsx";
import DraftsWindow from "./windows/DraftsWindow.jsx";
import Window from "./skin/Window.jsx";
import { ACTIVE, useJobs } from "./hooks/useJobs.js";


export default function App() {
  const [config, setConfig] = useState(null);
  const [folder, setFolder] = useState("");
  const [findDir, setFindDir] = useState("");
  const [watch, setWatch] = useState(false);
  const [playlistUrl, setPlaylistUrl] = useState("");
  const [library, setLibrary] = useState([]);
  const [playlist, setPlaylist] = useState(null);
  const [view, setView] = useState("playlist");
  const [drafts, setDrafts] = useState([]);
  const [error, setError] = useState("");
  const [loadingPlaylist, setLoadingPlaylist] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0); // bump to re-rank pairs
  const [currentDraft, setCurrentDraft] = useState(null);
  const [selectedTrack, setSelectedTrack] = useState(null);
  const [showPl, setShowPl] = useState(true);
  const [autoPlay, setAutoPlay] = useState(true);
  const [shuffle, setShuffle] = useState(false);
  const engine = useAudioEngine();

  const refreshData = useCallback(async () => {
    const [lib, pl, dr] = await Promise.all([api.library(), api.playlist(), api.drafts()]);
    setLibrary(lib);
    setPlaylist(pl);
    setDrafts(dr);
    setRefreshKey((k) => k + 1);
    return dr;
  }, []);

  const { load } = engine;
  const { jobs, setJobs, finished } = useJobs({ refreshData, load, autoPlay, setCurrentDraft, setError });

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
      setFindDir(c.find_dir || "");
      setWatch(!!c.watch);
    });
    api.jobs().then((js) => {
      js.forEach((j) => !ACTIVE.has(j.status) && finished.current.add(j.id));
      setJobs(js);
    });
    refreshData()
      .then((dr) => dr[0] && setCurrentDraft(dr[0].draft_id))
      .catch((e) => setError(e.message));
  }, [refreshData, setJobs, finished]);

  const draft = drafts.find((d) => d.draft_id === currentDraft) ?? null;

  const selectDraft = useCallback(
    (id, play = false) => {
      setCurrentDraft(id);
      const d = drafts.find((x) => x.draft_id === id);
      if (d) load(draftUrl(d.draft_id, d.mp3 || d.wav), play);
    },
    [drafts, load],
  );

  // Keep the player loaded with the current draft.
  useEffect(() => {
    if (draft) load(draftUrl(draft.draft_id, draft.mp3 || draft.wav));
  }, [draft, load]);

  const startJob = async (fn) => {
    setError("");
    try {
      await fn();
      setJobs(await api.jobs());
    } catch (e) {
      setError(e.message);
    }
  };

  const loadPlaylist = async () => {
    setError("");
    setLoadingPlaylist(true);
    try {
      setPlaylist(await api.loadPlaylist(playlistUrl));
      setView("playlist");
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

  const step = (dir) => {
    if (!drafts.length) return;
    const i = drafts.findIndex((d) => d.draft_id === currentDraft);
    const next = shuffle
      ? Math.floor(Math.random() * drafts.length)
      : (Math.max(i, 0) + dir + drafts.length) % drafts.length;
    selectDraft(drafts[next].draft_id, engine.status === "playing");
  };

  const scanJob = jobs.find((j) => j.kind === "scan" && ACTIVE.has(j.status));
  const lookupJob = jobs.find((j) => j.kind === "lookup" && ACTIVE.has(j.status));
  const findJob = jobs.find((j) => j.kind === "find" && ACTIVE.has(j.status));
  const draftJobs = jobs.filter((j) => j.kind === "draft" && j.error !== "server restarted");
  const activeDraftJob = draftJobs.find((j) => ACTIVE.has(j.status));
  const trackB = draft ? library.find((t) => t.id === draft.b) : null;
  const onError = useCallback((m) => setError(m), []);

  return (
    <div className="desktop">
      <div className="wa-stack">
        <MainWindow
          engine={engine}
          draft={draft}
          trackB={trackB}
          activeJob={activeDraftJob}
          onPrev={() => step(-1)}
          onNext={() => step(1)}
          onEject={() => draft && (window.location.href = draftUrl(draft.draft_id, draft.mp3 || draft.wav))}
          autoPlay={autoPlay}
          onAutoPlay={() => setAutoPlay(!autoPlay)}
          showPl={showPl}
          onTogglePl={() => setShowPl(!showPl)}
          shuffle={shuffle}
          onShuffle={() => setShuffle(!shuffle)}
        />
        {showPl && (
          <PlaylistWindow
            config={config}
            playlist={playlist}
            library={library}
            view={playlist ? view : "library"}
            onView={setView}
            folder={folder}
            onFolder={setFolder}
            onScan={() => folder && startJob(() => api.scan(folder))}
            scanJob={scanJob}
            playlistUrl={playlistUrl}
            onPlaylistUrl={setPlaylistUrl}
            onLoadPlaylist={loadPlaylist}
            loadingPlaylist={loadingPlaylist}
            onDisconnect={disconnectSpotify}
            selectedTrack={selectedTrack}
            onSelectTrack={setSelectedTrack}
            onClose={() => setShowPl(false)}
            lookupJob={lookupJob}
            onLookup={() => startJob(api.lookup)}
            onCancelJob={(id) => startJob(() => api.cancelJob(id))}
            findDir={findDir}
            onFindDir={setFindDir}
            onFind={() => findDir && startJob(() => api.find(findDir, watch))}
            findJob={findJob}
            watch={watch}
            onWatch={async () => {
              try {
                setWatch((await api.setWatch(!watch)).watch);
              } catch (e) {
                setError(e.message);
              }
            }}
          />
        )}
      </div>

      <div className="wa-stack">
        <JobsWindow jobs={jobs} onCancel={(id) => startJob(() => api.cancelJob(id))} />
        <MatchesWindow
          refreshKey={refreshKey}
          hasPlaylist={!!playlist}
          selectedTrack={selectedTrack}
          onClearTrack={() => setSelectedTrack(null)}
          onDraft={(a, b, quantize) =>
            startJob(() => api.createDraft({ vocals_id: a, instrumental_id: b, quantize }))
          }
          busy={!!activeDraftJob}
          onError={onError}
        />
        <DraftsWindow jobs={draftJobs} drafts={drafts} current={draft} onSelect={selectDraft} />
        <p className="desktop-note">Only audio files you own are processed. Spotify is used for track names only.</p>
      </div>

      {error && (
        <div className="wa-dialog-backdrop" role="alertdialog" aria-modal="true" aria-label="Error">
          <Window title="MASHUP ERROR" className="wa-dialog" onClose={() => setError("")}>
            <div className="wa-dialog-body">
              <span className="wa-dialog-icon" aria-hidden="true">!</span>
              <p>{error}</p>
            </div>
            <div className="wa-dialog-actions">
              <button type="button" className="wa-btn" autoFocus onClick={() => setError("")}>
                OK
              </button>
            </div>
          </Window>
        </div>
      )}
    </div>
  );
}
