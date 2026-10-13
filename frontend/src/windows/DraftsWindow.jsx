import { draftUrl } from "../api.js";
import Window from "../skin/Window.jsx";
import { Progress, TrackList, formatTime } from "../skin/controls.jsx";

const ACTIVE = new Set(["queued", "running"]);

// How far A's beats wander against B's over the draft (measured on the audio).
// Older drafts have no measurement.
function syncLabel(d) {
  if (!d.sync) return null;
  const w = d.sync.wander_ms;
  return { text: `SYNC ±${Math.round(w / 2)}MS`, loose: w > 60 };
}

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
              <div className={`wa-minilcd-bottom ${current && syncLabel(current)?.loose ? "is-warn" : ""}`}>
                {current
                  ? `${syncLabel(current)?.text ?? "SYNC ?"}${current.quantized ? " Q" : ""} ×${current.stretch}${current.phrases ? ` ${current.phrases}PH` : ""}${syncLabel(current)?.loose ? " LOOSE" : ""}`
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
            title={[
              `A from ${formatTime(d.start_a)}, B from ${formatTime(d.start_b)}`,
              d.bpm_a_section && `Tempo: A ${d.bpm_a_section} → B ${d.bpm_b_section} BPM (×${d.stretch})`,
              d.phrases && `${d.phrases} × 8-bar phrases`,
              d.b_sections?.length && `B: ${d.b_sections.join(" → ")}`,
              d.a_sections?.length && `A (vocals from): ${d.a_sections.join(" → ")}`,
              d.sync && `Beats wander ${d.sync.wander_ms} ms over the draft (lock ${d.sync.lock})`,
              d.quantized && `Quantized onto B's grid (without: ${d.sync_unquantized?.wander_ms ?? "?"} ms)`,
              d.key_source && `Key: ${d.key_source}`,
            ].filter(Boolean).join("\n")}
          >
            <span className="wa-row-text">
              {i + 1}. {d.title}
            </span>
            {syncLabel(d)?.loose && <span className="wa-row-key is-warn">LOOSE</span>}
            <span className="wa-row-time">{formatTime(d.duration).replace(/^0/, "")}</span>
          </button>
        ))}
      </TrackList>
    </Window>
  );
}
