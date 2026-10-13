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

from . import config, stems, structure, sync
from .analysis import estimate_key
from .camelot import key_name, min_shift_to_compatible, score_keys
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


# --- section analysis ---------------------------------------------------------
# Songs change tempo between sections (and beat trackers lock onto 5-BPM
# steps), so the render measures tempo and key in the exact sections it uses.

TEMPO_SR = 22050
MIN_KEY_CONFIDENCE = 0.05
LOOSE_MS = 40.0  # beat wander above which "auto" quantize kicks in


def to_mono22(y: np.ndarray, sr: int) -> np.ndarray:
    import librosa

    return librosa.resample(y.mean(0), orig_sr=sr, target_sr=TEMPO_SR)


def section_tempo(y: np.ndarray, target_bpm: float | None = None) -> dict | None:
    """Precise tempo of one section (mono, TEMPO_SR).

    The tempogram lists the plausible beat levels (e.g. 172 vs 86 BPM); when
    `target_bpm` is given we prefer the level that lines up with it, the way a
    DJ hears a track "in" the other one's tempo. Beats are then tracked at that
    level and a line is fitted through them, which is far finer than the
    tracker's own estimate. `steadiness` is the beat jitter as a fraction of a
    beat: ~0.02 for a steady section, large when the tempo changes inside it.
    """
    import librosa

    oenv = librosa.onset.onset_strength(y=y, sr=TEMPO_SR)
    if oenv.size < 100 or not np.any(oenv):
        return None
    tg = librosa.feature.tempogram(onset_envelope=oenv, sr=TEMPO_SR)
    bpms = librosa.tempo_frequencies(tg.shape[0], sr=TEMPO_SR)
    prof = tg.mean(axis=1)
    ok = np.flatnonzero((bpms >= 60) & (bpms <= 200))
    peaks = [i for i in ok[1:-1] if prof[i] >= prof[i - 1] and prof[i] >= prof[i + 1]]
    if not peaks:
        return None
    strongest = max(prof[i] for i in peaks)
    strong = [i for i in peaks if prof[i] >= 0.5 * strongest]

    def preference(i: int) -> float:
        weight = prof[i] / strongest
        if target_bpm:
            diff = min(abs(bpms[i] * r - target_bpm) / target_bpm for r in (1.0, 2.0, 0.5))
            weight *= max(0.05, 1.0 - diff / 0.12)
        return weight

    level = float(bpms[max(strong, key=preference)])
    _, beats = librosa.beat.beat_track(
        onset_envelope=oenv, sr=TEMPO_SR, start_bpm=level, tightness=300, units="time"
    )
    if len(beats) < 8:
        return None
    idx = np.arange(len(beats))
    period, first = np.polyfit(idx, beats, 1)
    resid = beats - (period * idx + first)
    return {
        "bpm": float(60.0 / period),
        "beats": np.asarray(beats, dtype=float),
        "steadiness": float(np.std(resid) / period),
    }


def section_key(y: np.ndarray) -> tuple[int, str, float] | None:
    """Key of one section from the harmonic part of an instrumental."""
    import librosa

    harmonic = librosa.effects.harmonic(y)
    chroma = librosa.feature.chroma_cqt(y=harmonic, sr=TEMPO_SR)
    if not np.any(chroma):
        return None
    k = estimate_key(chroma.mean(axis=1))
    return k.pitch_class, k.mode, k.confidence


def _candidates(beats: np.ndarray, energy: np.ndarray, dt: float, length: float,
                total: float, margin: float, keep: int,
                min_gap: float = 12.0) -> list[tuple[float, float]]:
    """Bar-aligned start times whose next `length` seconds fit, best energy
    first, at least `min_gap` apart so the candidates cover different parts
    of the song instead of neighbouring bars of one loud passage."""
    starts = beats[::BEATS_PER_BAR]
    starts = starts[(starts >= margin) & (starts + length <= total - margin)]
    if starts.size == 0:
        starts = np.array([max(0.0, min(margin, total - length))])
    csum = np.concatenate([[0.0], np.cumsum(energy)])
    n = max(1, int(length / dt))
    scored = []
    for t in starts:
        i = int(t / dt)
        j = min(i + n, len(energy))
        scored.append((float((csum[j] - csum[i]) / max(j - i, 1)), float(t)))
    scored.sort(reverse=True)
    picked: list[tuple[float, float]] = []
    for e, t in scored:
        if all(abs(t - p) >= min_gap for p, _ in picked):
            picked.append((t, e))
            if len(picked) == keep:
                break
    return picked


