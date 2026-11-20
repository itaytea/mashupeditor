"""Fuzzy matching of Spotify playlist tracks to local files by title/artist."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

import numpy as np
from rapidfuzz import fuzz, process

from scipy.optimize import linear_sum_assignment
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

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


_CHUNK = 256  # playlist rows scored per cdist call, to bound memory


def _candidate_scores(playlist: list[dict], local_norm: list[tuple[int, str, str]]):
    """Yield (rows, cols, scores) for every pair scoring >= MATCH_THRESHOLD, computed
    with rapidfuzz.process.cdist a chunk of playlist rows at a time. Same formula
    as _combine over _title_score_normalized / _artist_score_normalized."""
    lt_titles = [t for _, t, _ in local_norm]
    lt_artists = [a for _, _, a in local_norm]
    lt_title_ok = np.array([bool(t) for t in lt_titles])
    lt_artist_ok = np.array([bool(a) for a in lt_artists])
    for start in range(0, len(playlist), _CHUNK):
        chunk = playlist[start:start + _CHUNK]
        titles = [normalize(pt["title"]) for pt in chunk]
        # Per row: the joined artist string first, then each artist.
        flat: list[str] = []
        offsets = []
        for pt in chunk:
            joined, each = _normalize_artists(pt["artists"])
            offsets.append(len(flat))
            flat.extend([joined, *each])
        kw = {"dtype": np.float32, "workers": -1}
        title = np.maximum(
            process.cdist(titles, lt_titles, scorer=fuzz.token_sort_ratio, **kw),
            process.cdist(titles, lt_titles, scorer=fuzz.ratio, **kw),
        ).astype(np.float64)
        title[~np.array([bool(t) for t in titles])[:, None] | ~lt_title_ok[None, :]] = 0.0
        artist = np.maximum.reduceat(
            process.cdist(flat, lt_artists, scorer=fuzz.token_set_ratio, **kw),
            offsets, axis=0,
        ).astype(np.float64)
        score = np.where(
            lt_artist_ok[None, :],
            np.where(artist < ARTIST_MIN, 0.0, 0.7 * title + 0.3 * artist),
            title * 0.9,  # B has no artist: can't confirm it
        )
        rows, cols = np.nonzero(score >= MATCH_THRESHOLD)
        yield rows + start, cols, score[rows, cols]


def _assign(rows: np.ndarray, cols: np.ndarray, scores: np.ndarray) -> list[tuple[int, int, float]]:
    """One-to-one assignment of candidate pairs maximizing the total score.

    The candidate pairs form a bipartite graph. It is split into connected
    components and each one is solved on its own with scipy's
    linear_sum_assignment, so memory and time scale with the largest component
    rather than with (rows with a candidate) x (columns with a candidate)."""
    if not len(scores):
        return []
    # Only rows/columns that have a candidate matter.
    ur, ri = np.unique(rows, return_inverse=True)
    uc, ci = np.unique(cols, return_inverse=True)
    nr, nc = len(ur), len(uc)
    graph = coo_matrix((np.ones(len(scores), dtype=np.int8), (ri, nr + ci)),
                       shape=(nr + nc, nr + nc))
    n_comp, labels = connected_components(graph, directed=False)
    pair_comp = labels[ri]
    order = np.argsort(pair_comp, kind="stable")
    bounds = np.searchsorted(pair_comp[order], np.arange(n_comp + 1))
    out: list[tuple[int, int, float]] = []
    for k in range(n_comp):
        idx = order[bounds[k]:bounds[k + 1]]
        if len(idx) == 1:
            if scores[idx[0]] >= MATCH_THRESHOLD:
                out.append((int(rows[idx[0]]), int(cols[idx[0]]), float(scores[idx[0]])))
            continue
        cr, cri = np.unique(ri[idx], return_inverse=True)
        cc, cci = np.unique(ci[idx], return_inverse=True)
        cost = np.zeros((len(cr), len(cc)))
        cost[cri, cci] = scores[idx]
        r_idx, c_idx = linear_sum_assignment(cost, maximize=True)
        out.extend((int(ur[cr[r]]), int(uc[cc[c]]), float(cost[r, c]))
                   for r, c in zip(r_idx, c_idx) if cost[r, c] >= MATCH_THRESHOLD)
    return out


def match_playlist(playlist: list[dict], local: list[dict]) -> list[Match]:
    """Match each playlist entry ({title, artists}) to at most one local track
    ({id, title, artist}). Every pair is scored in bulk with rapidfuzz's cdist;
    pairs at or above MATCH_THRESHOLD are then assigned one-to-one so a local
    file is never claimed by two playlist rows, maximizing the total score."""
    if not playlist or not local:
        return [Match(pos, None, None) for pos in range(len(playlist))]
    local_norm = [
        (lt["id"], normalize(lt.get("title") or ""), normalize(lt.get("artist") or ""))
        for lt in local
    ]
    parts = list(_candidate_scores(playlist, local_norm))
    rows = np.concatenate([p[0] for p in parts])
    cols = np.concatenate([p[1] for p in parts])
    scores = np.concatenate([p[2] for p in parts])
    taken = {pos: (local_norm[c][0], round(s, 1)) for pos, c, s in _assign(rows, cols, scores)}
    return [Match(pos, *taken.get(pos, (None, None))) for pos in range(len(playlist))]
