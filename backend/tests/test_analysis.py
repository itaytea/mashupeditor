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


# --- scan_folder ------------------------------------------------------------

def _scan_setup(tmp_path, monkeypatch, temp_db):
    from app import analysis, db

    for n in ("a", "b", "c", "bad"):
        (tmp_path / f"{n}.mp3").write_bytes(b"x")
    monkeypatch.setattr(analysis, "read_metadata", lambda p: (p.stem, None))

    def fake_analyze(path):
        if path.stem == "bad":
            raise RuntimeError("undecodable")
        return {"bpm": 120.0}

    monkeypatch.setattr(analysis, "analyze_file", fake_analyze)
    return analysis, db


@pytest.mark.parametrize("pooled", [False, True])
def test_scan_folder_stats_and_parent_only_writes(tmp_path, monkeypatch, temp_db, pooled):
    import os
    import threading
    from concurrent.futures import ThreadPoolExecutor

    analysis, db = _scan_setup(tmp_path, monkeypatch, temp_db)
    writers = []
    real_upsert = db.upsert_track
    monkeypatch.setattr(
        db, "upsert_track",
        lambda rec: (writers.append((os.getpid(), threading.current_thread())),
                     real_upsert(rec))[1],
    )
    if pooled:
        # Stand-in for the process pool: workers run on other threads.
        monkeypatch.setattr(
            analysis, "ProcessPoolExecutor",
            lambda max_workers, mp_context: ThreadPoolExecutor(max_workers),
        )
    calls = []
    stats = analysis.scan_folder(
        tmp_path, lambda f, m: calls.append(m), workers=2 if pooled else 1
    )
    assert stats == {"total": 4, "cached": 0, "analyzed": 3, "failed": 1}
    assert len(writers) == 4
    assert {w[1] for w in writers} == {threading.current_thread()}  # caller only
    assert calls[-1].startswith("Done: 3 analysed")

    # Second scan: good files cached, the recorded failure is not retried.
    writers.clear()
    stats = analysis.scan_folder(tmp_path, workers=1)
    assert stats == {"total": 4, "cached": 3, "analyzed": 0, "failed": 1}
    assert writers == []


def test_scan_folder_vanished_file_counts_failed(tmp_path, monkeypatch, temp_db):
    analysis, db = _scan_setup(tmp_path, monkeypatch, temp_db)
    monkeypatch.setattr(
        analysis, "find_audio_files",
        lambda folder: [tmp_path / "a.mp3", tmp_path / "gone.mp3"],
    )
    stats = analysis.scan_folder(tmp_path, workers=1)
    assert stats == {"total": 2, "cached": 0, "analyzed": 1, "failed": 1}
    assert db.get_cached_track(str(tmp_path / "gone.mp3"), 0, 0) is None


def test_scan_workers_env(monkeypatch):
    from app.analysis import scan_workers

    monkeypatch.setenv("MASHUP_SCAN_WORKERS", "4")
    assert scan_workers() == 4
    assert scan_workers(2) == 2
    monkeypatch.setenv("MASHUP_SCAN_WORKERS", "0")
    assert scan_workers() == 1
    monkeypatch.setenv("MASHUP_SCAN_WORKERS", "junk")
    assert scan_workers() >= 1
