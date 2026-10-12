import Window from "../skin/Window.jsx";
import { ChromeButton, Progress, TrackList, formatTime } from "../skin/controls.jsx";

function Row({ n, text, sub, keyCode, bpm, seconds, dim, lookedUp, selected, onClick, title }) {
  return (
    <button
      type="button"
      className={`wa-row ${dim ? "is-dim" : ""} ${lookedUp ? "is-lookup" : ""} ${selected ? "is-selected" : ""}`}
      onClick={onClick}
      title={title}
      aria-pressed={selected}
    >
      <span className="wa-row-text">
        {n}. {text}
        {sub && <span className="wa-row-sub"> {sub}</span>}
      </span>
      <span className="wa-row-key">{keyCode ?? ""}</span>
      <span className="wa-row-bpm">{bpm ? Math.round(bpm) : ""}</span>
      <span className="wa-row-time">{seconds ? formatTime(seconds).replace(/^0/, "") : ""}</span>
    </button>
  );
}

export default function PlaylistWindow({
  config, playlist, library, view, onView, folder, onFolder, onScan, scanJob,
  playlistUrl, onPlaylistUrl, onLoadPlaylist, loadingPlaylist, onDisconnect,
  selectedTrack, onSelectTrack, onClose, lookupJob, onLookup, onCancelJob,
}) {
  const showPlaylist = view === "playlist" && playlist;
  const matched = playlist ? playlist.tracks.filter((t) => t.local).length : 0;
  const lookedUp = playlist ? playlist.tracks.filter((t) => !t.local && t.lookup).length : 0;
  const busyJob = scanJob ?? lookupJob;

  let lcdTop;
  if (scanJob) lcdTop = `SCAN ${Math.round(scanJob.progress * 100)}%`;
  else if (lookupJob) lcdTop = `LOOKUP ${Math.round(lookupJob.progress * 100)}%`;
  else if (showPlaylist) lcdTop = `${matched} OWNED ${lookedUp} LOOKED UP`;
  else lcdTop = `${library.length} FILES`;

  const totalSecs = showPlaylist
    ? playlist.tracks.reduce((s, t) => s + (t.duration_ms ?? 0) / 1000, 0)
    : library.reduce((s, t) => s + (t.duration ?? 0), 0);

  const spotifyLabel = !config?.spotify_configured
    ? "Spotify keys not set (see README)"
    : config.spotify_connected
      ? `Spotify: ${config.spotify_user ?? "connected"}`
      : "Spotify: not connected";

  return (
    <Window
      title="PLAYLIST"
      className="wa-pl"
      onClose={onClose}
      footer={
        <>
          <ChromeButton icon="plus" label="Analyse music folder" onClick={onScan} disabled={!folder || !!scanJob} />
          <ChromeButton
            icon="list"
            label={view === "playlist" ? "Show whole library" : "Show Spotify playlist"}
            onClick={() => onView(view === "playlist" ? "library" : "playlist")}
            disabled={!playlist}
          />
          {lookupJob ? (
            <ChromeButton icon="cancel" label="Stop the key/BPM lookup" onClick={() => onCancelJob(lookupJob.id)} />
          ) : (
            <ChromeButton
              icon="search"
              label="Look up key/BPM for playlist tracks you don't own"
              onClick={onLookup}
              disabled={!playlist}
            />
          )}
          {config?.spotify_connected ? (
            <ChromeButton icon="spotify" label="Disconnect Spotify" onClick={onDisconnect} />
          ) : (
            <a className="wa-chrome" href="/api/spotify/login" aria-label="Connect Spotify" title="Connect Spotify">
              <svg viewBox="0 0 20 20" aria-hidden="true" className="wa-icon">
                <path d="M10 2.5a7.5 7.5 0 100 15 7.5 7.5 0 000-15zm3.3 10.9a.5.5 0 01-.7.2c-1.9-1.2-4.3-1.4-7.1-.8a.5.5 0 11-.2-1c3-.7 5.7-.4 7.8.9.3.2.4.5.2.7zm1-2.2a.6.6 0 01-.9.2c-2.2-1.3-5.4-1.7-8-.9a.6.6 0 11-.3-1.2c2.9-.9 6.5-.5 9 1 .3.2.4.6.2.9zm.1-2.3C11.8 7.4 7.6 7.2 5.2 8a.7.7 0 11-.4-1.4c2.8-.8 7.4-.7 10.4 1.1a.7.7 0 01-.8 1.2z" />
              </svg>
            </a>
          )}
          <div className="wa-minilcd">
            <div className="wa-minilcd-top">
              <span>{lcdTop}</span>
              <span>{formatTime(totalSecs)}</span>
            </div>
            {busyJob ? (
              <Progress value={busyJob.progress} />
            ) : (
              <div className="wa-minilcd-bottom">
                {showPlaylist ? `${playlist.tracks.length} TRACKS` : "LIBRARY"}
              </div>
            )}
          </div>
          <span className="wa-grip" aria-hidden="true" />
        </>
      }
    >
      <div className="wa-source">
        <form
          className="wa-source-row"
          onSubmit={(e) => {
            e.preventDefault();
            onScan();
          }}
        >
          <label htmlFor="folder">DIR</label>
          <input
            id="folder"
            className="wa-field"
            value={folder}
            onChange={(e) => onFolder(e.target.value)}
            placeholder="/Users/you/Music/my-own-files"
            spellCheck={false}
          />
          <button className="wa-btn" disabled={!folder || !!scanJob}>
            {scanJob ? "BUSY" : "SCAN"}
          </button>
        </form>
        <form
          className="wa-source-row"
          onSubmit={(e) => {
            e.preventDefault();
            onLoadPlaylist();
          }}
        >
          <label htmlFor="playlist">URL</label>
          <input
            id="playlist"
            className="wa-field"
            value={playlistUrl}
            onChange={(e) => onPlaylistUrl(e.target.value)}
            placeholder="https://open.spotify.com/playlist/…"
            spellCheck={false}
          />
          <button className="wa-btn" disabled={!playlistUrl || loadingPlaylist}>
            {loadingPlaylist ? "WAIT" : "LOAD"}
          </button>
        </form>
        <div className="wa-source-status">
          {busyJob ? busyJob.message : spotifyLabel}
          {!busyJob && config?.spotify_configured && !config.spotify_connected && (
            <> · <a href="/api/spotify/login">connect</a></>
          )}
          {!busyJob && (
            <span className="wa-credit">
              Key/BPM for tracks you don't own:{" "}
              <a href="https://getsongbpm.com" target="_blank" rel="noreferrer">GetSongBPM</a>
            </span>
          )}
        </div>
      </div>

      {showPlaylist ? (
        <TrackList label={playlist.name}>
          <div className="wa-list-caption">{playlist.name}</div>
          {playlist.tracks.map((t) => {
            const e = t.entity;
            let title;
            if (t.local) title = `Your file: ${t.local.path}\n${Math.round(t.match_score)}% name match · ${t.local.key_name} (analysed)`;
            else if (t.lookup) title = `Not owned. Key/BPM from GetSongBPM: ${t.lookup.key_name}, ${t.lookup.bpm} BPM\nMatched "${t.lookup.matched_title}" by ${t.lookup.matched_artist}`;
            else if (t.lookup_status === "not_found") title = "Not owned, and GetSongBPM has no data for it";
            else title = "Not owned. Press the magnifier to look up its key/BPM";
            return (
              <Row
                key={t.position}
                n={t.position + 1}
                text={`${t.artists.join(", ")} - ${t.title}`}
                keyCode={e?.camelot}
                bpm={e?.bpm}
                seconds={(t.duration_ms ?? 0) / 1000}
                dim={!t.local}
                lookedUp={!t.local && !!t.lookup}
                selected={!!e && e.id === selectedTrack}
                onClick={() => e && onSelectTrack(e.id === selectedTrack ? null : e.id)}
                title={title}
              />
            );
          })}
        </TrackList>
      ) : (
        <TrackList
          label="Library"
          empty={library.length === 0 && "Type your music folder above and press SCAN."}
        >
          {library.map((t, i) => (
            <Row
              key={t.id}
              n={i + 1}
              text={`${t.artist || "Unknown"} - ${t.title}`}
              keyCode={t.camelot}
              bpm={t.bpm}
              seconds={t.duration}
              dim={!!t.error}
              selected={`t${t.id}` === selectedTrack}
              onClick={() => !t.error && onSelectTrack(`t${t.id}` === selectedTrack ? null : `t${t.id}`)}
              title={t.error ? `Analysis failed: ${t.error}` : `${t.path}\n${t.key_name}`}
            />
          ))}
        </TrackList>
      )}
    </Window>
  );
}
