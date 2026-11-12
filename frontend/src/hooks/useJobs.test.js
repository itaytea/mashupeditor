import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useJobs } from "./useJobs.js";

vi.mock("../api.js", () => ({
  api: { jobs: vi.fn() },
  draftUrl: (id, file) => `/drafts/${id}/${file}`,
}));

import { api } from "../api.js";

function setup(overrides = {}) {
  const props = {
    refreshData: vi.fn().mockResolvedValue([]),
    load: vi.fn(),
    autoPlay: true,
    setCurrentDraft: vi.fn(),
    setError: vi.fn(),
    ...overrides,
  };
  const hook = renderHook(() => useJobs(props));
  return { props, ...hook };
}

const tick = (ms = 1000) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

beforeEach(() => {
  vi.useFakeTimers();
  api.jobs.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useJobs", () => {
  it("does not poll when no job is active", async () => {
    setup();
    await tick(5000);
    expect(api.jobs).not.toHaveBeenCalled();
  });

  it("polls every second while a job is active, and stops when done", async () => {
    const { result } = setup();
    act(() => result.current.setJobs([{ id: 1, kind: "scan", status: "running" }]));
    api.jobs.mockResolvedValue([{ id: 1, kind: "scan", status: "running" }]);
    await tick(3000);
    expect(api.jobs).toHaveBeenCalledTimes(3);

    api.jobs.mockResolvedValue([{ id: 1, kind: "scan", status: "done" }]);
    await tick(1000);
    const calls = api.jobs.mock.calls.length;
    await tick(5000);
    expect(api.jobs.mock.calls.length).toBe(calls);
    expect(result.current.jobs[0].status).toBe("done");
  });

  it("refreshes data once when a job finishes", async () => {
    const { result, props } = setup();
    act(() => result.current.setJobs([{ id: 1, kind: "scan", status: "running" }]));
    api.jobs.mockResolvedValue([{ id: 1, kind: "scan", status: "done" }]);
    await tick(1000);
    expect(props.refreshData).toHaveBeenCalledTimes(1);
    expect(result.current.finished.current.has(1)).toBe(true);
  });

  it("loads and selects the finished draft", async () => {
    const draftJob = { id: 2, kind: "draft", status: "done", result: { draft_id: "d1" }, params: {} };
    const props = { refreshData: vi.fn().mockResolvedValue([{ draft_id: "d1", mp3: "d1.mp3" }]) };
    const { result, props: p } = setup(props);
    act(() => result.current.setJobs([{ id: 2, kind: "draft", status: "running", params: {} }]));
    api.jobs.mockResolvedValue([draftJob]);
    await tick(1000);
    expect(p.setCurrentDraft).toHaveBeenCalledWith("d1");
    expect(p.load).toHaveBeenCalledWith("/drafts/d1/d1.mp3", true);
  });

  it("reports failed jobs", async () => {
    const { result, props } = setup();
    act(() => result.current.setJobs([{ id: 3, kind: "find", status: "running", params: {} }]));
    api.jobs.mockResolvedValue([{ id: 3, kind: "find", status: "failed", error: "boom", params: { title: "T" } }]);
    await tick(1000);
    expect(props.setError).toHaveBeenCalledWith("T: boom");
  });

  it("refreshes every 15s while a lookup is running", async () => {
    const { result, props } = setup();
    const running = [{ id: 4, kind: "lookup", status: "running" }];
    act(() => result.current.setJobs(running));
    api.jobs.mockResolvedValue(running);
    await tick(14000);
    expect(props.refreshData).not.toHaveBeenCalled();
    await tick(1000);
    expect(props.refreshData).toHaveBeenCalledTimes(1);
  });

  it("ignores jobs already marked finished", async () => {
    const { result, props } = setup();
    result.current.finished.current.add(5);
    act(() => result.current.setJobs([{ id: 6, kind: "scan", status: "running" }]));
    api.jobs.mockResolvedValue([
      { id: 5, kind: "scan", status: "done" },
      { id: 6, kind: "scan", status: "running" },
    ]);
    await tick(1000);
    expect(props.refreshData).not.toHaveBeenCalled();
  });

  it("surfaces api.jobs rejection and keeps polling", async () => {
    const { result, props } = setup();
    const running = [{ id: 7, kind: "scan", status: "running" }];
    act(() => result.current.setJobs(running));
    api.jobs.mockRejectedValueOnce(new Error("offline"));
    api.jobs.mockResolvedValue(running);
    await tick(1000);
    expect(props.setError).toHaveBeenCalledWith("Could not refresh jobs: offline");
    await tick(2000);
    expect(api.jobs).toHaveBeenCalledTimes(3);
  });

  it("skips ticks while the previous request is pending", async () => {
    const { result } = setup();
    const running = [{ id: 8, kind: "scan", status: "running" }];
    act(() => result.current.setJobs(running));
    let resolve;
    api.jobs.mockImplementationOnce(() => new Promise((r) => { resolve = r; }));
    api.jobs.mockResolvedValue(running);
    await tick(4000);
    expect(api.jobs).toHaveBeenCalledTimes(1);
    await act(async () => { resolve(running); });
    await tick(1000);
    expect(api.jobs).toHaveBeenCalledTimes(2);
  });
});
