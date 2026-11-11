import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAudioEngine } from "./audio.js";

class FakeAudio extends EventTarget {
  constructor() {
    super();
    this.preload = "";
    this.currentTime = 0;
    this.duration = NaN;
    this.volume = 1;
    this.loop = false;
    this.paused = true;
    this._src = null;
    FakeAudio.last = this;
  }
  set src(v) {
    this._src = v;
  }
  getAttribute(name) {
    return name === "src" ? this._src : null;
  }
  play() {
    this.paused = false;
    return Promise.resolve();
  }
  pause() {
    this.paused = true;
  }
}

class FakeAudioContext {
  constructor() {
    this.destination = {};
    this.resume = vi.fn();
  }
  createMediaElementSource() {
    return { connect: (n) => n };
  }
  createStereoPanner() {
    const pan = { value: 0 };
    const node = { pan, connect: (n) => n };
    FakeAudioContext.panner = node;
    return node;
  }
  createAnalyser() {
    return { connect: (n) => n };
  }
}

beforeEach(() => {
  vi.stubGlobal("Audio", FakeAudio);
  vi.stubGlobal("AudioContext", FakeAudioContext);
  window.AudioContext = FakeAudioContext;
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.AudioContext;
});

describe("useAudioEngine", () => {
  it("starts stopped with no source", () => {
    const { result } = renderHook(() => useAudioEngine());
    expect(result.current.status).toBe("stopped");
    expect(result.current.time).toBe(0);
    expect(result.current.duration).toBe(0);
    expect(result.current.src).toBeNull();
    expect(result.current.analyser()).toBeNull();
  });

  it("load sets the source and resets time/duration", () => {
    const { result } = renderHook(() => useAudioEngine());
    act(() => result.current.load("/a.mp3"));
    expect(result.current.src).toBe("/a.mp3");
    expect(FakeAudio.last.getAttribute("src")).toBe("/a.mp3");
  });

  it("load with autoplay starts playback and builds the graph", async () => {
    const { result } = renderHook(() => useAudioEngine());
    await act(async () => result.current.load("/a.mp3", true));
    expect(result.current.status).toBe("playing");
    expect(result.current.analyser()).not.toBeNull();
  });

  it("play does nothing without a source", async () => {
    const { result } = renderHook(() => useAudioEngine());
    await act(async () => result.current.play());
    expect(result.current.status).toBe("stopped");
  });

  it("pause toggles between playing and paused", async () => {
    const { result } = renderHook(() => useAudioEngine());
    await act(async () => result.current.load("/a.mp3", true));
    act(() => result.current.pause());
    expect(result.current.status).toBe("paused");
    expect(FakeAudio.last.paused).toBe(true);
    await act(async () => result.current.pause());
    expect(result.current.status).toBe("playing");
  });

  it("stop rewinds and stops", async () => {
    const { result } = renderHook(() => useAudioEngine());
    await act(async () => result.current.load("/a.mp3", true));
    act(() => result.current.seek(12));
    expect(result.current.time).toBe(12);
    act(() => result.current.stop());
    expect(result.current.time).toBe(0);
    expect(FakeAudio.last.currentTime).toBe(0);
    expect(result.current.status).toBe("stopped");
  });

  it("applies volume and loop to the element", () => {
    const { result } = renderHook(() => useAudioEngine());
    act(() => {
      result.current.setVolume(0.4);
      result.current.setLoop(true);
    });
    expect(FakeAudio.last.volume).toBe(0.4);
    expect(FakeAudio.last.loop).toBe(true);
  });

  it("keeps a balance set before the graph exists and applies it later", async () => {
    const { result } = renderHook(() => useAudioEngine());
    act(() => result.current.setBalance(-0.5));
    await act(async () => result.current.load("/a.mp3", true));
    expect(FakeAudioContext.panner.pan.value).toBe(-0.5);
    act(() => result.current.setBalance(0.25));
    expect(FakeAudioContext.panner.pan.value).toBe(0.25);
  });

  it("tracks element events: time, duration and ended", async () => {
    const { result } = renderHook(() => useAudioEngine());
    await act(async () => result.current.load("/a.mp3", true));
    const el = FakeAudio.last;
    act(() => {
      el.currentTime = 3;
      el.dispatchEvent(new Event("timeupdate"));
      el.duration = 90;
      el.dispatchEvent(new Event("loadedmetadata"));
    });
    expect(result.current.time).toBe(3);
    expect(result.current.duration).toBe(90);
    act(() => el.dispatchEvent(new Event("ended")));
    expect(result.current.status).toBe("stopped");
  });

  it("does not stop on ended while looping", async () => {
    const { result } = renderHook(() => useAudioEngine());
    await act(async () => result.current.load("/a.mp3", true));
    act(() => result.current.setLoop(true));
    act(() => FakeAudio.last.dispatchEvent(new Event("ended")));
    expect(result.current.status).toBe("playing");
  });
});
