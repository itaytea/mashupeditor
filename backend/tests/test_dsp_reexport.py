"""The DSP helpers live in app.dsp but stay importable from app.render."""
import pytest

from app import dsp, render

NAMES = [
    "load_stereo", "stretch_and_shift", "beat_map", "quantize_stretch", "beat_times",
    "rms_curve", "onset_lag", "place", "active_rms", "fade", "to_mono22",
]


@pytest.mark.parametrize("name", NAMES)
def test_helper_is_same_object_in_both_modules(name):
    assert getattr(render, name) is getattr(dsp, name)


def test_tempo_sr_shared():
    assert render.TEMPO_SR == dsp.TEMPO_SR == 22050
