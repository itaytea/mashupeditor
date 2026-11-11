import { useEffect, useRef, useState } from "react";
import { api, draftUrl } from "../api.js";

export const ACTIVE = new Set(["queued", "running"]);

/**
 * Holds the job list and polls it while any job is active; refreshes data
 * when a job finishes. `finished` tracks job ids that were already handled.
 */
export function useJobs({ refreshData, load, autoPlay, setCurrentDraft, setError }) {
  const [jobs, setJobs] = useState([]);
  const finished = useRef(new Set());

  // Poll jobs while any are active; refresh data when one finishes.
  const anyActive = jobs.some((j) => ACTIVE.has(j.status));
  useEffect(() => {
    if (!anyActive) return;
    let ticks = 0;
    const timer = setInterval(async () => {
      const js = await api.jobs();
      setJobs(js);
      // A long lookup fills in the playlist as it goes; show results every 15s.
      if (++ticks % 15 === 0 && js.some((j) => j.kind === "lookup" && j.status === "running")) {
        refreshData();
      }
      const newlyDone = js.filter((j) => !ACTIVE.has(j.status) && !finished.current.has(j.id));
      if (newlyDone.length) {
        newlyDone.forEach((j) => finished.current.add(j.id));
        const dr = await refreshData();
        const done = newlyDone.find((j) => j.kind === "draft" && j.status === "done");
        if (done) {
          const d = dr.find((x) => x.draft_id === done.result.draft_id);
          setCurrentDraft(done.result.draft_id);
          if (d) load(draftUrl(d.draft_id, d.mp3 || d.wav), autoPlay);
        }
        const failed = newlyDone.find((j) => j.status === "failed");
        if (failed) setError(`${failed.params.title ?? failed.kind}: ${failed.error}`);
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [anyActive, refreshData, load, autoPlay, setCurrentDraft, setError]);

  return { jobs, setJobs, finished };
}
