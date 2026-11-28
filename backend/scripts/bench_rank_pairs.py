"""Time matching.rank_pairs against rank_pairs_bruteforce on synthetic libraries.

    cd backend && python -m scripts.bench_rank_pairs [--drift] [library_size ...]

Prints wall time for both implementations and whether the top results agree
(same scores, best first). The brute-force version is quadratic, so keep the
sizes modest (default 200 1000).

With --drift it also renders a drifting click track onto a steady grid with
render.quantize_stretch and prints sync.drift_report before and after. That
part needs librosa, pyrubberband and the rubberband binary; if one is missing
it is skipped with a note.
"""

from __future__ import annotations

import argparse
import random
import shutil
import sys
import time

import numpy as np

from app import matching

TOP = 20
MODES = ("major", "minor")


def make_library(n: int, rng: random.Random) -> list[dict]:
    return [{"id": i, "bpm": round(rng.uniform(70, 180), 1), "key_pc": rng.randrange(12),
             "mode": rng.choice(MODES), "title": f"track {i}", "artist": f"artist {i}"}
            for i in range(n)]


def timed(fn, *args, **kwargs):
    t0 = time.perf_counter()
    out = fn(*args, **kwargs)
    return out, time.perf_counter() - t0


def agree(fast: list[dict], slow: list[dict], top: int = TOP) -> bool:
    """Top scores must match. Ids are not compared, since tied pairs may come
    out in a different order."""
    a = [p["score"] for p in fast[:top]]
    b = [p["score"] for p in slow[:top]]
    return len(a) == len(b) and all(abs(x - y) < 1e-3 for x, y in zip(a, b))


def bench_rank(sizes: list[int]) -> bool:
    rng = random.Random(1)
    ok = True
    print(f"{'tracks':>7} {'pairs':>10} {'impl':>10} {'seconds':>8} {'top ok':>7}")
    for n in sizes:
        tracks = make_library(n, rng)
        slow, t_slow = timed(matching.rank_pairs_bruteforce, tracks, TOP)
        fast, t_fast = timed(matching.rank_pairs, tracks, TOP)
        same = agree(fast, slow)
        ok &= same
        pairs = n * (n - 1) // 2
        print(f"{n:>7} {pairs:>10} {'bruteforce':>10} {t_slow:>8.2f} {'':>7}")
        print(f"{n:>7} {pairs:>10} {'vectorized':>10} {t_fast:>8.2f} {'yes' if same else 'NO':>7}")
    return ok


def clicks(bpm: float, seconds: float, sr: int, seed: int, drift_to: float | None = None):
    """Kick-like clicks on every beat, with an optional tempo ramp."""
    rng = np.random.default_rng(seed)
    y = 0.01 * rng.standard_normal(int(seconds * sr))
    t, k = 0.0, 0
    while t < seconds:
        i = int(t * sr)
        n = min(int(0.05 * sr), y.size - i)
        amp = 1.0 if k % 4 == 0 else 0.55
        y[i:i + n] += amp * np.exp(-np.linspace(0, 7, n)) * np.sin(2 * np.pi * 80 * np.arange(n) / sr)
        cur = bpm if drift_to is None else bpm + (drift_to - bpm) * t / seconds
        t += 60.0 / cur
        k += 1
    return y.astype(np.float32)


def bench_drift(seconds: int = 40) -> None:
    try:
        import librosa  # noqa: F401
        import pyrubberband  # noqa: F401

        from app import render, structure, sync
    except ImportError as e:
        print(f"drift: skipped, missing dependency ({e.name})")
        return
    if shutil.which("rubberband") is None:
        print("drift: skipped, rubberband binary not found")
        return

    sr = sync.SR
    loose = clicks(120, seconds + 4, sr, seed=7, drift_to=126)
    steady = clicks(123, seconds, sr, seed=8)
    src_beats = structure.track_beats(loose, 123)
    dst_beats = structure.track_beats(steady, 123)
    pre = min(0.2, float(src_beats[0]), float(dst_beats[0]))
    start = src_beats[0] - pre
    seg = loose[int(start * sr):][None, :]
    tmap = render.beat_map(src_beats, dst_beats, start, pre, seg.shape[1], sr)
    if tmap is None:
        print("drift: skipped, beat_map rejected the synthetic beats")
        return

    def placed(y):
        return render.place(y[None, :], int((dst_beats[0] - pre) * sr), steady.size)[0]

    (warped, t_q) = timed(render.quantize_stretch, seg, sr, tmap, 0)
    before = sync.drift_report(placed(seg[0]), steady, 123)
    after = sync.drift_report(placed(warped[0]), steady, 123)
    print(f"\ndrift on a {seconds}s click track (120 -> 126 BPM take vs steady 123 BPM)")
    print(f"{'stage':>18} {'offset ms':>10} {'wander ms':>10} {'lock':>6}")
    for name, rep in (("before quantize", before), ("after quantize", after)):
        if rep is None:
            print(f"{name:>18} {'n/a':>10}")
        else:
            print(f"{name:>18} {rep['offset_ms']:>10} {rep['wander_ms']:>10} {rep['lock']:>6}")
    print(f"quantize_stretch took {t_q:.2f}s")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sizes", nargs="*", type=int, default=[200, 1000], help="library sizes")
    ap.add_argument("--drift", action="store_true", help="also report quantize drift")
    args = ap.parse_args(argv)
    ok = bench_rank(args.sizes)
    if args.drift:
        bench_drift()
    if not ok:
        print("top results disagree", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
