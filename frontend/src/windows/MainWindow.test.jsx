import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import MainWindow from "./MainWindow.jsx";

function makeEngine(overrides = {}) {
  return {
    status: "stopped",
    time: 0,
    duration: 0,
    analyser: vi.fn(() => null),
    play: vi.fn(),
    pause: vi.fn(),
    stop: vi.fn(),
    seek: vi.fn(),
    setVolume: vi.fn(),
    setBalance: vi.fn(),
    setLoop: vi.fn(),
    ...overrides,
  };
}

function setup(props = {}) {
  const engine = props.engine ?? makeEngine();
  const handlers = {
    onPrev: vi.fn(),
    onNext: vi.fn(),
    onEject: vi.fn(),
    onAutoPlay: vi.fn(),
    onTogglePl: vi.fn(),
    onShuffle: vi.fn(),
  };
  render(
    <MainWindow
      engine={engine}
      draft={null}
      trackB={null}
      activeJob={null}
      autoPlay={false}
      showPl={false}
      shuffle={false}
      {...handlers}
      {...props}
    />,
  );
  return { engine, ...handlers };
}

describe("MainWindow", () => {
  beforeEach(() => {
    // jsdom has no canvas; the spectrum draws on one every frame.
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      clearRect: vi.fn(),
      fillRect: vi.fn(),
    });
    vi.stubGlobal("requestAnimationFrame", () => 1);
    vi.stubGlobal("cancelAnimationFrame", () => {});
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("shows the idle prompt and placeholder info with no draft", () => {
    setup();
    expect(screen.getByLabelText(/PICK A MATCH AND RENDER A DRAFT/)).toBeInTheDocument();
    expect(screen.getAllByText("---")).toHaveLength(1);
    expect(screen.getByText("--")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download draft (MP3)" })).toBeDisabled();
    expect(screen.getByLabelText("Seek")).toBeDisabled();
  });

  it("shows the draft title and track B info", () => {
    setup({
      draft: { title: "Cool Mashup", engine: "librosa" },
      trackB: { bpm: 127.6, camelot: "8A" },
    });
    expect(screen.getByLabelText("1. COOL MASHUP")).toBeInTheDocument();
    expect(screen.getByText("128")).toBeInTheDocument();
    expect(screen.getByText("8A")).toBeInTheDocument();
    expect(screen.getByText("PV")).toHaveClass("is-lit");
    expect(screen.getByText("RUBBERBAND")).not.toHaveClass("is-lit");
    expect(screen.getByRole("button", { name: "Download draft (MP3)" })).toBeEnabled();
  });

  it("shows render progress for an active job instead of the draft title", () => {
    setup({
      draft: { title: "Old Draft" },
      activeJob: { progress: 0.42, message: "Separating", params: { title: "New One" } },
    });
    expect(screen.getByLabelText("42% SEPARATING - NEW ONE")).toBeInTheDocument();
    expect(screen.queryByLabelText(/OLD DRAFT/)).not.toBeInTheDocument();
  });

  it("falls back to QUEUED when the active job has no message", () => {
    setup({ activeJob: { progress: 0, message: "", params: { title: "X" } } });
    expect(screen.getByLabelText("0% QUEUED - X")).toBeInTheDocument();
  });

  it("toggles elapsed and remaining time", () => {
    setup({ engine: makeEngine({ time: 30, duration: 100 }) });
    const toggle = screen.getByTitle("Toggle elapsed / remaining");
    expect(toggle).toHaveTextContent("00:30");
    fireEvent.click(toggle);
    expect(toggle).toHaveTextContent("-01:10");
    fireEvent.click(toggle);
    expect(toggle).toHaveTextContent("00:30");
  });

  it("wires the transport buttons to the engine and callbacks", () => {
    const { engine, onPrev, onNext, onEject } = setup({ draft: { title: "D" } });
    fireEvent.click(screen.getByRole("button", { name: "Play" }));
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    fireEvent.click(screen.getByRole("button", { name: "Previous draft" }));
    fireEvent.click(screen.getByRole("button", { name: "Next draft" }));
    fireEvent.click(screen.getByRole("button", { name: "Download draft (MP3)" }));
    expect(engine.play).toHaveBeenCalledTimes(1);
    expect(engine.pause).toHaveBeenCalledTimes(1);
    expect(engine.stop).toHaveBeenCalledTimes(1);
    expect(onPrev).toHaveBeenCalledTimes(1);
    expect(onNext).toHaveBeenCalledTimes(1);
    expect(onEject).toHaveBeenCalledTimes(1);
  });

  it("calls the AUTO, PL and shuffle handlers and reflects their state", () => {
    const { onAutoPlay, onTogglePl, onShuffle } = setup({ autoPlay: true, shuffle: true });
    expect(screen.getByRole("button", { name: "AUTO" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "PL" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByTitle("Shuffle drafts")).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "AUTO" }));
    fireEvent.click(screen.getByRole("button", { name: "PL" }));
    fireEvent.click(screen.getByTitle("Shuffle drafts"));
    expect(onAutoPlay).toHaveBeenCalledTimes(1);
    expect(onTogglePl).toHaveBeenCalledTimes(1);
    expect(onShuffle).toHaveBeenCalledTimes(1);
  });

  it("toggles repeat through engine.setLoop", () => {
    const { engine } = setup();
    const repeat = screen.getByTitle("Repeat");
    fireEvent.click(repeat);
    expect(engine.setLoop).toHaveBeenLastCalledWith(true);
    expect(repeat).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(repeat);
    expect(engine.setLoop).toHaveBeenLastCalledWith(false);
    expect(repeat).toHaveAttribute("aria-pressed", "false");
  });

  it("sets volume and snaps balance near the centre", () => {
    const { engine } = setup();
    fireEvent.change(screen.getByLabelText("Volume"), { target: { value: "0.3" } });
    expect(engine.setVolume).toHaveBeenCalledWith(0.3);
    fireEvent.change(screen.getByLabelText("Balance"), { target: { value: "0.5" } });
    expect(engine.setBalance).toHaveBeenLastCalledWith(0.5);
    fireEvent.change(screen.getByLabelText("Balance"), { target: { value: "-0.05" } });
    expect(engine.setBalance).toHaveBeenLastCalledWith(0);
  });

  it("seeks only when the scrub is released", () => {
    const { engine } = setup({ engine: makeEngine({ time: 5, duration: 100 }) });
    const seek = screen.getByLabelText("Seek");
    expect(seek).toBeEnabled();
    fireEvent.change(seek, { target: { value: "60" } });
    expect(engine.seek).not.toHaveBeenCalled();
    fireEvent.pointerUp(seek);
    expect(engine.seek).toHaveBeenCalledWith(60);
  });
});
