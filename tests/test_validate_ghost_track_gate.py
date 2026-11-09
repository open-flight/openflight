"""Unit tests for validate_ghost_track_gate script."""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

_script_path = (
    Path(__file__).resolve().parents[1] / "scripts" / "analysis" / "validate_ghost_track_gate.py"
)
_spec = importlib.util.spec_from_file_location("validate_ghost_track_gate", _script_path)
validate_ghost_track_gate = importlib.util.module_from_spec(_spec)
sys.modules["validate_ghost_track_gate"] = validate_ghost_track_gate
_spec.loader.exec_module(validate_ghost_track_gate)

CandidateTrack = validate_ghost_track_gate.CandidateTrack
DriverShot = validate_ghost_track_gate.DriverShot
GateEvaluationResult = validate_ghost_track_gate.GateEvaluationResult
evaluate_ghost_track_gate = validate_ghost_track_gate.evaluate_ghost_track_gate
format_markdown_report = validate_ghost_track_gate.format_markdown_report
generate_synthetic_driver_dataset = validate_ghost_track_gate.generate_synthetic_driver_dataset
is_ghost_track = validate_ghost_track_gate.is_ghost_track
load_dataset_from_file = validate_ghost_track_gate.load_dataset_from_file
recover_fast_track = validate_ghost_track_gate.recover_fast_track


class TestGhostTrackDetection:
    """Test ghost track identification logic."""

    def test_ghost_track_detected_when_speed_ratio_below_threshold(self):
        # 55 mph track on 160 mph OPS shot = 34.3% speed ratio (< 65%)
        assert (
            is_ghost_track(track_speed_mph=55.0, ops_ball_speed_mph=160.0, min_speed_ratio=0.65)
            is True
        )

    def test_valid_track_not_flagged_as_ghost(self):
        # 158 mph track on 160 mph OPS shot = 98.7% speed ratio
        assert (
            is_ghost_track(track_speed_mph=158.0, ops_ball_speed_mph=160.0, min_speed_ratio=0.65)
            is False
        )

    def test_zero_or_negative_ops_speed_handled(self):
        assert is_ghost_track(track_speed_mph=55.0, ops_ball_speed_mph=0.0) is False


class TestFastTrackRecovery:
    """Test candidate recovery logic."""

    def test_recover_valid_fast_track(self):
        candidates = [
            CandidateTrack(
                track_id=1, speed_mph=55.0, launch_angle_deg=22.0, point_count=10, snr_db=15.0
            ),
            CandidateTrack(
                track_id=2, speed_mph=159.0, launch_angle_deg=11.2, point_count=12, snr_db=20.0
            ),
        ]
        recovered = recover_fast_track(
            candidate_tracks=candidates,
            ops_ball_speed_mph=160.0,
            recovery_window=0.15,
        )
        assert recovered is not None
        assert recovered.track_id == 2
        assert recovered.launch_angle_deg == 11.2

    def test_no_recovery_when_no_candidates_in_window(self):
        candidates = [
            CandidateTrack(track_id=1, speed_mph=55.0, launch_angle_deg=22.0),
            CandidateTrack(track_id=2, speed_mph=80.0, launch_angle_deg=18.0),
        ]
        recovered = recover_fast_track(
            candidate_tracks=candidates,
            ops_ball_speed_mph=160.0,
            recovery_window=0.15,
        )
        assert recovered is None


class TestGateEvaluation:
    """Test full evaluation pipeline on synthetic driver shots."""

    def test_evaluation_reduces_mae_and_bias(self):
        shots = generate_synthetic_driver_dataset(n_shots=40, ghost_fraction=0.30, seed=42)
        result = evaluate_ghost_track_gate(shots, min_speed_ratio=0.65, recovery_window=0.15)

        assert result.total_shots == 40
        assert result.ghost_tracks_detected > 0
        assert result.ghost_tracks_recovered > 0
        assert result.gated_mae_deg < result.ungated_mae_deg
        assert result.improvement_deg > 0.5
        assert len(result.details) == 40

    def test_empty_shots_returns_zero_summary(self):
        result = evaluate_ghost_track_gate([])
        assert result.total_shots == 0
        assert result.ungated_mae_deg == 0.0

    def test_markdown_report_formatting(self):
        shots = generate_synthetic_driver_dataset(n_shots=10, ghost_fraction=0.30, seed=42)
        result = evaluate_ghost_track_gate(shots)
        report = format_markdown_report(result)
        assert "# Driver Ghost-Track Gate & Fast-Track Recovery Validation Report" in report
        assert "Launch Angle MAE" in report
        assert "Shot Log Breakdown" in report


class TestDatasetLoaders:
    """Test loading driver datasets from files."""

    def test_load_jsonl_dataset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.jsonl"
            sample_data = {
                "shot_id": 1,
                "ops_ball_speed_mph": 165.2,
                "true_launch_angle_deg": 11.5,
                "primary_track_speed_mph": 54.0,
                "primary_launch_angle_deg": 23.4,
                "candidate_tracks": [
                    {"track_id": 1, "speed_mph": 54.0, "launch_angle_deg": 23.4},
                    {"track_id": 2, "speed_mph": 164.0, "launch_angle_deg": 11.8},
                ],
            }
            path.write_text(json.dumps(sample_data) + "\n", encoding="utf-8")
            loaded = load_dataset_from_file(path)
            assert len(loaded) == 1
            assert loaded[0].ops_ball_speed_mph == 165.2
            assert len(loaded[0].candidate_tracks) == 2

    def test_load_csv_dataset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.csv"
            path.write_text(
                "shot_number,ops_ball_speed,true_launch_angle,primary_track_speed,primary_launch_angle\n"
                "1,155.0,12.0,154.5,12.2\n",
                encoding="utf-8",
            )
            loaded = load_dataset_from_file(path)
            assert len(loaded) == 1
            assert loaded[0].ops_ball_speed_mph == 155.0
            assert loaded[0].true_launch_angle_deg == 12.0
