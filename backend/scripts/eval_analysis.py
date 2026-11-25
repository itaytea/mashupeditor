"""Evaluate key and BPM detection against labelled audio files.

    cd backend && python -m scripts.eval_analysis labels.csv [--json]

Labels are a CSV (header ``path,bpm,key``) or a JSON list of objects with the same
fields. Relative paths are resolved against the labels file. ``bpm`` and ``key``
may each be left empty to skip that metric for a file. Keys can be written as
``C``, ``Am``, ``F#m``, ``Bb minor``, ``A major`` or as a Camelot code (``8A``).

Every file goes through the app's own ``analysis.analyze_file``. Reported:

* MIREX weighted key score: exact 1.0, perfect fifth (same mode) 0.5,
  relative major/minor 0.3, parallel major/minor 0.2, anything else 0.
* BPM Acc1: estimate within 4% of the reference tempo.
* BPM Acc2: like Acc1, but also accepts x2, x1/2, x3 and x1/3 of the reference.

Note that the app folds its BPM estimate into 70-180, so references outside that
range can only score on Acc2.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

BPM_TOLERANCE = 0.04
BPM_ACC2_FACTORS = (1.0, 2.0, 0.5, 3.0, 1.0 / 3.0)

KEY_SCORES = {"exact": 1.0, "fifth": 0.5, "relative": 0.3, "parallel": 0.2, "other": 0.0}

_NOTE_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_NOTE_RE = re.compile(r"^([A-Ga-g])\s*([#b♯♭]?)\s*(.*)$")
_CAMELOT_RE = re.compile(r"^(1[0-2]|[1-9])\s*([ABab])$")

Key = tuple[int, str]  # (pitch class 0..11, "major" | "minor")


def parse_key(text: str) -> Key:
    """Parse a key label such as "C", "Am", "F# minor" or "8A" into (pc, mode)."""
    s = str(text).strip()
    m = _CAMELOT_RE.match(s)
    if m:
        number, letter = int(m.group(1)), m.group(2).upper()
        # Camelot 8B = C major, 8A = A minor; each step clockwise is a fifth.
        major_pc = (number - 8) * 7 % 12  # pitch class of the major key for this number
        if letter == "B":
            return major_pc, "major"
        return (major_pc + 9) % 12, "minor"
    m = _NOTE_RE.match(s)
    if not m:
        raise ValueError(f"unrecognised key: {text!r}")
    letter, accidental, rest = m.groups()
    pc = _NOTE_PC[letter.upper()]
    if accidental in ("#", "♯"):
        pc += 1
    elif accidental in ("b", "♭"):
        pc -= 1
    rest = rest.strip().lower()
    if rest in ("", "maj", "major"):
        mode = "major"
    elif rest in ("m", "min", "minor"):
        mode = "minor"
    else:
        raise ValueError(f"unrecognised key: {text!r}")
    return pc % 12, mode


def classify_key_error(ref: Key, est: Key) -> str:
    """Relationship of the estimate to the reference: exact, fifth, relative,
    parallel or other (MIREX categories)."""
    (rpc, rmode), (epc, emode) = ref, est
    if (rpc, rmode) == (epc, emode):
        return "exact"
    if rmode == emode and (epc - rpc) % 12 in (5, 7):
        return "fifth"
    if rmode == "major" and emode == "minor" and epc == (rpc + 9) % 12:
        return "relative"
    if rmode == "minor" and emode == "major" and epc == (rpc + 3) % 12:
        return "relative"
    if rpc == epc:
        return "parallel"
    return "other"


def key_score(ref: Key, est: Key) -> float:
    """MIREX weighted key score for one file."""
    return KEY_SCORES[classify_key_error(ref, est)]


def bpm_acc1(ref: float, est: float, tol: float = BPM_TOLERANCE) -> bool:
    """True if `est` is within `tol` (relative) of `ref`."""
    return ref > 0 and est > 0 and abs(est - ref) <= tol * ref


def bpm_acc2(ref: float, est: float, tol: float = BPM_TOLERANCE) -> bool:
    """Like bpm_acc1, but also accepts x2, x1/2, x3 and x1/3 of the reference."""
    return any(bpm_acc1(ref * f, est, tol) for f in BPM_ACC2_FACTORS)


# --- labels -----------------------------------------------------------------

def _blank(v) -> bool:
    return v is None or str(v).strip() == ""


def load_labels(path: Path) -> list[dict]:
    """Read labels from CSV or JSON. Returns dicts with path (Path), bpm (float|None),
    key (Key|None)."""
    path = Path(path)
    if path.suffix.lower() == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("JSON labels must be a list of {path, bpm, key} objects")
    else:
        with path.open(newline="", encoding="utf-8-sig") as f:
            raw = list(csv.DictReader(f))
    out = []
    for i, row in enumerate(raw, 1):
        if _blank(row.get("path")):
            raise ValueError(f"label {i}: missing path")
        p = Path(str(row["path"])).expanduser()
        if not p.is_absolute():
            p = path.parent / p
        out.append({
            "path": p,
            "bpm": None if _blank(row.get("bpm")) else float(row["bpm"]),
            "key": None if _blank(row.get("key")) else parse_key(row["key"]),
        })
    return out


# --- evaluation ---------------------------------------------------------------

def _key_text(k: Key | None) -> str:
    if k is None:
        return ""
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return names[k[0]] + ("m" if k[1] == "minor" else "")


def evaluate(labels: list[dict], analyze=None) -> dict:
    """Run `analyze(path) -> dict` (default: app.analysis.analyze_file) on every
    label and score it. Files that fail to analyse are reported and left out of
    the averages."""
    if analyze is None:
        from app.analysis import analyze_file
        analyze = analyze_file
    rows = []
    for lab in labels:
        row = {"path": str(lab["path"]), "ref_bpm": lab["bpm"], "ref_key": _key_text(lab["key"]),
               "est_bpm": None, "est_key": "", "key_class": None, "key_score": None,
               "acc1": None, "acc2": None, "error": None}
        try:
            res = analyze(lab["path"])
            est_key: Key = (int(res["key_pc"]), res["mode"])
            row["est_bpm"] = float(res["bpm"])
            row["est_key"] = _key_text(est_key)
            if lab["key"] is not None:
                row["key_class"] = classify_key_error(lab["key"], est_key)
                row["key_score"] = KEY_SCORES[row["key_class"]]
            if lab["bpm"] is not None:
                row["acc1"] = bpm_acc1(lab["bpm"], row["est_bpm"])
                row["acc2"] = bpm_acc2(lab["bpm"], row["est_bpm"])
        except Exception as e:  # keep going; one bad file should not stop the run
            row["error"] = f"{type(e).__name__}: {e}"
        rows.append(row)

    def mean(vals):
        vals = list(vals)
        return sum(vals) / len(vals) if vals else None

    scored_keys = [r for r in rows if r["key_score"] is not None]
    scored_bpm = [r for r in rows if r["acc1"] is not None]
    summary = {
        "files": len(rows),
        "failed": sum(1 for r in rows if r["error"]),
        "key_files": len(scored_keys),
        "key_weighted_score": mean(r["key_score"] for r in scored_keys),
        "key_counts": {c: sum(1 for r in scored_keys if r["key_class"] == c) for c in KEY_SCORES},
        "bpm_files": len(scored_bpm),
        "bpm_acc1": mean(float(r["acc1"]) for r in scored_bpm),
        "bpm_acc2": mean(float(r["acc2"]) for r in scored_bpm),
    }
    return {"summary": summary, "files": rows}


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def format_table(result: dict) -> str:
    rows = result["files"]
    head = ["file", "ref bpm", "est bpm", "acc1", "acc2", "ref key", "est key", "key result", "score"]
    body = []
    for r in rows:
        if r["error"]:
            body.append([Path(r["path"]).name, "", "", "", "", "", "", "ERROR " + r["error"], ""])
            continue
        body.append([
            Path(r["path"]).name,
            "" if r["ref_bpm"] is None else f"{r['ref_bpm']:g}",
            f"{r['est_bpm']:.1f}",
            "" if r["acc1"] is None else ("yes" if r["acc1"] else "no"),
            "" if r["acc2"] is None else ("yes" if r["acc2"] else "no"),
            r["ref_key"], r["est_key"], r["key_class"] or "",
            "" if r["key_score"] is None else f"{r['key_score']:.1f}",
        ])
    widths = [max(len(head[i]), *(len(b[i]) for b in body)) if body else len(head[i])
              for i in range(len(head))]
    line = lambda cells: "  ".join(c.ljust(w) for c, w in zip(cells, widths)).rstrip()  # noqa: E731
    out = [line(head), line(["-" * w for w in widths]), *(line(b) for b in body), ""]
    s = result["summary"]
    kc = ", ".join(f"{k} {v}" for k, v in s["key_counts"].items())
    out.append(f"Files: {s['files']} ({s['failed']} failed)")
    out.append(f"Key  ({s['key_files']} files): weighted score {_pct(s['key_weighted_score'])}  [{kc}]")
    out.append(f"BPM  ({s['bpm_files']} files): Acc1 {_pct(s['bpm_acc1'])}  Acc2 {_pct(s['bpm_acc2'])}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate key/BPM detection on labelled files.")
    ap.add_argument("labels", type=Path, help="CSV or JSON file with path, bpm, key")
    ap.add_argument("--json", action="store_true", help="print machine-readable JSON instead of a table")
    args = ap.parse_args(argv)
    try:
        labels = load_labels(args.labels)
    except (OSError, ValueError, KeyError) as e:
        print(f"error reading labels: {e}", file=sys.stderr)
        return 2
    result = evaluate(labels)
    print(json.dumps(result, indent=2) if args.json else format_table(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
