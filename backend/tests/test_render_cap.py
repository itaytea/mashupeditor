import pytest

from app import render, stems


def _track(title, duration):
    return {"path": f"/nonexistent/{title}.wav", "title": title, "duration": duration, "bpm": 120.0}


def test_overlong_track_fails_before_stem_separation(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("stems.separate must not be called")

    monkeypatch.setattr(stems, "separate", boom)
    with pytest.raises(ValueError, match=r"Track too long for draft \(>15 min\): Long One"):
        render.render_draft(_track("Short", 200.0), _track("Long One", render.MAX_TRACK_SECONDS + 1))


def test_overlong_first_track_also_rejected(monkeypatch):
    monkeypatch.setattr(stems, "separate", lambda *a, **k: pytest.fail("called"))
    with pytest.raises(ValueError, match="Long A"):
        render.render_draft(_track("Long A", 3600.0), _track("Short", 200.0))


def test_missing_duration_read_from_file(monkeypatch, tmp_path):
    import numpy as np
    import soundfile as sf

    wav = tmp_path / "t.wav"
    sf.write(wav, np.zeros(8000, dtype="float32"), 8000)
    called = []
    monkeypatch.setattr(stems, "separate", lambda *a, **k: called.append(1) or (_ for _ in ()).throw(RuntimeError("reached")))
    track = {"path": str(wav), "title": "ok", "duration": None, "bpm": 120.0}
    with pytest.raises(RuntimeError, match="reached"):
        render.render_draft(track, track)
    assert called
