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
_PUNCT = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
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


def title_score(title_a: str, title_b: str) -> float:
    ta, tb = normalize(title_a), normalize(title_b)
    if not ta or not tb:
        return 0.0
    return max(fuzz.token_sort_ratio(ta, tb), fuzz.ratio(ta, tb))


def artist_score(artists_a: list[str], artist_b: str) -> float | None:
    """0..100, or None when B has no artist to compare."""
    ab = normalize(artist_b)
    if not ab:
        return None
    joined = normalize(" ".join(artists_a))
    return max(
        [fuzz.token_set_ratio(joined, ab)]
        + [fuzz.token_set_ratio(normalize(a), ab) for a in artists_a]
    )


def title_artist_score(
    title_a: str, artists_a: list[str], title_b: str, artist_b: str
) -> float:
    """0..100 similarity. Title dominates; the artist only counts when B has
    one (filenames often lack it), but a clearly different artist is a veto."""
    title = title_score(title_a, title_b)
    artist = artist_score(artists_a, artist_b)
    if artist is None:
        return title * 0.9  # can't confirm the artist; slightly less sure
    if artist < ARTIST_MIN:
        return 0.0
    return 0.7 * title + 0.3 * artist


@dataclass
class Match:
    position: int
    track_id: int | None
    score: float | None


def match_playlist(playlist: list[dict], local: list[dict]) -> list[Match]:
    """Match each playlist entry ({title, artists}) to at most one local track
    ({id, title, artist}). Pairs are assigned greedily from highest score so a
    local file is never claimed by two playlist rows."""
    candidates: list[tuple[float, int, int]] = []
    for pos, pt in enumerate(playlist):
        for lt in local:
            s = title_artist_score(
                pt["title"], pt["artists"], lt.get("title") or "", lt.get("artist") or ""
            )
            if s >= MATCH_THRESHOLD:
                candidates.append((s, pos, lt["id"]))

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
