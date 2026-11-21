"""spotify._get: bounded 429 retries and Retry-After parsing."""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from app import config, spotify


@pytest.fixture
def sleeps(monkeypatch):
    calls = []
    monkeypatch.setattr(spotify.time, "sleep", lambda s: calls.append(s))
    monkeypatch.setattr(config, "SPOTIFY_ACCESS_TOKEN", "tok")
    return calls


def _client(responses):
    """Client whose transport replays `responses` (callables or Response args)."""
    seen = []

    def handler(request):
        seen.append(request)
        status, headers = responses[min(len(seen), len(responses)) - 1]
        return httpx.Response(status, headers=headers, json={"ok": True})

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_success_after_429(sleeps):
    client, seen = _client([(429, {"Retry-After": "5"}), (200, {})])
    assert spotify._get(client, "https://api.test/x") == {"ok": True}
    assert len(seen) == 2
    assert sleeps == [5]


def test_retry_after_http_date(sleeps):
    when = datetime.now(timezone.utc) + timedelta(seconds=10)
    client, _ = _client([(429, {"Retry-After": format_datetime(when, usegmt=True)}), (200, {})])
    spotify._get(client, "https://api.test/x")
    assert len(sleeps) == 1 and 7 <= sleeps[0] <= 10


def test_retry_after_is_capped(sleeps):
    client, _ = _client([(429, {"Retry-After": "600"}), (200, {})])
    spotify._get(client, "https://api.test/x")
    assert sleeps == [30]


def test_garbage_retry_after_uses_default(sleeps):
    client, _ = _client([(429, {"Retry-After": "soon-ish"}), (200, {})])
    spotify._get(client, "https://api.test/x")
    assert sleeps == [2]


def test_missing_retry_after_uses_default(sleeps):
    client, _ = _client([(429, {}), (200, {})])
    spotify._get(client, "https://api.test/x")
    assert sleeps == [2]


def test_retries_exhausted_raises(sleeps):
    client, seen = _client([(429, {"Retry-After": "1"})])
    with pytest.raises(spotify.SpotifyError, match="429"):
        spotify._get(client, "https://api.test/x")
    assert len(seen) == 4  # first try + 3 retries
    assert sleeps == [1, 1, 1]


class _Cancelled(Exception):
    pass


def test_cancel_interrupts_429_wait(monkeypatch):
    """A raising cancel callback stops a long Retry-After wait quickly."""
    import time

    monkeypatch.setattr(config, "SPOTIFY_ACCESS_TOKEN", "tok")
    monkeypatch.setattr(spotify, "RETRY_AFTER_MAX_S", 600.0)
    monkeypatch.setattr(spotify, "SLEEP_SLICE_S", 0.01)
    client, seen = _client([(429, {"Retry-After": "300"}), (200, {})])
    start = time.monotonic()
    calls = []

    def check():
        calls.append(1)
        if len(calls) > 3:
            raise _Cancelled

    with pytest.raises(_Cancelled):
        spotify._get(client, "https://api.test/x", check_cancelled=check)
    assert time.monotonic() - start < 5
    assert len(seen) == 1  # never retried after the cancel


def test_cancel_callback_not_raising_still_retries(monkeypatch):
    monkeypatch.setattr(config, "SPOTIFY_ACCESS_TOKEN", "tok")
    monkeypatch.setattr(spotify, "SLEEP_SLICE_S", 0.01)
    client, seen = _client([(429, {"Retry-After": "0"}), (200, {})])
    assert spotify._get(client, "https://api.test/x", check_cancelled=lambda: None) == {"ok": True}
    assert len(seen) == 2
