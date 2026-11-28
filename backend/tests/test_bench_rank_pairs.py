"""The rank_pairs benchmark script runs and the two implementations agree."""

from scripts import bench_rank_pairs


def test_bench_runs_on_tiny_library(capsys):
    assert bench_rank_pairs.main(["30", "60"]) == 0
    out = capsys.readouterr().out
    assert "vectorized" in out and "bruteforce" in out
    assert "NO" not in out
