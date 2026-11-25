"""Tests for the key/BPM evaluation harness (scripts/eval_analysis.py)."""

import json

import pytest
import soundfile as sf

from scripts import eval_analysis as ev
from tests.test_analysis import SR, synth


def k(text):
    return ev.parse_key(text)


# --- key parsing --------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("C", (0, "major")), ("Am", (9, "minor")), ("F#m", (6, "minor")),
    ("Bb minor", (10, "minor")), ("A major", (9, "major")), ("Db", (1, "major")),
    ("8B", (0, "major")), ("8A", (9, "minor")), ("9B", (7, "major")),
    ("12A", (1, "minor")), ("1B", (11, "major")),
])
def test_parse_key(text, expected):
    assert ev.parse_key(text) == expected


@pytest.mark.parametrize("bad", ["", "H", "C dorian", "13A"])
def test_parse_key_rejects_garbage(bad):
    with pytest.raises(ValueError):
        ev.parse_key(bad)


# --- MIREX key score ------------------------------------------------------------

@pytest.mark.parametrize("ref,est,cls,score", [
    ("C", "C", "exact", 1.0),
    ("C", "G", "fifth", 0.5),      # a fifth above
    ("C", "F", "fifth", 0.5),      # a fifth below
    ("Am", "Em", "fifth", 0.5),
    ("C", "Am", "relative", 0.3),
    ("Am", "C", "relative", 0.3),
    ("C", "Cm", "parallel", 0.2),
    ("Am", "A", "parallel", 0.2),
    ("C", "D", "other", 0.0),
    ("C", "Gm", "other", 0.0),     # fifth away but wrong mode
    ("C", "Em", "other", 0.0),     # not the relative minor
])
def test_key_score(ref, est, cls, score):
    assert ev.classify_key_error(k(ref), k(est)) == cls
    assert ev.key_score(k(ref), k(est)) == score


# --- BPM accuracy -----------------------------------------------------------------

def test_bpm_acc1_tolerance():
    assert ev.bpm_acc1(120, 120)
    assert ev.bpm_acc1(120, 124.8)       # exactly +4%
    assert not ev.bpm_acc1(120, 125.0)
    assert ev.bpm_acc1(120, 115.2)       # exactly -4%
    assert not ev.bpm_acc1(120, 115.0)
    assert not ev.bpm_acc1(0, 0)


@pytest.mark.parametrize("est", [60, 120, 240, 360, 40, 121])
def test_bpm_acc2_accepts_metrical_levels(est):
    assert ev.bpm_acc2(120, est)
    # Acc1 only accepts the exact level.
    assert ev.bpm_acc1(120, est) == (est == 121 or est == 120)


@pytest.mark.parametrize("est", [90, 150, 80, 180])
def test_bpm_acc2_rejects_other_ratios(est):
    assert not ev.bpm_acc2(120, est)


# --- labels -------------------------------------------------------------------------

def test_load_labels_csv_and_json(tmp_path):
    (tmp_path / "labels.csv").write_text("path,bpm,key\na.wav,120,C\nb.wav,,Am\nc.wav,100,\n")
    rows = ev.load_labels(tmp_path / "labels.csv")
    assert rows[0] == {"path": tmp_path / "a.wav", "bpm": 120.0, "key": (0, "major")}
    assert rows[1]["bpm"] is None and rows[1]["key"] == (9, "minor")
    assert rows[2]["key"] is None

    (tmp_path / "labels.json").write_text(json.dumps([{"path": "a.wav", "bpm": 90, "key": "8A"}]))
    rows = ev.load_labels(tmp_path / "labels.json")
    assert rows[0]["key"] == (9, "minor") and rows[0]["bpm"] == 90.0


# --- evaluate() with a stubbed analysis function ---------------------------------------

def test_evaluate_summary_and_failures(tmp_path):
    labels = [
        {"path": tmp_path / "a.wav", "bpm": 120.0, "key": k("C")},    # exact, acc1
        {"path": tmp_path / "b.wav", "bpm": 100.0, "key": k("Am")},   # relative, acc2 only
        {"path": tmp_path / "c.wav", "bpm": 100.0, "key": k("C")},    # fails to analyse
    ]
    fake = {
        "a.wav": {"bpm": 121.0, "key_pc": 0, "mode": "major"},
        "b.wav": {"bpm": 200.0, "key_pc": 0, "mode": "major"},
    }

    def analyze(path):
        if path.name not in fake:
            raise ValueError("boom")
        return fake[path.name]

    res = ev.evaluate(labels, analyze=analyze)
    s = res["summary"]
    assert s["files"] == 3 and s["failed"] == 1
    assert s["key_files"] == 2 and s["key_weighted_score"] == pytest.approx((1.0 + 0.3) / 2)
    assert s["bpm_files"] == 2 and s["bpm_acc1"] == 0.5 and s["bpm_acc2"] == 1.0
    assert res["files"][2]["error"].startswith("ValueError")
    text = ev.format_table(res)
    assert "weighted score 65.0%" in text and "Acc1 50.0%" in text and "ERROR" in text


# --- end to end on synthetic audio ---------------------------------------------------------

@pytest.mark.slow
def test_main_on_synthetic_click_tracks(tmp_path, capsys):
    sf.write(tmp_path / "c_120.wav", synth(120, [60, 64, 67]), SR)   # C major
    sf.write(tmp_path / "am_96.wav", synth(96, [57, 60, 64]), SR)     # A minor
    (tmp_path / "labels.csv").write_text(
        "path,bpm,key\nc_120.wav,120,C\nam_96.wav,96,Am\n")

    assert ev.main([str(tmp_path / "labels.csv"), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    s = out["summary"]
    assert s["failed"] == 0
    assert s["bpm_acc1"] == 1.0
    assert s["key_weighted_score"] == 1.0

    assert ev.main([str(tmp_path / "labels.csv")]) == 0
    assert "Acc1 100.0%" in capsys.readouterr().out


def test_main_reports_bad_labels(tmp_path, capsys):
    (tmp_path / "labels.csv").write_text("path,bpm,key\na.wav,120,H#\n")
    assert ev.main([str(tmp_path / "labels.csv")]) == 2
    assert "error reading labels" in capsys.readouterr().err
