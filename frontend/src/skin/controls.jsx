import { useEffect, useRef } from "react";

const ICONS = {
  prev: "M4 4h2v12H4zM16 4v12L7 10zM16 10z",
  play: "M6 3.5v13L17 10z",
  pause: "M5 4h3.5v12H5zM11.5 4H15v12h-3.5z",
  stop: "M5 5h10v10H5z",
  next: "M14 4h2v12h-2zM4 4v12l9-6z",
  eject: "M10 4l6 7H4zM4 13h12v3H4z",
  plus: "M8.5 4h3v4.5H16v3h-4.5V16h-3v-4.5H4v-3h4.5z",
  minus: "M4 8.5h12v3H4z",
  list: "M4 4h2.5v2.5H4zM8 4.5h8v1.5H8zM4 8.75h2.5v2.5H4zM8 9.25h8v1.5H8zM4 13.5h2.5V16H4zM8 14h8v1.5H8z",
  folder: "M3 5h5l1.5 1.5H17V15H3z",
  download: "M8.5 3h3v6h3L10 14 5.5 9h3zM4 15h12v2H4z",
  spotify:
    "M10 2.5a7.5 7.5 0 100 15 7.5 7.5 0 000-15zm3.3 10.9a.5.5 0 01-.7.2c-1.9-1.2-4.3-1.4-7.1-.8a.5.5 0 11-.2-1c3-.7 5.7-.4 7.8.9.3.2.4.5.2.7zm1-2.2a.6.6 0 01-.9.2c-2.2-1.3-5.4-1.7-8-.9a.6.6 0 11-.3-1.2c2.9-.9 6.5-.5 9 1 .3.2.4.6.2.9zm.1-2.3C11.8 7.4 7.6 7.2 5.2 8a.7.7 0 11-.4-1.4c2.8-.8 7.4-.7 10.4 1.1a.7.7 0 01-.8 1.2z",
};

export function Icon({ name }) {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" className="wa-icon">
      <path d={ICONS[name]} />
    </svg>
  );
}

/** Round chrome button (transport, playlist bottom bar). */
export function ChromeButton({ icon, label, small, ...props }) {
  return (
    <button type="button" className={`wa-chrome ${small ? "is-small" : ""}`} aria-label={label} title={label} {...props}>
      <Icon name={icon} />
    </button>
  );
}

/** Capsule toggle like EQ / PL / ON / AUTO. */
export function Pill({ children, on, variant = "light", ...props }) {
  return (
    <button
      type="button"
      className={`wa-pill wa-pill-${variant} ${on ? "is-on" : ""}`}
      aria-pressed={on === undefined ? undefined : on}
      {...props}
    >
      {children}
    </button>
  );
}

/** Text that scrolls Winamp-style (character steps) when it overflows. */
export function Marquee({ text, width = 31 }) {
  const long = text.length > width;
  const loop = `${text}  ***  `;
  return (
    <div className="wa-marquee" aria-label={text} title={text}>
      {long ? (
        <span
          className="wa-marquee-track"
          style={{ "--chars": loop.length, animationDuration: `${loop.length * 0.22}s` }}
        >
          {loop}
          {loop}
        </span>
      ) : (
        <span>{text}</span>
      )}
    </div>
  );
}

const fmt = (s) => {
  if (!Number.isFinite(s) || s < 0) s = 0;
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
};
export const formatTime = fmt;

/** 19-band analyser drawn as stacked dashes, with falling peaks. */
export function Spectrum({ getAnalyser, active }) {
  const canvas = useRef(null);
  const state = useRef({ levels: new Float32Array(19), peaks: new Float32Array(19) });

  useEffect(() => {
    const c = canvas.current;
    const ctx = c.getContext("2d");
    const W = c.width;
    const H = c.height;
    const BARS = 19;
    const barW = 6;
    const gap = 2;
    const seg = 2;
    let data = null;
    let raf;

    const draw = () => {
      const analyser = active ? getAnalyser() : null;
      const { levels, peaks } = state.current;
      if (analyser) {
        if (!data || data.length !== analyser.frequencyBinCount) {
          data = new Uint8Array(analyser.frequencyBinCount);
        }
        analyser.getByteFrequencyData(data);
        const nyquist = analyser.context.sampleRate / 2;
        for (let i = 0; i < BARS; i++) {
          // log-spaced bands from 40 Hz to 16 kHz
          const lo = 40 * Math.pow(16000 / 40, i / BARS);
          const hi = 40 * Math.pow(16000 / 40, (i + 1) / BARS);
          const a = Math.floor((lo / nyquist) * data.length);
          const b = Math.max(a + 1, Math.floor((hi / nyquist) * data.length));
          let m = 0;
          for (let k = a; k < b; k++) m = Math.max(m, data[k]);
          const v = m / 255;
          levels[i] = Math.max(v, levels[i] - 0.06);
        }
      } else {
        for (let i = 0; i < BARS; i++) levels[i] = Math.max(0, levels[i] - 0.06);
      }

      ctx.clearRect(0, 0, W, H);
      for (let i = 0; i < BARS; i++) {
        const h = Math.round(levels[i] * H);
        peaks[i] = h >= peaks[i] ? h : Math.max(0, peaks[i] - 0.5);
        const x = i * (barW + gap);
        ctx.fillStyle = "#e6eeff";
        for (let y = H - seg; y >= H - h; y -= seg * 2) ctx.fillRect(x, y, barW, seg);
        if (peaks[i] > 1) {
          ctx.fillStyle = "#9fb8ee";
          ctx.fillRect(x, H - Math.round(peaks[i]) - seg, barW, seg);
        }
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [getAnalyser, active]);

  return <canvas ref={canvas} className="wa-spectrum" width={152} height={34} aria-hidden="true" />;
}

/** Thin horizontal progress groove used for jobs. */
export function Progress({ value }) {
  return (
    <div className="wa-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round((value ?? 0) * 100)}>
      <div style={{ width: `${Math.round((value ?? 0) * 100)}%` }} />
    </div>
  );
}

/** The playlist-style list body with a striped scrollbar look. */
export function TrackList({ children, label, empty }) {
  return (
    <div className="wa-list" role="listbox" aria-label={label} tabIndex={-1}>
      {children}
      {empty && <div className="wa-list-empty">{empty}</div>}
    </div>
  );
}
