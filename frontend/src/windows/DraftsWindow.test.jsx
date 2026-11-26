import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import DraftsWindow from "./DraftsWindow.jsx";

vi.mock("../api.js", () => ({
  draftUrl: (id, file) => `/drafts/${id}/${file}`,
}));

afterEach(cleanup);

const draft = (n, extra = {}) => ({
  draft_id: `d${n}`,
  title: `Draft ${n}`,
  duration: 60,
  start_a: 0,
  start_b: 0,
  stretch: 1,
  wav: `d${n}.wav`,
  mp3: `d${n}.mp3`,
  ...extra,
});

describe("DraftsWindow", () => {
  it("shows the empty state when there are no drafts or jobs", () => {
    render(<DraftsWindow jobs={[]} drafts={[]} current={null} onSelect={vi.fn()} />);
    expect(screen.getByText("Pick a match and press A VOX + B INST.")).toBeInTheDocument();
    expect(screen.getByText("0 DRAFTS")).toBeInTheDocument();
    expect(screen.getByText("NO DRAFT LOADED")).toBeInTheDocument();
  });

  it("renders drafts, active jobs and failed jobs", () => {
    const jobs = [
      { id: "j1", status: "running", progress: 0.5, message: "mixing", params: { title: "Rendering one" } },
      { id: "j2", status: "failed", progress: 0, error: "boom", params: { title: "Broken" } },
    ];
    render(<DraftsWindow jobs={jobs} drafts={[draft(1), draft(2)]} current={draft(1)} onSelect={vi.fn()} />);
    expect(screen.getByText(/1\. Draft 1/)).toBeInTheDocument();
    expect(screen.getByText(/2\. Draft 2/)).toBeInTheDocument();
    expect(screen.getByText(/Rendering one/)).toBeInTheDocument();
    expect(screen.getByText(/Failed · Broken: boom/)).toBeInTheDocument();
    expect(screen.queryByText("Pick a match and press A VOX + B INST.")).not.toBeInTheDocument();
    expect(screen.getByText("RENDER 50%")).toBeInTheDocument();
    expect(screen.getByLabelText("Download WAV")).toHaveAttribute("href", "/drafts/d1/d1.wav");
    expect(screen.getByLabelText("Download MP3")).toHaveAttribute("href", "/drafts/d1/d1.mp3");
  });

  it("calls onSelect with the draft id and autoplay when a row is clicked", () => {
    const onSelect = vi.fn();
    render(<DraftsWindow jobs={[]} drafts={[draft(1), draft(2)]} current={draft(1)} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: /2\. Draft 2/ }));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith("d2", true);
  });
});
