"""Pure DSP helpers for the draft renderer (arrays are shaped (channels, samples))."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import config

TEMPO_SR = 22050  # sample rate of the mono copies used for tempo/key/beat analysis


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


def beat_map(src_beats: np.ndarray, dst_beats: np.ndarray, src_start: float,
             pre_out: float, n_src: int, sr: int,
             expected: float | None = None) -> list[tuple[int, int]] | None:
    """Rubber Band time map sending source beat k to destination beat k.

    `src_beats` are absolute times in the source song, `src_start` is where the
    cut source segment begins; `dst_beats` are relative to the output start
    plus `pre_out` seconds of pre-roll. Returns None if the beats disagree too
    much to warp safely (a tracking glitch would smear the audio).
    """
    k = min(src_beats.size, dst_beats.size)
    if k < 8:
        return None
    src = (src_beats[:k] - src_start) * sr
    dst = (pre_out + dst_beats[:k] - dst_beats[0]) * sr
    local = np.diff(dst) / np.diff(src)  # per-beat stretch
    if np.any(local <= 0) or np.max(local) / np.min(local) > 1.35:
        return None
    # Both beat lists must be counted at the same level: if they run at
    # different speeds, beat k of A isn't beat k of B and the warp would smear.
    if expected and abs(float(np.median(local)) * expected - 1.0) > 0.06:
        return None
    rate_end = float(np.median(local))
    pairs = [(0, 0)]
    for a, b in zip(src, dst):
        a, b = int(round(a)), int(round(b))
        if pairs[-1][0] < a < n_src and b > pairs[-1][1]:
            pairs.append((a, b))
    tail = n_src - pairs[-1][0]
    if tail > 0:
        pairs.append((n_src, pairs[-1][1] + int(round(tail * rate_end))))
    return pairs


def quantize_stretch(y: np.ndarray, sr: int, time_map: list[tuple[int, int]],
                     semitones: float) -> np.ndarray:
    """Warp `y` (channels, samples) along `time_map` so every beat lands on
    the grid, optionally pitch-shifting in the same pass."""
    import pyrubberband as pyrb

    rbargs = {"--formant": "", "--pitch": semitones} if semitones else {}
    out = pyrb.timemap_stretch(y.T, sr, time_map, rbargs=rbargs)
    return np.asarray(out, dtype=np.float32).reshape(-1, y.shape[0]).T


def beat_times(y_mono: np.ndarray, sr: int, bpm_hint: float | None) -> np.ndarray:
    import librosa

    kwargs = {"start_bpm": bpm_hint} if bpm_hint else {}
    _, beats = librosa.beat.beat_track(y=y_mono, sr=sr, units="time", **kwargs)
    return np.asarray(beats, dtype=float)


def rms_curve(y_mono: np.ndarray, sr: int, hop: int = 1024) -> tuple[np.ndarray, float]:
    import librosa

    rms = librosa.feature.rms(y=y_mono, frame_length=2048, hop_length=hop)[0]
    return rms, hop / sr


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


def to_mono22(y: np.ndarray, sr: int) -> np.ndarray:
    import librosa

    return librosa.resample(y.mean(0), orig_sr=sr, target_sr=TEMPO_SR)
