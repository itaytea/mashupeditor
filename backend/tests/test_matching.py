from app.matching import rank_pairs, score_pair

C, G, A, E = 0, 7, 9, 4


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
