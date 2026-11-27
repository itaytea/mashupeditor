import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

vi.mock("../api.js", () => ({
  api: { pairs: vi.fn() },
  youtubeUrl: (artist, title) => `https://yt.test/?q=${artist}-${title}`,
}));

import { api } from "../api.js";
import MatchesWindow from "./MatchesWindow.jsx";

const track = (id, over = {}) => ({
  id,
  title: `Title ${id}`,
  artist: `Artist ${id}`,
  camelot: "8A",
  bpm: 120.4,
  local_id: null,
  ...over,
});

const ownedData = {
  total_tracks: 3,
  tracks: {
    a1: track("a1", { local_id: 11 }),
    b2: track("b2", { local_id: 22, camelot: "9A" }),
    c3: track("c3", { local_id: 33 }),
  },
  pairs: [
    { a: "a1", b: "b2", score: 0.91, reasons: ["Adjacent keys"] },
    { a: "b2", b: "c3", score: 0.7, reasons: ["Close BPM"] },
  ],
};

function setup(props = {}) {
  const handlers = { onClearTrack: vi.fn(), onDraft: vi.fn(), onError: vi.fn() };
  render(
    <MatchesWindow
      refreshKey={0}
      hasPlaylist={false}
      selectedTrack={null}
      busy={false}
      {...handlers}
      {...props}
    />,
  );
  return handlers;
}

describe("MatchesWindow", () => {
  beforeEach(() => {
    api.pairs.mockReset();
  });
  afterEach(cleanup);

  it("starts in library scope and fetches pairs", async () => {
    api.pairs.mockResolvedValue(ownedData);
    setup();
    await screen.findByText("91");
    expect(api.pairs).toHaveBeenCalledWith("library", 300, null, false);
    expect(screen.getByText("3 TRACKS")).toBeInTheDocument();
    expect(screen.getByText("91")).toBeInTheDocument();
    expect(screen.getByText("8A/9A")).toBeInTheDocument();
  });

  it("starts in playlist scope when a playlist is loaded", async () => {
    api.pairs.mockResolvedValue(ownedData);
    setup({ hasPlaylist: true });
    await screen.findByText("91");
    expect(api.pairs).toHaveBeenCalledWith("playlist", 300, null, false);
    expect(screen.getByRole("button", { name: "PL" })).toHaveAttribute("aria-pressed", "true");
  });

  it("shows the library empty message before data arrives and when there are no pairs", async () => {
    api.pairs.mockResolvedValue({ tracks: {}, pairs: [], total_tracks: 0 });
    setup();
    expect(screen.getByText(/Need at least two analysed files/)).toBeInTheDocument();
    await waitFor(() => expect(api.pairs).toHaveBeenCalled());
    expect(screen.getByText(/Need at least two analysed files/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "A VOX + B INST" })).not.toBeInTheDocument();
  });

  it("shows the playlist empty message in playlist scope", async () => {
    api.pairs.mockResolvedValue({ tracks: {}, pairs: [], total_tracks: 0 });
    setup({ hasPlaylist: true });
    expect(screen.getByText(/No playlist tracks have key\/BPM yet/)).toBeInTheDocument();
    await waitFor(() => expect(api.pairs).toHaveBeenCalled());
  });

  it("reports API errors through onError", async () => {
    api.pairs.mockRejectedValue(new Error("boom"));
    const { onError } = setup();
    await waitFor(() => expect(onError).toHaveBeenCalledWith("boom"));
    expect(screen.getByText(/Need at least two analysed files/)).toBeInTheDocument();
  });

  it("drafts A vox + B inst and B vox + A inst with the quantize mode", async () => {
    api.pairs.mockResolvedValue(ownedData);
    const { onDraft } = setup();
    await screen.findByText("91");
    expect(screen.getByText("Adjacent keys")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "A VOX + B INST" }));
    expect(onDraft).toHaveBeenLastCalledWith(11, 22, "auto");
    fireEvent.click(screen.getByRole("button", { name: "B VOX + A INST" }));
    expect(onDraft).toHaveBeenLastCalledWith(22, 11, "auto");

    // auto -> on -> off
    fireEvent.click(screen.getByRole("button", { name: "Q:AUTO" }));
    fireEvent.click(screen.getByRole("button", { name: "Q:ON" }));
    fireEvent.click(screen.getByRole("button", { name: "A VOX + B INST" }));
    expect(onDraft).toHaveBeenLastCalledWith(11, 22, "off");
  });

  it("selecting another row changes which pair is drafted", async () => {
    api.pairs.mockResolvedValue(ownedData);
    const { onDraft } = setup();
    await screen.findByText("91");
    fireEvent.click(screen.getByText("70").closest("button"));
    expect(screen.getByText("Close BPM")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "A VOX + B INST" }));
    expect(onDraft).toHaveBeenLastCalledWith(22, 33, "auto");
  });

  it("disables drafting while busy", async () => {
    api.pairs.mockResolvedValue(ownedData);
    setup({ busy: true });
    await screen.findByText("91");
    const buttons = screen.getAllByRole("button", { name: "RENDERING…" });
    expect(buttons).toHaveLength(2);
    buttons.forEach((b) => expect(b).toBeDisabled());
  });

  it("blocks drafting and links to YouTube when a file is missing", async () => {
    const data = {
      ...ownedData,
      tracks: { ...ownedData.tracks, b2: track("b2", { local_id: null, artist: "Bee", title: "Beta" }) },
    };
    api.pairs.mockResolvedValue(data);
    const { onDraft } = setup();
    await screen.findByText("91");
    const draft = screen.getByRole("button", { name: "A VOX + B INST" });
    expect(draft).toBeDisabled();
    fireEvent.click(draft);
    expect(onDraft).not.toHaveBeenCalled();
    expect(screen.getByText(/To render, get "Beta"/)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /Beta/ });
    expect(link).toHaveAttribute("href", "https://yt.test/?q=Bee-Beta");
  });

  it("refetches with the owned-only flag and the scope toggles", async () => {
    api.pairs.mockResolvedValue(ownedData);
    setup({ hasPlaylist: true });
    await screen.findByText("91");
    fireEvent.click(screen.getByRole("button", { name: "OWN" }));
    await waitFor(() => expect(api.pairs).toHaveBeenLastCalledWith("playlist", 300, null, true));
    fireEvent.click(screen.getByRole("button", { name: "LIB" }));
    await waitFor(() => expect(api.pairs).toHaveBeenLastCalledWith("library", 300, null, true));
  });

  it("disables the PL scope without a playlist", async () => {
    api.pairs.mockResolvedValue(ownedData);
    setup();
    await screen.findByText("91");
    expect(screen.getByRole("button", { name: "PL" })).toBeDisabled();
  });

  it("focuses on the selected track and clears the filter", async () => {
    api.pairs.mockResolvedValue(ownedData);
    const { onClearTrack } = setup({ selectedTrack: "a1" });
    await screen.findByText("WITH: TITLE A1");
    expect(api.pairs).toHaveBeenCalledWith("library", 300, "a1", false);
    expect(screen.getByText("WITH: TITLE A1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "CLEAR" }));
    expect(onClearTrack).toHaveBeenCalledTimes(1);
  });

  it("ignores selections that cannot be ranked", async () => {
    api.pairs.mockResolvedValue(ownedData);
    setup({ selectedTrack: "p3" });
    await screen.findByText("91");
    expect(api.pairs).toHaveBeenCalledWith("library", 300, null, false);
    expect(screen.queryByText(/Pairs with/)).not.toBeInTheDocument();
  });
});
