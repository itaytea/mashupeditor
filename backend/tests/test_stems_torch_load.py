import sys
import types

import pytest

from app import stems


def test_torch_load_patched_and_restored(monkeypatch):
    calls = []

    def original(*args, **kwargs):
        calls.append(kwargs)

    fake = types.SimpleNamespace(load=original)
    monkeypatch.setitem(sys.modules, "torch", fake)

    with stems._torch_load_weights_only_false():
        fake.load("a.th")
        fake.load("b.th", weights_only=True)
    assert calls == [{"weights_only": False}, {"weights_only": True}]
    assert fake.load is original


def test_torch_load_restored_on_error(monkeypatch):
    def original(*a, **k):
        pass

    fake = types.SimpleNamespace(load=original)
    monkeypatch.setitem(sys.modules, "torch", fake)
    with pytest.raises(RuntimeError):
        with stems._torch_load_weights_only_false():
            raise RuntimeError("boom")
    assert fake.load is original
