from app.analysis import read_metadata
from app.fuzzy import match_playlist, normalize, title_artist_score


def test_normalize_strips_noise():
    assert normalize("Don't Stop Me Now - Remastered 2011") == "don t stop me now"
    assert normalize("Levitating (feat. DaBaby)") == "levitating"
    assert normalize("Beyoncé") == "beyonce"
    assert normalize("Simon & Garfunkel") == "simon and garfunkel"


def test_score_title_and_artist():
    good = title_artist_score("Get Lucky", ["Daft Punk", "Pharrell Williams"],
                              "Get Lucky (Radio Edit)", "Daft Punk")
    wrong_artist = title_artist_score("Get Lucky", ["Daft Punk"], "Get Lucky", "Someone Else")
    assert good > 90
    assert wrong_artist < good


def test_match_playlist_greedy_unique():
    playlist = [
        {"title": "Get Lucky", "artists": ["Daft Punk"]},
        {"title": "Around the World", "artists": ["Daft Punk"]},
        {"title": "Unrelated Song", "artists": ["Nobody"]},
    ]
    local = [
        {"id": 10, "title": "Around The World", "artist": "Daft Punk"},
        {"id": 11, "title": "Get Lucky", "artist": ""},  # filename-only, no artist
        {"id": 12, "title": "Something Else", "artist": "Daft Punk"},
    ]
    m = {x.position: x.track_id for x in match_playlist(playlist, local)}
    assert m == {0: 11, 1: 10, 2: None}


def test_read_metadata_falls_back_to_filename(tmp_path):
    p = tmp_path / "03 - Daft Punk - One More Time.wav"
    p.write_bytes(b"")
    title, artist = read_metadata(p)
    assert (artist, title) == ("Daft Punk", "One More Time")


def test_same_title_different_artist_is_vetoed():
    assert title_artist_score("Diva", ["Model/Actriz"], "DIVA", "ART-SCHOOL") == 0.0
    assert title_artist_score("Stereo Love", ["Vincent", "KnownAsNat"], "Stereo Love", "Novi Sad") == 0.0
    # a featured artist alone still counts as the same artist
    assert title_artist_score("Stereo Love", ["Vincent", "KnownAsNat"], "Stereo Love", "KnownAsNat") > 90


def test_local_file_by_other_artist_is_not_matched():
    playlist = [{"title": "Diva", "artists": ["Model/Actriz"]}]
    local = [{"id": 1, "title": "Diva", "artist": "Beyoncé"}, {"id": 2, "title": "Diva", "artist": ""}]
    (m,) = match_playlist(playlist, local)
    assert m.track_id == 2  # the untagged file may be it; the Beyoncé one isn't


def test_copy_markers_are_the_same_song():
    from app.fuzzy import strip_copy_marker

    for raw in ("Illegal(1)", "Illegal (2)", "Illegal copy", "Illegal copy 3", "Illegal - Copy", "Illegal(1)(1)"):
        assert strip_copy_marker(raw) == "Illegal", raw
        assert normalize(raw) == "illegal"
    # real titles that end in numbers or "copy" words stay
    assert normalize("Blink-182") == "blink 182"
    assert normalize("Carbon Copy Cat") == "carbon copy cat"
    assert normalize("Track (Pt. 2)") == "track pt 2"


def _reference_match_playlist(playlist, local):
    """The original per-pair implementation, kept to prove score parity."""
    from app.fuzzy import MATCH_THRESHOLD, Match

    candidates = []
    for pos, pt in enumerate(playlist):
        for lt in local:
            s = title_artist_score(
                pt["title"], pt["artists"], lt.get("title") or "", lt.get("artist") or ""
            )
            if s >= MATCH_THRESHOLD:
                candidates.append((s, pos, lt["id"]))
    candidates.sort(reverse=True)
    taken_pos, taken_local = {}, set()
    for s, pos, tid in candidates:
        if pos in taken_pos or tid in taken_local:
            continue
        taken_pos[pos] = (tid, round(s, 1))
        taken_local.add(tid)
    return [Match(pos, *taken_pos.get(pos, (None, None))) for pos in range(len(playlist))]


_PARITY_PLAYLIST = [
    {"title": "Get Lucky", "artists": ["Daft Punk", "Pharrell Williams"]},
    {"title": "Don't Stop Me Now - Remastered 2011", "artists": ["Queen"]},
    {"title": "Levitating (feat. DaBaby)", "artists": ["Dua Lipa"]},
    {"title": "Beyoncé Song", "artists": []},
    {"title": "Diva", "artists": ["Model/Actriz"]},
    {"title": "", "artists": ["Nobody"]},
]
_PARITY_LOCAL = [
    {"id": 1, "title": "Get Lucky (Radio Edit)", "artist": "Daft Punk"},
    {"id": 2, "title": "Get Lucky", "artist": ""},
    {"id": 3, "title": "Dont Stop Me Now(1)", "artist": "Queen"},
    {"id": 4, "title": "Levitating", "artist": "Dua Lipa"},
    {"id": 5, "title": "Beyonce Song", "artist": "Someone"},
    {"id": 6, "title": "DIVA", "artist": "ART-SCHOOL"},
    {"id": 7, "title": None, "artist": None},
    {"id": 8, "title": "Unrelated", "artist": "Nobody"},
]


def test_match_playlist_parity_with_per_pair_scoring():
    assert match_playlist(_PARITY_PLAYLIST, _PARITY_LOCAL) == _reference_match_playlist(
        _PARITY_PLAYLIST, _PARITY_LOCAL
    )


def test_precomputed_scores_equal_public_scores():
    from app.fuzzy import (
        _artist_score_normalized,
        _combine,
        _normalize_artists,
        _title_score_normalized,
    )

    for pt in _PARITY_PLAYLIST:
        joined, each = _normalize_artists(pt["artists"])
        for lt in _PARITY_LOCAL:
            title_b, artist_b = lt["title"] or "", lt["artist"] or ""
            fast = _combine(
                _title_score_normalized(normalize(pt["title"]), normalize(title_b)),
                _artist_score_normalized(joined, each, normalize(artist_b)),
            )
            assert fast == title_artist_score(pt["title"], pt["artists"], title_b, artist_b)


def test_normalize_artists_helper():
    from app.fuzzy import _normalize_artists

    assert _normalize_artists(["Beyoncé", "Simon & Garfunkel"]) == (
        "beyonce simon and garfunkel",
        ["beyonce", "simon and garfunkel"],
    )
    assert _normalize_artists([]) == ("", [])


def test_normalized_scorers_edge_cases():
    from app.fuzzy import _artist_score_normalized, _combine, _title_score_normalized

    assert _title_score_normalized("", "x") == 0.0
    assert _artist_score_normalized("daft punk", ["daft punk"], "") is None
    assert _combine(100.0, None) == 90.0
    assert _combine(100.0, 59.0) == 0.0
    assert _combine(100.0, 100.0) == 100.0
