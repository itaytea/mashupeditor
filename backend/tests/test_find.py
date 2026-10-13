"""FIND folder: filename cleanup, matching only wanted tracks, the watcher."""

import numpy as np
import pytest
import soundfile as sf

from app import analysis, config, db, settings
from app.analysis import clean_title, find_in_folder, read_metadata
from app.watcher import FolderWatcher


@pytest.mark.parametrize(
    "raw, clean",
    [
        ("Blinding Lights (Official Video)", "Blinding Lights"),
        ("Electric Shock [Official Audio] [dQw4w9WgXcQ]", "Electric Shock"),
        ("Diva (Lyrics) HD", "Diva"),
        ("Stereo Love (Official Music Video)", "Stereo Love"),
        ("Hello - Lyric Video", "Hello"),
        ("Illegal (Official Audio)(1)", "Illegal"),
        ("Like a taxi (HQ instrumental)(1)", "Like a taxi (HQ instrumental)"),
        # real titles that only look noisy stay intact
        ("Video Killed the Radio Star", "Video Killed the Radio Star"),
        ("Audio (Remix)", "Audio (Remix)"),
        ("Around the World (Radio Edit)", "Around the World (Radio Edit)"),
    ],
)
def test_clean_title(raw, clean):
    assert clean_title(raw) == clean


def test_downloaded_filename_parses(tmp_path):
    p = tmp_path / "Deee-Lite - Electric Shock (Official Video) [a1B2c3D4e5F].mp3"
    p.write_bytes(b"")
    assert read_metadata(p) == ("Electric Shock", "Deee-Lite")


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "STEMS_DIR", tmp_path / "data" / "stems")
    monkeypatch.setattr(config, "DRAFTS_DIR", tmp_path / "data" / "drafts")
    db.init(tmp_path / "test.sqlite3")
    yield
    db._conn.close()
    db._conn = None


def tone(path, seconds=6.0, sr=22050):
    t = np.arange(int(seconds * sr)) / sr
    y = 0.2 * np.sin(2 * np.pi * 220 * t)
    y[:: sr // 2] += 0.9  # clicks so the beat tracker has something
    sf.write(path, y.astype(np.float32), sr)


@pytest.mark.slow
def test_find_in_folder_only_analyses_wanted_tracks(tmp_path, temp_db):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    tone(downloads / "Deee-Lite - Electric Shock (Official Video) [a1B2c3D4e5F].wav")
    tone(downloads / "Some Podcast Episode 12.wav")
    tone(downloads / "Model Actriz - Diva.wav")
    (downloads / "notes.txt").write_text("not audio")

    wanted = [
        {"title": "Electric Shock", "artists": ["Deee-Lite"]},
        {"title": "Diva", "artists": ["Model/Actriz"]},
        {"title": "Not Downloaded", "artists": ["Nobody"]},
    ]
    stats = find_in_folder(downloads, wanted)
    assert stats["files"] == 3
    assert stats["matched"] == 2
    assert stats["analyzed"] == 2
    analysed = {t["title"] for t in db.list_tracks()}
    assert analysed == {"Electric Shock", "Diva"}  # the podcast was never analysed
    assert len(stats["checked"]) == 3

    again = find_in_folder(downloads, wanted)  # second pass hits the cache
    assert again["cached"] == 2 and again["analyzed"] == 0


def test_watcher_only_reports_new_or_changed_files(tmp_path, temp_db, monkeypatch):
    folder = tmp_path / "inbox"
    folder.mkdir()
    a = folder / "a.mp3"
    a.write_bytes(b"x")
    settings.update(find_dir=str(folder), watch=True)

    started = []
    w = FolderWatcher(lambda f, files: started.append([p.name for p in files]) or "job")
    assert w.tick() == "job" and started[-1] == ["a.mp3"]

    w.mark_checked([str(a)])
    assert w.tick() is None  # nothing new

    b = folder / "b.mp3"
    b.write_bytes(b"y")
    assert w.tick() == "job" and started[-1] == ["b.mp3"]

    settings.update(watch=False)
    (folder / "c.mp3").write_bytes(b"z")
    assert w.tick() is None  # watching switched off


def test_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    assert settings.load()["watch"] is False
    settings.update(find_dir="/tmp/x", watch=True, bogus=1)
    s = settings.load()
    assert s["find_dir"] == "/tmp/x" and s["watch"] is True and "bogus" not in s
