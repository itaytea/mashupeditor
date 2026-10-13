"""Beat grid, downbeats and 8-bar phrases (intro / build / drop / ...).

Most electronic and pop music is built from 8-bar phrases, and sections change
on phrase boundaries. We find:

1. a clean beat grid (tracked beats with missed/extra beats repaired),
2. the downbeat phase (which beat of 4 is the "1"): kicks and chord changes
   land on downbeats more than on other beats,
3. the phrase phase (which bar of 8 starts a phrase): the music changes most
   between the 4 bars before and after a phrase boundary,
4. a label per phrase from its energy profile relative to the rest of the song.

These are heuristics; they're right for most 4/4 dance and pop music and
degrade gracefully (worst case: phrases start on a wrong bar).
"""

from __future__ import annotations

import numpy as np

SR = 22050
HOP = 256
BEATS_PER_BAR = 4
BARS_PER_PHRASE = 8


def clean_beats(beats: np.ndarray) -> np.ndarray:
    """Repair a tracked beat list: drop beats squeezed in between two others
    (extra detections) and fill gaps of ~2+ beats (missed detections)."""
    beats = np.asarray(beats, dtype=float)
    if beats.size < 4:
        return beats
    period = float(np.median(np.diff(beats)))
    out = [beats[0]]
    for t in beats[1:]:
        gap = t - out[-1]
        if gap < 0.6 * period:
            continue  # extra beat
        missing = int(round(gap / period)) - 1
        step = gap / (missing + 1)
        for _ in range(missing):  # missed beat(s): interpolate evenly
            out.append(out[-1] + step)
        out.append(t)
    return np.array(out)


def extend_grid(beats: np.ndarray, duration: float, edge: int = 16) -> np.ndarray:
    """Continue the grid into quiet intros/outros the tracker skipped, using
    the period of the nearest `edge` beats (dance music keeps a fixed grid)."""
    if beats.size < 8:
        return beats
    head = np.median(np.diff(beats[:edge]))
    tail = np.median(np.diff(beats[-edge:]))
    before = np.arange(beats[0] - head, -0.5 * head, -head)[::-1]
    after = np.arange(beats[-1] + tail, duration - 0.25 * tail, tail)
    before = before[before >= 0]
    return np.concatenate([before, beats, after])


def track_beats(y: np.ndarray, bpm_hint: float | None = None, fixed: bool = False,
                tightness: float = 400) -> np.ndarray:
    """Beat times (s). With `fixed`, the tracker counts at `bpm_hint`'s level
    (use a low `tightness` so beats can still follow a performer's drift)."""
    import librosa

    from .sync import onset_env

    env = onset_env(y)  # kick-weighted, so beats land on kicks, not off-beat hats
    if bpm_hint and fixed:
        kwargs = {"bpm": bpm_hint, "tightness": tightness}
    elif bpm_hint:
        kwargs = {"start_bpm": bpm_hint, "tightness": 300}
    else:
        kwargs = {}
    _, beats = librosa.beat.beat_track(onset_envelope=env, sr=SR, hop_length=HOP,
                                       units="time", trim=False, **kwargs)
    return extend_grid(clean_beats(beats), len(y) / SR)


