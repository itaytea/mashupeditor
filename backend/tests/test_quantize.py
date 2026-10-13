"""Quantizing a loose (drifting) performance onto a steady beat grid."""

import shutil

import numpy as np
import pytest

from app import render, structure, sync
from tests.test_sync import clicks

SR = sync.SR
pytestmark = pytest.mark.skipif(shutil.which("rubberband") is None, reason="needs rubberband")


def test_beat_map_rejects_glitchy_beats():
    src = np.arange(0, 20, 0.5)
    dst = np.arange(0, 20, 0.5)
    assert render.beat_map(src, dst, 0.0, 0.0, int(21 * SR), SR) is not None
    glitch = dst.copy()
    glitch[10:] += 0.4  # a beat jumps by most of a beat: don't warp through that
    assert render.beat_map(src, glitch, 0.0, 0.0, int(21 * SR), SR) is None


def test_quantize_locks_a_drifting_take_to_a_steady_grid():
    seconds = 40
    a = clicks(120, seconds + 4, drift_to=126, seed=7)  # speeds up: a "loose" take
    b = clicks(123, seconds, seed=8)                       # steady reference grid

    src_beats = structure.track_beats(a, 123)
    dst_beats = structure.track_beats(b, 123)
    pre = min(0.2, float(src_beats[0]), float(dst_beats[0]))  # never cut before 0
    start = src_beats[0] - pre
    seg = a[int(start * SR):][None, :]
    tmap = render.beat_map(src_beats, dst_beats, start, pre, seg.shape[1], SR)
    assert tmap is not None
    warped = render.quantize_stretch(seg, SR, tmap, 0)[0]
    out = render.place(warped[None, :], int((dst_beats[0] - pre) * SR), b.size)[0]

    # Baseline: one constant stretch for the whole section (what loose songs got before)
    const = render.stretch_and_shift(seg, SR, 123 / 123.0, 0)[0]
    const = render.place(const[None, :], int((dst_beats[0] - pre) * SR), b.size)[0]

    q = sync.drift_report(out, b, 123)
    c = sync.drift_report(const, b, 123)
    assert q["wander_ms"] < 25, q
    assert c["wander_ms"] > 3 * q["wander_ms"], (c, q)


def test_beat_map_refuses_different_beat_levels():
    src = np.arange(0, 30, 0.383)   # A counted at ~157 BPM
    dst = np.arange(0, 30, 0.337)   # B counted at ~178 BPM
    # the lock said A only needs ~2.5% speed-up, so these lists disagree
    assert render.beat_map(src, dst, 0.0, 0.0, int(31 * SR), SR, expected=1.025) is None
    assert render.beat_map(src, dst, 0.0, 0.0, int(31 * SR), SR, expected=0.383 / 0.337) is not None
