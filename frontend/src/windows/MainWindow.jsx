import { useState } from "react";
import Window from "../skin/Window.jsx";
import { ChromeButton, Marquee, Pill, Spectrum, formatTime } from "../skin/controls.jsx";

/** The main player: plays rendered drafts. */
export default function MainWindow({
  engine, draft, trackB, activeJob, onPrev, onNext, onEject,
  autoPlay, onAutoPlay, showPl, onTogglePl, shuffle, onShuffle,
}) {
  const [remaining, setRemaining] = useState(false);
  const [volume, setVolume] = useState(0.8);
  const [balance, setBalance] = useState(0);
  const [repeat, setRepeat] = useState(false);
  const [scrub, setScrub] = useState(null);

  const { status, time, duration } = engine;
  const shownTime = remaining ? Math.max(0, duration - time) : time;

  let title = "MASHUP MAKER - PICK A MATCH AND RENDER A DRAFT";
  if (activeJob) {
    title = `${Math.round(activeJob.progress * 100)}% ${activeJob.message || "QUEUED"} - ${activeJob.params.title}`;
  } else if (draft) {
    title = draft.title;
  }

  return (
    <Window title="MASHUP" className="wa-main">
      <div className="wa-main-top">
        <div className="wa-lcd wa-main-lcd">
          <div className="wa-clutter" aria-hidden="true">
            <i /><i /><i /><i /><i />
          </div>
          <div className="wa-lcd-left">
            <div className="wa-time-row">
              <span className={`wa-state wa-state-${status}`} aria-label={status} />
              <button
                type="button"
                className="wa-time"
                onClick={() => setRemaining((r) => !r)}
                title="Toggle elapsed / remaining"
              >
                <span className="wa-time-minus">{remaining ? "-" : " "}</span>
                {formatTime(shownTime)}
              </button>
            </div>
            <Spectrum getAnalyser={engine.analyser} active={status === "playing"} />
          </div>
          <div className="wa-lcd-right">
            <Marquee text={`${draft && !activeJob ? "1. " : ""}${title}`.toUpperCase()} width={29} />
            <div className="wa-info-row">
              <span className="wa-info-box">{trackB ? Math.round(trackB.bpm) : "---"}</span>
              <span className="wa-info-unit">BPM</span>
              <span className="wa-info-box is-narrow">{trackB?.camelot ?? "--"}</span>
              <span className="wa-info-unit">KEY</span>
              <span className={`wa-ind ${draft?.engine === "librosa" ? "is-lit" : ""}`}>PV</span>
              <span className={`wa-ind ${draft?.engine === "rubberband" ? "is-lit" : ""}`}>RUBBERBAND</span>
            </div>
          </div>
        </div>

        <div className="wa-deck">
          <label className="wa-hslider wa-volume" title={`Volume ${Math.round(volume * 100)}%`}>
            <span className="sr-only">Volume</span>
            <input
              type="range" min="0" max="1" step="0.01" value={volume}
              style={{ "--fill": `${volume * 100}%` }}
              onChange={(e) => {
                const v = Number(e.target.value);
                setVolume(v);
                engine.setVolume(v);
              }}
            />
          </label>
          <label className="wa-hslider wa-balance" title={`Balance ${balance === 0 ? "center" : balance < 0 ? "left" : "right"}`}>
            <span className="sr-only">Balance</span>
            <input
              type="range" min="-1" max="1" step="0.05" value={balance}
              onChange={(e) => {
                const b = Number(e.target.value);
                setBalance(Math.abs(b) < 0.08 ? 0 : b);
                engine.setBalance(Math.abs(b) < 0.08 ? 0 : b);
              }}
            />
          </label>
          <span className="wa-deck-pills">
            <Pill on={autoPlay} onClick={onAutoPlay} title="Auto-play each draft as soon as it finishes rendering">AUTO</Pill>
            <Pill on={showPl} onClick={onTogglePl} title="Toggle playlist">PL</Pill>
          </span>
        </div>
      </div>

      <label className="wa-seek">
        <span className="sr-only">Seek</span>
        <input
          type="range" min="0" max={duration || 1} step="0.1"
          value={scrub ?? time}
          disabled={!duration}
          onChange={(e) => setScrub(Number(e.target.value))}
          onPointerUp={() => {
            if (scrub !== null) engine.seek(scrub);
            setScrub(null);
          }}
          onKeyUp={() => {
            if (scrub !== null) engine.seek(scrub);
            setScrub(null);
          }}
        />
      </label>

      <div className="wa-transport">
        <div className="wa-transport-buttons">
          <ChromeButton icon="prev" label="Previous draft" onClick={onPrev} />
          <ChromeButton icon="play" label="Play" onClick={engine.play} />
          <ChromeButton icon="pause" label="Pause" onClick={engine.pause} />
          <ChromeButton icon="stop" label="Stop" onClick={engine.stop} />
          <ChromeButton icon="next" label="Next draft" onClick={onNext} />
          <ChromeButton icon="eject" label="Download draft (MP3)" small onClick={onEject} disabled={!draft} />
        </div>
        <div className="wa-transport-extra">
          <button
            type="button"
            className={`wa-oval ${shuffle ? "is-on" : ""}`}
            aria-pressed={shuffle}
            onClick={onShuffle}
            title="Shuffle drafts"
          >
            <svg viewBox="0 0 20 20" aria-hidden="true" className="wa-icon">
              <path d="M3 6h3.5l6 8H16v-2l3 3-3 3v-2h-4.5l-6-8H3zM12.5 6H16V4l3 3-3 3V8h-2.5l-1.6 2.1-1.2-1.6z" />
            </svg>
          </button>
          <i className={`wa-led ${shuffle ? "is-on" : ""}`} aria-hidden="true" />
          <button
            type="button"
            className={`wa-oval ${repeat ? "is-on" : ""}`}
            aria-pressed={repeat}
            onClick={() => {
              engine.setLoop(!repeat);
              setRepeat(!repeat);
            }}
            title="Repeat"
          >
            <svg viewBox="0 0 20 20" aria-hidden="true" className="wa-icon">
              <path d="M10 4a6 6 0 016 6h2l-3 3.5L12 10h2a4 4 0 10-1.2 2.9l1.4 1.4A6 6 0 1110 4z" />
            </svg>
          </button>
          <i className={`wa-led ${repeat ? "is-on" : ""}`} aria-hidden="true" />
          <span className="wa-logo" aria-hidden="true">
            <svg viewBox="0 0 32 32">
              <defs>
                <linearGradient id="logo-g" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0" stopColor="#ffd34a" />
                  <stop offset="1" stopColor="#e2560f" />
                </linearGradient>
              </defs>
              <circle cx="12" cy="16" r="9" fill="url(#logo-g)" stroke="#5a2306" strokeWidth="1.5" />
              <circle cx="20" cy="16" r="9" fill="url(#logo-g)" stroke="#5a2306" strokeWidth="1.5" opacity="0.92" />
              <circle cx="12" cy="16" r="2" fill="#5a2306" />
              <circle cx="20" cy="16" r="2" fill="#5a2306" />
            </svg>
          </span>
          <span className="wa-grip" aria-hidden="true" />
        </div>
      </div>
    </Window>
  );
}
