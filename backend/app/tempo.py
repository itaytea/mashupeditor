"""Tempo-closeness scoring, allowing half- and double-time matches."""

from __future__ import annotations

from dataclasses import dataclass

# Tempo difference (as a fraction) at which the score reaches zero. Beyond ~8%
# a time-stretch starts to sound obviously processed.
MAX_STRETCH = 0.08
# Small penalty so an exact 1:1 match ranks above an equally close half/double.
HALF_DOUBLE_PENALTY = 0.9

RATIOS = (1.0, 2.0, 0.5)


@dataclass(frozen=True)
class TempoScore:
    score: float
    ratio: float  # multiply A's BPM by this to land near B (1, 2 or 0.5)
    stretch: float  # time-stretch rate for A: B_bpm / (A_bpm * ratio)
    diff_pct: float  # |A*ratio - B| / B, as a percentage
    reason: str


def score_tempos(bpm_a: float, bpm_b: float) -> TempoScore:
    if bpm_a <= 0 or bpm_b <= 0:
        raise ValueError("BPM must be positive")

    candidates = []
    for ratio in RATIOS:
        effective = bpm_a * ratio
        diff = abs(effective - bpm_b) / bpm_b
        score = max(0.0, 1.0 - diff / MAX_STRETCH)
        if ratio != 1.0:
            score *= HALF_DOUBLE_PENALTY
        candidates.append((score, -diff, ratio))

    # Highest score wins; when everything scores 0, report the closest ratio.
    score, neg_diff, ratio = max(candidates)
    diff = -neg_diff
    return TempoScore(
        score=round(score, 4),
        ratio=ratio,
        stretch=bpm_b / (bpm_a * ratio),
        diff_pct=round(diff * 100, 2),
        reason=_reason(bpm_a, bpm_b, ratio, diff),
    )


def _reason(bpm_a: float, bpm_b: float, ratio: float, diff: float) -> str:
    a, b = f"{bpm_a:.1f}", f"{bpm_b:.1f}"
    pct = f"{diff * 100:.1f}%"
    if ratio == 1.0:
        if diff < 0.005:
            return f"Same tempo ({a} BPM)"
        return f"Tempos {a} vs {b} BPM ({pct} apart)"
    label = "Double-time" if ratio == 2.0 else "Half-time"
    op = "×2" if ratio == 2.0 else "÷2"
    return f"{label}: {a} {op} = {bpm_a * ratio:.1f} vs {b} BPM ({pct} apart)"
