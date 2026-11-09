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

    monkeypatch.setattr(settings.json, "dump", boom)
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
