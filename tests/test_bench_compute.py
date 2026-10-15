"""Tests for the compute timing tool."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "analysis"))

import bench_compute as bc  # noqa: E402  pylint: disable=wrong-import-position

CAPTURES = str(Path(__file__).parent.parent / "session_logs" / "capture_*.pkl")

pytestmark = pytest.mark.skipif(
    not list(Path(CAPTURES).parent.glob("capture_*.pkl")),
    reason="tracked OPS capture pickles not present",
)


def test_load_captures_returns_4096_sample_iq_captures():
    captures = bc.load_captures(CAPTURES)

    assert len(captures) > 0
    assert all(len(c.i_samples) == 4096 and len(c.q_samples) == 4096 for c in captures)


def test_summarize_reports_min_median_max():
    assert bc.summarize([3.0, 1.0, 2.0]) == {
        "n": 3,
        "min_ms": 1.0,
        "median_ms": 2.0,
        "max_ms": 3.0,
    }


def test_ballistics_grid_covers_every_combination():
    grid = bc.ballistics_grid()

    assert len(grid) == (len(bc.BALL_SPEEDS_MPH) * len(bc.SPIN_RPMS) * len(bc.LAUNCH_ANGLES_DEG))


def test_bench_ballistics_times_every_grid_point():
    result = bc.bench_ballistics(iterations=2, warmup=0)

    assert len(result["points"]) == len(bc.ballistics_grid())
    assert result["all"]["n"] == 2 * len(bc.ballistics_grid())
    assert result["all"]["min_ms"] > 0


def test_bench_captures_splits_fft_from_total():
    captures = bc.load_captures(CAPTURES)[:1]

    result = bc.bench_captures(captures, iterations=2, warmup=0)

    assert len(result) == 1
    assert isinstance(result[0]["processed"], bool)
    assert result[0]["total"]["n"] == 2
    assert result[0]["fft_speed"]["median_ms"] > 0
    assert result[0]["spin_and_rest_median_ms"] == pytest.approx(
        max(0.0, result[0]["total"]["median_ms"] - result[0]["fft_speed"]["median_ms"])
    )


def test_run_sustained_logs_one_row_per_interval_without_real_sleeping():
    captures = bc.load_captures(CAPTURES)[:1]
    now = [0.0]
    slept = []

    def clock():
        return now[0]

    def fake_sleep(seconds):
        slept.append(seconds)
        now[0] += 30.0

    shots = bc.run_sustained(captures, interval_s=30.0, minutes=1.5, sleep=fake_sleep, clock=clock)

    assert [s["shot"] for s in shots] == [0, 1, 2]
    assert all(s["latency_ms"] == s["process_ms"] + s["ballistics_ms"] for s in shots)
    assert slept == [30.0, 30.0, 30.0]


def test_main_writes_json_report(tmp_path, capsys):
    out = tmp_path / "bench.json"

    code = bc.main(["--captures", CAPTURES, "-n", "1", "--warmup", "0", "--json-out", str(out)])

    report = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0
    assert report["iterations"] == 1
    assert report["captures"] and report["ballistics"]["points"]
    assert "ballistics" in capsys.readouterr().out


def test_main_fails_when_no_captures_match(tmp_path, capsys):
    code = bc.main(["--captures", str(tmp_path / "nothing_*.pkl")])

    assert code == 1
    assert "No captures found" in capsys.readouterr().err


def test_main_rejects_zero_iterations():
    with pytest.raises(SystemExit):
        bc.main(["-n", "0"])
