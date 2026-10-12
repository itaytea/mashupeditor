"""Rank every pair of analysed tracks by key compatibility and tempo closeness."""

from __future__ import annotations

from itertools import combinations

from .camelot import score_keys
from .tempo import score_tempos

KEY_WEIGHT = 0.55
TEMPO_WEIGHT = 0.45


def score_pair(a: dict, b: dict) -> dict:
    """Score two tracks (dicts with bpm, key_pc, mode). The key shift and tempo
    stretch are expressed as adjustments applied to A to fit B."""
    key = score_keys(a["key_pc"], a["mode"], b["key_pc"], b["mode"])
    tempo = score_tempos(a["bpm"], b["bpm"])
    total = KEY_WEIGHT * key.score + TEMPO_WEIGHT * tempo.score
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
        "reasons": [key.reason, tempo.reason],
    }


def rank_pairs(tracks: list[dict], limit: int | None = 100, min_score: float = 0.0) -> list[dict]:
    usable = [t for t in tracks if t.get("bpm") and t.get("mode") is not None]
    pairs = [score_pair(a, b) for a, b in combinations(usable, 2)]
    pairs = [p for p in pairs if p["score"] >= min_score]
    pairs.sort(key=lambda p: (-p["score"], p["tempo_diff_pct"]))
    return pairs[:limit] if limit else pairs
