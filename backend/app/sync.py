"""Rhythmic sync by measurement rather than by BPM numbers.

Tempo estimates are ambiguous (172 vs 86 vs 112 can all "fit" a busy track)
and songs change tempo between sections. Instead of trusting one number, we
compare onset envelopes (where the drum hits are) directly:

1. For each plausible stretch rate (from the tempo levels of both sections),
   time-warp A's envelope and slide it against B's in short windows.
2. Keep the rate where the windows agree on one offset (locked, no drift).
3. Fit the remaining drift and correct the rate, so A's beats stay on B's
   for the whole draft.
"""

from __future__ import annotations

import numpy as np

SR = 22050
HOP = 256
DT = HOP / SR  # seconds per envelope frame (~11.6 ms)
MIN_RATE, MAX_RATE = 0.84, 1.19  # beyond this stretching sounds obviously processed


def onset_env(y: np.ndarray) -> np.ndarray:
    import librosa

    env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
    return env.astype(np.float64)


def tempo_levels(env: np.ndarray, keep: int = 3) -> list[float]:
    """The strongest beat levels (BPM) in a section, strongest first."""
    import librosa

    if env.size < 64 or not np.any(env):
        return []
    tg = librosa.feature.tempogram(onset_envelope=env, sr=SR, hop_length=HOP)
    bpms = librosa.tempo_frequencies(tg.shape[0], sr=SR, hop_length=HOP)
    prof = tg.mean(axis=1)
    ok = np.flatnonzero((bpms >= 55) & (bpms <= 210))
    peaks = [i for i in ok[1:-1] if prof[i] >= prof[i - 1] and prof[i] >= prof[i + 1]]
    if not peaks:
        return []
    peaks.sort(key=lambda i: -prof[i])
    top = prof[peaks[0]]
    return [float(bpms[i]) for i in peaks[:keep] if prof[i] >= 0.35 * top]


def warp(env: np.ndarray, rate: float, n: int) -> np.ndarray:
    """Output frame k reads source frame k * rate (rate > 1 = A sped up)."""
    src = np.arange(n) * rate
    return np.interp(src, np.arange(env.size), env, right=0.0)


def window_lags(a: np.ndarray, b: np.ndarray, max_lag: int, win: int, step: int):
    """Best lag (frames to delay `a`) and its correlation in each window."""
    n = min(a.size, b.size)
    lags, corrs, centers = [], [], []
    for start in range(max_lag, n - win - max_lag, step):
        seg_b = b[start:start + win]
        sb = seg_b.std()
        if sb == 0:
            continue
        zb = (seg_b - seg_b.mean()) / sb
        best_l, best_c = 0, -np.inf
        for lag in range(-max_lag, max_lag + 1):
            seg_a = a[start - lag:start - lag + win]
            sa = seg_a.std()
            if sa == 0:
                continue
            c = float(np.dot((seg_a - seg_a.mean()) / sa, zb) / win)
            if c > best_c:
                best_l, best_c = lag, c
        if best_c > -np.inf:
            lags.append(best_l)
            corrs.append(best_c)
            centers.append(start + win / 2)
    return np.array(lags), np.array(corrs), np.array(centers)


def evaluate(env_a: np.ndarray, env_b: np.ndarray, rate: float, beat_frames: float) -> dict | None:
    """How well A (warped by `rate`) locks onto B. Score rewards strong
    per-window correlation and windows agreeing on one offset."""
    n = env_b.size
    wa = warp(env_a, rate, n)
    max_lag = max(2, int(beat_frames * 0.5))
    win = int(8.0 / DT)
    lags, corrs, centers = window_lags(wa, env_b, max_lag, win, step=int(4.0 / DT))
    if lags.size < 3:
        return None
    # Lags wrap at the beat period: treat them on a circle so a lock right at
    # +/- half a beat isn't mistaken for drift.
    phase = 2 * np.pi * lags / beat_frames
    mean_phase = np.angle(np.mean(np.exp(1j * phase)))
    unwrapped = lags - np.round((lags - mean_phase * beat_frames / (2 * np.pi)) / beat_frames) * beat_frames
    slope = np.polyfit(centers, unwrapped, 1)[0] if lags.size >= 3 else 0.0
    spread = float(np.std(unwrapped - np.polyval(np.polyfit(centers, unwrapped, 1), centers)))
    consistency = float(abs(np.mean(np.exp(1j * phase))))  # 1 = every window agrees
    score = float(np.median(corrs)) * consistency
    return {
        "rate": rate, "score": score, "lag": float(np.median(unwrapped)),
        "slope": float(slope), "spread_frames": spread, "consistency": consistency,
        "corr": float(np.median(corrs)),
    }


def candidate_rates(levels_a: list[float], levels_b: list[float]) -> list[float]:
    rates = {1.0}
    for pa in levels_a:
        for pb in levels_b:
            for ratio in (1.0, 2.0, 0.5):
                r = pb / (pa * ratio)
                if MIN_RATE <= r <= MAX_RATE:
                    rates.add(round(r, 5))
    return sorted(rates)


def best_rate(env_a: np.ndarray, env_b: np.ndarray, levels_a: list[float],
              levels_b: list[float], bpm_b_hint: float) -> dict | None:
    """Try every plausible rate, keep the best lock, then remove residual drift."""
    beat_frames = 60.0 / (levels_b[0] if levels_b else bpm_b_hint) / DT
    best = None
    for r in candidate_rates(levels_a, levels_b):
        ev = evaluate(env_a, env_b, r, beat_frames)
        if ev and (best is None or ev["score"] > best["score"]):
            best = ev
    if best is None:
        return None
    # Drift correction: a lag that grows by `slope` frames per output frame
    # means A runs fast by that fraction.
    for _ in range(3):
        if abs(best["slope"]) < 1e-5:
            break
        r = best["rate"] * (1.0 - best["slope"])
        if not MIN_RATE <= r <= MAX_RATE:
            break
        ev = evaluate(env_a, env_b, r, beat_frames)
        if ev is None or ev["score"] < best["score"] * 0.98:
            break
        best = ev
    best["beat_frames"] = beat_frames
    return best


def drift_report(a: np.ndarray, b: np.ndarray, bpm_b: float) -> dict | None:
    """Independent check on rendered audio (mono, SR): per-window offset of
    A's hits against B's, in ms. `wander_ms` is how far they move apart over
    the draft; under ~20 ms is tight, over ~60 ms you'll hear flamming."""
    ea, eb = onset_env(a), onset_env(b)
    beat_frames = 60.0 / bpm_b / DT
    lags, corrs, centers = window_lags(ea, eb, max(2, int(beat_frames * 0.5)),
                                       int(6.0 / DT), int(3.0 / DT))
    if lags.size < 3:
        return None
    phase = 2 * np.pi * lags / beat_frames
    mean_phase = np.angle(np.mean(np.exp(1j * phase)))
    unwrapped = lags - np.round((lags - mean_phase * beat_frames / (2 * np.pi)) / beat_frames) * beat_frames
    ms = unwrapped * DT * 1000
    return {
        "offset_ms": round(float(np.median(ms)), 1),
        "wander_ms": round(float(np.percentile(ms, 90) - np.percentile(ms, 10)), 1),
        "lock": round(float(abs(np.mean(np.exp(1j * phase)))), 3),
        "corr": round(float(np.median(corrs)), 3),
    }
