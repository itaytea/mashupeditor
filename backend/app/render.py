"""Render a rough mashup draft: vocals of A over the instrumental of B.

Steps:
1. Separate both songs (cached) into vocals / instrumental.
2. Pick a bar-aligned window in B with the most instrumental energy and a
   bar-aligned window in A with the most vocal energy.
3. Time-stretch (and if needed pitch-shift) A's vocals to B's tempo and key.
4. Fine-align by cross-correlating onset envelopes of A's (stretched)
   instrumental against B's, so the beat grids line up.
5. Balance levels, fade, export WAV + MP3.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Callable

import numpy as np

from . import config, stems
from .camelot import score_keys
from .tempo import score_tempos

log = logging.getLogger(__name__)

ProgressFn = Callable[[float, str], None]

BEATS_PER_BAR = 4
ALIGN_SEARCH_BEATS = 0.5  # fine alignment searches +/- half a beat
MAX_PITCH_SHIFT = 6


# --- DSP helpers (arrays are shaped (channels, samples)) ---------------------

def load_stereo(path: Path, sr: int) -> np.ndarray:
    import librosa

    y, _ = librosa.load(path, sr=sr, mono=False)
    if y.ndim == 1:
        y = np.stack([y, y])
    return y[:2].astype(np.float32)


def stretch_and_shift(y: np.ndarray, sr: int, rate: float, semitones: float) -> np.ndarray:
    """Change tempo by `rate` (>1 = faster) and pitch by `semitones`.

    Uses Rubber Band (via pyrubberband) when its CLI is installed, otherwise
    falls back to librosa's phase vocoder, which is noticeably rougher.
    """
    if abs(rate - 1.0) < 1e-4 and semitones == 0:
        return y
    if config.has_rubberband():
        import pyrubberband as pyrb

        rbargs = {"--formant": ""} if semitones else {}
        if semitones:
            rbargs["--pitch"] = semitones
        out = pyrb.time_stretch(y.T, sr, rate, rbargs=rbargs)
        return np.asarray(out, dtype=np.float32).T

    import librosa

    out = librosa.effects.time_stretch(y, rate=rate) if abs(rate - 1) > 1e-4 else y
    if semitones:
        out = librosa.effects.pitch_shift(out, sr=sr, n_steps=semitones)
    return out.astype(np.float32)


def beat_times(y_mono: np.ndarray, sr: int, bpm_hint: float | None) -> np.ndarray:
    import librosa

    kwargs = {"start_bpm": bpm_hint} if bpm_hint else {}
    _, beats = librosa.beat.beat_track(y=y_mono, sr=sr, units="time", **kwargs)
    return np.asarray(beats, dtype=float)


def rms_curve(y_mono: np.ndarray, sr: int, hop: int = 1024) -> tuple[np.ndarray, float]:
    import librosa

    rms = librosa.feature.rms(y=y_mono, frame_length=2048, hop_length=hop)[0]
    return rms, hop / sr


def best_window_start(
    beats: np.ndarray, energy: np.ndarray, frame_dt: float, window_s: float,
    total_s: float, margin_s: float = 0.0,
) -> float:
    """Bar-aligned beat whose following `window_s` seconds have the highest
    mean energy. Falls back to 0 when there are no usable beats."""
    candidates = beats[::BEATS_PER_BAR]
    candidates = candidates[(candidates >= margin_s) & (candidates + window_s <= total_s)]
    if candidates.size == 0:
        candidates = beats[(beats >= margin_s) & (beats + window_s <= total_s)]
    if candidates.size == 0:
        return float(max(0.0, min(margin_s, total_s - window_s)))

    csum = np.concatenate([[0.0], np.cumsum(energy)])
    n = int(window_s / frame_dt)
    best_t, best_e = float(candidates[0]), -1.0
    for t in candidates:
        i = int(t / frame_dt)
        j = min(i + n, len(energy))
        e = (csum[j] - csum[i]) / max(j - i, 1)
        if e > best_e:
            best_t, best_e = float(t), e
    return best_t


def onset_lag(ref: np.ndarray, moving: np.ndarray, sr: int, max_lag_s: float) -> float:
    """Seconds to delay `moving` so its onsets line up with `ref`."""
    import librosa

    hop = 256
    a = librosa.onset.onset_strength(y=ref, sr=sr, hop_length=hop)
    b = librosa.onset.onset_strength(y=moving, sr=sr, hop_length=hop)
    n = min(len(a), len(b))
    a, b = a[:n] - a[:n].mean(), b[:n] - b[:n].mean()
    max_lag = int(max_lag_s * sr / hop)
    best_lag, best_c = 0, -np.inf
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            c = float(np.dot(a[lag:], b[: n - lag]))
        else:
            c = float(np.dot(a[: n + lag], b[-lag:]))
        if c > best_c:
            best_lag, best_c = lag, c
    return best_lag * hop / sr


def place(y: np.ndarray, offset: int, length: int) -> np.ndarray:
    """Shift `y` right by `offset` samples (left if negative), crop/pad to length."""
    out = np.zeros((y.shape[0], length), dtype=np.float32)
    src_start = max(0, -offset)
    dst_start = max(0, offset)
    n = min(y.shape[1] - src_start, length - dst_start)
    if n > 0:
        out[:, dst_start:dst_start + n] = y[:, src_start:src_start + n]
    return out


def active_rms(y: np.ndarray) -> float:
    """RMS over the louder half of 50ms frames, so silence between vocal
    phrases doesn't drag the level down."""
    mono = y.mean(0)
    frame = 2205
    n = len(mono) // frame
    if n == 0:
        return float(np.sqrt(np.mean(mono ** 2)) + 1e-9)
    frames = np.sqrt((mono[: n * frame].reshape(n, frame) ** 2).mean(1))
    loud = np.sort(frames)[n // 2:]
    return float(loud.mean() + 1e-9)


def fade(y: np.ndarray, sr: int, fade_in: float, fade_out: float) -> np.ndarray:
    y = y.copy()
    fi, fo = int(fade_in * sr), int(fade_out * sr)
    if fi:
        y[:, :fi] *= np.linspace(0, 1, fi, dtype=np.float32)
    if fo:
        y[:, -fo:] *= np.linspace(1, 0, fo, dtype=np.float32)
    return y


# --- main entry --------------------------------------------------------------

def render_draft(
    track_a: dict, track_b: dict, *, duration: float = 60.0,
    semitones: int | None = None, vocal_gain_db: float = 0.0,
    progress: ProgressFn | None = None,
) -> dict:
    """Vocals of `track_a` over the instrumental of `track_b`."""
    import soundfile as sf

    report = progress or (lambda f, m: None)

    # 1. stems (the bulk of the time on first run; cached afterwards)
    stems_a = stems.separate(track_a, lambda f, m: report(0.45 * f, f"A: {m}"))
    stems_b = stems.separate(track_b, lambda f, m: report(0.45 + 0.35 * f, f"B: {m}"))

    report(0.80, "Loading stems")
    sr = 44100
    vox_a = load_stereo(stems_a["vocals"], sr)
    inst_a = load_stereo(stems_a["instrumental"], sr)
    inst_b = load_stereo(stems_b["instrumental"], sr)

    # 2. tempo / key plan
    tempo = score_tempos(track_a["bpm"], track_b["bpm"])
    rate = tempo.stretch  # >1 speeds A up
    if semitones is None:
        semitones = score_keys(
            track_a["key_pc"], track_a["mode"], track_b["key_pc"], track_b["mode"]
        ).shift
    semitones = int(max(-MAX_PITCH_SHIFT, min(MAX_PITCH_SHIFT, semitones)))

    # 3. choose windows
    report(0.82, "Finding beats")
    total_b = inst_b.shape[1] / sr
    total_a = inst_a.shape[1] / sr
    duration = min(duration, total_b, total_a / rate)
    beats_b = beat_times(inst_b.mean(0), sr, track_b["bpm"])
    beats_a = beat_times(inst_a.mean(0), sr, track_a["bpm"])

    e_b, dt = rms_curve(inst_b.mean(0), sr)
    start_b = best_window_start(beats_b, e_b, dt, duration, total_b)

    pad_s = 2.0  # extra source audio so alignment shifts never run dry
    src_len_a = duration * rate
    e_a, dt = rms_curve(vox_a.mean(0), sr)
    start_a = best_window_start(beats_a, e_a, dt, src_len_a, total_a, margin_s=pad_s)

    # 4. stretch / shift the chosen part of A
    report(0.86, f"Time-stretching A ×{rate:.3f}"
                 + (f", pitch {semitones:+d} st" if semitones else ""))
    a0 = int(max(0.0, start_a - pad_s) * sr)
    a1 = int(min(total_a, start_a + src_len_a + pad_s) * sr)
    lead_in = (start_a - a0 / sr) / rate  # seconds before A's chosen beat, post-stretch
    vox = stretch_and_shift(vox_a[:, a0:a1], sr, rate, semitones)
    inst_a_st = stretch_and_shift(inst_a[:, a0:a1].mean(0, keepdims=True), sr, rate, 0)

    n_out = int(duration * sr)
    b0 = int(start_b * sr)
    bed = inst_b[:, b0:b0 + n_out]
    if bed.shape[1] < n_out:
        bed = place(bed, 0, n_out)

    # 5. beat-align: coarse from the beat grids, then refine by onset xcorr
    report(0.92, "Aligning beats")
    coarse = -int(lead_in * sr)
    beat_s = 60.0 / track_b["bpm"]
    ref = bed.mean(0)
    moving = place(inst_a_st, coarse, n_out)[0]
    fine = onset_lag(ref, moving, sr, ALIGN_SEARCH_BEATS * beat_s)
    offset = coarse + int(fine * sr)
    vox = place(vox, offset, n_out)

    # 6. mix
    report(0.95, "Mixing")
    gain = active_rms(bed) / active_rms(vox) * 10 ** (vocal_gain_db / 20)
    gain = float(min(gain, 8.0))  # don't blow up near-silent vocal stems
    mix = bed * 0.85 + vox * gain * 0.85
    mix = fade(mix, sr, 0.3, 2.0)
    peak = float(np.abs(mix).max())
    if peak > 0.89:
        mix *= 0.89 / peak  # about -1 dBFS

    draft_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    out_dir = config.DRAFTS_DIR / draft_id
    out_dir.mkdir(parents=True, exist_ok=True)
    wav_path = out_dir / "draft.wav"
    sf.write(wav_path, mix.T, sr, subtype="PCM_16")

    mp3_path = None
    if config.has_ffmpeg():
        report(0.98, "Encoding MP3")
        mp3_path = out_dir / "draft.mp3"
        subprocess.run(
            [shutil.which("ffmpeg"), "-y", "-loglevel", "error", "-i", str(wav_path),
             "-codec:a", "libmp3lame", "-b:a", "192k", str(mp3_path)],
            check=True,
        )

    return {
        "draft_id": draft_id,
        "a": track_a["id"],
        "b": track_b["id"],
        "title": f"{track_a['title']} (vocals) × {track_b['title']} (instrumental)",
        "duration": round(n_out / sr, 2),
        "stretch": round(rate, 4),
        "tempo_ratio": tempo.ratio,
        "semitones": semitones,
        "start_a": round(start_a, 2),
        "start_b": round(start_b, 2),
        "align_fine_ms": round(fine * 1000, 1),
        "engine": "rubberband" if config.has_rubberband() else "librosa",
        "wav": wav_path.name,
        "mp3": mp3_path.name if mp3_path else None,
    }
