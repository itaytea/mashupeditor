"""Vocal / instrumental separation with Demucs, cached per source file."""

from __future__ import annotations

import hashlib
import logging
import threading
import types
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

from . import config

log = logging.getLogger(__name__)
_model_lock = threading.Lock()
_model = None
# Device chosen for DEMUCS_DEVICE=auto, resolved once (and downgraded to "cpu"
# if the GPU fails), so we don't re-probe or re-fail on every track.
_auto_device: str | None = None


def stem_dir(track: dict) -> Path:
    key = f"{track['path']}|{track['mtime']}|{track['size']}|{config.DEMUCS_MODEL}"
    return config.STEMS_DIR / hashlib.sha1(key.encode()).hexdigest()[:16]


def cached_stems(track: dict) -> dict[str, Path] | None:
    d = stem_dir(track)
    vocals, inst = d / "vocals.wav", d / "instrumental.wav"
    if vocals.exists() and inst.exists():
        return {"vocals": vocals, "instrumental": inst}
    return None


def resolve_device(device: str | None = None) -> str:
    """Turn "auto" into the best available torch device (cuda > mps > cpu).
    Any other value is returned unchanged."""
    device = (device or config.DEMUCS_DEVICE or "auto").strip().lower()
    if device != "auto":
        return device
    import torch

    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


def _active_device() -> str:
    """The device to separate on. Only "auto" is cached; an explicit
    DEMUCS_DEVICE is always honoured as given."""
    global _auto_device
    if (config.DEMUCS_DEVICE or "auto").strip().lower() != "auto":
        return resolve_device()
    if _auto_device is None:
        _auto_device = resolve_device()
    return _auto_device


def _can_fall_back(device: str) -> bool:
    """Retry on cpu only when "auto" picked a GPU; never override an explicit choice."""
    return (
        (config.DEMUCS_DEVICE or "auto").strip().lower() == "auto"
        and device != "cpu"
    )


def _get_model():
    global _model
    with _model_lock:
        if _model is None:
            from demucs.pretrained import get_model

            _model = get_model(config.DEMUCS_MODEL)
            _model.eval()
        return _model


class _ProgressTqdm:
    """Stand-in for the `tqdm` module inside demucs.apply. Demucs wraps its
    chunk iterator in `tqdm.tqdm(...)`; we wrap it in a counter instead."""

    def __init__(self, n_passes: int, report: Callable[[float], None]):
        self.n_passes = max(n_passes, 1)
        self.passes_done = 0
        self.report = report

    def tqdm(self, iterable: Iterable, **_kwargs):
        items = list(iterable)
        total = max(len(items), 1)
        for i, item in enumerate(items):
            yield item
            self.report((self.passes_done + (i + 1) / total) / self.n_passes)
        self.passes_done += 1


def separate(
    track: dict, progress: Callable[[float, str], None] | None = None
) -> dict[str, Path]:
    """Split `track` into vocals.wav and instrumental.wav (everything else
    summed). Results are cached on disk next to the analysis DB."""
    hit = cached_stems(track)
    if hit:
        if progress:
            progress(1.0, "Stems cached")
        return hit

    import librosa
    import soundfile as sf
    import torch
    from demucs import apply as demucs_apply
    from demucs.apply import BagOfModels, apply_model

    report = progress or (lambda f, m: None)
    name = Path(track["path"]).name
    report(0.0, f"Loading Demucs model ({config.DEMUCS_MODEL})")
    model = _get_model()

    report(0.02, f"Decoding {name}")
    y, _ = librosa.load(track["path"], sr=model.samplerate, mono=False)
    if y.ndim == 1:
        y = np.stack([y, y])
    y = y[: model.audio_channels]
    wav = torch.from_numpy(np.ascontiguousarray(y, dtype=np.float32))

    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    wav = (wav - mean) / std

    n_passes = len(model.models) if isinstance(model, BagOfModels) else 1

    def run(device: str):
        shim = _ProgressTqdm(n_passes, lambda f: report(0.05 + 0.9 * f, f"Separating {name}"))
        original_tqdm = demucs_apply.tqdm
        demucs_apply.tqdm = types.SimpleNamespace(tqdm=shim.tqdm)
        try:
            with torch.no_grad():
                return apply_model(
                    model, wav[None], device=device, shifts=config.DEMUCS_SHIFTS,
                    split=True, overlap=config.DEMUCS_OVERLAP, progress=True,
                )[0]
        finally:
            demucs_apply.tqdm = original_tqdm

    global _auto_device
    device = _active_device()
    try:
        sources = run(device)
    except RuntimeError as e:
        if not _can_fall_back(device):
            raise
        log.warning("Demucs failed on %s (%s); retrying on cpu", device, e)
        report(0.05, f"GPU failed, retrying {name} on CPU")
        _auto_device = "cpu"
        sources = run("cpu")
    sources = sources * std + mean

    report(0.96, "Writing stems")
    vocal_idx = model.sources.index("vocals")
    vocals = sources[vocal_idx]
    instrumental = sources.sum(0) - vocals

    out = stem_dir(track)
    out.mkdir(parents=True, exist_ok=True)
    paths = {"vocals": out / "vocals.wav", "instrumental": out / "instrumental.wav"}
    for key, data in (("vocals", vocals), ("instrumental", instrumental)):
        tmp = paths[key].with_suffix(".tmp.wav")
        sf.write(tmp, data.cpu().numpy().T, model.samplerate, subtype="FLOAT")
        tmp.replace(paths[key])  # atomic, so a half-written file never looks cached
    report(1.0, "Stems ready")
    return paths
