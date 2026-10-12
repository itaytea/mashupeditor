import { draftUrl } from "./api.js";
import { ProgressBar } from "./Badges.jsx";

export default function Drafts({ jobs, drafts, selected, onSelect }) {
  const active = jobs.filter((j) => j.status === "queued" || j.status === "running");
  const failed = jobs.filter((j) => j.status === "failed").slice(0, 3);
  const current = drafts.find((d) => d.draft_id === selected) ?? drafts[0];

  return (
    <>
      <div className="panel-head">
        <h2>Drafts</h2>
      </div>

      {active.map((j) => (
        <div key={j.id} className="job">
          <div className="job-title">{j.params.title}</div>
          <ProgressBar value={j.progress} status={j.status} />
          <div className="job-msg">
            {Math.round(j.progress * 100)}% · {j.message || j.status}
          </div>
        </div>
      ))}

      {failed.map((j) => (
        <div key={j.id} className="job failed">
          <div className="job-title">{j.params.title}</div>
          <div className="job-msg">Failed: {j.error}</div>
        </div>
      ))}

      {current ? (
        <div className="player">
          <div className="player-title">{current.title}</div>
          <audio key={current.draft_id} controls src={draftUrl(current.draft_id, current.mp3 || current.wav)} />
          <dl className="draft-facts">
            <dt>Stretch</dt>
            <dd>
              ×{current.stretch}
              {current.tempo_ratio !== 1 && ` (${current.tempo_ratio === 2 ? "double" : "half"}-time)`}
            </dd>
            <dt>Pitch</dt>
            <dd>{current.semitones ? `${current.semitones > 0 ? "+" : ""}${current.semitones} st` : "unchanged"}</dd>
            <dt>From</dt>
            <dd>
              A @ {fmt(current.start_a)}, B @ {fmt(current.start_b)}
            </dd>
            <dt>Engine</dt>
            <dd>{current.engine}</dd>
          </dl>
          <div className="downloads">
            <a href={draftUrl(current.draft_id, current.wav)} download>
              Download WAV
            </a>
            {current.mp3 && (
              <a href={draftUrl(current.draft_id, current.mp3)} download>
                Download MP3
              </a>
            )}
          </div>
        </div>
      ) : (
        active.length === 0 && <p className="empty">Pick a match and render a 60-second draft.</p>
      )}

      {drafts.length > 1 && (
        <ul className="draft-list">
          {drafts.map((d) => (
            <li key={d.draft_id}>
              <button
                className={`link ${d.draft_id === current?.draft_id ? "on" : ""}`}
                onClick={() => onSelect(d.draft_id)}
              >
                {d.title}
              </button>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
