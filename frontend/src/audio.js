import { useCallback, useEffect, useRef, useState } from "react";

// Classic Winamp EQ bands.
export const EQ_BANDS = [60, 170, 310, 600, 1000, 3000, 6000, 12000, 14000, 16000];
export const EQ_LABELS = ["60", "170", "310", "600", "1K", "3K", "6K", "12K", "14K", "16K"];
export const EQ_MAX_DB = 12;

export const EQ_PRESETS = {
  Flat: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  "Vocal Boost": [-2, -1, 0, 2, 4, 4, 3, 1, 0, -1],
  "Bass Boost": [7, 6, 4, 1, 0, 0, 0, 0, 0, 0],
  Rock: [5, 3, -3, -5, -2, 2, 5, 6, 6, 6],
  Dance: [6, 4, 1, 0, 0, -3, -4, -4, 0, 0],
  Club: [0, 0, 3, 4, 4, 4, 2, 0, 0, 0],
  Live: [-3, 0, 2, 3, 3, 3, 2, 1, 1, 1],
};

/**
 * One <audio> element routed through Web Audio:
 * source → preamp → 10 EQ filters → balance → volume → analyser → speakers.
 * The graph is built on first play (browsers require a user gesture).
 */
export function useAudioEngine() {
  const audio = useRef(null);
  const graph = useRef(null);
  const eqState = useRef({ on: true, preamp: 0, bands: EQ_PRESETS.Flat });
  const [status, setStatus] = useState("stopped"); // playing | paused | stopped
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [src, setSrc] = useState(null);

  if (audio.current === null && typeof Audio !== "undefined") {
    audio.current = new Audio();
    audio.current.preload = "auto";
  }

  useEffect(() => {
    const el = audio.current;
    const onTime = () => setTime(el.currentTime);
    const onMeta = () => setDuration(el.duration || 0);
    const onEnd = () => {
      if (!el.loop) setStatus("stopped");
    };
    el.addEventListener("timeupdate", onTime);
    el.addEventListener("loadedmetadata", onMeta);
    el.addEventListener("ended", onEnd);
    return () => {
      el.removeEventListener("timeupdate", onTime);
      el.removeEventListener("loadedmetadata", onMeta);
      el.removeEventListener("ended", onEnd);
    };
  }, []);

  const applyEq = useCallback(() => {
    const g = graph.current;
    if (!g) return;
    const { on, preamp, bands } = eqState.current;
    g.preamp.gain.value = on ? 10 ** (preamp / 20) : 1;
    g.filters.forEach((f, i) => (f.gain.value = on ? bands[i] : 0));
  }, []);

  const ensureGraph = useCallback(() => {
    if (graph.current) return graph.current;
    const Ctx = window.AudioContext || window.webkitAudioContext;
    const ctx = new Ctx();
    const source = ctx.createMediaElementSource(audio.current);
    const preamp = ctx.createGain();
    const filters = EQ_BANDS.map((freq, i) => {
      const f = ctx.createBiquadFilter();
      f.type = i === 0 ? "lowshelf" : i === EQ_BANDS.length - 1 ? "highshelf" : "peaking";
      f.frequency.value = freq;
      f.Q.value = 1.4;
      return f;
    });
    const balance = ctx.createStereoPanner();
    const volume = ctx.createGain();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;
    analyser.smoothingTimeConstant = 0.6;
    [source, preamp, ...filters, balance, volume, analyser, ctx.destination].reduce((a, b) =>
      a.connect(b),
    );
    graph.current = { ctx, preamp, filters, balance, volume, analyser };
    applyEq();
    return graph.current;
  }, [applyEq]);

  const load = useCallback((url, autoplay = false) => {
    const el = audio.current;
    if (el.getAttribute("src") !== url) {
      el.src = url;
      setTime(0);
      setDuration(0);
      setSrc(url);
      setStatus("stopped");
    }
    if (autoplay) {
      ensureGraph().ctx.resume();
      el.play().then(() => setStatus("playing")).catch((e) => console.warn("play failed:", e));
    }
  }, [ensureGraph]);

  const play = useCallback(() => {
    if (!audio.current.getAttribute("src")) return;
    ensureGraph().ctx.resume();
    audio.current.play().then(() => setStatus("playing")).catch((e) => console.warn("play failed:", e));
  }, [ensureGraph]);

  const pause = useCallback(() => {
    const el = audio.current;
    if (status === "paused") play();
    else if (status === "playing") {
      el.pause();
      setStatus("paused");
    }
  }, [status, play]);

  const stop = useCallback(() => {
    const el = audio.current;
    el.pause();
    el.currentTime = 0;
    setTime(0);
    setStatus("stopped");
  }, []);

  const seek = useCallback((t) => {
    audio.current.currentTime = t;
    setTime(t);
  }, []);

  const setVolume = useCallback((v) => {
    audio.current.volume = v; // works before the graph exists too
  }, []);

  const setBalance = useCallback(
    (b) => {
      const g = graph.current;
      if (g) g.balance.pan.value = b;
      else eqState.current.pendingBalance = b;
    },
    [],
  );

  const setLoop = useCallback((on) => {
    audio.current.loop = on;
  }, []);

  const setEq = useCallback(
    (eq) => {
      eqState.current = { ...eqState.current, ...eq };
      applyEq();
    },
    [applyEq],
  );

  // Apply a balance chosen before the graph existed.
  useEffect(() => {
    if (status === "playing" && graph.current && eqState.current.pendingBalance !== undefined) {
      graph.current.balance.pan.value = eqState.current.pendingBalance;
      delete eqState.current.pendingBalance;
    }
  }, [status]);

  return {
    status, time, duration, src,
    analyser: () => graph.current?.analyser ?? null,
    load, play, pause, stop, seek, setVolume, setBalance, setLoop, setEq,
  };
}
