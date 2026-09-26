#!/usr/bin/env python3
"""Radar interference check analysis tool (A/B/AB Protocol).

Quantifies RF interference and degradation between OpenFlight (OPS243-A 24 GHz CW Doppler)
and a reference launch monitor radar (e.g., Rapsodo MLM2 Pro K-band radar) operating
in the same hitting enclosure.

Protocol Phases:
    - Phase A: OpenFlight only (baseline spin SNR, read rate, speed spread)
    - Phase B: Reference instrument only (standalone reference baseline)
    - Phase AB: Simultaneous joint operation (both radars transmitting)

Degradation Gates:
    - Spin SNR drop > 1.5 dB -> Warning
    - Spin SNR drop > 3.0 dB -> Severe Interference
    - Spin read rate drop > 10% -> Warning
    - Ball speed jitter increase > 2.0x -> Radar Mutual Jamming

Usage::

    uv run python scripts/analysis/radar_interference_check.py \\
        --phase-a session_logs/session_phase_a.jsonl \\
        --phase-b session_logs/session_phase_b.csv \\
        --phase-ab session_logs/session_phase_ab.jsonl \\
        --output notes/radar_interference_report.md

Or test with synthetic session data::

    uv run python scripts/analysis/radar_interference_check.py --synthetic
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class PhaseStats:
    """Summary statistics for one phase of the interference check protocol."""

    phase_name: str
    total_shots: int
    spin_read_count: int
    spin_read_rate_pct: float
    spin_snr_mean_db: Optional[float]
    spin_snr_median_db: Optional[float]
    spin_snr_std_db: Optional[float]
    ball_speed_mean_mph: float
    ball_speed_std_mph: float
    trigger_latency_mean_ms: Optional[float]
    trigger_latency_std_ms: Optional[float]


@dataclass
class InterferenceComparison:
    """Comparison metrics between baseline and joint phases."""

    spin_read_rate_delta_pct: float
    spin_snr_delta_db: float
    ball_speed_std_ratio: float
    is_snr_degraded: bool
    is_read_rate_degraded: bool
    severity: str  # "clean", "mild", "moderate", "severe"
    recommendation: str


@dataclass
class InterferenceReport:
    """Complete multi-phase interference protocol report."""

    phase_a: PhaseStats
    phase_b: Optional[PhaseStats]
    phase_ab: PhaseStats
    comparison: InterferenceComparison
    notes: List[str] = field(default_factory=list)


def compute_phase_stats(
    shots: List[Dict[str, Any]],
    phase_name: str,
) -> PhaseStats:
    """Compute summary statistics for a collection of shots in a protocol phase."""
    if not shots:
        return PhaseStats(
            phase_name=phase_name,
            total_shots=0,
            spin_read_count=0,
            spin_read_rate_pct=0.0,
            spin_snr_mean_db=None,
            spin_snr_median_db=None,
            spin_snr_std_db=None,
            ball_speed_mean_mph=0.0,
            ball_speed_std_mph=0.0,
            trigger_latency_mean_ms=None,
            trigger_latency_std_ms=None,
        )

    ball_speeds = [
        float(s["ball_speed_mph"])
        for s in shots
        if s.get("ball_speed_mph") is not None and float(s["ball_speed_mph"]) > 0
    ]

    spin_snrs = [
        float(s["spin_snr"])
        for s in shots
        if s.get("spin_snr") is not None and not math.isnan(float(s["spin_snr"]))
    ]

    valid_spins = [s for s in shots if s.get("spin_rpm") is not None and float(s["spin_rpm"]) > 0]

    latencies = [
        float(s["trigger_latency_ms"]) for s in shots if s.get("trigger_latency_ms") is not None
    ]

    speed_mean = statistics.mean(ball_speeds) if ball_speeds else 0.0
    speed_std = statistics.stdev(ball_speeds) if len(ball_speeds) > 1 else 0.0

    snr_mean = statistics.mean(spin_snrs) if spin_snrs else None
    snr_median = statistics.median(spin_snrs) if spin_snrs else None
    snr_std = statistics.stdev(spin_snrs) if len(spin_snrs) > 1 else 0.0 if spin_snrs else None

    lat_mean = statistics.mean(latencies) if latencies else None
    lat_std = statistics.stdev(latencies) if len(latencies) > 1 else 0.0 if latencies else None

    read_rate = (len(valid_spins) / len(shots) * 100.0) if shots else 0.0

    return PhaseStats(
        phase_name=phase_name,
        total_shots=len(shots),
        spin_read_count=len(valid_spins),
        spin_read_rate_pct=round(read_rate, 1),
        spin_snr_mean_db=round(snr_mean, 2) if snr_mean is not None else None,
        spin_snr_median_db=round(snr_median, 2) if snr_median is not None else None,
        spin_snr_std_db=round(snr_std, 2) if snr_std is not None else None,
        ball_speed_mean_mph=round(speed_mean, 2),
        ball_speed_std_mph=round(speed_std, 2),
        trigger_latency_mean_ms=round(lat_mean, 2) if lat_mean is not None else None,
        trigger_latency_std_ms=round(lat_std, 2) if lat_std is not None else None,
    )


def compare_phases(
    baseline_a: PhaseStats,
    joint_ab: PhaseStats,
) -> InterferenceComparison:
    """Compare Phase AB against baseline Phase A to determine RF degradation."""
    read_rate_delta = joint_ab.spin_read_rate_pct - baseline_a.spin_read_rate_pct

    snr_a = baseline_a.spin_snr_mean_db or 0.0
    snr_ab = joint_ab.spin_snr_mean_db or 0.0
    snr_delta = snr_ab - snr_a

    speed_std_a = baseline_a.ball_speed_std_mph if baseline_a.ball_speed_std_mph > 0 else 1.0
    speed_std_ratio = joint_ab.ball_speed_std_mph / speed_std_a

    is_snr_degraded = snr_delta < -1.5
    is_read_rate_degraded = read_rate_delta < -10.0

    # Determine severity
    if snr_delta < -3.0 or read_rate_delta < -25.0:
        severity = "severe"
        recommendation = (
            "High mutual RF jamming detected. Switch to alternating-shot capture protocol."
        )
    elif snr_delta < -1.5 or read_rate_delta < -10.0 or speed_std_ratio > 1.8:
        severity = "moderate"
        recommendation = (
            "Measurable degradation observed. Increase lateral separation between units to >= 1.5m."
        )
    elif snr_delta < -0.8 or read_rate_delta < -8.0:
        severity = "mild"
        recommendation = (
            "Minor noise floor elevation. Clear for joint validation with recorded SNR baseline."
        )
    else:
        severity = "clean"
        recommendation = (
            "Zero significant RF interference. Fully approved for paired cross-validation."
        )

    return InterferenceComparison(
        spin_read_rate_delta_pct=round(read_rate_delta, 1),
        spin_snr_delta_db=round(snr_delta, 2),
        ball_speed_std_ratio=round(speed_std_ratio, 2),
        is_snr_degraded=is_snr_degraded,
        is_read_rate_degraded=is_read_rate_degraded,
        severity=severity,
        recommendation=recommendation,
    )


def analyze_interference_session(
    phase_a_shots: List[Dict[str, Any]],
    phase_b_shots: Optional[List[Dict[str, Any]]],
    phase_ab_shots: List[Dict[str, Any]],
) -> InterferenceReport:
    """Run end-to-end interference protocol analysis."""
    stats_a = compute_phase_stats(phase_a_shots, "Phase A (OpenFlight Only)")
    stats_b = (
        compute_phase_stats(phase_b_shots, "Phase B (Reference Only)") if phase_b_shots else None
    )
    stats_ab = compute_phase_stats(phase_ab_shots, "Phase AB (Simultaneous Joint)")

    comparison = compare_phases(stats_a, stats_ab)

    notes = []
    if stats_a.total_shots < 10 or stats_ab.total_shots < 10:
        notes.append(
            "Protocol recommendation: At least 10 shots per phase are recommended for robust statistics."
        )
    if comparison.severity == "clean":
        notes.append(
            "OPS243-A Doppler radar and reference unit operate without measurable mutual desensitization."
        )

    return InterferenceReport(
        phase_a=stats_a,
        phase_b=stats_b,
        phase_ab=stats_ab,
        comparison=comparison,
        notes=notes,
    )


def generate_synthetic_interference_session(
    simulated_interference_level: str = "clean",
    n_shots: int = 15,
    seed: int = 101,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Generate synthetic shot records for phases A, B, and AB."""
    rng = random.Random(seed)

    def gen_shots(snr_center: float, read_prob: float, speed_jitter: float) -> List[Dict[str, Any]]:
        res = []
        for i in range(1, n_shots + 1):
            speed = rng.gauss(155.0, speed_jitter)
            has_spin = rng.random() < read_prob
            snr = rng.gauss(snr_center, 0.8) if has_spin else rng.uniform(4.0, 7.0)
            spin = rng.gauss(2600.0, 150.0) if has_spin else None
            res.append(
                {
                    "shot_id": i,
                    "ball_speed_mph": round(speed, 1),
                    "spin_rpm": round(spin, 0) if spin else None,
                    "spin_snr": round(snr, 1),
                    "trigger_latency_ms": round(rng.gauss(0.12, 0.02), 3),
                }
            )
        return res

    # Phase A: pristine baseline
    shots_a = gen_shots(snr_center=18.5, read_prob=0.95, speed_jitter=1.5)

    # Phase B: reference baseline
    shots_b = gen_shots(snr_center=18.5, read_prob=0.95, speed_jitter=1.5)

    # Phase AB
    if simulated_interference_level == "severe":
        shots_ab = gen_shots(snr_center=14.0, read_prob=0.60, speed_jitter=3.2)
    elif simulated_interference_level == "moderate":
        shots_ab = gen_shots(snr_center=16.5, read_prob=0.80, speed_jitter=2.2)
    else:  # clean
        shots_ab = gen_shots(snr_center=18.5, read_prob=0.95, speed_jitter=1.5)

    return shots_a, shots_b, shots_ab


