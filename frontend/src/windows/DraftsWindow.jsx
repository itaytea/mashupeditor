import { draftUrl } from "../api.js";
import Window from "../skin/Window.jsx";
import { Progress, TrackList, formatTime } from "../skin/controls.jsx";

const ACTIVE = new Set(["queued", "running"]);

export default function DraftsWindow({ jobs, drafts, current, onSelect }) {
  const active = jobs.filter((j) => ACTIVE.has(j.status));
  const failed = jobs.filter((j) => j.status === "failed").slice(0, 3);
  const running = active.find((j) => j.status === "running") ?? active[0];

  return (
    <Window
      title="DRAFTS"
      className="wa-drafts"
      footer={
        <>
          <a
            className={`wa-chrome ${current ? "" : "is-disabled"}`}
            href={current ? draftUrl(current.draft_id, current.wav) : undefined}
            download
            aria-label="Download WAV"
            title="Download WAV"
          >
            <span className="wa-chrome-text">WAV</span>
          </a>
          <a
            className={`wa-chrome ${current?.mp3 ? "" : "is-disabled"}`}
            href={current?.mp3 ? draftUrl(current.draft_id, current.mp3) : undefined}
            download
            aria-label="Download MP3"
            title="Download MP3"
          >
            <span className="wa-chrome-text">MP3</span>
          </a>
          <div className="wa-minilcd">
            <div className="wa-minilcd-top">
              <span>{running ? `RENDER ${Math.round(running.progress * 100)}%` : `${drafts.length} DRAFTS`}</span>
              <span>{current ? formatTime(current.duration) : "00:00"}</span>
            </div>
            {running ? (
              <Progress value={running.progress} />
            ) : (
              <div className="wa-minilcd-bottom">
                {current
                  ? `×${current.stretch} ${current.semitones ? `${current.semitones > 0 ? "+" : ""}${current.semitones}ST` : "0ST"} ${current.engine}`.toUpperCase()
                  : "NO DRAFT LOADED"}
              </div>
            )}
          </div>
          <span className="wa-grip" aria-hidden="true" />
        </>
      }
    >
      <TrackList
        label="Drafts"
        empty={
          drafts.length === 0 && active.length === 0 && "Pick a match and press A VOX + B INST."
        }
      >
        {active.map((j) => (
          <div key={j.id} className="wa-row is-job">
            <span className="wa-row-text">
              {j.status === "queued" ? "Queued" : `${Math.round(j.progress * 100)}%`} · {j.params.title}
              <span className="wa-row-sub"> {j.message}</span>
            </span>
            <Progress value={j.progress} />
          </div>
        ))}
        {failed.map((j) => (
          <div key={j.id} className="wa-row is-failed" title={j.error}>
            <span className="wa-row-text">
              Failed · {j.params.title}: {j.error}
            </span>
          </div>
        ))}
        {drafts.map((d, i) => (
          <button
            type="button"
            key={d.draft_id}
            className={`wa-row ${d.draft_id === current?.draft_id ? "is-selected" : ""}`}
            onClick={() => onSelect(d.draft_id, true)}
            title={`Start A @ ${formatTime(d.start_a)}, B @ ${formatTime(d.start_b)}`}
          >
            <span className="wa-row-text">
              {i + 1}. {d.title}
            </span>
            <span className="wa-row-time">{formatTime(d.duration).replace(/^0/, "")}</span>
          </button>
        ))}
      </TrackList>
    </Window>
  );
}
