"""Rank every pair of tracks by key compatibility and tempo closeness."""

from __future__ import annotations

from functools import lru_cache
from itertools import combinations

import numpy as np

from .camelot import score_keys, to_camelot
from .fuzzy import normalize
from .tempo import HALF_DOUBLE_PENALTY, MAX_STRETCH, RATIOS, score_tempos

KEY_WEIGHT = 0.55
TEMPO_WEIGHT = 0.45
# Below this the key detector was basically guessing; its "match" counts for
# less. Looked-up keys (no confidence value) are trusted.
MIN_KEY_CONFIDENCE = 0.05
UNSURE_KEY_FACTOR = 0.6


def _key_trust(t: dict) -> float:
    conf = t.get("key_confidence")
    return UNSURE_KEY_FACTOR if conf is not None and conf < MIN_KEY_CONFIDENCE else 1.0


def score_pair(a: dict, b: dict) -> dict:
    """Score two tracks (dicts with id, bpm, key_pc, mode). The key shift and
    tempo stretch are expressed as adjustments applied to A to fit B."""
    key = score_keys(a["key_pc"], a["mode"], b["key_pc"], b["mode"])
    tempo = score_tempos(a["bpm"], b["bpm"])
    trust = min(_key_trust(a), _key_trust(b))
    total = KEY_WEIGHT * key.score * trust + TEMPO_WEIGHT * tempo.score
    reasons = [key.reason, tempo.reason]
    if trust < 1.0:
        reasons.append("Key detection was unsure for one of these; the key match may not hold")
    return {
        "a": a["id"],
        "b": b["id"],
        "score": round(total, 4),
        "key_score": key.score,
        "tempo_score": tempo.score,
        "key_relation": key.relation,
        "key_shift": key.shift,
        "tempo_ratio": tempo.ratio,
        "stretch": round(tempo.stretch, 4),
        "tempo_diff_pct": tempo.diff_pct,
        "reasons": reasons,
    }


def dedupe(tracks: list[dict]) -> list[dict]:
    """Drop extra copies of the same song (e.g. one file in Music and one in
    Downloads) so a song is never offered as a match for itself. Same song =
    same normalized title, and the same artist or one of them untagged. The
    first copy wins, so callers should list preferred copies first."""
    kept: list[dict] = []
    seen: dict[str, list[str]] = {}  # title -> artists already kept
    for t in tracks:
        title = normalize(t.get("title") or "")
        artist = normalize(t.get("artist") or "")
        if title:
            artists = seen.setdefault(title, [])
            if any(a == artist or not a or not artist for a in artists):
                continue
            artists.append(artist)
        kept.append(t)
    return kept


def _usable(tracks: list[dict]) -> list[dict]:
    return dedupe([t for t in tracks if t.get("bpm") and t.get("mode") is not None])


def _sort_key(p: dict) -> tuple:
    return (-p["score"], p["tempo_diff_pct"])


def rank_pairs_bruteforce(tracks: list[dict], limit: int | None = 100,
                          min_score: float = 0.0) -> list[dict]:
    """Reference implementation: score every pair in Python. Fine for a few
    hundred tracks; used by the tests to check the fast path."""
    pairs = [score_pair(a, b) for a, b in combinations(_usable(tracks), 2)]
    pairs = [p for p in pairs if p["score"] >= min_score]
    pairs.sort(key=_sort_key)
    return pairs[:limit] if limit else pairs


# --- vectorized path ---------------------------------------------------------

def _key_index(pc: int, mode: str) -> int:
    code = to_camelot(pc, mode)
    return (code.number - 1) * 2 + (code.letter == "B")


@lru_cache(maxsize=1)
def _key_table() -> np.ndarray:
    """24x24 key-score table indexed by Camelot code (each code is one key)."""
    reps: dict[int, tuple[int, str]] = {}
    for pc in range(12):
        for mode in ("major", "minor"):
            reps[_key_index(pc, mode)] = (pc, mode)
    table = np.zeros((24, 24), dtype=np.float32)
    for i, (pa, ma) in reps.items():
        for j, (pb, mb) in reps.items():
            table[i, j] = score_keys(pa, ma, pb, mb).score
    return table


def _tempo_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Vectorized score_tempos for every (a_i, b_j)."""
    best = np.zeros((a.size, b.size), dtype=np.float32)
    for ratio in RATIOS:
        diff = np.abs(a[:, None] * ratio - b[None, :]) / b[None, :]
        s = np.clip(1.0 - diff / MAX_STRETCH, 0.0, None)
        if ratio != 1.0:
            s *= HALF_DOUBLE_PENALTY
        np.maximum(best, s, out=best)
    return best


def rank_pairs(tracks: list[dict], limit: int | None = 100, min_score: float = 0.0,
               focus=None, chunk: int = 512) -> list[dict]:
    """Top pairs by score. Scores are computed in numpy blocks so thousands of
    tracks (millions of pairs) rank in about a second; only the winners get
    the full score_pair() treatment with reasons. With `focus` set to a track
    id, only pairs involving that track are ranked."""
    usable = _usable(tracks)
    n = len(usable)
    if n < 2:
        return []
    if limit is None:
        return rank_pairs_bruteforce(usable, None, min_score)

    bpm = np.array([t["bpm"] for t in usable], dtype=np.float64)
    kidx = np.array([_key_index(t["key_pc"], t["mode"]) for t in usable])
    trust = np.array([_key_trust(t) for t in usable], dtype=np.float32)
    table = _key_table()
    # Over-select a little so rounding/tie-breaks can't push a true winner out.
    want = min(limit * 2 + 50, n * (n - 1) // 2)

    if focus is not None:
        rows = [i for i, t in enumerate(usable) if t["id"] == focus]
        if not rows:
            return []
    else:
        rows = list(range(n))

    cand_scores: list[np.ndarray] = []
    cand_i: list[np.ndarray] = []
    cand_j: list[np.ndarray] = []
    for start in range(0, len(rows), chunk):
        ri = np.array(rows[start:start + chunk])
        pair_trust = np.minimum(trust[ri][:, None], trust[None, :])
        total = (KEY_WEIGHT * table[kidx[ri][:, None], kidx[None, :]] * pair_trust
                 + TEMPO_WEIGHT * _tempo_matrix(bpm[ri], bpm))
        jj = np.arange(n)[None, :]
        # each unordered pair once; with a focus track, every partner counts
        mask = (jj != ri[:, None]) if focus is not None else (jj > ri[:, None])
        total = np.where(mask & (total >= min_score - 1e-6), total, -1.0)
        flat = total.ravel()
        k = min(want, int((flat >= 0).sum()))
        if k == 0:
            continue
        top = np.argpartition(-flat, k - 1)[:k]
        cand_scores.append(flat[top])
        cand_i.append(ri[top // n])
        cand_j.append(top % n)

    if not cand_scores:
        return []
    scores = np.concatenate(cand_scores)
    ii, jj = np.concatenate(cand_i), np.concatenate(cand_j)
    order = np.argsort(-scores, kind="stable")[:want]

    pairs = []
    for o in order:
        i, j = int(ii[o]), int(jj[o])
        if focus is not None and usable[j]["id"] == focus:
            i, j = j, i
        pairs.append(score_pair(usable[i], usable[j]))
    pairs = [p for p in pairs if p["score"] >= min_score]
    pairs.sort(key=_sort_key)
    return pairs[:limit]

