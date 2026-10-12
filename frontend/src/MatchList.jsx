import { useEffect, useState } from "react";
import { api } from "./api.js";
import { BpmBadge, KeyBadge } from "./Badges.jsx";

export default function MatchList({ refreshKey, hasPlaylist, onDraft, busy }) {
  const [scope, setScope] = useState(hasPlaylist ? "playlist" : "library");
  const [data, setData] = useState({ tracks: {}, pairs: [] });
  const [error, setError] = useState("");

  useEffect(() => setScope(hasPlaylist ? "playlist" : "library"), [hasPlaylist]);

  useEffect(() => {
    api
      .pairs(scope)
      .then((d) => {
        setData(d);
        setError("");
      })
      .catch((e) => setError(e.message));
  }, [scope, refreshKey]);

  return (
    <>
      <div className="panel-head">
        <h2>Matches</h2>
        <div className="seg" role="tablist">
          {["playlist", "library"].map((s) => (
            <button
              key={s}
              role="tab"
              aria-selected={scope === s}
              className={scope === s ? "on" : ""}
              onClick={() => setScope(s)}
              disabled={s === "playlist" && !hasPlaylist}
            >
              {s === "playlist" ? "Playlist" : "Whole library"}
            </button>
          ))}
        </div>
      </div>
      {error && <p className="empty">{error}</p>}
      {!error && data.pairs.length === 0 && (
        <p className="empty">Need at least two analysed tracks{scope === "playlist" ? " matched to the playlist" : ""}.</p>
      )}
      <ol className="pair-list">
        {data.pairs.map((p) => {
          const a = data.tracks[p.a];
          const b = data.tracks[p.b];
          return (
            <li key={`${p.a}-${p.b}`} className="pair">
              <div className="pair-score" title="Combined score">
                <div className="ring" style={{ "--pct": p.score }}>
                  {Math.round(p.score * 100)}
                </div>
              </div>
              <div className="pair-body">
                <div className="pair-tracks">
                  <PairTrack t={a} />
                  <span className="x">×</span>
                  <PairTrack t={b} />
                </div>
                <ul className="reasons">
                  {p.reasons.map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ul>
                <div className="pair-actions">
                  <button disabled={busy} onClick={() => onDraft(p.a, p.b)}>
                    Vocals of <b>{a.title}</b> over <b>{b.title}</b>
                  </button>
                  <button disabled={busy} className="secondary" onClick={() => onDraft(p.b, p.a)}>
                    Swap
                  </button>
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    </>
  );
}

function PairTrack({ t }) {
  return (
    <div className="pair-track">
      <span className="title">{t.title}</span>
      <span className="artist">{t.artist || "Unknown artist"}</span>
      <div className="badges">
        <KeyBadge track={t} />
        <BpmBadge track={t} />
      </div>
    </div>
  );
}