def format_markdown_report(report: InterferenceReport) -> str:
    """Format interference analysis into Markdown."""
    lines = [
        "# Radar Interference Check: A/B/AB Protocol Report",
        "",
        f"**Interference Status:** `{report.comparison.severity.upper()}`",
        f"**Recommendation:** {report.comparison.recommendation}",
        "",
        "## Phase Summary Statistics",
        "",
        "| Metric | Phase A (OpenFlight Only) | Phase B (Reference Only) | Phase AB (Joint Simultaneous) | Delta (AB vs A) |",
        "| --- | --- | --- | --- | --- |",
        f"| **Shots Recorded** | {report.phase_a.total_shots} | {report.phase_b.total_shots if report.phase_b else 'N/A'} | {report.phase_ab.total_shots} | — |",
        f"| **Spin Read Rate** | {report.phase_a.spin_read_rate_pct}% | {report.phase_b.spin_read_rate_pct if report.phase_b else 'N/A'}% | {report.phase_ab.spin_read_rate_pct}% | **{report.comparison.spin_read_rate_delta_pct:+.1f}%** |",
        f"| **Spin SNR Mean** | {report.phase_a.spin_snr_mean_db or 'N/A'} dB | {report.phase_b.spin_snr_mean_db if report.phase_b else 'N/A'} dB | {report.phase_ab.spin_snr_mean_db or 'N/A'} dB | **{report.comparison.spin_snr_delta_db:+.2f} dB** |",
        f"| **Ball Speed Mean** | {report.phase_a.ball_speed_mean_mph} mph | {report.phase_b.ball_speed_mean_mph if report.phase_b else 'N/A'} mph | {report.phase_ab.ball_speed_mean_mph} mph | {report.phase_ab.ball_speed_mean_mph - report.phase_a.ball_speed_mean_mph:+.2f} mph |",
        f"| **Ball Speed StdDev** | {report.phase_a.ball_speed_std_mph} mph | {report.phase_b.ball_speed_std_mph if report.phase_b else 'N/A'} mph | {report.phase_ab.ball_speed_std_mph} mph | {report.comparison.ball_speed_std_ratio:.2f}x ratio |",
        "",
        "## Diagnostic Evaluation",
        "",
        f"- **Spin SNR Degradation Threshold (> 1.5 dB drop):** {'FAIL (Degraded)' if report.comparison.is_snr_degraded else 'PASS (Normal)'}",
        f"- **Spin Read Rate Degradation Threshold (> 10% drop):** {'FAIL (Degraded)' if report.comparison.is_read_rate_degraded else 'PASS (Normal)'}",
        "",
    ]

    if report.notes:
        lines.append("## Protocol Notes")
        for n in report.notes:
            lines.append(f"- {n}")
        lines.append("")

    return "\n".join(lines)


