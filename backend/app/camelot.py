"""Musical keys, the Camelot wheel, and key-compatibility scoring.

Keys are represented as (pitch_class, mode) where pitch_class is 0..11 (C=0)
and mode is "major" or "minor". The Camelot wheel maps each key to a number
1..12 plus a letter: "B" for major, "A" for minor. Harmonic mixing rules:

* same code               -> same key
* same number, other letter -> relative major/minor (e.g. 8A <-> 8B)
* number +/-1, same letter  -> adjacent on the wheel (a fifth up/down)
"""

from __future__ import annotations

from dataclasses import dataclass

PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Scores for each compatibility relation. Exported so the matcher and tests
# agree on the numbers.
SCORE_SAME = 1.0
SCORE_RELATIVE = 0.9
SCORE_ADJACENT = 0.85
# Not compatible as-is, but a small pitch shift of one song would fix it.
SCORE_SHIFT_1 = 0.5
SCORE_SHIFT_2 = 0.3


@dataclass(frozen=True)
class Camelot:
    number: int  # 1..12
    letter: str  # "A" (minor) or "B" (major)

    def __str__(self) -> str:
        return f"{self.number}{self.letter}"

    @classmethod
    def parse(cls, code: str) -> "Camelot":
        code = code.strip().upper()
        number, letter = int(code[:-1]), code[-1]
        if not 1 <= number <= 12 or letter not in ("A", "B"):
            raise ValueError(f"invalid Camelot code: {code!r}")
        return cls(number, letter)


def key_name(pitch_class: int, mode: str) -> str:
    return PITCH_NAMES[pitch_class % 12] + ("m" if mode == "minor" else "")


def to_camelot(pitch_class: int, mode: str) -> Camelot:
    """Map a key to its Camelot code.

    Moving a fifth up (+7 semitones) moves one step clockwise, so the number is
    7 * pitch_class plus a per-mode offset: C major is 8B, A minor is 8A.
    """
    if mode not in ("major", "minor"):
        raise ValueError(f"mode must be 'major' or 'minor', got {mode!r}")
    offset = 8 if mode == "major" else 5
    number = (7 * pitch_class + offset - 1) % 12 + 1
    return Camelot(number, "B" if mode == "major" else "A")


def wheel_distance(a: int, b: int) -> int:
    """Steps between two Camelot numbers going the short way round (0..6)."""
    d = abs(a - b) % 12
    return min(d, 12 - d)


def relation(a: Camelot, b: Camelot) -> str | None:
    """Name the harmonic relation between two codes, or None if incompatible."""
    if a == b:
        return "same"
    if a.number == b.number:
        return "relative"
    if a.letter == b.letter and wheel_distance(a.number, b.number) == 1:
        return "adjacent"
    return None


def shift_key(pitch_class: int, semitones: int) -> int:
    return (pitch_class + semitones) % 12


def min_shift_to_compatible(
    pc_a: int, mode_a: str, pc_b: int, mode_b: str, max_shift: int = 6
) -> int | None:
    """Smallest pitch shift (in semitones, applied to song A) that makes A
    harmonically compatible with B. Ties prefer the smaller absolute shift and
    then shifting down (pitching vocals down usually sounds more natural)."""
    target = to_camelot(pc_b, mode_b)
    for magnitude in range(0, max_shift + 1):
        for s in (-magnitude, magnitude) if magnitude else (0,):
            if relation(to_camelot(shift_key(pc_a, s), mode_a), target):
                return s
    return None


@dataclass(frozen=True)
class KeyScore:
    score: float
    relation: str | None  # same / relative / adjacent, or None
    shift: int  # semitones to apply to A to reach a compatible key
    reason: str


def score_keys(pc_a: int, mode_a: str, pc_b: int, mode_b: str) -> KeyScore:
    ca, cb = to_camelot(pc_a, mode_a), to_camelot(pc_b, mode_b)
    rel = relation(ca, cb)
    ka, kb = key_name(pc_a, mode_a), key_name(pc_b, mode_b)
    if rel == "same":
        return KeyScore(SCORE_SAME, rel, 0, f"Same key ({ca} {ka})")
    if rel == "relative":
        return KeyScore(
            SCORE_RELATIVE, rel, 0, f"Relative major/minor ({ca} {ka} ↔ {cb} {kb})"
        )
    if rel == "adjacent":
        return KeyScore(
            SCORE_ADJACENT, rel, 0, f"Adjacent on the Camelot wheel ({ca} → {cb})"
        )

    shift = min_shift_to_compatible(pc_a, mode_a, pc_b, mode_b)
    if shift is not None and abs(shift) == 1:
        score = SCORE_SHIFT_1
    elif shift is not None and abs(shift) == 2:
        score = SCORE_SHIFT_2
    else:
        score = 0.0
    if shift is None:
        reason = f"Keys clash ({ca} {ka} vs {cb} {kb})"
    else:
        reason = (
            f"Keys clash ({ca} vs {cb}); shifting A by {shift:+d} semitone"
            f"{'s' if abs(shift) != 1 else ''} makes them compatible"
        )
    return KeyScore(score, None, shift if shift is not None else 0, reason)
