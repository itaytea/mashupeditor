import pytest

from app import camelot
from app.camelot import Camelot, score_keys, to_camelot

C, Cs, D, Ds, E, F, Fs, G, Gs, A, As, B = range(12)


@pytest.mark.parametrize(
    "pc, mode, code",
    [
        (C, "major", "8B"), (A, "minor", "8A"),
        (G, "major", "9B"), (E, "minor", "9A"),
        (E, "major", "12B"), (Cs, "minor", "12A"),
        (B, "major", "1B"), (Gs, "minor", "1A"),
        (F, "major", "7B"), (D, "minor", "7A"),
        (Fs, "major", "2B"), (Ds, "minor", "2A"),
        (C, "minor", "5A"), (Ds, "major", "5B"),
    ],
)
def test_to_camelot_known_codes(pc, mode, code):
    assert str(to_camelot(pc, mode)) == code


def test_every_key_maps_to_a_unique_code():
    codes = {str(to_camelot(pc, m)) for pc in range(12) for m in ("major", "minor")}
    assert len(codes) == 24


def test_parse_roundtrip_and_validation():
    assert Camelot.parse("8a") == Camelot(8, "A")
    with pytest.raises(ValueError):
        Camelot.parse("13B")
    with pytest.raises(ValueError):
        Camelot.parse("5C")


def test_wheel_distance_wraps_around():
    assert camelot.wheel_distance(12, 1) == 1
    assert camelot.wheel_distance(1, 7) == 6
    assert camelot.wheel_distance(3, 3) == 0


def test_same_key():
    s = score_keys(A, "minor", A, "minor")
    assert s.relation == "same"
    assert s.score == camelot.SCORE_SAME
    assert s.shift == 0


def test_relative_major_minor_both_directions():
    for args in ((C, "major", A, "minor"), (A, "minor", C, "major")):
        s = score_keys(*args)
        assert s.relation == "relative"
        assert s.score == camelot.SCORE_RELATIVE


@pytest.mark.parametrize(
    "a, b",
    [
        ((C, "major"), (G, "major")),   # 8B -> 9B
        ((C, "major"), (F, "major")),   # 8B -> 7B
        ((A, "minor"), (E, "minor")),   # 8A -> 9A
        ((E, "major"), (B, "major")),   # 12B -> 1B (wraps)
        ((B, "major"), (E, "major")),   # 1B -> 12B (wraps)
    ],
)
def test_adjacent_plus_minus_one(a, b):
    s = score_keys(*a, *b)
    assert s.relation == "adjacent"
    assert s.score == camelot.SCORE_ADJACENT


def test_diagonal_is_not_compatible_as_is():
    # 8B (C) vs 9A (Em): neighbouring number but different letter.
    s = score_keys(C, "major", E, "minor")
    assert s.relation is None
    assert s.score < camelot.SCORE_ADJACENT


def test_one_semitone_shift_suggested():
    # C major (8B) vs C# major (3B) clash, but C +1 = C# is the same key.
    s = score_keys(C, "major", Cs, "major")
    assert s.relation is None
    assert s.shift == 1
    assert s.score == camelot.SCORE_SHIFT_1


def test_two_semitone_shift_suggested():
    # C major (8B) vs D major (10B): two steps on the wheel; C +2 = D.
    s = score_keys(C, "major", D, "major")
    assert s.relation is None
    assert abs(s.shift) == 2
    assert s.score == camelot.SCORE_SHIFT_2


def test_shift_prefers_down_on_ties():
    # Shifting C# down 1 gives C (same key as target); shifting up 1 gives D
    # which is adjacent-compatible with... not C. Check down is chosen.
    s = score_keys(Cs, "major", C, "major")
    assert s.shift == -1


def test_shift_always_leads_to_compatible_key():
    for pa in range(12):
        for ma in ("major", "minor"):
            for pb in range(12):
                for mb in ("major", "minor"):
                    shift = camelot.min_shift_to_compatible(pa, ma, pb, mb)
                    assert shift is not None and abs(shift) <= 6
                    shifted = to_camelot(camelot.shift_key(pa, shift), ma)
                    assert camelot.relation(shifted, to_camelot(pb, mb))


def test_score_ordering():
    assert (
        camelot.SCORE_SAME > camelot.SCORE_RELATIVE > camelot.SCORE_ADJACENT
        > camelot.SCORE_SHIFT_1 > camelot.SCORE_SHIFT_2 > 0
    )


def test_invalid_mode():
    with pytest.raises(ValueError):
        to_camelot(0, "dorian")
