import { BpmBadge, KeyBadge } from "./Badges.jsx";

export default function PlaylistView({ playlist, library }) {
  if (playlist) {
    const matched = playlist.tracks.filter((t) => t.local).length;
    return (
      <>
        <div className="panel-head">
          <h2>{playlist.name}</h2>
          <span className="count">
            {matched}/{playlist.tracks.length} found locally
          </span>
        </div>
        <ol className="track-list">
          {playlist.tracks.map((t) => (
            <li key={t.position} className={t.local ? "" : "missing"}>
              <span className="pos">{t.position + 1}</span>
              <div className="track-meta">
                <span className="title">{t.title}</span>
                <span className="artist">{t.artists.join(", ")}</span>
                {t.local ? (
                  <span className="file" title={t.local.path}>
                    ↳ {t.local.path.split("/").pop()} · {Math.round(t.match_score)}% match
                  </span>
                ) : (
                  <span className="file">No local file</span>
                )}
              </div>
              <div className="badges">
                <KeyBadge track={t.local} />
                <BpmBadge track={t.local} />
              </div>
            </li>
          ))}
        </ol>
      </>
    );
  }

  return (
    <>
      <div className="panel-head">
        <h2>Library</h2>
        <span className="count">{library.length} files</span>
      </div>
      {library.length === 0 ? (
        <p className="empty">Analyse a folder of your own audio files to get started.</p>
      ) : (
        <ol className="track-list">
          {library.map((t, i) => (
            <li key={t.id} className={t.error ? "missing" : ""}>
              <span className="pos">{i + 1}</span>
              <div className="track-meta">
                <span className="title">{t.title}</span>
                <span className="artist">{t.artist || "Unknown artist"}</span>
                {t.error && <span className="file">Analysis failed: {t.error}</span>}
              </div>
              <div className="badges">
                <KeyBadge track={t} />
                <BpmBadge track={t} />
              </div>
            </li>
          ))}
        </ol>
      )}
    </>
  );
}
