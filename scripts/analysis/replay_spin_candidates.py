#!/usr/bin/env python3
"""Replay raw captures and score spin candidates against TrackMan.

This is the offline harness for improving spin coverage without loosening
production thresholds blindly. It writes one CSV row per spin candidate so each
detector change can be compared against the same TrackMan session.

Usage:
    uv run --no-sync python scripts/analysis/replay_spin_candidates.py \
        --openflight session_logs/session_20260511_120001_range.jsonl \
        --trackman session_logs/Openflight-Test2.csv \
        --comparison session_logs/comparison_test2.csv \
        --output session_logs/spin_candidate_scoreboard_test2.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import compare_trackman as ct  # noqa: E402  pylint: disable=wrong-import-position

from openflight.clubs import ClubType  # noqa: E402
from openflight.rolling_buffer.monitor import get_optimal_spin_for_ball_speed  # noqa: E402
from openflight.rolling_buffer.processor import RollingBufferProcessor  # noqa: E402
from openflight.rolling_buffer.types import IQCapture  # noqa: E402


def _club_enum(normalized_club: str) -> ClubType:
    """Map compare_trackman's normalized club names to OpenFlight enums."""
    aliases = {
        "driver": ClubType.DRIVER,
        "3-wood": ClubType.WOOD_3,
        "5-wood": ClubType.WOOD_5,
        "7-wood": ClubType.WOOD_7,
        "3-hybrid": ClubType.HYBRID_3,
        "5-hybrid": ClubType.HYBRID_5,
        "7-hybrid": ClubType.HYBRID_7,
        "9-hybrid": ClubType.HYBRID_9,
        "2-iron": ClubType.IRON_2,
        "3-iron": ClubType.IRON_3,
        "4-iron": ClubType.IRON_4,
        "5-iron": ClubType.IRON_5,
        "6-iron": ClubType.IRON_6,
        "7-iron": ClubType.IRON_7,
        "8-iron": ClubType.IRON_8,
        "9-iron": ClubType.IRON_9,
        "pw": ClubType.PW,
        "gw": ClubType.GW,
        "sw": ClubType.SW,
        "lw": ClubType.LW,
    }
    return aliases.get(normalized_club, ClubType.UNKNOWN)


def _load_session_entries(path: Path) -> tuple[list[dict], list[dict]]:
    shots = []
    captures = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("type") == "shot_detected":
                shots.append(entry)
            elif entry.get("type") == "rolling_buffer_capture":
                captures.append(entry)
    return shots, captures


def _load_trackman_by_shot(args: argparse.Namespace) -> dict[int, dict[str, Any]]:
    if args.comparison:
        by_shot = {}
        with args.comparison.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                shot_number = _to_int(row.get("shot_number_of"))
                if shot_number is None:
                    continue
                by_shot[shot_number] = {
                    "match_quality": row.get("match_quality"),
                    "spin_tm": _to_float(row.get("spin_tm")),
                    "ball_speed_tm": _to_float(row.get("ball_speed_tm")),
                }
        return by_shot

    if not args.trackman:
        return {}

    of_shots = ct.load_openflight(args.openflight)
    tm_shots = ct.load_trackman(args.trackman)
    pairs = ct.pair_shots(of_shots, tm_shots, ball_speed_tol_mph=args.ball_speed_tol)
    by_shot = {}
    for pair in pairs:
        if pair.of is None or pair.of.shot_number is None:
            continue
        by_shot[pair.of.shot_number] = {
            "match_quality": pair.match_quality,
            "spin_tm": pair.tm.spin_rpm if pair.tm else None,
            "ball_speed_tm": pair.tm.ball_speed_mph if pair.tm else None,
        }
    return by_shot


def _to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    number = _to_float(value)
    return int(number) if number is not None else None


def _scoreboard_rows(
    shots: list[dict],
    captures: list[dict],
    trackman_by_shot: dict[int, dict[str, Any]],
    sample_rate_hz: int,
) -> list[dict[str, Any]]:
    processor = RollingBufferProcessor(sample_rate=sample_rate_hz)
    rows = []

    for shot_entry, capture_entry in zip(shots, captures):
        shot_data = shot_entry.get("data", shot_entry)
        shot_number = _to_int(shot_data.get("shot_number"))
        normalized_club = ct.normalize_club(shot_data.get("club"))
        club = _club_enum(normalized_club)
        capture = IQCapture(
            sample_time=capture_entry.get("sample_time", 0),
            trigger_time=capture_entry.get("trigger_time", 0),
            i_samples=capture_entry["i_samples"],
            q_samples=capture_entry["q_samples"],
        )
        result = processor.process_capture(
            capture,
            expected_spin_for_ball_speed=lambda ball_speed, club=club: (
                get_optimal_spin_for_ball_speed(ball_speed, club)
            ),
        )
        spin = result.spin if result else None
        tm = trackman_by_shot.get(shot_number or -1, {})
        tm_spin = tm.get("spin_tm")
        expected_spin = (
            get_optimal_spin_for_ball_speed(result.ball_speed_mph, club)
            if result else None
        )
        candidates = spin.candidates if spin else []
        if not candidates:
            rows.append(_row(
                shot_number=shot_number,
                club=normalized_club,
                result=result,
                spin=spin,
                trackman=tm,
                expected_spin=expected_spin,
                candidate=None,
            ))
            continue

        for candidate in candidates:
            rows.append(_row(
                shot_number=shot_number,
                club=normalized_club,
                result=result,
                spin=spin,
                trackman=tm,
                expected_spin=expected_spin,
                candidate=candidate,
                candidate_error=(
                    candidate.rpm - tm_spin
                    if tm_spin is not None else None
                ),
            ))

    return rows


