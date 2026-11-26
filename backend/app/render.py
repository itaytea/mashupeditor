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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from . import config, stems, structure, sync
from .analysis import estimate_key
from .camelot import key_name, min_shift_to_compatible, score_keys
from .dsp import (  # noqa: F401  (re-exported: tests and callers use render.X)
    TEMPO_SR,
    active_rms,
    beat_map,
    beat_times,
    fade,
    load_stereo,
    onset_lag,
    place,
    quantize_stretch,
    rms_curve,
    stretch_and_shift,
    to_mono22,
)
from .tempo import score_tempos

log = logging.getLogger(__name__)

MP3_ENCODE_TIMEOUT_S = 120

ProgressFn = Callable[[float, str], None]

BEATS_PER_BAR = 4
ALIGN_SEARCH_BEATS = 0.5  # fine alignment searches +/- half a beat
MAX_PITCH_SHIFT = 6
MAX_TRACK_SECONDS = 15 * 60  # longer tracks are rejected before the slow stem separation


# --- section analysis ---------------------------------------------------------
# Songs change tempo between sections (and beat trackers lock onto 5-BPM
# steps), so the render measures tempo and key in the exact sections it uses.

MIN_KEY_CONFIDENCE = 0.05
LOOSE_MS = 40.0  # beat wander above which "auto" quantize kicks in


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

