import { useCallback, useEffect, useRef, useState } from "react";
import { api, draftUrl } from "../api.js";

export const ACTIVE = new Set(["queued", "running"]);

/**
 * Holds the job list and polls it while any job is active; refreshes data
 * when a job finishes. `finished` tracks job ids that were already handled.
 */
export function useJobs({ refreshData, load, autoPlay, setCurrentDraft, setError }) {
  const [jobs, setJobs] = useState([]);
  const finished = useRef(new Set());
  const inFlight = useRef(false);
  const lastError = useRef(null);
  // True after a failed post-finish refresh, so polling keeps going to retry.
  const [retrying, setRetrying] = useState(false);

  const reportError = useCallback((msg) => {
    if (msg === lastError.current) return; // don't re-set the same message every tick
    lastError.current = msg;
    setError(msg);
  }, [setError]);

  // Poll jobs while any are active; refresh data when one finishes.
  const anyActive = jobs.some((j) => ACTIVE.has(j.status)) || retrying;
  useEffect(() => {
    if (!anyActive) return;
    let ticks = 0;
    const timer = setInterval(async () => {
      if (inFlight.current) return;
      inFlight.current = true;
      try {
          const js = await api.jobs();
          setJobs(js);
          // A long lookup fills in the playlist as it goes; show results every 15s.
          if (++ticks % 15 === 0 && js.some((j) => j.kind === "lookup" && j.status === "running")) {
            refreshData();
          }
          const newlyDone = js.filter((j) => !ACTIVE.has(j.status) && !finished.current.has(j.id));
          if (newlyDone.length) {
            // Only mark these handled once the refresh worked; if it throws they
            // are picked up again on the next tick.
            const dr = await refreshData();
            newlyDone.forEach((j) => finished.current.add(j.id));
            setRetrying(false);
            const done = newlyDone.find((j) => j.kind === "draft" && j.status === "done");
            if (done) {
              const d = dr.find((x) => x.draft_id === done.result.draft_id);
              setCurrentDraft(done.result.draft_id);
              if (d) load(draftUrl(d.draft_id, d.mp3 || d.wav), autoPlay);
            }
            const failed = newlyDone.find((j) => j.status === "failed");
            if (failed) reportError(`${failed.params.title ?? failed.kind}: ${failed.error}`);
          }
          lastError.current = null;
      } catch (e) {
        setRetrying(true);
        reportError(`Could not refresh jobs: ${e?.message ?? e}`);
      } finally {
        inFlight.current = false;
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [anyActive, refreshData, load, autoPlay, setCurrentDraft, reportError]);

  return { jobs, setJobs, finished };
}
