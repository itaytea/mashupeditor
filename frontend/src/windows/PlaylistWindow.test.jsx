import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

vi.mock("../api.js", () => ({
  youtubeUrl: (artist, title) => `https://yt.test/?q=${artist}-${title}`,
}));

import PlaylistWindow from "./PlaylistWindow.jsx";

const playlist = {
  name: "Road Trip",
  tracks: [
    {
      position: 0,
      title: "Owned Song",
      artists: ["Alpha"],
      duration_ms: 180000,
      match_score: 97,
      local: { path: "/music/owned.mp3", key_name: "A minor" },
      entity: { id: "e1", camelot: "8A", bpm: 123.4 },
    },
    {
      position: 1,
      title: "Missing Song",
      artists: ["Beta", "Gamma"],
      duration_ms: 200000,
      local: null,
      lookup: { key_name: "C major", bpm: 100, matched_title: "Missing Song", matched_artist: "Beta" },
      entity: { id: "s2", camelot: "8B", bpm: 100 },
    },
    { position: 2, title: "Unknown Song", artists: ["Delta"], duration_ms: 60000, local: null, lookup: null },
  ],
};

const library = [
  { id: 1, artist: "Lib Artist", title: "Lib One", camelot: "5A", bpm: 90, duration: 120, path: "/m/1.mp3", key_name: "E minor" },
  { id: 2, artist: "", title: "Broken", error: "decode failed", path: "/m/2.mp3" },
];

function setup(props = {}) {
  const handlers = {
    onView: vi.fn(),
    onFolder: vi.fn(),
    onScan: vi.fn(),
    onPlaylistUrl: vi.fn(),
    onLoadPlaylist: vi.fn(),
    onDisconnect: vi.fn(),
    onSelectTrack: vi.fn(),
    onClose: vi.fn(),
    onLookup: vi.fn(),
    onCancelJob: vi.fn(),
    onFindDir: vi.fn(),
    onFind: vi.fn(),
    onWatch: vi.fn(),
  };
  render(
    <PlaylistWindow
      config={{ spotify_configured: true, spotify_connected: false }}
      playlist={null}
      library={[]}
      view="library"
      folder=""
      playlistUrl=""
      loadingPlaylist={false}
      selectedTrack={null}
      scanJob={null}
      lookupJob={null}
      findJob={null}
      findDir=""
      watch={false}
      {...handlers}
      {...props}
    />,
  );
  return handlers;
}

