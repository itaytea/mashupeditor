import { useCallback, useEffect, useRef, useState } from "react";

/**
 * One <audio> element routed through Web Audio:
 * source → balance → analyser → speakers (volume is the element's own).
 * The graph is built on first play (browsers require a user gesture).
 */
export function useAudioEngine() {
  const audio = useRef(null);
  const graph = useRef(null);
  const pendingBalance = useRef(null);
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

  const ensureGraph = useCallback(() => {
    if (graph.current) return graph.current;
    const Ctx = window.AudioContext || window.webkitAudioContext;
    const ctx = new Ctx();
    const source = ctx.createMediaElementSource(audio.current);
    const balance = ctx.createStereoPanner();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;
    analyser.smoothingTimeConstant = 0.6;
    source.connect(balance).connect(analyser).connect(ctx.destination);
    if (pendingBalance.current !== null) balance.pan.value = pendingBalance.current;
    graph.current = { ctx, balance, analyser };
    return graph.current;
  }, []);

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

  const setBalance = useCallback((b) => {
    if (graph.current) graph.current.balance.pan.value = b;
    else pendingBalance.current = b;
  }, []);

  const setLoop = useCallback((on) => {
    audio.current.loop = on;
  }, []);

  return {
    status, time, duration, src,
    analyser: () => graph.current?.analyser ?? null,
    load, play, pause, stop, seek, setVolume, setBalance, setLoop,
  };
}
