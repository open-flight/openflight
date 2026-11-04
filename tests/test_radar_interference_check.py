"""Unit tests for radar_interference_check script."""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import pytest

_script_path = (
    Path(__file__).resolve().parents[1] / "scripts" / "analysis" / "radar_interference_check.py"
)
_spec = importlib.util.spec_from_file_location("radar_interference_check", _script_path)
radar_interference_check = importlib.util.module_from_spec(_spec)
sys.modules["radar_interference_check"] = radar_interference_check
_spec.loader.exec_module(radar_interference_check)

InterferenceComparison = radar_interference_check.InterferenceComparison
InterferenceReport = radar_interference_check.InterferenceReport
PhaseStats = radar_interference_check.PhaseStats
analyze_interference_session = radar_interference_check.analyze_interference_session
compare_phases = radar_interference_check.compare_phases
compute_phase_stats = radar_interference_check.compute_phase_stats
format_markdown_report = radar_interference_check.format_markdown_report
generate_synthetic_interference_session = (
    radar_interference_check.generate_synthetic_interference_session
)
load_shots = radar_interference_check.load_shots


class TestPhaseStatsComputation:
    """Test phase summary metrics extraction."""

    def test_compute_stats_from_clean_shots(self):
        shots = [
            {
                "ball_speed_mph": 150.0,
                "spin_rpm": 2500.0,
                "spin_snr": 18.0,
                "trigger_latency_ms": 0.12,
            },
            {
                "ball_speed_mph": 152.0,
                "spin_rpm": 2550.0,
                "spin_snr": 19.0,
                "trigger_latency_ms": 0.11,
            },
            {
                "ball_speed_mph": 149.0,
                "spin_rpm": None,
                "spin_snr": 5.0,
                "trigger_latency_ms": 0.13,
            },
        ]
        stats = compute_phase_stats(shots, "Phase A")
        assert stats.total_shots == 3
        assert stats.spin_read_count == 2
        assert stats.spin_read_rate_pct == pytest.approx(66.7, 0.1)
        assert stats.spin_snr_mean_db == 14.0
        assert stats.ball_speed_mean_mph == pytest.approx(150.33, 0.01)

    def test_compute_stats_empty_shots(self):
        stats = compute_phase_stats([], "Phase Empty")
        assert stats.total_shots == 0
        assert stats.spin_read_rate_pct == 0.0
        assert stats.spin_snr_mean_db is None


class TestPhaseComparisonAndSeverity:
    """Test degradation calculations and recommendation classification."""

    def test_clean_interference_classification(self):
        stats_a = PhaseStats(
            phase_name="A",
            total_shots=15,
            spin_read_count=14,
            spin_read_rate_pct=93.3,
            spin_snr_mean_db=18.5,
            spin_snr_median_db=18.5,
            spin_snr_std_db=1.2,
            ball_speed_mean_mph=155.0,
            ball_speed_std_mph=1.5,
            trigger_latency_mean_ms=0.12,
            trigger_latency_std_ms=0.02,
        )
        stats_ab = PhaseStats(
            phase_name="AB",
            total_shots=15,
            spin_read_count=14,
            spin_read_rate_pct=93.3,
            spin_snr_mean_db=18.3,
            spin_snr_median_db=18.3,
            spin_snr_std_db=1.3,
            ball_speed_mean_mph=155.1,
            ball_speed_std_mph=1.6,
            trigger_latency_mean_ms=0.12,
            trigger_latency_std_ms=0.02,
        )
        comp = compare_phases(stats_a, stats_ab)
        assert comp.severity == "clean"
        assert comp.is_snr_degraded is False
        assert comp.is_read_rate_degraded is False
        assert "Zero significant RF interference" in comp.recommendation

    def test_severe_interference_classification(self):
        stats_a = PhaseStats(
            phase_name="A",
            total_shots=15,
            spin_read_count=14,
            spin_read_rate_pct=93.3,
            spin_snr_mean_db=18.5,
            spin_snr_median_db=18.5,
            spin_snr_std_db=1.2,
            ball_speed_mean_mph=155.0,
            ball_speed_std_mph=1.5,
            trigger_latency_mean_ms=0.12,
            trigger_latency_std_ms=0.02,
        )
        stats_ab = PhaseStats(
            phase_name="AB",
            total_shots=15,
            spin_read_count=8,
            spin_read_rate_pct=53.3,
            spin_snr_mean_db=13.0,
            spin_snr_median_db=13.0,
            spin_snr_std_db=2.5,
            ball_speed_mean_mph=154.5,
            ball_speed_std_mph=3.5,
            trigger_latency_mean_ms=0.15,
            trigger_latency_std_ms=0.04,
        )
        comp = compare_phases(stats_a, stats_ab)
        assert comp.severity == "severe"
        assert comp.is_snr_degraded is True
        assert comp.is_read_rate_degraded is True
        assert "alternating-shot capture protocol" in comp.recommendation


class TestEndToEndReportGeneration:
    """Test full session analysis and report generation."""

    def test_synthetic_session_generation_and_report(self):
        shots_a, shots_b, shots_ab = generate_synthetic_interference_session(
            simulated_interference_level="clean",
            n_shots=15,
            seed=42,
        )
        report = analyze_interference_session(shots_a, shots_b, shots_ab)
        assert report.phase_a.total_shots == 15
        assert report.phase_ab.total_shots == 15
        assert report.comparison.severity == "clean"

        md = format_markdown_report(report)
        assert "# Radar Interference Check: A/B/AB Protocol Report" in md
        assert "Phase Summary Statistics" in md
        assert "Diagnostic Evaluation" in md

    def test_file_loader_jsonl_and_csv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jsonl_path = Path(tmpdir) / "phase_a.jsonl"
            jsonl_path.write_text(
                json.dumps(
                    {
                        "event": "shot_detected",
                        "ball_speed_mph": 150.0,
                        "spin_rpm": 2500.0,
                        "spin_snr": 18.0,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            shots_jsonl = load_shots(jsonl_path)
            assert len(shots_jsonl) == 1
            assert shots_jsonl[0]["ball_speed_mph"] == 150.0

            csv_path = Path(tmpdir) / "phase_b.csv"
            csv_path.write_text(
                "ball_speed,spin_rpm,spin_snr\n152.0,2600.0,19.0\n",
                encoding="utf-8",
            )
            shots_csv = load_shots(csv_path)
            assert len(shots_csv) == 1
            assert shots_csv[0]["ball_speed_mph"] == 152.0
