"""End to end: POST /api/drafts renders a draft from two synthetic tracks.

Stem separation (Demucs) is stubbed with synthetic stem WAVs; everything else
(job queue, tempo/structure analysis, alignment, mixing, WAV export) is real.
"""

import time

import numpy as np
import pytest

pytest.importorskip("librosa")
pytest.importorskip("soundfile")
import soundfile as sf  # noqa: E402

from app import config, db, stems  # noqa: E402
from tests.conftest import make_track  # noqa: E402

SR = 44100
BPM = 120.0
SECONDS = 20


def _click_track(freq: float) -> np.ndarray:
    """Click on every beat plus a sustained tone, 120 BPM, SECONDS long."""
    n = SR * SECONDS
    t = np.arange(n) / SR
    y = 0.15 * np.sin(2 * np.pi * freq * t)
    beat = int(SR * 60 / BPM)
    click = np.hanning(400) * np.sin(2 * np.pi * 1000 * np.arange(400) / SR)
    for i in range(0, n - 400, beat):
        y[i:i + 400] += 0.8 * click
    return y.astype(np.float32)


def _write(path, mono):
    sf.write(path, np.stack([mono, mono], axis=1), SR, subtype="PCM_16")


def _wait_job(job_id, timeout=120.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = db.get_job(job_id)
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} still {db.get_job(job_id)['status']}")


def test_draft_renders_end_to_end(client, tmp_path, monkeypatch):
    ids = []
    stem_files = {}
    for name, freq in (("a", 220.0), ("b", 330.0)):
        audio = tmp_path / f"{name}.wav"
        _write(audio, _click_track(freq))
        ids.append(db.upsert_track(make_track(audio, title=f"Track {name.upper()}",
                                              duration=float(SECONDS), bpm=BPM)))
        vocals, inst = tmp_path / f"{name}_vocals.wav", tmp_path / f"{name}_inst.wav"
        _write(vocals, 0.3 * _click_track(freq * 2))
        _write(inst, _click_track(freq))
        stem_files[str(audio)] = {"vocals": vocals, "instrumental": inst}

    monkeypatch.setattr(stems, "separate",
                        lambda track, progress=None: stem_files[track["path"]])

    resp = client.post("/api/drafts", json={"vocals_id": ids[0], "instrumental_id": ids[1],
                                            "duration": 10, "quantize": "off"})
    assert resp.status_code == 200, resp.text
    job = _wait_job(resp.json()["job_id"])
    assert job["status"] == "done", job.get("error")

    result = job["result"]
    wav = config.DRAFTS_DIR / result["draft_id"] / "draft.wav"
    assert wav.is_file() and wav.stat().st_size > 0
    assert sf.info(wav).duration > 1.0
    assert result["wav"] == "draft.wav"
    assert result["sync"]  # sync rating of the final audio
    assert client.get(f"/api/drafts/{result['draft_id']}/draft.wav").status_code == 200
