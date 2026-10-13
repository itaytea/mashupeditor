"""Downbeats, 8-bar phrases and section labels on a synthetic dance track."""

import numpy as np
import pytest

from app import structure

SR = structure.SR
BPM = 128.0
BEAT = 60.0 / BPM
PHRASE = BEAT * 4 * 8


def note(f, n, amp):
    t = np.arange(n) / SR
    return amp * np.sin(2 * np.pi * f * t)


def synth_track(layout, pickup_beats=1, seed=0):
    """layout: list of section names, one per 8-bar phrase. Audio starts
    `pickup_beats` before the first downbeat, so beat 0 is NOT a downbeat."""
    rng = np.random.default_rng(seed)
    n_beats = len(layout) * 32 + pickup_beats
    y = np.zeros(int((n_beats + 2) * BEAT * SR))
    roots = [57, 53, 60, 55]  # Am F C G, one chord per bar
    for b in range(-pickup_beats, len(layout) * 32):
        t0 = int((b + pickup_beats) * BEAT * SR)
        section = layout[b // 32] if b >= 0 else layout[0]
        bar, beat_in_bar = divmod(b, 4) if b >= 0 else (-1, b % 4)
        down = beat_in_bar == 0
        kick_amp = {"intro": 0.35 if down else 0.0, "build": 0.6, "drop": 1.0 if down else 0.8,
                    "breakdown": 0.0, "outro": 0.3 if down else 0.0}[section]
        n = int(0.09 * SR)
        if kick_amp:
            y[t0:t0 + n] += kick_amp * np.exp(-np.linspace(0, 6, n)) * np.sin(2 * np.pi * 55 * np.arange(n) / SR)
        if section in ("drop", "build"):  # hats
            m = int(0.02 * SR)
            hat_amp = 0.15 if section == "drop" else 0.05 + 0.25 * ((b % 32) / 32)
            y[t0 + int(BEAT * SR / 2):t0 + int(BEAT * SR / 2) + m] += hat_amp * rng.standard_normal(m)
        if down and b >= 0:  # chord pad for the whole bar
            root = roots[bar % 4]
            L = int(4 * BEAT * SR)
            amp = {"intro": 0.08, "build": 0.08, "drop": 0.14, "breakdown": 0.12, "outro": 0.06}[section]
            seg = sum(note(440 * 2 ** ((root + i - 69) / 12), L, amp) for i in (0, 3, 7))
            y[t0:t0 + L] += seg[: max(0, min(L, y.size - t0))]
        if section == "drop" and b >= 0:  # bass on every beat
            n2 = int(BEAT * SR * 0.8)
            y[t0:t0 + n2] += 0.3 * note(440 * 2 ** ((roots[(b // 4) % 4] - 24 - 69) / 12), n2, 1.0)
    return (y / np.abs(y).max() * 0.9).astype(np.float32)


@pytest.fixture(scope="module")
def track():
    layout = ["intro", "intro", "build", "drop", "drop", "breakdown", "build", "drop", "outro"]
    y = synth_track(layout, pickup_beats=1)
    return layout, structure.analyze(y, BPM)


def test_clean_beats_repairs_missed_and_extra():
    beats = np.arange(0, 20, 0.5)
    damaged = np.delete(beats, [7, 15])                  # two missed beats
    damaged = np.sort(np.append(damaged, [3.2, 9.1]))    # two extra detections
    fixed = structure.clean_beats(damaged)
    assert fixed.size == beats.size
    assert np.allclose(fixed, beats, atol=0.06)


def test_tempo_and_downbeats(track):
    _, res = track
    assert res["bpm"] == pytest.approx(BPM, rel=0.01)
    # first downbeat is one beat in (the pickup), so ~BEAT seconds, not 0
    assert res["downbeats"][0] == pytest.approx(BEAT, abs=0.05)
    assert np.allclose(np.diff(res["downbeats"][:16]), 4 * BEAT, atol=0.05)


def test_phrases_start_on_section_boundaries(track):
    layout, res = track
    starts = [p["start"] for p in res["phrases"]]
    expected = [BEAT + i * PHRASE for i in range(len(layout))]
    assert len(starts) >= len(layout) - 1
    assert np.allclose(starts[: len(layout) - 1], expected[: len(layout) - 1], atol=0.08)


def test_section_labels(track):
    layout, res = track
    labels = [p["label"] for p in res["phrases"]]
    # the loud sections are found as drops, and what leads into them as builds
    for i, name in enumerate(layout[: len(labels)]):
        if name == "drop":
            assert labels[i] == "drop", (i, labels)
    assert labels[2] == "build" and labels[6] == "build", labels
    assert labels[0] == "intro", labels
