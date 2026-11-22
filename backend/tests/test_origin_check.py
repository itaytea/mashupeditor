"""State-changing requests from a foreign Origin/Referer are rejected."""

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def test_post_without_origin_passes():
    assert client.post("/api/spotify/logout").status_code != 403


def test_same_host_origin_passes():
    r = client.post("/api/spotify/logout", headers={"Origin": "http://testserver"})
    assert r.status_code != 403


def test_dev_origin_passes():
    r = client.post("/api/spotify/logout", headers={"Origin": "http://localhost:5173"})
    assert r.status_code != 403


def test_foreign_origin_post_is_forbidden():
    r = client.post("/api/spotify/logout", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_foreign_origin_delete_is_forbidden():
    r = client.delete("/api/drafts/abc", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_foreign_referer_without_origin_is_forbidden():
    r = client.post("/api/spotify/logout", headers={"Referer": "https://evil.example/page"})
    assert r.status_code == 403


def test_null_origin_is_forbidden():
    assert client.post("/api/spotify/logout", headers={"Origin": "null"}).status_code == 403


def test_foreign_origin_get_passes():
    r = client.get("/api/config", headers={"Origin": "https://evil.example"})
    assert r.status_code == 200
