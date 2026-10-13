import { useEffect, useState } from "react";
import { api, youtubeUrl } from "../api.js";
import Window from "../skin/Window.jsx";
import { Pill, TrackList } from "../skin/controls.jsx";

// ● you own the file (analysed)   ○ key/BPM looked up, no file yet
const mark = (t) => (t.local_id ? "●" : "○");

export default function MatchesWindow({
  refreshKey, hasPlaylist, selectedTrack, onClearTrack, onDraft, busy, onError,
}) {
  const [scope, setScope] = useState(hasPlaylist ? "playlist" : "library");
  const [ownedOnly, setOwnedOnly] = useState(false);
  const [data, setData] = useState({ tracks: {}, pairs: [], total_tracks: 0 });
  const [selected, setSelected] = useState(null);

  useEffect(() => setScope(hasPlaylist ? "playlist" : "library"), [hasPlaylist]);

  // Rows without key/BPM ("p…") can't be ranked; looked-up tracks ("s…")
  // only exist in playlist scope.
  const focus =
    !selectedTrack || selectedTrack.startsWith("p") || (scope === "library" && selectedTrack.startsWith("s"))
      ? null
      : selectedTrack;

  useEffect(() => {
    let stale = false;
    api
      .pairs(scope, 300, focus, ownedOnly)
      .then((d) => !stale && setData(d))
      .catch((e) => onError(e.message));
    return () => {
      stale = true;
    };
  }, [scope, focus, ownedOnly, refreshKey, onError]);

  const shown = data.pairs.slice(0, 150);
  const pair = shown.find((p) => `${p.a}|${p.b}` === selected) ?? shown[0];
  const a = pair && data.tracks[pair.a];
  const b = pair && data.tracks[pair.b];
  const focusTrack = focus && data.tracks[focus];
  const missing = pair ? [a, b].filter((t) => !t.local_id) : [];

  return (
    <Window
      title="MATCHES"
      className="wa-matches"
      footer={
        <>
          <span className="wa-footer-pills">
            <Pill on={scope === "playlist"} disabled={!hasPlaylist} onClick={() => setScope("playlist")} title="Pairs across the playlist (owned + looked up)">
              PL
            </Pill>
            <Pill on={scope === "library"} onClick={() => setScope("library")} title="Pairs across your analysed files">
              LIB
            </Pill>
            <Pill on={ownedOnly} onClick={() => setOwnedOnly(!ownedOnly)} title="Only pairs you can render now (you own both files)">
              OWN
            </Pill>
          </span>
          <div className="wa-minilcd">
            <div className="wa-minilcd-top">
              <span>{data.total_tracks} TRACKS</span>
              <span>{scope === "playlist" ? "PL" : "LIB"}</span>
            </div>
            <div className="wa-minilcd-bottom">
              {focusTrack ? `WITH: ${focusTrack.title}`.slice(0, 26).toUpperCase() : "● OWNED  ○ LOOKED UP"}
            </div>
          </div>
          <span className="wa-grip" aria-hidden="true" />
        </>
      }
    >
      {focusTrack && (
        <div className="wa-filter">
          Pairs with <b>{focusTrack.title}</b>
          <button type="button" className="wa-btn is-small" onClick={onClearTrack}>
            CLEAR
          </button>
        </div>
      )}
      <TrackList
        label="Ranked pairs"
        empty={
          shown.length === 0 &&
          (scope === "playlist"
            ? "No playlist tracks have key/BPM yet. SCAN your music folder, or press the magnifier in the playlist to look them up."
            : "Need at least two analysed files. SCAN your music folder.")
        }
      >
        {shown.map((p, i) => {
          const ta = data.tracks[p.a];
          const tb = data.tracks[p.b];
          const isSel = p === pair;
          const owned = ta.local_id && tb.local_id;
          return (
            <button
              type="button"
              key={`${p.a}|${p.b}`}
              className={`wa-row ${isSel ? "is-selected" : ""} ${owned ? "" : "is-dim"}`}
              onClick={() => setSelected(`${p.a}|${p.b}`)}
              aria-pressed={isSel}
              title={owned ? "You own both files" : "Missing a local file; key/BPM looked up"}
            >
              <span className="wa-row-text">
                {i + 1}. <span className="wa-own">{mark(ta)}</span>{ta.title} × <span className="wa-own">{mark(tb)}</span>{tb.title}
              </span>
              <span className="wa-row-key">
                {ta.camelot}/{tb.camelot}
              </span>
              <span className="wa-row-time">{Math.round(p.score * 100)}</span>
            </button>
          );
        })}
      </TrackList>

      {pair && (
        <div className="wa-detail">
          <div className="wa-lcd wa-detail-lcd">
            <div className="wa-detail-tracks">
              {[["A", a], ["B", b]].map(([label, t]) => (
                <span key={label}>
                  <b>{label}</b> {t.title} <em>{t.camelot} · {Math.round(t.bpm)} · {t.local_id ? "your file" : "looked up"}</em>
                </span>
              ))}
            </div>
            <ul>
              {pair.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
              {missing.length > 0 && (
                <li className="is-warn">
                  To render, get {missing.map((t) => `"${t.title}"`).join(" and ")} into your FIND folder (with WATCH on it's picked up automatically).
                </li>
              )}
            </ul>
          </div>
          <div className="wa-detail-actions">
            {missing.map((t) => (
              <a
                key={t.id}
                className="wa-btn is-link"
                href={youtubeUrl(t.artist, t.title)}
                target="_blank"
                rel="noreferrer"
                title={`Search YouTube for ${t.artist} - ${t.title}`}
              >
                ▶ {t.title.length > 14 ? `${t.title.slice(0, 13)}…` : t.title}
              </a>
            ))}
            <span className="wa-spacer" />
            <button
              type="button" className="wa-btn"
              disabled={busy || missing.length > 0}
              onClick={() => onDraft(a.local_id, b.local_id)}
              title={busy ? "A draft is rendering; see JOBS" : missing.length ? "You need both files" : undefined}
            >
              {busy ? "RENDERING…" : "A VOX + B INST"}
            </button>
            <button
              type="button" className="wa-btn"
              disabled={busy || missing.length > 0}
              onClick={() => onDraft(b.local_id, a.local_id)}
              title={busy ? "A draft is rendering; see JOBS" : missing.length ? "You need both files" : undefined}
            >
              {busy ? "RENDERING…" : "B VOX + A INST"}
            </button>
          </div>
        </div>
      )}
    </Window>
  );
}
