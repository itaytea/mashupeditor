"""BPM and key detection with librosa, cached in SQLite."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from . import camelot, config, db

log = logging.getLogger(__name__)

ANALYSIS_SR = 22050
# Analysing the first few minutes is enough for tempo and key, and keeps long
# DJ mixes from taking forever.
MAX_ANALYSIS_SECONDS = 240

# Krumhansl-Kessler key profiles, index 0 = tonic.
MAJOR_PROFILE = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
MINOR_PROFILE = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)


@dataclass
class KeyEstimate:
    pitch_class: int
    mode: str
    confidence: float  # margin between the best and second-best correlation


def estimate_key(chroma_mean: np.ndarray) -> KeyEstimate:
    """Correlate a 12-bin mean chroma vector against all 24 rotated profiles."""
    scores: list[tuple[float, int, str]] = []
    for pc in range(12):
        for mode, profile in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
            r = np.corrcoef(chroma_mean, np.roll(profile, pc))[0, 1]
            scores.append((float(r), pc, mode))
    scores.sort(reverse=True)
    (best, pc, mode), (second, _, _) = scores[0], scores[1]
    return KeyEstimate(pc, mode, round(best - second, 4))


def fold_bpm(bpm: float, low: float = 70.0, high: float = 180.0) -> float:
    """Bring a tempo estimate into a typical range (beat trackers often land on
    half or double the felt tempo)."""
    if bpm <= 0:
        return bpm
    while bpm < low:
        bpm *= 2
    while bpm >= high:
        bpm /= 2
    return bpm


def analyze_signal(y: np.ndarray, sr: int) -> dict:
    import librosa

    harmonic, percussive = librosa.effects.hpss(y)

    onset_env = librosa.onset.onset_strength(y=percussive, sr=sr)
    tempo, _ = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
    bpm = fold_bpm(float(np.atleast_1d(tempo)[0]))

    chroma = librosa.feature.chroma_cqt(y=harmonic, sr=sr)
    key = estimate_key(chroma.mean(axis=1))
    code = camelot.to_camelot(key.pitch_class, key.mode)

    return {
        "bpm": round(bpm, 2),
        "key_pc": key.pitch_class,
        "mode": key.mode,
        "key_name": camelot.key_name(key.pitch_class, key.mode),
        "camelot": str(code),
        "key_confidence": key.confidence,
    }


def analyze_file(path: Path) -> dict:
    import librosa

    y, sr = librosa.load(path, sr=ANALYSIS_SR, mono=True, duration=MAX_ANALYSIS_SECONDS)
    if y.size < sr * 5:
        raise ValueError("audio is shorter than 5 seconds")
    result = analyze_signal(y, sr)
    result["duration"] = round(float(librosa.get_duration(path=path)), 2)
    return result


# --- metadata ---------------------------------------------------------------

_FILENAME_SPLIT = re.compile(r"\s+[-–—]\s+")
_TRACK_NUMBER = re.compile(r"^\d{1,3}[\s.\-_]+")


def read_metadata(path: Path) -> tuple[str, str]:
    """Title and artist from tags, falling back to "Artist - Title" filenames."""
    title = artist = ""
    try:
        import mutagen

        f = mutagen.File(path, easy=True)
        if f is not None and f.tags is not None:
            title = (f.tags.get("title") or [""])[0]
            artist = (f.tags.get("artist") or f.tags.get("albumartist") or [""])[0]
    except Exception:  # unreadable tags are not fatal
        log.debug("could not read tags from %s", path, exc_info=True)

    if not title:
        stem = _TRACK_NUMBER.sub("", path.stem.replace("_", " ")).strip()
        parts = _FILENAME_SPLIT.split(stem, maxsplit=1)
        if len(parts) == 2:
            artist = artist or parts[0].strip()
            title = parts[1].strip()
        else:
            title = stem
    return title, artist


# --- folder scan ------------------------------------------------------------

def find_audio_files(folder: Path) -> list[Path]:
    return sorted(
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in config.AUDIO_EXTENSIONS
        and not p.name.startswith("._")
    )


ProgressFn = Callable[[float, str], None]


def scan_folder(folder: Path, progress: ProgressFn | None = None) -> dict:
    """Analyse every audio file in `folder`, reusing cached results for files
    whose path, size and mtime are unchanged."""
    folder = folder.expanduser().resolve()
    if not folder.is_dir():
        raise FileNotFoundError(f"not a folder: {folder}")

    files = find_audio_files(folder)
    stats = {"total": len(files), "cached": 0, "analyzed": 0, "failed": 0}
    for i, path in enumerate(files):
        if progress:
            progress(i / max(len(files), 1), f"{i + 1}/{len(files)}: {path.name}")
        st = path.stat()
        if db.get_cached_track(str(path), st.st_mtime, st.st_size):
            stats["cached"] += 1
            continue

        title, artist = read_metadata(path)
        rec: dict = {
            "path": str(path), "mtime": st.st_mtime, "size": st.st_size,
            "title": title, "artist": artist, "analyzed_at": time.time(),
            "error": None,
        }
        try:
            rec.update(analyze_file(path))
            stats["analyzed"] += 1
        except Exception as e:  # keep going; record the failure
            log.warning("analysis failed for %s: %s", path, e)
            rec["error"] = str(e)[:500]
            stats["failed"] += 1
        db.upsert_track(rec)

    if progress:
        progress(1.0, f"Done: {stats['analyzed']} analysed, {stats['cached']} cached, "
                      f"{stats['failed']} failed")
    return stats
