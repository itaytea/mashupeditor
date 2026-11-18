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


def test_shifts_and_overlap_defaults():
    from app import config

    assert config.DEMUCS_SHIFTS >= 1
    assert 0 <= config.DEMUCS_OVERLAP < 1


def test_shifts_and_overlap_from_env(monkeypatch):
    import importlib

    from app import config

    monkeypatch.setenv("DEMUCS_SHIFTS", "3")
    monkeypatch.setenv("DEMUCS_OVERLAP", "0.5")
    try:
        importlib.reload(config)
        assert config.DEMUCS_SHIFTS == 3
        assert config.DEMUCS_OVERLAP == 0.5
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def _run_separate(monkeypatch, tmp_path, calls, fail_devices):
    """Run stems.separate with demucs/torch/librosa/soundfile all mocked."""
    import numpy as np

    class NoGrad:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Sources:
        def __getitem__(self, i):
            return self

        def __mul__(self, o):
            return self

        __add__ = __mul__

        def sum(self, axis):
            return self

        def __sub__(self, o):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return np.zeros((2, 4), dtype=np.float32)

    torch = types.SimpleNamespace(
        no_grad=NoGrad,
        cuda=types.SimpleNamespace(is_available=lambda: True),
        backends=types.SimpleNamespace(mps=None),
    )

    class Wav:
        def mean(self, *a):
            return types.SimpleNamespace(mean=lambda: 0.0, std=lambda: 1.0)

        def __sub__(self, o):
            return self

        def __truediv__(self, o):
            return self

        def __getitem__(self, i):
            return self

    torch.from_numpy = lambda a: Wav()

    def apply_model(model, x, device, **kw):
        calls.append(device)
        if device in fail_devices:
            raise RuntimeError("CUDA out of memory")
        return Sources()

    class Model:
        samplerate = 8000
        audio_channels = 2
        sources = ["drums", "vocals"]

    demucs_apply = types.SimpleNamespace(
        tqdm=None, apply_model=apply_model, BagOfModels=type("Bag", (), {})
    )
    demucs = types.SimpleNamespace(apply=demucs_apply)
    librosa = types.SimpleNamespace(load=lambda *a, **k: (np.zeros((2, 8), np.float32), 8000))
    sf = types.SimpleNamespace(write=lambda tmp, *a, **k: open(tmp, "wb").close())
    for name, mod in {
        "torch": torch, "demucs": demucs, "demucs.apply": demucs_apply,
        "librosa": librosa, "soundfile": sf,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setattr(stems, "_get_model", lambda: Model())
    monkeypatch.setattr(stems.config, "STEMS_DIR", tmp_path / "stems")
    track = {"path": str(tmp_path / "a.mp3"), "mtime": 1, "size": 1}
    return stems.separate(track)


def test_auto_gpu_failure_falls_back_to_cpu_and_caches(monkeypatch, tmp_path):
    monkeypatch.setattr(stems.config, "DEMUCS_DEVICE", "auto")
    monkeypatch.setattr(stems, "_auto_device", None)
    calls = []
    paths = _run_separate(monkeypatch, tmp_path, calls, fail_devices={"cuda"})
    assert calls == ["cuda", "cpu"]
    assert paths["vocals"].exists() and paths["instrumental"].exists()
    assert stems._auto_device == "cpu"

    # A later track goes straight to cpu, without retrying the GPU.
    calls.clear()
    stems_dir = tmp_path / "stems"
    for f in stems_dir.rglob("*.wav"):
        f.unlink()
    _run_separate(monkeypatch, tmp_path, calls, fail_devices={"cuda"})
    assert calls == ["cpu"]


def test_explicit_device_failure_is_not_retried(monkeypatch, tmp_path):
    monkeypatch.setattr(stems.config, "DEMUCS_DEVICE", "cuda")
    monkeypatch.setattr(stems, "_auto_device", None)
    calls = []
    with pytest.raises(RuntimeError):
        _run_separate(monkeypatch, tmp_path, calls, fail_devices={"cuda"})
    assert calls == ["cuda"]
    assert stems._auto_device is None


def test_auto_resolution_is_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(stems.config, "DEMUCS_DEVICE", "auto")
    monkeypatch.setattr(stems, "_auto_device", None)
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(True, False))
    assert stems._active_device() == "cuda"
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(False, False))
    assert stems._active_device() == "cuda"
