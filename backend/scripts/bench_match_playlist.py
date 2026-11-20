"""Time fuzzy.match_playlist against the old nested-loop version on synthetic data.

    cd backend && python -m scripts.bench_match_playlist [playlist_size ...]

The local library is twice the playlist size. Prints wall time, matches found and
the total match score for both implementations.
"""

from __future__ import annotations

import random
import sys
import time

from app import fuzzy

WORDS = ("love night fire heart dance dream city gold blue rain star time run baby "
         "wild light road ghost summer lost").split()
ARTISTS = ["Daft Punk", "Queen", "ABBA", "Muse", "Adele", "Beyonce", "Coldplay",
           "Drake", "Blur", "Oasis", "Bjork", "Air", "Moby"]


def match_playlist_nested(playlist, local):
    """The previous implementation: score every pair in Python, then assign greedily."""
    n = fuzzy.normalize
    local_norm = [(lt["id"], n(lt.get("title") or ""), n(lt.get("artist") or "")) for lt in local]
    candidates = []
    for pos, pt in enumerate(playlist):
        pt_title = n(pt["title"])
        joined, each = fuzzy._normalize_artists(pt["artists"])
        for tid, lt_title, lt_artist in local_norm:
            s = fuzzy._combine(fuzzy._title_score_normalized(pt_title, lt_title),
                               fuzzy._artist_score_normalized(joined, each, lt_artist))
            if s >= fuzzy.MATCH_THRESHOLD:
                candidates.append((s, pos, tid))
    candidates.sort(reverse=True)
    taken, used = {}, set()
    for s, pos, tid in candidates:
        if pos in taken or tid in used:
            continue
        taken[pos] = (tid, round(s, 1))
        used.add(tid)
    return [fuzzy.Match(p, *taken.get(p, (None, None))) for p in range(len(playlist))]


def make_data(n: int, rng: random.Random):
    def title():
        return " ".join(rng.choices(WORDS, k=rng.randint(1, 4))).title()

    playlist = [{"title": title(), "artists": rng.sample(ARTISTS, rng.randint(1, 2))}
                for _ in range(n)]
    local = [{"id": i, "title": p["title"] if rng.random() < 0.5 else title(),
              "artist": rng.choice(ARTISTS + [""])}
             for i, p in enumerate(rng.choices(playlist, k=2 * n))]
    return playlist, local


def make_sparse_data(n: int):
    """n distinct songs that each match only their own local file (many tiny components)."""
    playlist = [{"title": f"Song {i:05d} xq{i * 7919 % 100003}", "artists": [f"Artist{i:05d}"]}
                for i in range(n)]
    local = [{"id": i, "title": p["title"], "artist": p["artists"][0]}
             for i, p in enumerate(playlist)]
    return playlist, local


def timed(fn, *args):
    t0 = time.perf_counter()
    out = fn(*args)
    return out, time.perf_counter() - t0


def main(sizes: list[int]) -> None:
    rng = random.Random(1)
    print(f"{'playlist':>8} {'library':>8} {'impl':>7} {'seconds':>8} {'matched':>8} {'score sum':>10}")
    for n in sizes:
        playlist, local = make_data(n, rng)
        for name, fn in (("nested", match_playlist_nested), ("cdist", fuzzy.match_playlist)):
            res, secs = timed(fn, playlist, local)
            matched = sum(m.track_id is not None for m in res)
            total = sum(m.score or 0 for m in res)
            print(f"{n:>8} {len(local):>8} {name:>7} {secs:>8.2f} {matched:>8} {total:>10.1f}")
        playlist, local = make_sparse_data(n)
        res, secs = timed(fuzzy.match_playlist, playlist, local)
        matched = sum(m.track_id is not None for m in res)
        total = sum(m.score or 0 for m in res)
        print(f"{n:>8} {len(local):>8} {'sparse':>7} {secs:>8.2f} {matched:>8} {total:>10.1f}")


if __name__ == "__main__":
    main([int(a) for a in sys.argv[1:]] or [200, 1000])