describe("PlaylistWindow", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows the empty library hint and disabled actions", () => {
    setup();
    expect(screen.getByText("Type your music folder above and press SCAN.")).toBeInTheDocument();
    expect(screen.getByText("0 FILES")).toBeInTheDocument();
    expect(screen.getByText("LIBRARY")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "SCAN" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "LOAD" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Show Spotify playlist" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Look up key\/BPM/ })).toBeDisabled();
  });

  it("lists library tracks and selects/deselects a row", () => {
    const { onSelectTrack } = setup({ library });
    expect(screen.getByText("2 FILES")).toBeInTheDocument();
    fireEvent.click(screen.getByText(/Lib Artist - Lib One/).closest("button"));
    expect(onSelectTrack).toHaveBeenLastCalledWith("t1");
  });

  it("deselects an already selected library row", () => {
    const { onSelectTrack } = setup({ library, selectedTrack: "t1" });
    fireEvent.click(screen.getByText(/Lib Artist - Lib One/).closest("button"));
    expect(onSelectTrack).toHaveBeenLastCalledWith(null);
  });

  it("does not select a track that failed analysis", () => {
    const { onSelectTrack } = setup({ library });
    const row = screen.getByText(/Unknown - Broken/).closest("button");
    expect(row).toHaveAttribute("title", "Analysis failed: decode failed");
    fireEvent.click(row);
    expect(onSelectTrack).not.toHaveBeenCalled();
  });

  it("shows the playlist, its counts, and selects rows by entity id or position", () => {
    const { onSelectTrack } = setup({ playlist, view: "playlist", library });
    const list = screen.getByLabelText("Road Trip");
    expect(within(list).getByText(/Alpha - Owned Song/)).toBeInTheDocument();
    expect(within(list).getByText(/Beta, Gamma - Missing Song/)).toBeInTheDocument();
    expect(screen.getByText("1 OWNED 1 LOOKED UP")).toBeInTheDocument();
    expect(screen.getByText("3 TRACKS")).toBeInTheDocument();
    expect(within(list).getByText("123")).toBeInTheDocument();

    fireEvent.click(within(list).getByText(/Alpha - Owned Song/).closest("button"));
    expect(onSelectTrack).toHaveBeenLastCalledWith("e1");
    fireEvent.click(within(list).getByText(/Delta - Unknown Song/).closest("button"));
    expect(onSelectTrack).toHaveBeenLastCalledWith("p2");
  });

  it("toggles between playlist and library views", () => {
    const { onView } = setup({ playlist, view: "playlist" });
    fireEvent.click(screen.getByRole("button", { name: "Show whole library" }));
    expect(onView).toHaveBeenCalledWith("library");
  });

  it("switches to the playlist view from the library view", () => {
    const { onView } = setup({ playlist, view: "library" });
    fireEvent.click(screen.getByRole("button", { name: "Show Spotify playlist" }));
    expect(onView).toHaveBeenCalledWith("playlist");
  });

  it("opens a YouTube search for a selected track you don't own", () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    setup({ playlist, view: "playlist", selectedTrack: "s2" });
    fireEvent.click(screen.getByRole("button", { name: 'Search YouTube for "Missing Song"' }));
    expect(open).toHaveBeenCalledWith("https://yt.test/?q=Beta Gamma-Missing Song", "_blank", "noopener");
  });

  it("disables the YouTube button when the selected track is owned or nothing is selected", () => {
    setup({ playlist, view: "playlist", selectedTrack: "e1" });
    expect(screen.getByRole("button", { name: /Select a track you don't own/ })).toBeDisabled();
  });

  it("submits the three source forms and reports typing", () => {
    const h = setup({
      folder: "/music", playlistUrl: "https://open.spotify.com/playlist/x", findDir: "/dl", playlist,
    });
    fireEvent.change(screen.getByLabelText("DIR"), { target: { value: "/new" } });
    expect(h.onFolder).toHaveBeenCalledWith("/new");
    fireEvent.change(screen.getByLabelText("URL"), { target: { value: "u" } });
    expect(h.onPlaylistUrl).toHaveBeenCalledWith("u");
    fireEvent.change(screen.getByLabelText("FIND"), { target: { value: "/d2" } });
    expect(h.onFindDir).toHaveBeenCalledWith("/d2");

    fireEvent.click(screen.getByRole("button", { name: "SCAN" }));
    expect(h.onScan).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "LOAD" }));
    expect(h.onLoadPlaylist).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getAllByRole("button", { name: "FIND" })[0]);
    expect(h.onFind).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "WATCH" }));
    expect(h.onWatch).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Analyse music folder" }));
    expect(h.onScan).toHaveBeenCalledTimes(2);
  });

  it("shows loading state while the playlist loads", () => {
    setup({ playlistUrl: "x", loadingPlaylist: true });
    expect(screen.getByRole("button", { name: "WAIT" })).toBeDisabled();
  });

  it("shows scan progress and blocks a second scan", () => {
    setup({
      folder: "/music",
      scanJob: { id: "j1", status: "running", progress: 0.5, message: "Analysing 3/6" },
    });
    expect(screen.getByRole("button", { name: "SCANNING" })).toBeDisabled();
    expect(screen.getByText("SCAN 50%")).toBeInTheDocument();
    expect(screen.getByText("Analysing 3/6")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "50");
  });

  it("shows queued scan and find states", () => {
    setup({
      folder: "/m", findDir: "/d", playlist,
      scanJob: { id: "j1", status: "queued", progress: 0, message: "" },
      findJob: { id: "j2", status: "queued", progress: 0, message: "" },
    });
    expect(screen.getAllByRole("button", { name: "QUEUED" })).toHaveLength(2);
  });

  it("lets you cancel a running lookup by job id", () => {
    const { onCancelJob } = setup({
      playlist,
      lookupJob: { id: "job-9", status: "running", progress: 0.25, message: "Looking up" },
    });
    expect(screen.getByText("LOOKUP 25%")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Stop the key/BPM lookup" }));
    expect(onCancelJob).toHaveBeenCalledWith("job-9");
  });

  it("starts a lookup when a playlist is loaded", () => {
    const { onLookup } = setup({ playlist });
    fireEvent.click(screen.getByRole("button", { name: /Look up key\/BPM/ }));
    expect(onLookup).toHaveBeenCalledTimes(1);
  });

  it("describes Spotify state and offers connect / disconnect", () => {
    const { onDisconnect } = setup({ config: { spotify_configured: true, spotify_connected: true, spotify_user: "dj" } });
    expect(screen.getByText("Spotify: dj")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Disconnect Spotify" }));
    expect(onDisconnect).toHaveBeenCalledTimes(1);
  });

  it("shows connect links when Spotify is configured but not connected", () => {
    setup();
    expect(screen.getByText(/Spotify: not connected/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "connect" })).toHaveAttribute("href", "/api/spotify/login");
    expect(screen.getByRole("link", { name: "Connect Spotify" })).toBeInTheDocument();
  });

  it("explains missing Spotify keys", () => {
    setup({ config: { spotify_configured: false } });
    expect(screen.getByText(/Spotify keys not set/)).toBeInTheDocument();
  });

  it("closes the window", () => {
    const { onClose } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Close PLAYLIST" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