def beat_features(y: np.ndarray, beats: np.ndarray) -> dict[str, np.ndarray]:
    """Per-beat descriptors (one value per beat interval)."""
    import librosa

    stft = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP))
    freqs = librosa.fft_frequencies(sr=SR, n_fft=2048)
    frames = librosa.time_to_frames(beats, sr=SR, hop_length=HOP)
    frames = np.clip(frames, 0, stft.shape[1] - 1)

    low = stft[freqs < 150].sum(0)          # kick + bass
    high = stft[freqs > 4000].sum(0)        # hats, noise risers
    rms = librosa.feature.rms(S=stft, frame_length=2048, hop_length=HOP)[0]
    onset = librosa.onset.onset_strength(S=librosa.amplitude_to_db(stft), sr=SR)
    chroma = librosa.feature.chroma_stft(S=stft ** 2, sr=SR)

    # One segment per beat: [beat_i, beat_i+1), the last one running to the end.
    bounds = np.unique(np.append(frames, stft.shape[1]))

    def sync(x):
        out = librosa.util.sync(x, bounds, aggregate=np.mean, pad=False)
        if out.shape[-1] < frames.size:  # duplicate frames collapsed by unique()
            out = np.pad(out, [(0, 0)] * (out.ndim - 1) + [(0, frames.size - out.shape[-1])], mode="edge")
        return out[..., : frames.size]

    # kick strength right at each beat (a 2-frame window), for downbeats
    kick = np.array([low[max(0, f - 1):f + 2].max() if f < low.size else 0.0 for f in frames])
    return {
        "rms": sync(rms[None])[0],
        "low": sync(low[None])[0],
        "high": sync(high[None])[0],
        "onset": sync(onset[None])[0],
        "chroma": sync(chroma),
        "kick": kick,
    }


def downbeat_phase(feats: dict[str, np.ndarray]) -> int:
    """Which beat (0..3) is the "1": strongest kicks plus biggest chord changes."""
    kick = feats["kick"]
    chroma = feats["chroma"]
    n = kick.size
    change = np.zeros(n)
    if chroma.shape[1] > 1:
        change[1:] = np.linalg.norm(np.diff(chroma, axis=1), axis=0)[: n - 1]
    k = (kick - kick.mean()) / (kick.std() + 1e-9)
    c = (change - change.mean()) / (change.std() + 1e-9)
    score = [np.mean(k[p::4]) + 0.7 * np.mean(c[p::4]) for p in range(BEATS_PER_BAR)]
    return int(np.argmax(score))


def _bar_matrix(feats: dict[str, np.ndarray], first: int, n_bars: int) -> np.ndarray:
    """One normalised feature vector per bar."""
    rows = []
    for b in range(n_bars):
        s = first + b * BEATS_PER_BAR
        sl = slice(s, s + BEATS_PER_BAR)
        rows.append(np.concatenate([
            [np.log1p(feats["rms"][sl].mean() * 100), np.log1p(feats["low"][sl].mean()),
             np.log1p(feats["high"][sl].mean()), feats["onset"][sl].mean()],
            feats["chroma"][:, sl].mean(axis=1),
        ]))
    m = np.array(rows)
    return (m - m.mean(0)) / (m.std(0) + 1e-9)


def phrase_phase(bars: np.ndarray) -> int:
    """Which bar (0..7) starts the phrases: novelty between the 4 bars before
    and after a boundary peaks on phrase starts."""
    n = len(bars)
    if n < 2 * BARS_PER_PHRASE:
        return 0
    novelty = np.zeros(n)
    for b in range(4, n - 3):
        novelty[b] = np.linalg.norm(bars[b - 4:b].mean(0) - bars[b:b + 4].mean(0))
    scores = [novelty[o::BARS_PER_PHRASE].mean() for o in range(BARS_PER_PHRASE)]
    return int(np.argmax(scores))


