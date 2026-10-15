#!/usr/bin/env python3
"""Time the per-shot compute path on the current machine (e.g. a Raspberry Pi).

Replays recorded OPS243 I/Q captures through RollingBufferProcessor and runs
the ballistics simulator over a small grid. Prints a short summary and writes
machine-readable JSON.

Usage:
    uv run python scripts/analysis/bench_compute.py
    uv run python scripts/analysis/bench_compute.py -n 50 --json-out bench.json
    uv run python scripts/analysis/bench_compute.py --sustained --interval-s 10 --minutes 5
"""

import argparse
import glob
import json
import logging
import os
import pickle
import platform
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from openflight.ballistics import LaunchConditions, simulate
from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.rolling_buffer.types import IQCapture

DEFAULT_CAPTURES = "session_logs/capture_*.pkl"
BALL_SPEEDS_MPH = (80.0, 120.0, 160.0)
SPIN_RPMS = (2500.0, 6000.0)
LAUNCH_ANGLES_DEG = (10.0, 18.0)


def load_captures(pattern: str) -> list[IQCapture]:
    """Load IQCapture objects from capture_iq.py pickles matching a glob."""
    captures = []
    for path in sorted(glob.glob(os.path.expanduser(pattern))):
        with open(path, "rb") as handle:
            data = pickle.load(handle)
        for entry in data["captures"]:
            captures.append(
                IQCapture(
                    sample_time=entry["radar_sample_time"],
                    trigger_time=entry["radar_trigger_time"],
                    i_samples=[int(v) for v in entry["i_samples"]],
                    q_samples=[int(v) for v in entry["q_samples"]],
                )
            )
    return captures


def time_ms(func, *args, **kwargs) -> float:
    """Wall time of one call in milliseconds."""
    start = time.perf_counter()
    func(*args, **kwargs)
    return (time.perf_counter() - start) * 1000.0


def summarize(samples_ms: list[float]) -> dict:
    """Min/median/max of timing samples."""
    return {
        "n": len(samples_ms),
        "min_ms": min(samples_ms),
        "median_ms": statistics.median(samples_ms),
        "max_ms": max(samples_ms),
    }


def bench_captures(captures: list[IQCapture], iterations: int, warmup: int) -> list[dict]:
    """Time process_capture, and its FFT/speed stage alone, for each capture.

    The stage split uses the processor's FFT/speed pass directly. The
    remainder (total minus fft_speed, floored at 0) is everything else in
    process_capture: _find_consistent_ball_speed, find_club_speed,
    estimate_impact and detect_spin_multitaper. Spin alone is not isolated.
    """
    processor = RollingBufferProcessor()
    results = []
    for index, capture in enumerate(captures):
        for _ in range(warmup):
            processor.process_capture(capture)
        total = [time_ms(processor.process_capture, capture) for _ in range(iterations)]
        fft = [
            time_ms(processor._process_overlapping_with_standard, capture)  # pylint: disable=protected-access
            for _ in range(iterations)
        ]
        results.append(
            {
                "capture": index,
                "processed": processor.process_capture(capture) is not None,
                "total": summarize(total),
                "fft_speed": summarize(fft),
                "spin_and_rest_median_ms": max(
                    0.0, statistics.median(total) - statistics.median(fft)
                ),
            }
        )
    return results


def ballistics_grid() -> list[LaunchConditions]:
    """Ball speed x spin x launch angle grid."""
    return [
        LaunchConditions(
            ball_speed_mph=speed,
            launch_angle_v=angle,
            launch_angle_h=0.0,
            spin_rpm=spin,
            spin_axis_deg=0.0,
            spin_source="measured",
        )
        for speed in BALL_SPEEDS_MPH
        for spin in SPIN_RPMS
        for angle in LAUNCH_ANGLES_DEG
    ]