def _row(
    shot_number: Optional[int],
    club: str,
    result: Any,
    spin: Any,
    trackman: dict[str, Any],
    expected_spin: Optional[float],
    candidate: Any,
    candidate_error: Optional[float] = None,
) -> dict[str, Any]:
    selected_rpm = spin.spin_rpm if spin and spin.spin_rpm > 0 else None
    return {
        "shot_number": shot_number,
        "club": club,
        "match_quality": trackman.get("match_quality"),
        "ball_speed_of": round(result.ball_speed_mph, 3) if result else None,
        "ball_speed_tm": trackman.get("ball_speed_tm"),
        "expected_spin_rpm": round(expected_spin) if expected_spin else None,
        "selected_spin_rpm": round(selected_rpm) if selected_rpm else None,
        "selected_spin_quality": spin.quality if spin else None,
        "selected_spin_snr": spin.snr if spin else None,
        "selected_at_lower_rail": spin.at_lower_rail if spin else None,
        "selected_at_upper_rail": spin.at_upper_rail if spin else None,
        "spin_rejection_reason": spin.rejection_reason if spin else None,
        "spin_tm": trackman.get("spin_tm"),
        "candidate_rank": candidate.rank if candidate else None,
        "candidate_rpm": round(candidate.rpm) if candidate else None,
        "candidate_snr": round(candidate.snr, 2) if candidate else None,
        "candidate_relative_magnitude": (
            round(candidate.relative_magnitude, 3) if candidate else None
        ),
        "candidate_expected_error_pct": (
            round(candidate.expected_spin_error_pct, 1)
            if candidate and candidate.expected_spin_error_pct is not None else None
        ),
        "candidate_at_lower_rail": candidate.at_lower_rail if candidate else None,
        "candidate_at_upper_rail": candidate.at_upper_rail if candidate else None,
        "candidate_selected": candidate.selected if candidate else None,
        "candidate_error_rpm": (
            round(candidate_error, 1) if candidate_error is not None else None
        ),
    }


def _write_csv(rows: list[dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _print_summary(rows: list[dict[str, Any]]) -> None:
    selected_trackman = [
        row for row in rows
        if row["candidate_selected"]
        and row["match_quality"] == "good"
        and row["spin_tm"] is not None
        and row["candidate_error_rpm"] is not None
        and not row["candidate_at_lower_rail"]
        and not row["candidate_at_upper_rail"]
    ]
    reported = [
        row for row in selected_trackman
        if row["selected_spin_rpm"] is not None
    ]
    errors = [float(row["candidate_error_rpm"]) for row in reported]
    print(f"Rows: {len(rows)}")
    print(f"Selected non-rail TrackMan candidates: {len(selected_trackman)}")
    print(f"Reported selected non-rail TrackMan pairs: {len(errors)}")
    if errors:
        print(f"Bias: {statistics.mean(errors):.1f} rpm")
        print(f"MAE: {statistics.mean(abs(err) for err in errors):.1f} rpm")
        print(f"RMSE: {math.sqrt(statistics.mean(err * err for err in errors)):.1f} rpm")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openflight", required=True, type=Path)
    parser.add_argument("--trackman", type=Path)
    parser.add_argument("--comparison", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sample-rate", type=int, default=30000)
    parser.add_argument("--ball-speed-tol", type=float, default=5.0)
    args = parser.parse_args()

    shots, captures = _load_session_entries(args.openflight)
    if len(shots) != len(captures):
        print(
            f"Warning: {len(shots)} shot entries but {len(captures)} captures; "
            "pairing by order",
            file=sys.stderr,
        )
    trackman_by_shot = _load_trackman_by_shot(args)
    rows = _scoreboard_rows(shots, captures, trackman_by_shot, args.sample_rate)
    _write_csv(rows, args.output)
    _print_summary(rows)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