def label_phrases(phrases: list[dict]) -> None:
    """intro / build / drop / breakdown / outro / main, from relative energy."""
    if not phrases:
        return
    energy = np.array([p["energy"] for p in phrases])
    low = np.array([p["low"] for p in phrases])
    q = lambda arr, x: float(np.quantile(arr, x))  # noqa: E731
    hi_e, hi_l, mid_e = q(energy, 0.7), q(low, 0.55), q(energy, 0.45)

    for p in phrases:
        p["label"] = "drop" if p["energy"] >= hi_e and p["low"] >= hi_l else "main"
    drops = [i for i, p in enumerate(phrases) if p["label"] == "drop"]
    for i in drops:
        j = i - 1
        if j >= 0 and phrases[j]["label"] != "drop" and (
            phrases[j]["rise"] > 0 or phrases[j]["energy"] < phrases[i]["energy"]
        ):
            phrases[j]["label"] = "build"
    if drops:
        for i in range(drops[0]):
            if phrases[i]["label"] == "main" and phrases[i]["energy"] < mid_e:
                phrases[i]["label"] = "intro"
        for i in range(drops[0] + 1, len(phrases)):
            if phrases[i]["label"] == "main" and phrases[i]["energy"] < mid_e:
                phrases[i]["label"] = "breakdown"
        for i in range(len(phrases) - 1, drops[-1], -1):
            if phrases[i]["label"] in ("breakdown", "main") and phrases[i]["energy"] < mid_e:
                phrases[i]["label"] = "outro"
            else:
                break
    else:  # no clear drop: just mark the quiet start and end
        for i in range(len(phrases)):
            if phrases[i]["energy"] < mid_e:
                phrases[i]["label"] = "intro"
            else:
                break
        for i in range(len(phrases) - 1, -1, -1):
            if phrases[i]["energy"] < mid_e and phrases[i]["label"] == "main":
                phrases[i]["label"] = "outro"
            else:
                break


def analyze(y: np.ndarray, bpm_hint: float | None = None, extra_energy: np.ndarray | None = None) -> dict:
    """Beat grid, downbeats and labelled 8-bar phrases for a mono SR signal.

    `extra_energy` (same length as y, e.g. the vocal stem) adds a per-phrase
    "vocal" energy so callers can find the sung parts.
    """
    import librosa

    beats = track_beats(y, bpm_hint)
    if beats.size < BEATS_PER_BAR * 4:
        return {"beats": beats, "downbeats": np.array([]), "phrases": [], "bpm": None}
    period = float(np.polyfit(np.arange(beats.size), beats, 1)[0])
    feats = beat_features(y, beats)
    first_db = downbeat_phase(feats)
    downbeats = beats[first_db::BEATS_PER_BAR]
    n_bars = (beats.size - first_db) // BEATS_PER_BAR
    bars = _bar_matrix(feats, first_db, n_bars) if n_bars else np.zeros((0, 0))
    offset = phrase_phase(bars)

    vocal_rms = None
    if extra_energy is not None:
        vocal_rms = librosa.feature.rms(y=extra_energy, hop_length=HOP)[0]

    phrases = []
    rms = feats["rms"]
    for b0 in range(offset, n_bars, BARS_PER_PHRASE):
        bars_here = min(BARS_PER_PHRASE, n_bars - b0)
        if bars_here < BARS_PER_PHRASE // 2:
            break  # a short tail isn't a phrase
        s = first_db + b0 * BEATS_PER_BAR
        e = s + bars_here * BEATS_PER_BAR
        seg = rms[s:min(e, rms.size)]
        half = max(1, seg.size // 2)
        start_t = float(beats[s])
        end_t = float(beats[e]) if e < beats.size else float(beats[-1] + period)
        ph = {
            "start": round(start_t, 3), "end": round(end_t, 3), "bar": b0, "bars": bars_here,
            "energy": float(seg.mean()) if seg.size else 0.0,
            "low": float(feats["low"][s:e].mean()) if e > s else 0.0,
            "rise": float(seg[half:].mean() - seg[:half].mean()) if seg.size > 1 else 0.0,
        }
        if vocal_rms is not None:
            f0, f1 = (librosa.time_to_frames([start_t, end_t], sr=SR, hop_length=HOP))
            ph["vocal"] = float(vocal_rms[f0:f1].mean()) if f1 > f0 else 0.0
        phrases.append(ph)
    label_phrases(phrases)
    return {"beats": beats, "downbeats": downbeats, "phrases": phrases,
            "bpm": 60.0 / period, "phrase_seconds": period * BEATS_PER_BAR * BARS_PER_PHRASE}