def bench_ballistics(iterations: int, warmup: int) -> dict:
    """Time simulate() per grid point and over all points."""
    points = []
    all_samples = []
    for conditions in ballistics_grid():
        for _ in range(warmup):
            simulate(conditions)
        samples = [time_ms(simulate, conditions) for _ in range(iterations)]
        all_samples.extend(samples)
        points.append(
            {
                "ball_speed_mph": conditions.ball_speed_mph,
                "spin_rpm": conditions.spin_rpm,
                "launch_angle_v": conditions.launch_angle_v,
                **summarize(samples),
            }
        )
    return {"points": points, "all": summarize(all_samples)}


def run_sustained(captures, interval_s, minutes, sleep=time.sleep, clock=time.monotonic) -> list:
    """Replay one shot every interval_s for the given minutes; log per-shot latency."""
    processor = RollingBufferProcessor()
    conditions = ballistics_grid()
    deadline = clock() + minutes * 60.0
    shots = []
    while clock() < deadline:
        started = clock()
        capture = captures[len(shots) % len(captures)]
        process_ms = time_ms(processor.process_capture, capture)
        ballistics_ms = time_ms(simulate, conditions[len(shots) % len(conditions)])
        shots.append(
            {
                "shot": len(shots),
                "unix_time": time.time(),
                "process_ms": process_ms,
                "ballistics_ms": ballistics_ms,
                "latency_ms": process_ms + ballistics_ms,
            }
        )
        print(f"shot {len(shots)}: {shots[-1]['latency_ms']:.1f} ms", flush=True)
        sleep(max(0.0, interval_s - (clock() - started)))
    return shots


def machine_info() -> dict:
    """Identify the machine the numbers came from."""
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
    }


def print_summary(report: dict) -> None:
    """Print the short human summary of a report."""
    info = report["machine"]
    print(f"{info['machine']} / {info['cpu_count']} cpu / python {info['python']}")
    for item in report.get("captures", []):
        total, fft = item["total"], item["fft_speed"]
        print(
            f"capture {item['capture']}{'' if item['processed'] else ' (no shot found)'}: total min/med/max "
            f"{total['min_ms']:.1f}/{total['median_ms']:.1f}/{total['max_ms']:.1f} ms; "
            f"fft+speed median {fft['median_ms']:.1f} ms; "
            f"spin+rest median {item['spin_and_rest_median_ms']:.1f} ms"
        )
    if "ballistics" in report:
        overall = report["ballistics"]["all"]
        print(
            f"ballistics ({len(report['ballistics']['points'])} points) min/med/max "
            f"{overall['min_ms']:.1f}/{overall['median_ms']:.1f}/{overall['max_ms']:.1f} ms"
        )
    if "sustained" in report:
        latencies = [s["latency_ms"] for s in report["sustained"]]
        print(
            f"sustained: {len(latencies)} shots, latency min/med/max "
            f"{min(latencies):.1f}/{statistics.median(latencies):.1f}/{max(latencies):.1f} ms"
        )


def main(argv=None) -> int:
    """Run the benchmark CLI; returns the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--captures", default=DEFAULT_CAPTURES, help="glob of capture .pkl files")
    parser.add_argument("-n", "--iterations", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--json-out", default=None, help="write the JSON report here")
    parser.add_argument("--sustained", action="store_true", help="replay one shot every interval")
    parser.add_argument("--interval-s", type=float, default=10.0)
    parser.add_argument("--minutes", type=float, default=5.0)
    args = parser.parse_args(argv)

    if args.iterations < 1 or args.warmup < 0:
        parser.error("--iterations must be >= 1 and --warmup >= 0")
    logging.disable(logging.WARNING)

    captures = load_captures(args.captures)
    if not captures:
        print(f"No captures found for {args.captures}", file=sys.stderr)
        return 1

    report = {"machine": machine_info(), "iterations": args.iterations}
    if args.sustained:
        report["sustained"] = run_sustained(captures, args.interval_s, args.minutes)
    else:
        report["captures"] = bench_captures(captures, args.iterations, args.warmup)
        report["ballistics"] = bench_ballistics(args.iterations, args.warmup)

    print_summary(report)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
