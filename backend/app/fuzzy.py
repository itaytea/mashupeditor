"""Fuzzy matching of Spotify playlist tracks to local files by title/artist."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

MATCH_THRESHOLD = 75.0

# Suffixes that differ between streaming metadata and local tags without
# changing which song it is.
_NOISE = [
    r"\((?:feat|ft|featuring|with)\.?[^)]*\)",
    r"\[(?:feat|ft|featuring|with)\.?[^\]]*\]",
    r"\s(?:feat|ft|featuring)\.?\s.*$",
    r"\s-\s.*\b(?:remaster(?:ed)?|version|edit|mono|stereo|live|mix)\b.*$",
    r"\((?:[^)]*\b(?:remaster(?:ed)?|radio edit|album version|single version|mono|stereo)\b[^)]*)\)",
    r"\[(?:[^\]]*\b(?:remaster(?:ed)?|radio edit|album version|single version|mono|stereo)\b[^\]]*)\]",
]
_NOISE_RE = [re.compile(p, re.IGNORECASE) for p in _NOISE]
# Copies made by browsers/Finder/Windows: "Song(1)", "Song (2)", "Song copy",
# "Song - Copy". Stripped so a duplicate download is the same song.
_COPY_MARKER = re.compile(r"(?:\s?\(\d{1,2}\)|\s+copy(?:\s+\d{1,2})?|\s+-\s+copy)\s*$", re.IGNORECASE)
_PUNCT = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")


def strip_copy_marker(text: str) -> str:
    prev = None
    while prev != text:  # "Song(1)(1)"
        prev, text = text, _COPY_MARKER.sub("", text).rstrip()
    return text


def normalize(text: str) -> str:
    text = strip_copy_marker(text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    for rx in _NOISE_RE:
        text = rx.sub(" ", text)
    text = text.lower().replace("&", " and ")
    text = _PUNCT.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


# Below this artist similarity it's a different artist's song with the same
# name ("Diva" by Model/Actriz is not "DIVA" by ART-SCHOOL).
ARTIST_MIN = 60.0


def _title_score_normalized(ta: str, tb: str) -> float:
    if not ta or not tb:
        return 0.0
    return max(fuzz.token_sort_ratio(ta, tb), fuzz.ratio(ta, tb))


def title_score(title_a: str, title_b: str) -> float:
    return _title_score_normalized(normalize(title_a), normalize(title_b))


def _artist_score_normalized(joined: str, each: list[str], ab: str) -> float | None:
    """Like artist_score, with the artists_a side and artist_b already normalized."""
    if not ab:
        return None
    return max([fuzz.token_set_ratio(joined, ab)] + [fuzz.token_set_ratio(a, ab) for a in each])


def _normalize_artists(artists_a: list[str]) -> tuple[str, list[str]]:
    return normalize(" ".join(artists_a)), [normalize(a) for a in artists_a]


def artist_score(artists_a: list[str], artist_b: str) -> float | None:
    """0..100, or None when B has no artist to compare."""
    ab = normalize(artist_b)
    if not ab:
        return None
    joined, each = _normalize_artists(artists_a)
    return _artist_score_normalized(joined, each, ab)


def _combine(title: float, artist: float | None) -> float:
    if artist is None:
        return title * 0.9  # can't confirm the artist; slightly less sure
    if artist < ARTIST_MIN:
        return 0.0
    return 0.7 * title + 0.3 * artist


def title_artist_score(
    title_a: str, artists_a: list[str], title_b: str, artist_b: str
) -> float:
    """0..100 similarity. Title dominates; the artist only counts when B has
    one (filenames often lack it), but a clearly different artist is a veto."""
    return _combine(title_score(title_a, title_b), artist_score(artists_a, artist_b))


@dataclass
class Match:
    position: int
    track_id: int | None
    score: float | None


def match_playlist(playlist: list[dict], local: list[dict]) -> list[Match]:
    """Match each playlist entry ({title, artists}) to at most one local track
    ({id, title, artist}). Pairs are assigned greedily from highest score so a
    local file is never claimed by two playlist rows."""
    # Normalize each side once instead of once per pair.
    local_norm = [
        (lt["id"], normalize(lt.get("title") or ""), normalize(lt.get("artist") or ""))
        for lt in local
    ]
    candidates: list[tuple[float, int, int]] = []
    for pos, pt in enumerate(playlist):
        pt_title = normalize(pt["title"])
        joined, each = _normalize_artists(pt["artists"])
        for tid, lt_title, lt_artist in local_norm:
            s = _combine(
                _title_score_normalized(pt_title, lt_title),
                _artist_score_normalized(joined, each, lt_artist),
            )
            if s >= MATCH_THRESHOLD:
                candidates.append((s, pos, tid))

    candidates.sort(reverse=True)
    taken_pos: dict[int, tuple[int, float]] = {}
    taken_local: set[int] = set()
    for s, pos, tid in candidates:
        if pos in taken_pos or tid in taken_local:
            continue
        taken_pos[pos] = (tid, round(s, 1))
        taken_local.add(tid)

    return [
        Match(pos, *taken_pos.get(pos, (None, None))) for pos in range(len(playlist))
    ]