def encode_mp3(wav_path: Path, mp3_path: Path) -> Path | None:
    """Encode wav_path to mp3_path with ffmpeg. Returns None (and leaves no
    partial file) if encoding fails or hangs, so the draft keeps its WAV."""
    try:
        subprocess.run(
            [shutil.which("ffmpeg"), "-y", "-loglevel", "error", "-i", str(wav_path),
             "-codec:a", "libmp3lame", "-b:a", "192k", str(mp3_path)],
            check=True,
            timeout=MP3_ENCODE_TIMEOUT_S,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        log.warning("MP3 encode failed, keeping WAV-only draft: %s", e)
        try:
            Path(mp3_path).unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return mp3_path


@dataclass
class _Draft:
    """Mutable state handed from one render_draft stage to the next."""

    track_a: dict
    track_b: dict
    duration: float
    semitones: int | None
    vocal_gain_db: float
    quantize: str
    report: ProgressFn
    sr: int = 44100
    stems_a: dict | None = None
    stems_b: dict | None = None
    vox_a: np.ndarray | None = None
    inst_a: np.ndarray | None = None
    inst_b: np.ndarray | None = None
    total_a: float = 0.0
    total_b: float = 0.0
    inst_a22: np.ndarray | None = None
    vox_a22: np.ndarray | None = None
    inst_b22: np.ndarray | None = None
    level_a: float = 0.0
    level_b: float = 0.0
    struct_a: dict | None = None
    struct_b: dict | None = None
    beats_a: np.ndarray | None = None
    beats_b: np.ndarray | None = None
    win_b: dict | None = None
    start_b: float = 0.0
    levels_b: list | None = None
    env_b: np.ndarray | None = None
    bpm_b: float = 0.0
    pad_s: float = 2.0  # extra source audio so alignment shifts never run dry
    lock: dict | None = None
    rate: float = 1.0
    start_a: float = 0.0
    bpm_a: float = 0.0
    ratio: float = 1.0
    key_source: str = "manual"
    a0: int = 0
    a1: int = 0
    lead_in: float = 0.0
    vox: np.ndarray | None = None
    inst_a_st: np.ndarray | None = None
    n_out: int = 0
    bed: np.ndarray | None = None
    fine: float = 0.0
    offset: int = 0
    bed22: np.ndarray | None = None
    check: dict | None = None
    quantized: bool = False
    check_constant: dict | None = None


def _check_lengths(track_a: dict, track_b: dict) -> None:
    import soundfile as sf

    for track in (track_a, track_b):
        length = track.get("duration")
        if not length:
            length = sf.info(track["path"]).duration
        if length > MAX_TRACK_SECONDS:
            raise ValueError(f"Track too long for draft (>15 min): {track.get('title') or track['path']}")


def _separate(st: _Draft) -> None:
    """Stems (the bulk of the time on first run; cached afterwards), loaded as stereo."""
    report, sr = st.report, st.sr
    st.stems_a = stems.separate(st.track_a, lambda f, m: report(0.45 * f, f"A: {m}"))
    st.stems_b = stems.separate(st.track_b, lambda f, m: report(0.45 + 0.35 * f, f"B: {m}"))

    report(0.80, "Loading stems")
    st.vox_a = load_stereo(st.stems_a["vocals"], sr)
    st.inst_a = load_stereo(st.stems_a["instrumental"], sr)
    st.inst_b = load_stereo(st.stems_b["instrumental"], sr)


def _analyse(st: _Draft) -> None:
    """Tempo/key analysis works on mono 22 kHz copies (fast, plenty for beats)."""
    report, sr = st.report, st.sr
    report(0.82, "Measuring tempo in each section")
    st.total_a, st.total_b = st.inst_a.shape[1] / sr, st.inst_b.shape[1] / sr
    st.duration = min(st.duration, st.total_b)
    st.inst_a22 = to_mono22(st.inst_a, sr)
    st.vox_a22 = to_mono22(st.vox_a, sr)
    st.inst_b22 = to_mono22(st.inst_b, sr)
    report(0.83, "Finding downbeats and 8-bar phrases")
    # Decide each song's real beat level once (not a 2/3 misread) and use it
    # for the phrase grid, the lock and quantizing alike.
    st.level_b = sync.main_level(st.inst_b22) or st.track_b["bpm"]
    st.level_a = sync.main_level(st.inst_a22) or st.track_a["bpm"]
    st.struct_b = structure.analyze(st.inst_b22, st.level_b)
    st.struct_a = structure.analyze(st.inst_a22, st.level_a, extra_energy=st.vox_a22)
    st.beats_a = (st.struct_a["beats"] if st.struct_a["beats"].size
                  else beat_times(st.inst_a22, TEMPO_SR, st.track_a["bpm"]))
    st.beats_b = (st.struct_b["beats"] if st.struct_b["beats"].size
                  else beat_times(st.inst_b22, TEMPO_SR, st.track_b["bpm"]))


def _choose_window(st: _Draft) -> None:
    """B is the reference: whole phrases, ideally with a build/drop, starting on
    a phrase boundary. Falls back to the steadiest loud section."""
    inst_b22, struct_b, level_b = st.inst_b22, st.struct_b, st.level_b
    st.win_b = choose_phrase_window(struct_b, st.duration, st.total_b)
    if st.win_b:
        st.start_b = st.win_b["start"]
        st.duration = min(st.win_b["duration"], st.total_b - st.win_b["start"])
    else:
        st.start_b = choose_section(inst_b22, inst_b22, st.beats_b, st.duration, None, keep=8)["anchor"]
    b0f = int(st.start_b * TEMPO_SR)
    st.env_b = sync.onset_env(inst_b22[b0f:b0f + int(st.duration * TEMPO_SR)])
    st.levels_b = sync.tempo_levels(sync.full_band_env(inst_b22[b0f:b0f + int(st.duration * TEMPO_SR)]))
    # B's beat in this window: its main level, refined by the section's own peaks.
    levels_b = st.levels_b
    st.bpm_b = min(levels_b, key=lambda lv: abs(lv / level_b - 1)) if levels_b else level_b
    if abs(st.bpm_b / level_b - 1) > 0.04:
        st.bpm_b = struct_b["bpm"] or level_b


def _lock(st: _Draft) -> None:
    """A: try its vocal-heavy sections and keep the one whose drums actually
    lock onto B's once stretched (measured, not guessed from BPM numbers)."""
    track_a, struct_a, inst_a22, total_a, pad_s = st.track_a, st.struct_a, st.inst_a22, st.total_a, st.pad_s
    st.report(0.84, "Locking A's rhythm onto B")
    src_len = st.duration * sync.MAX_RATE + 1.0
    # Candidates start on A's phrase boundaries (so A's phrase lines up with
    # B's); without a phrase grid, fall back to bar-aligned loud spots.
    cands = phrase_candidates(struct_a, min(src_len, total_a - 2 * pad_s), total_a, pad_s)
    if not cands:
        energy_a, edt = rms_curve(st.vox_a22, TEMPO_SR)
        cands = _candidates(st.beats_a, energy_a, edt, min(src_len, total_a - 2 * pad_s),
                            total_a, pad_s, keep=10)
    top_e = max((e for _, e in cands), default=0.0) or 1.0
    lock = None
    for start, e in cands:
        a0f = int(start * TEMPO_SR)
        env_a = sync.onset_env(inst_a22[a0f:a0f + int(src_len * TEMPO_SR)])
        levels_a = sync.tempo_levels(sync.full_band_env(inst_a22[a0f:a0f + int(src_len * TEMPO_SR)]))
        res = sync.best_rate(env_a, st.env_b, levels_a, st.levels_b, st.bpm_b)
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
        tempo = score_tempos(track_a["bpm"], st.track_b["bpm"])
        lock = {"rate": tempo.stretch, "lag": 0.0, "start": cands[0][0], "consistency": 0.0}
    st.lock = lock
    st.rate = lock["rate"]  # >1 speeds A up
    st.start_a = lock["start"]
    st.bpm_a = st.bpm_b / st.rate  # A's own tempo at the beat level that matches B
    st.ratio = min((1.0, 2.0, 0.5), key=lambda r: abs(st.bpm_a / (track_a["bpm"] * r) - 1))
    st.duration = min(st.duration, (total_a - st.start_a - pad_s) / st.rate)


def _choose_key(st: _Draft) -> None:
    """Key from the same sections; only trust it when the detector is sure."""
    track_a, track_b = st.track_a, st.track_b
    st.report(0.85, "Measuring key in each section")
    st.key_source = "manual"
    semitones = st.semitones
    if semitones is None:
        src_len = int(st.duration * st.rate * TEMPO_SR)
        a0k = int(st.start_a * TEMPO_SR)
        b0k = int(st.start_b * TEMPO_SR)
        ka = section_key(st.inst_a22[a0k:a0k + src_len])
        kb = section_key(st.inst_b22[b0k:b0k + int(st.duration * TEMPO_SR)])
        if ka and kb and min(ka[2], kb[2]) >= MIN_KEY_CONFIDENCE:
            semitones = min_shift_to_compatible(ka[0], ka[1], kb[0], kb[1]) or 0
            st.key_source = f"sections: {key_name(ka[0], ka[1])} → {key_name(kb[0], kb[1])}"
        elif min(track_a.get("key_confidence") or 0, track_b.get("key_confidence") or 0) >= MIN_KEY_CONFIDENCE:
            semitones = score_keys(track_a["key_pc"], track_a["mode"],
                                   track_b["key_pc"], track_b["mode"]).shift
            st.key_source = "whole songs"
        else:
            semitones = 0
            st.key_source = "uncertain, left unshifted"
    st.semitones = int(max(-MAX_PITCH_SHIFT, min(MAX_PITCH_SHIFT, semitones)))


def _stretch(st: _Draft) -> None:
    """Cut and stretch the chosen part of A; cut B's bed."""
    sr, rate, semitones = st.sr, st.rate, st.semitones
    st.report(0.87, f"Time-stretching A ×{rate:.4f}"
                    + (f", pitch {semitones:+d} st" if semitones else ""))
    src_len_a = st.duration * rate
    st.a0 = int(max(0.0, st.start_a - st.pad_s) * sr)
    st.a1 = int(min(st.total_a, st.start_a + src_len_a + st.pad_s) * sr)
    st.lead_in = (st.start_a - st.a0 / sr) / rate  # seconds of pre-roll, post-stretch
    st.vox = stretch_and_shift(st.vox_a[:, st.a0:st.a1], sr, rate, semitones)
    st.inst_a_st = stretch_and_shift(st.inst_a[:, st.a0:st.a1].mean(0, keepdims=True), sr, rate, 0)

    st.n_out = int(st.duration * sr)
    b0 = int(st.start_b * sr)
    st.bed = st.inst_b[:, b0:b0 + st.n_out]
    if st.bed.shape[1] < st.n_out:
        st.bed = place(st.bed, 0, st.n_out)


def _align(st: _Draft) -> None:
    """Place A where the lock search found B's beats, then polish on the real
    stretched audio (Rubber Band's output can shift by a few ms)."""
    sr, n_out = st.sr, st.n_out
    st.report(0.92, "Aligning beats")
    coarse = -int(st.lead_in * sr) + int(st.lock["lag"] * sync.DT * sr)
    moving = place(st.inst_a_st, coarse, n_out)[0]
    st.fine = onset_lag(st.bed.mean(0), moving, sr, 0.04)
    st.offset = coarse + int(st.fine * sr)
    st.vox = place(st.vox, st.offset, n_out)

    # independent check on the rendered audio: do A's hits stay on B's?
    st.bed22 = to_mono22(st.bed, sr)
    st.check = sync.drift_report(to_mono22(place(st.inst_a_st, st.offset, n_out), sr), st.bed22, st.bpm_b)


def _quantize(st: _Draft) -> None:
    """If one constant stretch leaves A's beats loose (live drums, tempo drift,
    swing), warp A beat-by-beat onto B's actual beats instead."""
    sr, n_out, a0, a1 = st.sr, st.n_out, st.a0, st.a1
    start_a, rate, bpm_a, bpm_b = st.start_a, st.rate, st.bpm_a, st.bpm_b
    inst_a, inst_a22, vox_a, bed22 = st.inst_a, st.inst_a22, st.vox_a, st.bed22
    quantize, check, report = st.quantize, st.check, st.report

    st.quantized, st.check_constant = False, None
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
                    end = int(min(st.total_a, start_a + st.duration * own_rate + st.pad_s) * sr)
                    seg_long = inst_a22[int(seg_start * TEMPO_SR):int(end / sr * TEMPO_SR)]
                    own = seg_start + structure.track_beats(seg_long, 60 / np.median(np.diff(free)))
                    results.append(attempt(own, own_rate, end))
            results = [r for r in results if r]
            best_q = min(results, key=lambda r: r[0]["wander_ms"]) if results else None
            if best_q:
                check_q, tmap, q_off, end = best_q
                better = check is None or check_q["wander_ms"] < check["wander_ms"]
                if quantize == "on" or better:
                    st.vox = place(quantize_stretch(vox_a[:, a0:end], sr, tmap, st.semitones), q_off, n_out)
                    st.quantized, st.check_constant, st.check = True, check, check_q


def _mix(st: _Draft) -> dict:
    """Balance levels, fade, export WAV + MP3 and describe the result."""
    import soundfile as sf

    sr, n_out, bed, vox = st.sr, st.n_out, st.bed, st.vox
    track_a, track_b, rate, start_a, start_b = st.track_a, st.track_b, st.rate, st.start_a, st.start_b
    duration, win_b = st.duration, st.win_b
    st.report(0.95, "Mixing")
    gain = active_rms(bed) / active_rms(vox) * 10 ** (st.vocal_gain_db / 20)
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
        st.report(0.98, "Encoding MP3")
        mp3_path = encode_mp3(wav_path, out_dir / "draft.mp3")

    return {
        "draft_id": draft_id,
        "a": track_a["id"],
        "b": track_b["id"],
        "title": f"{track_a['title']} (vocals) × {track_b['title']} (instrumental)",
        "duration": round(n_out / sr, 2),
        "stretch": round(rate, 4),
        "tempo_ratio": st.ratio,
        "semitones": st.semitones,
        "start_a": round(start_a, 2),
        "start_b": round(start_b, 2),
        "align_fine_ms": round(st.fine * 1000, 1),
        "bpm_a_section": round(st.bpm_a, 2),
        "bpm_b_section": round(st.bpm_b, 2),
        "lock": round(st.lock.get("consistency", 0.0), 3),
        "sync": st.check,  # {offset_ms, wander_ms, lock, corr} on the final audio
        "quantized": st.quantized,
        "sync_unquantized": st.check_constant,
        "phrases": win_b["phrases"] if win_b else None,
        "b_sections": labels_between(st.struct_b, start_b, start_b + duration),
        "a_sections": labels_between(st.struct_a, start_a, start_a + duration * rate),
        "key_source": st.key_source,
        "engine": "rubberband" if config.has_rubberband() else "librosa",
        "wav": wav_path.name,
        "mp3": mp3_path.name if mp3_path else None,
    }


def render_draft(
    track_a: dict, track_b: dict, *, duration: float = 60.0,
    semitones: int | None = None, vocal_gain_db: float = 0.0,
    quantize: str = "auto", progress: ProgressFn | None = None,
) -> dict:
    """Vocals of `track_a` over the instrumental of `track_b`.

    `quantize`: "auto" warps A beat-by-beat onto B's grid when a constant
    stretch leaves the beats loose; "on" always does; "off" never does."""
    _check_lengths(track_a, track_b)
    st = _Draft(
        track_a=track_a, track_b=track_b, duration=duration, semitones=semitones,
        vocal_gain_db=vocal_gain_db, quantize=quantize,
        report=progress or (lambda f, m: None),
    )
    _separate(st)
    _analyse(st)
    _choose_window(st)
    _lock(st)
    _choose_key(st)
    _stretch(st)
    _align(st)
    _quantize(st)
    return _mix(st)
