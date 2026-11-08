import sys
import types

import pytest

from app import stems


def _fake_torch(cuda: bool, mps: bool):
    t = types.SimpleNamespace()
    t.cuda = types.SimpleNamespace(is_available=lambda: cuda)
    t.backends = types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: mps))
    return t


@pytest.mark.parametrize(
    "cuda,mps,expected",
    [(True, True, "cuda"), (True, False, "cuda"), (False, True, "mps"), (False, False, "cpu")],
)
def test_auto_resolves(monkeypatch, cuda, mps, expected):
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda, mps))
    assert stems.resolve_device("auto") == expected


def test_explicit_device_is_kept(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(True, True))
    assert stems.resolve_device("cpu") == "cpu"
    assert stems.resolve_device("MPS") == "mps"


def test_default_comes_from_config(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(False, False))
    monkeypatch.setattr(stems.config, "DEMUCS_DEVICE", "auto")
    assert stems.resolve_device() == "cpu"
