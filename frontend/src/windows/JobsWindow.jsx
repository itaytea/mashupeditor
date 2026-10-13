import Window from "../skin/Window.jsx";
import { Progress, TrackList } from "../skin/controls.jsx";

const ACTIVE = new Set(["queued", "running"]);
const KIND = { scan: "SCAN", find: "FIND", lookup: "LOOKUP", draft: "RENDER" };

function label(j) {
  return j.params?.title || KIND[j.kind] || j.kind;
}

/** Everything the app is doing right now, and what's waiting on what. */
export default function JobsWindow({ jobs, onCancel }) {
  const active = jobs.filter((j) => ACTIVE.has(j.status)).reverse(); // oldest first
  const running = active.filter((j) => j.status === "running");
  const recent = jobs
    .filter((j) => (j.status === "failed" || j.status === "done") && j.error !== "server restarted")
    .filter((j) => Date.now() / 1000 - j.updated_at < 120)
    .slice(0, 3);

  const waitingFor = (j) => {
    const q = j.params?.queue;
    const blocker = running.find((r) => r.params?.queue === q) ?? active.find((r) => r !== j && r.params?.queue === q);
    return blocker ? `waiting for ${KIND[blocker.kind] ?? blocker.kind} to finish` : "starting…";
  };

  return (
    <Window
      title="JOBS"
      className="wa-jobs"
      footer={
        <>
          <div className="wa-minilcd">
            <div className="wa-minilcd-top">
              <span>{running.length} RUNNING</span>
              <span>{active.length - running.length} WAITING</span>
            </div>
            <div className="wa-minilcd-bottom">
              {active.length ? "SCAN/FIND SHARE A WORKER" : "IDLE"}
            </div>
          </div>
          <span className="wa-grip" aria-hidden="true" />
        </>
      }
    >
      <TrackList label="Jobs" empty={active.length === 0 && recent.length === 0 && "Nothing running."}>
        {active.map((j) => (
          <div key={j.id} className={`wa-job ${j.status === "queued" ? "is-waiting" : ""}`}>
            <div className="wa-job-head">
              <span className="wa-job-kind">{KIND[j.kind] ?? j.kind}</span>
              <span className="wa-job-title">{label(j)}</span>
              <span className="wa-job-pct">{j.status === "running" ? `${Math.round(j.progress * 100)}%` : "WAIT"}</span>
              <button
                type="button"
                className="wa-job-x"
                onClick={() => onCancel(j.id)}
                aria-label={`Cancel ${label(j)}`}
                title="Cancel"
              >
                ✕
              </button>
            </div>
            {j.status === "running" && <Progress value={j.progress} />}
            <div className="wa-job-msg">{j.status === "running" ? j.message || "Working…" : waitingFor(j)}</div>
          </div>
        ))}
        {recent.map((j) => (
          <div key={j.id} className={`wa-job is-${j.status}`} title={j.error || ""}>
            <div className="wa-job-head">
              <span className="wa-job-kind">{KIND[j.kind] ?? j.kind}</span>
              <span className="wa-job-title">{label(j)}</span>
              <span className="wa-job-pct">{j.status === "done" ? "DONE" : "FAILED"}</span>
            </div>
            {j.status === "failed" && <div className="wa-job-msg">{j.error}</div>}
          </div>
        ))}
      </TrackList>
    </Window>
  );
}
