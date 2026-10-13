import time

import pytest

from app.songlookup import find_song, lookup_key, parse_key, pending_rows

C, Cs, D, Ds, E, F, Fs, G, Gs, A, As, B = range(12)


@pytest.mark.parametrize(
    "key_of, expected",
    [
        ("C", (C, "major")), ("Em", (E, "minor")), ("F#", (Fs, "major")),
        ("F♯m", (Fs, "minor")), ("Bb", (As, "major")), ("B♭m", (As, "minor")),
        ("Cb", (B, "major")), ("E#", (F, "major")), ("C♯/D♭", (Cs, "major")),
        ("A minor", (A, "minor")), ("G major", (G, "major")), (" am ", (A, "minor")),
    ],
)
def test_parse_key_names(key_of, expected):
    assert parse_key(key_of) == expected


@pytest.mark.parametrize(
    "open_key, expected",
    [("1d", (C, "major")), ("1m", (A, "minor")), ("2d", (G, "major")),
     ("2m", (E, "minor")), ("12d", (F, "major")), ("6m", (Gs, "minor"))],
)
def test_parse_open_key_fallback(open_key, expected):
    assert parse_key("", open_key) == expected
    assert parse_key("garbage", open_key) == expected


def test_parse_key_rejects_junk():
    assert parse_key(None) is None
    assert parse_key("H#") is None
    assert parse_key("", "13d") is None


def test_lookup_key_prefers_spotify_id():
    assert lookup_key({"spotify_id": "abc", "title": "x", "artists": ["y"]}) == "abc"
    assert lookup_key({"spotify_id": None, "title": "Get Lucky (feat. X)", "artists": ["Daft Punk"]}) == "get lucky|daft punk"


def fake_api(responses):
    calls = []

    def get(path, params):
        calls.append((path, params))
        return responses.get((path, params.get("lookup") or params.get("id")),
                             {"search": {"error": "no result"}})

    return get, calls


def test_find_song_uses_search_result_when_it_has_data():
    get, calls = fake_api({
        ("/search/", "song:melatonin artist:tinashe"): {"search": [
            {"id": "x1", "title": "Something Else", "artist": {"name": "Tinashe"}, "tempo": "90", "key_of": "C"},
            {"id": "x2", "title": "Melatonin", "artist": {"name": "Tinashe"}, "tempo": "104", "key_of": "F♯m"},
        ]},
    })
    rec = find_song("Melatonin", ["Tinashe"], get)
    assert rec["bpm"] == 104 and rec["camelot"] == "11A" and rec["source_id"] == "x2"
    assert len(calls) == 1


def test_find_song_fetches_detail_when_search_lacks_tempo():
    get, calls = fake_api({
        ("/search/", "song:get lucky artist:daft punk"): {"search": [
            {"id": "s9", "title": "Get Lucky", "artist": {"name": "Daft Punk"}},
        ]},
        ("/song/", "s9"): {"song": {"id": "s9", "title": "Get Lucky", "artist": {"name": "Daft Punk"},
                                    "tempo": "116", "key_of": "", "open_key": "3m"}},
    })
    rec = find_song("Get Lucky (feat. Pharrell Williams)", ["Daft Punk", "Pharrell Williams"], get)
    assert rec["bpm"] == 116
    assert rec["key_name"] == "Bm"  # 3m = B minor
    assert [c[0] for c in calls] == ["/search/", "/song/"]


def test_find_song_rejects_poor_matches_and_no_results():
    get, _ = fake_api({
        ("/search/", "song:melatonin artist:tinashe"): {"search": [
            {"id": "z", "title": "Melatonin Dreams", "artist": {"name": "Other Band"}, "tempo": "100", "key_of": "C"},
        ]},
    })
    assert find_song("Melatonin", ["Tinashe"], get) is None
    get, calls = fake_api({})
    assert find_song("Nothing", ["Nobody"], get) is None
    assert [c[1]["type"] for c in calls] == ["both", "song"]  # tried the fallback too


def test_same_title_by_another_artist_is_rejected():
    # Real case: title-only search for "Diva" returns ART-SCHOOL's "DIVA".
    get, _ = fake_api({
        ("/search/", "diva"): {"search": [
            {"id": "a1", "title": "DIVA", "artist": {"name": "ART-SCHOOL"}, "tempo": "87", "key_of": "D"},
            {"id": "a2", "title": "Diva", "artist": {"name": "Beyoncé"}, "tempo": "150", "key_of": "Cm"},
        ]},
    })
    assert find_song("Diva", ["Model/Actriz"], get) is None


def test_title_only_fallback_finds_unusual_artist_spelling():
    # Real case: GetSongBPM spells Deee-Lite with U+2010, so the artist query misses.
    get, calls = fake_api({
        ("/search/", "electric shock"): {"search": [
            {"id": "e1", "title": "Electric Shock", "artist": {"name": "f(x)"}, "tempo": "128", "key_of": "Am"},
            {"id": "e2", "title": "Electric Shock", "artist": {"name": "Deee\u2010Lite"}, "tempo": "121", "key_of": "C"},
        ]},
    })
    rec = find_song("Electric Shock", ["Deee-Lite"], get)
    assert rec["source_id"] == "e2" and rec["bpm"] == 121
    assert [c[1]["type"] for c in calls] == ["both", "song"]


def test_pending_rows_skips_owned_found_and_recent_misses():
    now = time.time()
    rows = [
        {"spotify_id": "owned", "title": "a", "artists": ["x"], "track_id": 5},
        {"spotify_id": "found", "title": "b", "artists": ["x"], "track_id": None},
        {"spotify_id": "miss-new", "title": "c", "artists": ["x"], "track_id": None},
        {"spotify_id": "miss-old", "title": "d", "artists": ["x"], "track_id": None},
        {"spotify_id": "never", "title": "e", "artists": ["x"], "track_id": None},
    ]
    cached = {
        "found": {"status": "found", "fetched_at": now - 10**8},
        "miss-new": {"status": "not_found", "fetched_at": now - 60},
        "miss-old": {"status": "not_found", "fetched_at": now - 10**8},
    }
    assert [r["spotify_id"] for r in pending_rows(rows, cached, now)] == ["miss-old", "never"]


def test_tiny_titles_are_not_searched():
    get, calls = fake_api({})
    assert find_song("0", ["Someone"], get) is None
    assert find_song("( )", ["Someone"], get) is None
    assert calls == []


def test_redact_masks_keys_in_errors():
    from app.jobs import redact

    msg = "Client error for url 'https://api.getsong.co/search/?api_key=abc123&type=both&lookup=0'"
    assert "abc123" not in redact(msg)
    assert "api_key=***&type=both" in redact(msg)
