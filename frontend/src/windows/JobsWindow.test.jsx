import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import JobsWindow from "./JobsWindow.jsx";

vi.mock("../api.js", () => ({
  api: { cancelJob: vi.fn() },
}));

import { api } from "../api.js";

afterEach(cleanup);

const now = () => Date.now() / 1000;

describe("JobsWindow", () => {
  it("shows the idle state when nothing is running", () => {
    render(<JobsWindow jobs={[]} onCancel={vi.fn()} />);
    expect(screen.getByText("Nothing running.")).toBeInTheDocument();
    expect(screen.getByText("IDLE")).toBeInTheDocument();
    expect(screen.getByText("0 RUNNING")).toBeInTheDocument();
  });

  it("renders running, waiting and recent jobs", () => {
    const jobs = [
      { id: "3", kind: "scan", status: "done", progress: 1, updated_at: now(), params: {} },
      { id: "2", kind: "find", status: "queued", progress: 0, params: { queue: "q" } },
      { id: "1", kind: "scan", status: "running", progress: 0.25, message: "analysing", params: { queue: "q" } },
    ];
    render(<JobsWindow jobs={jobs} onCancel={vi.fn()} />);
    expect(screen.getByText("25%")).toBeInTheDocument();
    expect(screen.getByText("analysing")).toBeInTheDocument();
    expect(screen.getByText("WAIT")).toBeInTheDocument();
    expect(screen.getByText("waiting for SCAN to finish")).toBeInTheDocument();
    expect(screen.getByText("DONE")).toBeInTheDocument();
    expect(screen.getByText("1 RUNNING")).toBeInTheDocument();
    expect(screen.getByText("1 WAITING")).toBeInTheDocument();
    expect(screen.queryByText("Nothing running.")).not.toBeInTheDocument();
  });

  it("cancels a job by id when its cancel button is clicked", () => {
    // The window only reports the id; wire it to the (mocked) api like the app does.
    const onCancel = vi.fn((id) => api.cancelJob(id));
    const jobs = [{ id: "7", kind: "draft", status: "running", progress: 0.1, params: { title: "My mashup" } }];
    render(<JobsWindow jobs={jobs} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole("button", { name: "Cancel My mashup" }));
    expect(onCancel).toHaveBeenCalledWith("7");
    expect(api.cancelJob).toHaveBeenCalledWith("7");
  });
});
