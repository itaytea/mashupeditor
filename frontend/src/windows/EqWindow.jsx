import { useEffect, useRef, useState } from "react";
import Window from "../skin/Window.jsx";
import { Pill } from "../skin/controls.jsx";
import { EQ_LABELS, EQ_MAX_DB, EQ_PRESETS } from "../audio.js";

const GRAPH_W = 228;
const GRAPH_H = 38;

function curvePath(values) {
  const pts = values.map((v, i) => [
    4 + (i * (GRAPH_W - 8)) / (values.length - 1),
    GRAPH_H / 2 - (v / EQ_MAX_DB) * (GRAPH_H / 2 - 3),
  ]);
  // Catmull-Rom → cubic Bézier for a smooth line like the original.
  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] ?? pts[i];
    const [p1, p2] = [pts[i], pts[i + 1]];
    const p3 = pts[i + 2] ?? p2;
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C${c1},${c2},${p2}`;
  }
  return d;
}

function VSlider({ value, onChange, label }) {
  return (
    <label className="wa-vslider" title={`${label}: ${value > 0 ? "+" : ""}${value} dB`}>
      <span className="sr-only">{label}</span>
      <input
        type="range" min={-EQ_MAX_DB} max={EQ_MAX_DB} step="0.5" value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        onDoubleClick={() => onChange(0)}
      />
    </label>
  );
}

export default function EqWindow({ engine, autoPlay, onAutoPlay, onClose }) {
  const [on, setOn] = useState(true);
  const [preamp, setPreamp] = useState(0);
  const [bands, setBands] = useState(EQ_PRESETS.Flat);
  const [menu, setMenu] = useState(false);
  const menuRef = useRef(null);

  const { setEq } = engine;
  useEffect(() => setEq({ on, preamp, bands }), [setEq, on, preamp, bands]);

  useEffect(() => {
    if (!menu) return;
    const close = (e) => !menuRef.current?.contains(e.target) && setMenu(false);
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [menu]);

  return (
    <Window title="EQUALIZER" className="wa-eq" onClose={onClose}>
      <div className="wa-eq-tab" aria-hidden="true">EQUALIZER</div>
      <div className="wa-eq-top">
        <Pill variant="dark" on={on} onClick={() => setOn(!on)} title="Equalizer on/off">ON</Pill>
        <Pill variant="dark" on={autoPlay} onClick={onAutoPlay} title="Auto-play drafts when they finish rendering">AUTO</Pill>
        <svg className="wa-eq-graph" viewBox={`0 0 ${GRAPH_W} ${GRAPH_H}`} aria-hidden="true">
          {[0.25, 0.5, 0.75].map((f) => (
            <line key={f} x1="0" x2={GRAPH_W} y1={GRAPH_H * f} y2={GRAPH_H * f} className="grid" />
          ))}
          {EQ_LABELS.map((_, i) => {
            const x = 4 + (i * (GRAPH_W - 8)) / (EQ_LABELS.length - 1);
            return <line key={i} x1={x} x2={x} y1="0" y2={GRAPH_H} className="grid" />;
          })}
          <line x1="0" x2={GRAPH_W} y1={GRAPH_H / 2 - (preamp / EQ_MAX_DB) * (GRAPH_H / 2 - 3)} y2={GRAPH_H / 2 - (preamp / EQ_MAX_DB) * (GRAPH_H / 2 - 3)} className="preamp" />
          <path d={curvePath(bands)} className="curve" />
        </svg>
        <div className="wa-presets" ref={menuRef}>
          <Pill variant="white" onClick={() => setMenu(!menu)} aria-haspopup="menu" aria-expanded={menu}>
            PRESETS
          </Pill>
          {menu && (
            <ul className="wa-menu" role="menu">
              {Object.keys(EQ_PRESETS).map((name) => (
                <li key={name}>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setBands(EQ_PRESETS[name]);
                      setPreamp(0);
                      setMenu(false);
                    }}
                  >
                    {name}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="wa-eq-sliders">
        <div className="wa-eq-col">
          <VSlider value={preamp} onChange={setPreamp} label="Preamp" />
          <span className="wa-eq-label is-wide">PREAMP</span>
        </div>
        <div className="wa-eq-col wa-eq-scale" aria-hidden="true">
          <div className="wa-eq-ruler">
            <span>+12 db</span>
            <span>+0 db</span>
            <span>-12 db</span>
          </div>
          <span className="wa-eq-label is-wide">&nbsp;</span>
        </div>
        {EQ_LABELS.map((label, i) => (
          <div className="wa-eq-col" key={label}>
            <VSlider
              value={bands[i]}
              label={`${label} Hz`}
              onChange={(v) => setBands((b) => b.map((x, j) => (j === i ? v : x)))}
            />
            <span className="wa-eq-label">{label}</span>
          </div>
        ))}
      </div>
    </Window>
  );
}
