#!/usr/bin/env python3
"""Driver ghost-track gate validator and recovery analysis tool.

Evaluates the ghost-track rejection gate and OPS-guided fast-track recovery
algorithm for TI IWR6843 driver launch angle measurements against truth data.

Background:
    On high-speed driver shots (140-180 mph ball speed on OPS243), the 60 GHz
    radar tracker can occasionally lock onto slower clubhead or reflection
    artifacts (50-65 mph), yielding severe launch angle bias (+3.39 deg bias,
    3.55 deg MAE). The ghost-track gate rejects candidates where:
        track_speed < min_speed_ratio * ops_ball_speed (default 0.65)
    and recovers the true ball track from candidate tracks within a fractional
    window around the OPS ball speed.

Usage::

    uv run python scripts/analysis/validate_ghost_track_gate.py \\
        --dataset session_logs/driver_truth_session.jsonl \\
        --min-speed-ratio 0.65 \\
        --output validation_report.md

Or test with synthetic truth data::

    uv run python scripts/analysis/validate_ghost_track_gate.py --synthetic
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class CandidateTrack:
    """Individual radar track candidate."""

    track_id: int
    speed_mph: float
    launch_angle_deg: float
    point_count: int = 10
    snr_db: float = 18.0
    explained_fraction: float = 0.85


@dataclass
class DriverShot:
    """Driver shot record containing OPS measurement, reference truth, and radar tracks."""

    shot_id: int
    ops_ball_speed_mph: float
    true_launch_angle_deg: float
    primary_track_speed_mph: float
    primary_launch_angle_deg: float
    candidate_tracks: List[CandidateTrack] = field(default_factory=list)
    club: str = "driver"


@dataclass
class GateEvaluationResult:
    """Statistical summary of ghost-track gate validation."""

    total_shots: int
    ghost_tracks_detected: int
    ghost_tracks_recovered: int
    ungated_mae_deg: float
    ungated_bias_deg: float
    gated_mae_deg: float
    gated_bias_deg: float
    improvement_deg: float
    recovery_rate_pct: float
    details: List[Dict[str, Any]] = field(default_factory=list)


def is_ghost_track(
    track_speed_mph: float,
    ops_ball_speed_mph: float,
    min_speed_ratio: float = 0.65,
) -> bool:
    """Check if a radar track is a slow ghost track relative to OPS ball speed."""
    if ops_ball_speed_mph <= 0:
        return False
    ratio = track_speed_mph / ops_ball_speed_mph
    return ratio < min_speed_ratio


def recover_fast_track(
    candidate_tracks: List[CandidateTrack],
    ops_ball_speed_mph: float,
    recovery_window: float = 0.15,
    min_points: int = 6,
    min_snr_db: float = 10.0,
) -> Optional[CandidateTrack]:
    """Find a candidate track matching the OPS ball speed within the recovery window."""
    if not candidate_tracks or ops_ball_speed_mph <= 0:
        return None

    min_speed = ops_ball_speed_mph * (1.0 - recovery_window)
    max_speed = ops_ball_speed_mph * (1.0 + recovery_window)

    valid_candidates = [
        t
        for t in candidate_tracks
        if min_speed <= t.speed_mph <= max_speed
        and t.point_count >= min_points
        and t.snr_db >= min_snr_db
    ]

    if not valid_candidates:
        return None

    # Pick candidate closest to OPS ball speed with highest point count tie-breaker
    valid_candidates.sort(
        key=lambda t: (abs(t.speed_mph - ops_ball_speed_mph), -t.point_count),
    )
    return valid_candidates[0]


def evaluate_ghost_track_gate(
    shots: Iterable[DriverShot | Dict[str, Any]],
    min_speed_ratio: float = 0.65,
    recovery_window: float = 0.15,
) -> GateEvaluationResult:
    """Evaluate ghost-track gating and recovery across a collection of shots."""
    parsed_shots: List[DriverShot] = []
    for s in shots:
        if isinstance(s, DriverShot):
            parsed_shots.append(s)
        elif isinstance(s, dict):
            # Parse dict representation
            raw_candidates = s.get("candidate_tracks", [])
            cands = []
            for c in raw_candidates:
                if isinstance(c, CandidateTrack):
                    cands.append(c)
                elif isinstance(c, dict):
                    cands.append(
                        CandidateTrack(
                            track_id=int(c.get("track_id", 0)),
                            speed_mph=float(c.get("speed_mph", 0.0)),
                            launch_angle_deg=float(c.get("launch_angle_deg", 0.0)),
                            point_count=int(c.get("point_count", 10)),
                            snr_db=float(c.get("snr_db", 18.0)),
                            explained_fraction=float(c.get("explained_fraction", 0.85)),
                        ),
                    )
            parsed_shots.append(
                DriverShot(
                    shot_id=int(s.get("shot_id", len(parsed_shots) + 1)),
                    ops_ball_speed_mph=float(s.get("ops_ball_speed_mph", 0.0)),
                    true_launch_angle_deg=float(s.get("true_launch_angle_deg", 0.0)),
                    primary_track_speed_mph=float(s.get("primary_track_speed_mph", 0.0)),
                    primary_launch_angle_deg=float(s.get("primary_launch_angle_deg", 0.0)),
                    candidate_tracks=cands,
                    club=str(s.get("club", "driver")),
                ),
            )

    if not parsed_shots:
        return GateEvaluationResult(
            total_shots=0,
            ghost_tracks_detected=0,
            ghost_tracks_recovered=0,
            ungated_mae_deg=0.0,
            ungated_bias_deg=0.0,
            gated_mae_deg=0.0,
            gated_bias_deg=0.0,
            improvement_deg=0.0,
            recovery_rate_pct=0.0,
            details=[],
        )

    ungated_errors: List[float] = []
    gated_errors: List[float] = []
    ghost_count = 0
    recovered_count = 0
    shot_details: List[Dict[str, Any]] = []

    for shot in parsed_shots:
        ungated_err = shot.primary_launch_angle_deg - shot.true_launch_angle_deg
        ungated_errors.append(ungated_err)

        is_ghost = is_ghost_track(
            shot.primary_track_speed_mph,
            shot.ops_ball_speed_mph,
            min_speed_ratio=min_speed_ratio,
        )

        resolved_angle = shot.primary_launch_angle_deg
        resolved_source = "primary"

        if is_ghost:
            ghost_count += 1
            recovered = recover_fast_track(
                shot.candidate_tracks,
                shot.ops_ball_speed_mph,
                recovery_window=recovery_window,
            )
            if recovered is not None:
                recovered_count += 1
                resolved_angle = recovered.launch_angle_deg
                resolved_source = "recovered_candidate"
            else:
                # Fallback to model estimate when no valid fast candidate exists
                # Driver baseline launch model: ~11.5 deg
                resolved_angle = 11.5
                resolved_source = "model_fallback"

        gated_err = resolved_angle - shot.true_launch_angle_deg
        gated_errors.append(gated_err)

        shot_details.append(
            {
                "shot_id": shot.shot_id,
                "ops_ball_speed": shot.ops_ball_speed_mph,
                "primary_speed": shot.primary_track_speed_mph,
                "true_angle": shot.true_launch_angle_deg,
                "ungated_angle": shot.primary_launch_angle_deg,
                "resolved_angle": resolved_angle,
                "resolved_source": resolved_source,
                "is_ghost": is_ghost,
                "ungated_error": round(ungated_err, 2),
                "gated_error": round(gated_err, 2),
            },
        )

    ungated_mae = statistics.mean(abs(e) for e in ungated_errors)
    ungated_bias = statistics.mean(ungated_errors)
    gated_mae = statistics.mean(abs(e) for e in gated_errors)
    gated_bias = statistics.mean(gated_errors)
    improvement = ungated_mae - gated_mae
    recovery_rate = (recovered_count / ghost_count * 100.0) if ghost_count > 0 else 100.0

    return GateEvaluationResult(
        total_shots=len(parsed_shots),
        ghost_tracks_detected=ghost_count,
        ghost_tracks_recovered=recovered_count,
        ungated_mae_deg=round(ungated_mae, 3),
        ungated_bias_deg=round(ungated_bias, 3),
        gated_mae_deg=round(gated_mae, 3),
        gated_bias_deg=round(gated_bias, 3),
        improvement_deg=round(improvement, 3),
        recovery_rate_pct=round(recovery_rate, 1),
        details=shot_details,
    )


def generate_synthetic_driver_dataset(
    n_shots: int = 40,
    ghost_fraction: float = 0.25,
    seed: int = 42,
) -> List[DriverShot]:
    """Generate realistic driver shots with known truth launch angles and ghost tracks."""
    rng = random.Random(seed)
    shots: List[DriverShot] = []

    for i in range(1, n_shots + 1):
        ops_speed = rng.uniform(145.0, 175.0)
        true_angle = rng.uniform(9.5, 14.5)

        is_ghost_shot = rng.random() < ghost_fraction
        if is_ghost_shot:
            # Ghost track: 48-65 mph with high launch angle artifact (18-28 deg)
            primary_speed = rng.uniform(48.0, 65.0)
            primary_angle = rng.uniform(18.0, 26.0)

            # In 80% of ghost shots, a valid fast ball track exists among candidates
            has_recoverable_track = rng.random() < 0.80
            candidates = [
                CandidateTrack(
                    track_id=1,
                    speed_mph=primary_speed,
                    launch_angle_deg=primary_angle,
                    point_count=12,
                    snr_db=19.0,
                ),
            ]
            if has_recoverable_track:
                ball_cand_speed = ops_speed * rng.uniform(0.97, 1.03)
                ball_cand_angle = true_angle + rng.gauss(0.0, 0.6)
                candidates.append(
                    CandidateTrack(
                        track_id=2,
                        speed_mph=ball_cand_speed,
                        launch_angle_deg=ball_cand_angle,
                        point_count=rng.randint(8, 14),
                        snr_db=rng.uniform(14.0, 22.0),
                    ),
                )
        else:
            # Clean track: matches OPS speed within 3%, launch angle close to truth
            primary_speed = ops_speed * rng.uniform(0.98, 1.02)
            primary_angle = true_angle + rng.gauss(0.0, 0.5)
            candidates = [
                CandidateTrack(
                    track_id=1,
                    speed_mph=primary_speed,
                    launch_angle_deg=primary_angle,
                    point_count=rng.randint(10, 16),
                    snr_db=rng.uniform(16.0, 24.0),
                ),
            ]

        shots.append(
            DriverShot(
                shot_id=i,
                ops_ball_speed_mph=round(ops_speed, 1),
                true_launch_angle_deg=round(true_angle, 1),
                primary_track_speed_mph=round(primary_speed, 1),
                primary_launch_angle_deg=round(primary_angle, 1),
                candidate_tracks=candidates,
            ),
        )

    return shots


def format_markdown_report(result: GateEvaluationResult) -> str:
    """Format gate evaluation results into GitHub-flavored Markdown."""
    lines = [
        "# Driver Ghost-Track Gate & Fast-Track Recovery Validation Report",
        "",
        "## Summary Metrics",
        "",
        "| Metric | Ungated Baseline | Gated + Recovery | Delta / Status |",
        "| --- | --- | --- | --- |",
        f"| **Total Driver Shots** | {result.total_shots} | {result.total_shots} | — |",
        f"| **Ghost Tracks Detected** | — | {result.ghost_tracks_detected} ({result.ghost_tracks_detected / max(result.total_shots, 1) * 100:.1f}%) | Flagged (< 65% OPS speed) |",
        f"| **Fast Tracks Recovered** | — | {result.ghost_tracks_recovered} | {result.recovery_rate_pct}% recovery rate |",
        f"| **Launch Angle MAE** | {result.ungated_mae_deg:.2f}° | {result.gated_mae_deg:.2f}° | **-{result.improvement_deg:.2f}° MAE** |",
        f"| **Launch Angle Bias** | {result.ungated_bias_deg:+.2f}° | {result.gated_bias_deg:+.2f}° | **{abs(result.gated_bias_deg) - abs(result.ungated_bias_deg):+.2f}° Bias** |",
        "",
        "## Shot Log Breakdown",
        "",
        "| Shot # | OPS Speed (mph) | Primary Speed (mph) | True V.LA | Ungated V.LA | Resolved V.LA | Resolution | Ungated Err | Gated Err |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for d in result.details:
        lines.append(
            f"| #{d['shot_id']} | {d['ops_ball_speed']} | {d['primary_speed']} | "
            f"{d['true_angle']}° | {d['ungated_angle']}° | {d['resolved_angle']}° | "
            f"{d['resolved_source']} | {d['ungated_error']:+0.2f}° | {d['gated_error']:+0.2f}° |"
        )

    return "\n".join(lines)


def load_dataset_from_file(file_path: str | Path) -> List[DriverShot]:
    """Load driver shots from JSONL or CSV."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Dataset file not found: {path}")

    shots: List[DriverShot] = []
    if path.suffix.lower() == ".jsonl":
        with path.open("r", encoding="utf-8") as f:
            for idx, line in enumerate(f, 1):
                line_s = line.strip()
                if not line_s:
                    continue
                data = json.loads(line_s)
                cands = [
                    CandidateTrack(**c) if isinstance(c, dict) else c
                    for c in data.get("candidate_tracks", [])
                ]
                shots.append(
                    DriverShot(
                        shot_id=data.get("shot_id", idx),
                        ops_ball_speed_mph=float(data.get("ops_ball_speed_mph", 0.0)),
                        true_launch_angle_deg=float(data.get("true_launch_angle_deg", 0.0)),
                        primary_track_speed_mph=float(data.get("primary_track_speed_mph", 0.0)),
                        primary_launch_angle_deg=float(data.get("primary_launch_angle_deg", 0.0)),
                        candidate_tracks=cands,
                        club=data.get("club", "driver"),
                    ),
                )
    elif path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader, 1):
                shots.append(
                    DriverShot(
                        shot_id=int(row.get("shot_number", row.get("shot_id", idx))),
                        ops_ball_speed_mph=float(
                            row.get("ops_ball_speed", row.get("ball_speed", 0.0))
                        ),
                        true_launch_angle_deg=float(
                            row.get("true_launch_angle", row.get("reference_launch_angle", 0.0))
                        ),
                        primary_track_speed_mph=float(
                            row.get("primary_track_speed", row.get("radar_speed", 0.0))
                        ),
                        primary_launch_angle_deg=float(
                            row.get("primary_launch_angle", row.get("radar_launch_angle", 0.0))
                        ),
                        candidate_tracks=[],
                        club=row.get("club", "driver"),
                    ),
                )
    else:
        raise ValueError(f"Unsupported file format: {path.suffix}")

    return shots


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Validate driver ghost-track gating and recovery against truth datasets.",
    )
    parser.add_argument(
        "--dataset",
        "-d",
        default=None,
        help="Path to session JSONL or comparison CSV with driver truth data.",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Generate synthetic driver dataset for testing.",
    )
    parser.add_argument(
        "--min-speed-ratio",
        type=float,
        default=0.65,
        help="Minimum speed ratio relative to OPS ball speed (default: 0.65).",
    )
    parser.add_argument(
        "--recovery-window",
        type=float,
        default=0.15,
        help="Search window around OPS ball speed for candidate recovery (default: 0.15).",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Path to write validation report (.md or .json).",
    )

    args = parser.parse_args()

    if args.dataset:
        shots = load_dataset_from_file(args.dataset)
    else:
        shots = generate_synthetic_driver_dataset(n_shots=40, ghost_fraction=0.25)

    result = evaluate_ghost_track_gate(
        shots=shots,
        min_speed_ratio=args.min_speed_ratio,
        recovery_window=args.recovery_window,
    )

    report_md = format_markdown_report(result)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.suffix.lower() == ".json":
            out_path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
        else:
            out_path.write_text(report_md, encoding="utf-8")
        print(f"Validation report saved to {out_path}")
    else:
        print(report_md)


if __name__ == "__main__":
    main()