def choose_section(
    tempo_src: np.ndarray, energy_src: np.ndarray, beats: np.ndarray, length: float,
    target_bpm: float | None, margin: float = 0.0, keep: int = 12,
) -> dict:
    """Pick the bar-aligned section with the best mix of energy (vocals for A,
    instrumental for B), steady tempo, and tempo closeness to `target_bpm`."""
    total = len(tempo_src) / TEMPO_SR
    energy, dt = rms_curve(energy_src, TEMPO_SR)
    cands = _candidates(beats, energy, dt, length, total, margin, keep)
    top_energy = max(e for _, e in cands) or 1.0
    best = None
    for start, e in cands:
        a, b = int(start * TEMPO_SR), int(min(total, start + length) * TEMPO_SR)
        t = section_tempo(tempo_src[a:b], target_bpm)
        if t is None:
            continue
        steady = max(0.0, 1.0 - t["steadiness"] / 0.12)
        closeness = score_tempos(t["bpm"], target_bpm).score if target_bpm else 1.0
        score = (e / top_energy) * (0.3 + 0.7 * steady) * (0.2 + 0.8 * closeness)
        if best is None or score > best["score"]:
            best = {"start": start, "score": score, **t}
    if best is None:  # no measurable beat anywhere: fall back to the loudest part
        start = cands[0][0]
        best = {"start": start, "score": 0.0, "bpm": None, "beats": np.array([0.0]),
                "steadiness": 1.0}
    # Anchor on the section's own first tracked beat (precise phase).
    best["anchor"] = best["start"] + float(best["beats"][0])
    return best


def choose_phrase_window(struct: dict, duration: float, total: float) -> dict | None:
    """A run of whole 8-bar phrases for B: as close to `duration` as phrases
    allow, preferring windows with a drop (or a build into one) and avoiding
    intros/outros."""
    phrases, plen = struct.get("phrases") or [], struct.get("phrase_seconds")
    if not phrases or not plen:
        return None
    n = max(1, min(len(phrases), int(round(duration / plen))))
    top = max(p["energy"] for p in phrases) or 1.0
    best = None
    for i in range(len(phrases) - n + 1):
        win = phrases[i:i + n]
        if win[-1]["end"] > total:
            continue
        labels = [p["label"] for p in win]
        score = float(np.mean([p["energy"] for p in win])) / top
        if "drop" in labels:
            score *= 1.25
        if labels[0] in ("build", "drop"):
            score *= 1.1
        if labels[0] == "intro" or "outro" in labels:
            score *= 0.6
        if best is None or score > best["score"]:
            best = {"score": score, "start": win[0]["start"], "labels": labels,
                    "duration": win[-1]["end"] - win[0]["start"], "phrases": n}
    return best


def phrase_candidates(struct: dict, src_len: float, total: float, margin: float,
                      keep: int = 10) -> list[tuple[float, float]]:
    """Phrase starts in A ranked by how much singing follows them."""
    phrases = struct.get("phrases") or []
    out = []
    for i, p in enumerate(phrases):
        if p["start"] < margin or p["start"] + src_len > total - margin:
            continue
        covered = [q for q in phrases[i:] if q["start"] < p["start"] + src_len]
        out.append((p["start"], float(np.mean([q.get("vocal", 0.0) for q in covered]))))
    out.sort(key=lambda x: -x[1])
    return out[:keep]


def labels_between(struct: dict, start: float, end: float) -> list[str]:
    return [p["label"] for p in struct.get("phrases") or []
            if p["end"] > start + 0.5 and p["start"] < end - 0.5]


