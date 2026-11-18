"""The API only answers requests addressed to a local host name."""

import os
import subprocess
import sys
from pathlib import Path

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


def test_ipv6_loopback_is_allowed():
    client = TestClient(app)
    assert client.get("/api/config", headers={"Host": "[::1]:8000"}).status_code == 200


def test_default_hosts_exclude_testserver():
    env = {k: v for k, v in os.environ.items() if k != "MASHUP_ALLOWED_HOSTS"}
    out = subprocess.run(
        [sys.executable, "-c", "from app.main import ALLOWED_HOSTS; print(ALLOWED_HOSTS)"],
        capture_output=True, text=True, check=True, env=env,
        cwd=Path(__file__).resolve().parents[1],
    ).stdout.strip()
    assert out == "['localhost', '127.0.0.1', '[::1]']"
    assert "testserver" not in out
