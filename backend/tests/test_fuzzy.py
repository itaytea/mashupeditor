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