# --- main entry --------------------------------------------------------------

def render_draft(
    track_a: dict, track_b: dict, *, duration: float = 60.0,
    semitones: int | None = None, vocal_gain_db: float = 0.0,
    quantize: str = "auto", progress: ProgressFn | None = None,
) -> dict:
    """Vocals of `track_a` over the instrumental of `track_b`.

    `quantize`: "auto" warps A beat-by-beat onto B's grid when a constant
    stretch leaves the beats loose; "on" always does; "off" never does."""
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

    # 2. tempo/key analysis works on mono 22 kHz copies (fast, plenty for beats)
    report(0.82, "Measuring tempo in each section")
    total_a, total_b = inst_a.shape[1] / sr, inst_b.shape[1] / sr
    duration = min(duration, total_b)
    inst_a22, vox_a22, inst_b22 = to_mono22(inst_a, sr), to_mono22(vox_a, sr), to_mono22(inst_b, sr)
    report(0.83, "Finding downbeats and 8-bar phrases")
    # Decide each song's real beat level once (not a 2/3 misread) and use it
    # for the phrase grid, the lock and quantizing alike.
    level_b = sync.main_level(inst_b22) or track_b["bpm"]
    level_a = sync.main_level(inst_a22) or track_a["bpm"]
    struct_b = structure.analyze(inst_b22, level_b)
    struct_a = structure.analyze(inst_a22, level_a, extra_energy=vox_a22)
    beats_a = struct_a["beats"] if struct_a["beats"].size else beat_times(inst_a22, TEMPO_SR, track_a["bpm"])
    beats_b = struct_b["beats"] if struct_b["beats"].size else beat_times(inst_b22, TEMPO_SR, track_b["bpm"])

    # B is the reference: whole phrases, ideally with a build/drop, starting on
    # a phrase boundary. Falls back to the steadiest loud section.
    win_b = choose_phrase_window(struct_b, duration, total_b)
    if win_b:
        start_b, duration = win_b["start"], min(win_b["duration"], total_b - win_b["start"])
    else:
        start_b = choose_section(inst_b22, inst_b22, beats_b, duration, None, keep=8)["anchor"]
    b0f = int(start_b * TEMPO_SR)
    env_b = sync.onset_env(inst_b22[b0f:b0f + int(duration * TEMPO_SR)])
    levels_b = sync.tempo_levels(sync.full_band_env(inst_b22[b0f:b0f + int(duration * TEMPO_SR)]))
    # B's beat in this window: its main level, refined by the section's own peaks.
    bpm_b = min(levels_b, key=lambda l: abs(l / level_b - 1)) if levels_b else level_b
    if abs(bpm_b / level_b - 1) > 0.04:
        bpm_b = struct_b["bpm"] or level_b

    # A: try its vocal-heavy sections and keep the one whose drums actually
    # lock onto B's once stretched (measured, not guessed from BPM numbers).
    report(0.84, "Locking A's rhythm onto B")
    pad_s = 2.0  # extra source audio so alignment shifts never run dry
    src_len = duration * sync.MAX_RATE + 1.0
    # Candidates start on A's phrase boundaries (so A's phrase lines up with
    # B's); without a phrase grid, fall back to bar-aligned loud spots.
    cands = phrase_candidates(struct_a, min(src_len, total_a - 2 * pad_s), total_a, pad_s)
    if not cands:
        energy_a, edt = rms_curve(vox_a22, TEMPO_SR)
        cands = _candidates(beats_a, energy_a, edt, min(src_len, total_a - 2 * pad_s),
                            total_a, pad_s, keep=10)
    top_e = max((e for _, e in cands), default=0.0) or 1.0
    lock = None
    for start, e in cands:
        a0f = int(start * TEMPO_SR)
        env_a = sync.onset_env(inst_a22[a0f:a0f + int(src_len * TEMPO_SR)])
        levels_a = sync.tempo_levels(sync.full_band_env(inst_a22[a0f:a0f + int(src_len * TEMPO_SR)]))
        res = sync.best_rate(env_a, env_b, levels_a, levels_b, bpm_b)
        if res is None:
            continue
        res["start"], res["energy"] = start, e
        # Lock matters most, then a small stretch (big ones sound processed),
        # then vocal energy.
        closeness = max(0.2, 1.0 - abs(res["rate"] - 1.0) / 0.2)
        res["pick"] = res["score"] * closeness * (e / top_e) ** 0.5
        if lock is None or res["pick"] > lock["pick"]:
            lock = res
    if lock is None:  # nothing measurable: fall back to the BPM numbers
        tempo = score_tempos(track_a["bpm"], track_b["bpm"])
        lock = {"rate": tempo.stretch, "lag": 0.0, "start": cands[0][0], "consistency": 0.0}
    rate = lock["rate"]  # >1 speeds A up
    start_a = lock["start"]
    bpm_a = bpm_b / rate  # A's own tempo at the beat level that matches B
    ratio = min((1.0, 2.0, 0.5), key=lambda r: abs(bpm_a / (track_a["bpm"] * r) - 1))
    duration = min(duration, (total_a - start_a - pad_s) / rate)

    # Key from the same sections; only trust it when the detector is sure.
    report(0.85, "Measuring key in each section")
    key_source = "manual"
    if semitones is None:
        src_len = int(duration * rate * TEMPO_SR)
        a0k = int(start_a * TEMPO_SR)
        b0k = int(start_b * TEMPO_SR)
        ka = section_key(inst_a22[a0k:a0k + src_len])
        kb = section_key(inst_b22[b0k:b0k + int(duration * TEMPO_SR)])
        if ka and kb and min(ka[2], kb[2]) >= MIN_KEY_CONFIDENCE:
            semitones = min_shift_to_compatible(ka[0], ka[1], kb[0], kb[1]) or 0
            key_source = f"sections: {key_name(ka[0], ka[1])} → {key_name(kb[0], kb[1])}"
        elif min(track_a.get("key_confidence") or 0, track_b.get("key_confidence") or 0) >= MIN_KEY_CONFIDENCE:
            semitones = score_keys(track_a["key_pc"], track_a["mode"],
                                   track_b["key_pc"], track_b["mode"]).shift
            key_source = "whole songs"
        else:
            semitones = 0
            key_source = "uncertain, left unshifted"
    semitones = int(max(-MAX_PITCH_SHIFT, min(MAX_PITCH_SHIFT, semitones)))

    # 3. cut and stretch the chosen part of A
    report(0.87, f"Time-stretching A ×{rate:.4f}"
                 + (f", pitch {semitones:+d} st" if semitones else ""))
    src_len_a = duration * rate
    a0 = int(max(0.0, start_a - pad_s) * sr)
    a1 = int(min(total_a, start_a + src_len_a + pad_s) * sr)
    lead_in = (start_a - a0 / sr) / rate  # seconds of pre-roll, post-stretch
    vox = stretch_and_shift(vox_a[:, a0:a1], sr, rate, semitones)
    inst_a_st = stretch_and_shift(inst_a[:, a0:a1].mean(0, keepdims=True), sr, rate, 0)

    n_out = int(duration * sr)
    b0 = int(start_b * sr)
    bed = inst_b[:, b0:b0 + n_out]
    if bed.shape[1] < n_out:
        bed = place(bed, 0, n_out)

    # 4. place A where the lock search found B's beats, then polish on the
    # real stretched audio (Rubber Band's output can shift by a few ms).
    report(0.92, "Aligning beats")
    coarse = -int(lead_in * sr) + int(lock["lag"] * sync.DT * sr)
    moving = place(inst_a_st, coarse, n_out)[0]
    fine = onset_lag(bed.mean(0), moving, sr, 0.04)
    offset = coarse + int(fine * sr)
    vox = place(vox, offset, n_out)

    # 5. independent check on the rendered audio: do A's hits stay on B's?
    bed22 = to_mono22(bed, sr)
    check = sync.drift_report(to_mono22(place(inst_a_st, offset, n_out), sr), bed22, bpm_b)

    # 6. quantize: if one constant stretch leaves A's beats loose (live drums,
    # tempo drift, swing), warp A beat-by-beat onto B's actual beats instead.
    quantized, check_constant = False, None
    loose = check is None or check["wander_ms"] > LOOSE_MS
    if quantize == "on" or (quantize == "auto" and loose):
        if config.has_rubberband():
            report(0.93, "Quantizing A onto B's beat grid")
            seg_start = a0 / sr
            dst_beats = structure.track_beats(bed22, bpm_b)

            def attempt(src_beats: np.ndarray, rate_q: float, end: int):
                """Warp A onto B along matched beats; returns (check, tmap, offset, end)."""
                src_beats = src_beats[src_beats >= start_a - 0.05]
                if not (src_beats.size and dst_beats.size):
                    return None
                pre_out = (src_beats[0] - seg_start) / rate_q
                tmap = beat_map(src_beats, dst_beats, seg_start, pre_out, end - a0, sr, expected=rate_q)
                if not tmap:
                    return None
                q_off = int((dst_beats[0] - pre_out) * sr)
                inst_q = place(quantize_stretch(inst_a[:, a0:end].mean(0, keepdims=True), sr, tmap, 0), q_off, n_out)
                chk = sync.drift_report(to_mono22(inst_q, sr), bed22, bpm_b)
                return (chk, tmap, q_off, end) if chk else None

            # Follow A's real beats. Free tracking keeps its drift but may count
            # a different beat level; the level-guided pass forces the lock's
            # level (low tightness, so beats can still drift). If A's own count
            # disagrees with the lock, also try quantizing at A's counted level:
            # a bigger stretch, but every beat lands on B's grid. Keep the best.
            seg22 = inst_a22[int(seg_start * TEMPO_SR):int(a1 / sr * TEMPO_SR)]
            results = []
            free = seg_start + structure.track_beats(seg22, bpm_a)
            for src in (free, seg_start + structure.track_beats(seg22, bpm_a, fixed=True, tightness=100)):
                results.append(attempt(src, rate, a1))
            if free.size > 8 and dst_beats.size > 8:
                own_rate = float(np.median(np.diff(free)) / np.median(np.diff(dst_beats)))
                if abs(own_rate / rate - 1) > 0.06 and sync.MIN_RATE <= own_rate <= sync.MAX_RATE:
                    end = int(min(total_a, start_a + duration * own_rate + pad_s) * sr)
                    seg_long = inst_a22[int(seg_start * TEMPO_SR):int(end / sr * TEMPO_SR)]
                    own = seg_start + structure.track_beats(seg_long, 60 / np.median(np.diff(free)))
                    results.append(attempt(own, own_rate, end))
            results = [r for r in results if r]
            best_q = min(results, key=lambda r: r[0]["wander_ms"]) if results else None
            if best_q:
                check_q, tmap, q_off, end = best_q
                better = check is None or check_q["wander_ms"] < check["wander_ms"]
                if quantize == "on" or better:
                    vox = place(quantize_stretch(vox_a[:, a0:end], sr, tmap, semitones), q_off, n_out)
                    quantized, check_constant, check = True, check, check_q

    # 7. mix
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
        "tempo_ratio": ratio,
        "semitones": semitones,
        "start_a": round(start_a, 2),
        "start_b": round(start_b, 2),
        "align_fine_ms": round(fine * 1000, 1),
        "bpm_a_section": round(bpm_a, 2),
        "bpm_b_section": round(bpm_b, 2),
        "lock": round(lock.get("consistency", 0.0), 3),
        "sync": check,  # {offset_ms, wander_ms, lock, corr} on the final audio
        "quantized": quantized,
        "sync_unquantized": check_constant,
        "phrases": win_b["phrases"] if win_b else None,
        "b_sections": labels_between(struct_b, start_b, start_b + duration),
        "a_sections": labels_between(struct_a, start_a, start_a + duration * rate),
        "key_source": key_source,
        "engine": "rubberband" if config.has_rubberband() else "librosa",
        "wav": wav_path.name,
        "mp3": mp3_path.name if mp3_path else None,
    }
