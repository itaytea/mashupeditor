"""Sync by measurement, checked on click tracks with known tempos."""

import numpy as np
import pytest

from app import sync

SR = sync.SR


def clicks(bpm, seconds, offset=0.0, accent_every=4, seed=0, drift_to=None):
    """Kick-like clicks on every beat (accented bars), optional tempo ramp."""
    rng = np.random.default_rng(seed)
    y = 0.01 * rng.standard_normal(int(seconds * SR))
    t, k = offset, 0
    while t < seconds:
        i = int(t * SR)
        n = min(int(0.05 * SR), y.size - i)
        amp = 1.0 if k % accent_every == 0 else 0.55
        y[i:i + n] += amp * np.exp(-np.linspace(0, 7, n)) * np.sin(2 * np.pi * 80 * np.arange(n) / SR)
        cur = bpm if drift_to is None else bpm + (drift_to - bpm) * t / seconds
        t += 60.0 / cur
        k += 1
    return y.astype(np.float32)


def test_tempo_levels_include_true_tempo():
    levels = sync.tempo_levels(sync.onset_env(clicks(120, 30)))
    assert any(abs(l - 120) < 3 or abs(l - 60) < 2 or abs(l - 240) < 6 for l in levels)


@pytest.mark.parametrize("bpm_a, bpm_b", [(123, 120), (117.5, 121), (128, 128)])
def test_best_rate_finds_exact_stretch(bpm_a, bpm_b):
    ea = sync.onset_env(clicks(bpm_a, 75, offset=0.13))
    eb = sync.onset_env(clicks(bpm_b, 60, offset=0.0, seed=1))
    res = sync.best_rate(ea, eb, sync.tempo_levels(ea), sync.tempo_levels(eb), bpm_b)
    assert res["rate"] == pytest.approx(bpm_b / bpm_a, rel=0.003)
    assert res["consistency"] > 0.9


def test_best_rate_handles_half_time():
    # A at 171.5 against B at 83: A should be heard at half time (85.75 -> 83).
    ea = sync.onset_env(clicks(171.5, 75, offset=0.05))
    eb = sync.onset_env(clicks(83, 60, seed=2))
    res = sync.best_rate(ea, eb, sync.tempo_levels(ea), sync.tempo_levels(eb), 83)
    assert res["rate"] == pytest.approx(166 / 171.5, rel=0.004)


def test_drift_report_tight_vs_drifting():
    b = clicks(120, 40, seed=3)
    tight = sync.drift_report(clicks(120, 40, offset=0.0, seed=4), b, 120)
    drifting = sync.drift_report(clicks(122.4, 40, offset=0.0, seed=5), b, 120)  # 2% off
    assert tight["wander_ms"] < 25
    assert drifting["wander_ms"] > 100


def test_candidate_rates_stay_in_range():
    rates = sync.candidate_rates([171.5, 86.1, 114.0], [83.0, 112.3])
    assert 1.0 in rates
    assert all(sync.MIN_RATE <= r <= sync.MAX_RATE for r in rates)