def load_shots(file_path: str | Path) -> List[Dict[str, Any]]:
    """Load shots from a JSONL or CSV file."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    shots: List[Dict[str, Any]] = []
    if path.suffix.lower() == ".jsonl":
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line_s = line.strip()
                if line_s:
                    data = json.loads(line_s)
                    if data.get("event") in ("shot_detected", "shot", None):
                        shots.append(data)
    elif path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                shots.append(
                    {
                        "ball_speed_mph": float(row["ball_speed"])
                        if row.get("ball_speed")
                        else None,
                        "spin_rpm": float(row["spin_rpm"]) if row.get("spin_rpm") else None,
                        "spin_snr": float(row["spin_snr"]) if row.get("spin_snr") else None,
                        "trigger_latency_ms": float(row["trigger_latency_ms"])
                        if row.get("trigger_latency_ms")
                        else None,
                    }
                )
    return shots


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Radar interference check between OpenFlight and reference launch monitor (A/B/AB protocol).",
    )
    parser.add_argument("--phase-a", help="Path to Phase A (OpenFlight only) session JSONL.")
    parser.add_argument("--phase-b", help="Path to Phase B (Reference only) session CSV/JSONL.")
    parser.add_argument("--phase-ab", help="Path to Phase AB (Joint simultaneous) session JSONL.")
    parser.add_argument(
        "--synthetic", action="store_true", help="Run with synthetic protocol data."
    )
    parser.add_argument(
        "--simulated-level",
        choices=["clean", "moderate", "severe"],
        default="clean",
        help="Simulated interference severity when using --synthetic (default: clean).",
    )
    parser.add_argument("--output", "-o", help="Path to output Markdown/JSON report.")

    args = parser.parse_args()

    if args.synthetic or (not args.phase_a and not args.phase_ab):
        shots_a, shots_b, shots_ab = generate_synthetic_interference_session(
            simulated_interference_level=args.simulated_level,
        )
    else:
        if not args.phase_a or not args.phase_ab:
            print(
                "Error: Both --phase-a and --phase-ab are required unless using --synthetic.",
                file=sys.stderr,
            )
            sys.exit(1)
        shots_a = load_shots(args.phase_a)
        shots_b = load_shots(args.phase_b) if args.phase_b else None
        shots_ab = load_shots(args.phase_ab)

    report = analyze_interference_session(
        phase_a_shots=shots_a,
        phase_b_shots=shots_b,
        phase_ab_shots=shots_ab,
    )

    md_report = format_markdown_report(report)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.suffix.lower() == ".json":
            out_path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
        else:
            out_path.write_text(md_report, encoding="utf-8")
        print(f"Interference report saved to {out_path}")
    else:
        print(md_report)


if __name__ == "__main__":
    main()
