import pytest

from app.tempo import HALF_DOUBLE_PENALTY, MAX_STRETCH, score_tempos


def test_identical_tempo_is_perfect():
    s = score_tempos(120, 120)
    assert s.score == 1.0
    assert s.ratio == 1.0
    assert s.stretch == pytest.approx(1.0)
    assert "Same tempo" in s.reason


def test_close_tempo_scores_high_and_linear():
    s = score_tempos(118, 120)
    diff = 2 / 120
    assert s.ratio == 1.0
    assert s.score == pytest.approx(1 - diff / MAX_STRETCH, abs=1e-3)
    assert s.stretch == pytest.approx(120 / 118)


def test_closer_beats_further():
    assert score_tempos(121, 120).score > score_tempos(125, 120).score


def test_far_tempo_scores_zero():
    s = score_tempos(100, 128)
    assert s.score == 0.0


def test_double_time():
    # A at 70 BPM against B at 140: A doubled matches exactly.
    s = score_tempos(70, 140)
    assert s.ratio == 2.0
    assert s.score == pytest.approx(HALF_DOUBLE_PENALTY)
    assert s.stretch == pytest.approx(1.0)
    assert "Double-time" in s.reason


def test_half_time():
    s = score_tempos(174, 86)
    assert s.ratio == 0.5
    assert s.score == pytest.approx((1 - (1 / 86) / MAX_STRETCH) * HALF_DOUBLE_PENALTY, abs=1e-3)
    assert s.stretch == pytest.approx(86 / 87)
    assert "Half-time" in s.reason


def test_exact_match_outranks_equivalent_half_double():
    direct = score_tempos(128, 128)
    doubled = score_tempos(64, 128)
    assert direct.score > doubled.score


def test_stretch_maps_a_onto_b():
    for a, b in [(90, 95), (140, 72), (60, 118), (128, 124)]:
        s = score_tempos(a, b)
        assert a * s.ratio * s.stretch == pytest.approx(b)


def test_zero_score_reports_closest_ratio():
    # 100 vs 150: neither direct (50%) nor ×2 (33%) nor ÷2 (67%) is within
    # range, but ×2 is closest.
    s = score_tempos(100, 150)
    assert s.score == 0.0
    assert s.ratio == 2.0


def test_scores_are_bounded():
    for a in range(60, 200, 7):
        for b in range(60, 200, 11):
            s = score_tempos(a, b)
            assert 0.0 <= s.score <= 1.0


def test_rejects_non_positive():
    with pytest.raises(ValueError):
        score_tempos(0, 120)
