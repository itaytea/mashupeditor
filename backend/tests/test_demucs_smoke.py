import os

import pytest

from app import config, stems

pytestmark = pytest.mark.skipif(
    os.environ.get("MASHUP_DEMUCS_SMOKE") != "1",
    reason="opt-in: set MASHUP_DEMUCS_SMOKE=1 (downloads the htdemucs checkpoint)",
)


def test_htdemucs_loads(monkeypatch):
    monkeypatch.setattr(config, "DEMUCS_MODEL", "htdemucs")
    monkeypatch.setattr(stems, "_model", None)
    model = stems._get_model()
    assert model is not None
    assert "vocals" in model.sources
