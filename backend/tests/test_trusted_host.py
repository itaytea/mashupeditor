"""The API only answers requests addressed to a local host name."""

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def test_unknown_host_is_rejected():
    client = TestClient(app)
    assert client.get("/api/config", headers={"Host": "evil.example"}).status_code == 400


def test_localhost_is_allowed():
    client = TestClient(app)
    assert client.get("/api/config", headers={"Host": "localhost"}).status_code == 200
