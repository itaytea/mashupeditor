import subprocess

import pytest

pytest.importorskip("numpy")
pytest.importorskip("librosa")

from app import render


def _run_raising(exc, write_partial=True):
    def fake(cmd, **kw):
        assert kw.get("timeout") == render.MP3_ENCODE_TIMEOUT_S
        if write_partial:
            open(cmd[-1], "wb").write(b"partial")
        raise exc
    return fake


@pytest.mark.parametrize("exc", [
    subprocess.CalledProcessError(1, "ffmpeg"),
    subprocess.TimeoutExpired("ffmpeg", 120),
    OSError("no ffmpeg"),
])
def test_encode_failure_returns_none_and_removes_partial(tmp_path, monkeypatch, exc):
    monkeypatch.setattr(render.shutil, "which", lambda n: "ffmpeg")
    monkeypatch.setattr(render.subprocess, "run", _run_raising(exc))
    mp3 = tmp_path / "draft.mp3"
    assert render.encode_mp3(tmp_path / "draft.wav", mp3) is None
    assert not mp3.exists()


def test_encode_success(tmp_path, monkeypatch):
    monkeypatch.setattr(render.shutil, "which", lambda n: "ffmpeg")
    monkeypatch.setattr(render.subprocess, "run", lambda cmd, **kw: None)
    mp3 = tmp_path / "draft.mp3"
    assert render.encode_mp3(tmp_path / "draft.wav", mp3) == mp3
