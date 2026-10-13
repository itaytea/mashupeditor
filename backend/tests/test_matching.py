from app.matching import rank_pairs, score_pair

C, G, A, E, B = 0, 7, 9, 4, 11


def track(i, bpm, pc, mode):
    return {"id": i, "bpm": bpm, "key_pc": pc, "mode": mode}


def test_score_pair_combines_key_and_tempo():
    p = score_pair(track(1, 120, A, "minor"), track(2, 120, A, "minor"))
    assert p["score"] == 1.0
    assert len(p["reasons"]) == 2


def test_rank_pairs_orders_best_first():
    tracks = [
        track(1, 120, A, "minor"),
        track(2, 121, A, "minor"),   # same key, close tempo  -> best with 1
        track(3, 120, C, "major"),   # relative of 1/2, same tempo as 1
        track(4, 95, 1, "major"),    # clashes with everything, far tempo
    ]
    pairs = rank_pairs(tracks)
    assert len(pairs) == 6
    assert {pairs[0]["a"], pairs[0]["b"]} in ({1, 2}, {1, 3})
    assert all(pairs[i]["score"] >= pairs[i + 1]["score"] for i in range(5))
    worst = pairs[-1]
    assert 4 in (worst["a"], worst["b"])


def test_rank_pairs_skips_unanalysed_and_limits():
    tracks = [track(1, 120, A, "minor"), track(2, 122, E, "minor"),
              {"id": 3, "bpm": None, "key_pc": None, "mode": None}]
    assert len(rank_pairs(tracks)) == 1
    many = [track(i, 100 + i, i % 12, "major") for i in range(10)]
    assert len(rank_pairs(many, limit=5)) == 5


def test_half_time_pair_ranks_well():
    p = score_pair(track(1, 70, G, "major"), track(2, 140, G, "major"))
    assert p["tempo_ratio"] == 2.0
    assert p["score"] > 0.9


def random_tracks(n, seed=0):
    import random

    rng = random.Random(seed)
    return [
        track(i, round(rng.uniform(70, 175), 1), rng.randrange(12), rng.choice(["major", "minor"]))
        for i in range(n)
    ]


def test_fast_ranking_matches_bruteforce():
    from app.matching import rank_pairs_bruteforce

    tracks = random_tracks(160)
    fast = rank_pairs(tracks, limit=200)
    slow = rank_pairs_bruteforce(tracks, limit=200)
    assert [p["score"] for p in fast] == [p["score"] for p in slow]
    assert {(p["a"], p["b"]) for p in fast if p["score"] > slow[-1]["score"]} == \
        {(p["a"], p["b"]) for p in slow if p["score"] > slow[-1]["score"]}


def test_fast_ranking_small_chunks_and_min_score():
    from app.matching import rank_pairs_bruteforce

    tracks = random_tracks(90, seed=3)
    fast = rank_pairs(tracks, limit=50, min_score=0.6, chunk=7)
    slow = rank_pairs_bruteforce(tracks, limit=50, min_score=0.6)
    assert [p["score"] for p in fast] == [p["score"] for p in slow]
    assert all(p["score"] >= 0.6 for p in fast)


def test_focus_returns_only_pairs_with_that_track_as_a():
    tracks = random_tracks(60, seed=5)
    pairs = rank_pairs(tracks, limit=20, focus=7)
    assert len(pairs) == 20
    assert all(p["a"] == 7 for p in pairs)
    assert all(pairs[i]["score"] >= pairs[i + 1]["score"] for i in range(19))


def test_string_ids_work():
    tracks = [dict(track(0, 120, A, "minor"), id="t1"), dict(track(0, 121, C, "major"), id="sXYZ")]
    (p,) = rank_pairs(tracks)
    assert {p["a"], p["b"]} == {"t1", "sXYZ"}


def test_ranking_thousands_is_fast():
    import time

    tracks = random_tracks(4000, seed=9)
    t0 = time.perf_counter()
    pairs = rank_pairs(tracks, limit=300)
    assert len(pairs) == 300
    assert time.perf_counter() - t0 < 10


def test_copies_of_the_same_song_are_not_paired():
    tracks = [
        dict(track(1, 120, A, "minor"), title="Can I Be Honest", artist=""),
        dict(track(2, 120, A, "minor"), title="CAN I BE HONEST", artist=""),         # copy
        dict(track(3, 120, A, "minor"), title="Can I Be Honest", artist="Someone"),  # untagged copy above
        dict(track(4, 121, A, "minor"), title="Other Song", artist="X"),
        dict(track(5, 122, A, "minor"), title="Other Song", artist="Y"),             # different artist: keep
    ]
    pairs = rank_pairs(tracks)
    ids = {p["a"] for p in pairs} | {p["b"] for p in pairs}
    assert ids == {1, 4, 5}
    assert all({p["a"], p["b"]} != {1, 2} for p in pairs)


def test_unsure_key_counts_for_less_in_both_paths():
    from app.matching import rank_pairs_bruteforce

    sure = [dict(track(1, 120, A, "minor"), key_confidence=0.3), dict(track(2, 120, A, "minor"), key_confidence=0.3)]
    unsure = [dict(track(1, 120, A, "minor"), key_confidence=0.3), dict(track(2, 120, A, "minor"), key_confidence=0.01)]
    (p_sure,), (p_unsure,) = rank_pairs(sure), rank_pairs(unsure)
    assert p_sure["score"] == 1.0
    assert p_unsure["score"] < p_sure["score"]
    assert any("unsure" in r for r in p_unsure["reasons"])
    tracks = [dict(t, key_confidence=(0.01 if t["id"] % 3 == 0 else 0.4)) for t in random_tracks(120, seed=11)]
    assert [p["score"] for p in rank_pairs(tracks, limit=80)] == [p["score"] for p in rank_pairs_bruteforce(tracks, limit=80)]


def test_duplicate_download_is_not_paired_with_original():
    tracks = [
        dict(track(1, 120, A, "minor"), title="Illegal", artist="PinkPantheress"),
        dict(track(2, 120, A, "minor"), title="Illegal(1)", artist="PinkPantheress"),
        dict(track(3, 121, A, "minor"), title="Ghost(1)", artist=""),
        dict(track(4, 121, A, "minor"), title="Ghost", artist=""),
    ]
    pairs = rank_pairs(tracks)
    assert [(p["a"], p["b"]) for p in pairs] in ([(1, 3)], [(3, 1)])


def test_same_song_with_overlapping_credits_is_one_song():
    tracks = [
        dict(track(1, 109, B, "major"), title="Strange Overtones", artist="David Byrne, Brian Eno"),
        dict(track(2, 109, B, "major"), title="Strange Overtones", artist="David Byrne"),
        dict(track(3, 109, B, "major"), title="Strange Overtones", artist="Some Cover Band"),  # a cover: keep
        dict(track(4, 110, B, "major"), title="Other", artist="X feat. Y"),
        dict(track(5, 110, B, "major"), title="Other", artist="Y"),                          # featured artist: same
    ]
    ids = {i for p in rank_pairs(tracks) for i in (p["a"], p["b"])}
    assert ids == {1, 3, 4}
