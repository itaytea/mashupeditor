"""The PKCE _pending dict is shared across request threads; guard it with a lock."""

import threading
from urllib.parse import parse_qs, urlparse

import pytest

from app import config, spotify


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(config, "SPOTIFY_CLIENT_ID", "cid")
    spotify._pending.clear()
    yield
    spotify._pending.clear()


def test_login_url_registers_state():
    url = spotify.login_url()
    state = parse_qs(urlparse(url).query)["state"][0]
    assert state in spotify._pending


def test_login_url_prunes_expired_entries():
    spotify._pending["old"] = ("v", 0.0)
    spotify.login_url()
    assert "old" not in spotify._pending


def test_unknown_state_is_rejected():
    with pytest.raises(spotify.SpotifyError):
        spotify.finish_login("code", "nope")


def test_concurrent_logins_do_not_lose_or_corrupt_entries():
    threads = [threading.Thread(target=lambda: [spotify.login_url() for _ in range(50)])
               for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(spotify._pending) == 400

