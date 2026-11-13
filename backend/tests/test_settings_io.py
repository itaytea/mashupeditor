"""Atomic settings writes and private Spotify token file."""

import json
import os
import stat
import sys

import pytest

from app import config, settings, spotify


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    return tmp_path / "data"


def test_settings_round_trip(data_dir):
    settings.update(find_dir="/tmp/x", watch=True)
    loaded = settings.load()
    assert loaded["find_dir"] == "/tmp/x" and loaded["watch"] is True
    assert json.loads((data_dir / "settings.json").read_text())["watch"] is True


def test_failed_write_leaves_no_partial_file(data_dir, monkeypatch):
    settings.update(find_dir="/tmp/before")
    good = (data_dir / "settings.json").read_text()

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(settings.json, "dumps", boom)
    with pytest.raises(RuntimeError):
        settings.update(find_dir="/tmp/after")
    assert (data_dir / "settings.json").read_text() == good
    assert [p.name for p in data_dir.iterdir()] == ["settings.json"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_token_file_is_private(data_dir):
    spotify._save_user_token({"access_token": "secret"})
    path = data_dir / "spotify_token.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert json.loads(path.read_text())["access_token"] == "secret"


def test_atomic_write_text_replaces_and_sets_mode(tmp_path):
    from app.fsutil import atomic_write_text

    target = tmp_path / "f.txt"
    target.write_text("old")
    os.chmod(target, 0o644)
    atomic_write_text(target, "new", mode=0o600)
    assert target.read_text() == "new"
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["f.txt"]


def test_atomic_write_text_failure_keeps_original(tmp_path, monkeypatch):
    from app import fsutil

    target = tmp_path / "f.txt"
    target.write_text("keep")

    def boom(*a, **k):
        raise OSError("replace failed")

    monkeypatch.setattr(fsutil.os, "replace", boom)
    with pytest.raises(OSError):
        fsutil.atomic_write_text(target, "lost")
    assert target.read_text() == "keep"
    assert [p.name for p in tmp_path.iterdir()] == ["f.txt"]


def test_concurrent_updates_do_not_lose_changes(data_dir):
    import threading

    keys = ["find_dir", "music_dir"]
    barrier = threading.Barrier(2)

    def work(key):
        barrier.wait()
        for i in range(30):
            settings.update(**{key: f"/v/{key}/{i}"})

    ts = [threading.Thread(target=work, args=(k,)) for k in keys]
    [t.start() for t in ts]
    [t.join() for t in ts]
    final = settings.load()
    assert final["find_dir"] == "/v/find_dir/29" and final["music_dir"] == "/v/music_dir/29"
