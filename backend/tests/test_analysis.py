"""Analysis checks on synthetic audio (no real music needed)."""

import numpy as np
import pytest

from app.analysis import MAJOR_PROFILE, MINOR_PROFILE, analyze_signal, estimate_key, fold_bpm

SR = 22050


def test_estimate_key_from_profiles():
    for pc in range(12):
        k = estimate_key(np.roll(MAJOR_PROFILE, pc))
        assert (k.pitch_class, k.mode) == (pc, "major")
        k = estimate_key(np.roll(MINOR_PROFILE, pc))
        assert (k.pitch_class, k.mode) == (pc, "minor")


def test_fold_bpm():
    assert fold_bpm(60) == 120
    assert fold_bpm(200) == 100
    assert fold_bpm(128) == 128


def synth(bpm: float, chord_midi: list[int], seconds: float = 20.0) -> np.ndarray:
    """Sustained chord plus a kick-like click on every beat."""
    t = np.arange(int(seconds * SR)) / SR
    y = np.zeros_like(t)
    for m in chord_midi:
        f = 440.0 * 2 ** ((m - 69) / 12)
        y += 0.15 * np.sin(2 * np.pi * f * t)
    beat = 60.0 / bpm
    click_len = int(0.03 * SR)
    env = np.exp(-np.linspace(0, 8, click_len))
    noise = np.random.default_rng(0).standard_normal(click_len)
    for start in np.arange(0, seconds, beat):
        i = int(start * SR)
        n = min(click_len, len(y) - i)
        y[i:i + n] += 0.8 * env[:n] * noise[:n]
    return y.astype(np.float32)


@pytest.mark.slow
def test_analyze_signal_tempo_and_key():
    # C major triad (C4 E4 G4) at 120 BPM.
    res = analyze_signal(synth(120, [60, 64, 67]), SR)
    assert res["bpm"] == pytest.approx(120, rel=0.03)
    assert res["key_name"] == "C"
    assert res["camelot"] == "8B"

    # A minor triad (A3 C4 E4) at 96 BPM.
    res = analyze_signal(synth(96, [57, 60, 64]), SR)
    assert res["bpm"] == pytest.approx(96, rel=0.03)
    assert res["key_name"] == "Am"
    assert res["camelot"] == "8A"
