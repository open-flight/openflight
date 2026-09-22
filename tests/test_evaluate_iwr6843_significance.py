"""Unit tests for evaluate_iwr6843_significance script."""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

_script_path = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "analysis"
    / "evaluate_iwr6843_significance.py"
)
_spec = importlib.util.spec_from_file_location("evaluate_iwr6843_significance", _script_path)
evaluate_iwr6843_significance = importlib.util.module_from_spec(_spec)
sys.modules["evaluate_iwr6843_significance"] = evaluate_iwr6843_significance
_spec.loader.exec_module(evaluate_iwr6843_significance)

AccuracyBenchmark = evaluate_iwr6843_significance.AccuracyBenchmark
RadarComparisonReport = evaluate_iwr6843_significance.RadarComparisonReport
RadarTechSpecs = evaluate_iwr6843_significance.RadarTechSpecs
format_markdown_report = evaluate_iwr6843_significance.format_markdown_report
generate_comparison_report = evaluate_iwr6843_significance.generate_comparison_report
get_iwr6843_accuracy = evaluate_iwr6843_significance.get_iwr6843_accuracy
get_iwr6843_specs = evaluate_iwr6843_significance.get_iwr6843_specs
get_kld7_accuracy = evaluate_iwr6843_significance.get_kld7_accuracy
get_kld7_specs = evaluate_iwr6843_significance.get_kld7_specs


class TestRadarSpecifications:
    """Test hardware and physical RF specs."""

    def test_kld7_specs(self):
        specs = get_kld7_specs()
        assert specs.rf_frequency_ghz == 24.125
        assert specs.rx_antennas == 2
        assert specs.hardware_units_needed == 2
        assert "Same 24 GHz" in specs.ops243_rf_interference_risk

    def test_iwr6843_specs(self):
        specs = get_iwr6843_specs()
        assert specs.rf_frequency_ghz == 60.0
        assert specs.virtual_channels == 12
        assert specs.hardware_units_needed == 1
        assert "Zero cross-talk" in specs.ops243_rf_interference_risk


class TestAccuracyBenchmarks:
    """Test accuracy numbers and improvement factors."""

    def test_iron_accuracy_improvement(self):
        kld7_acc = get_kld7_accuracy()
        iwr_acc = get_iwr6843_accuracy()

        assert iwr_acc.iron_launch_angle_mae_deg < 1.0
        assert iwr_acc.iron_launch_angle_mae_deg < kld7_acc.iron_launch_angle_mae_deg
        assert iwr_acc.club_path_supported is True
        assert kld7_acc.club_path_supported is False

    def test_comparison_report_generation(self):
        report = generate_comparison_report()
        assert report.iron_accuracy_improvement_factor >= 2.0
        assert report.is_upgrade_recommended is True
        assert len(report.key_findings) >= 5


class TestReportFormattingAndCli:
    """Test formatting and CLI execution."""

    def test_markdown_report_formatting(self):
        report = generate_comparison_report()
        md = format_markdown_report(report)
        assert "# Technical Evaluation: TI IWR6843 (60 GHz) vs K-LD7 (24 GHz)" in md
        assert "Discussion #161" in md
        assert "Hardware & RF Physical Comparison" in md
        assert "Empirical Accuracy & Field Benchmark Data" in md

    def test_cli_execution_markdown_and_json(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_md = Path(tmpdir) / "comp.md"
            out_json = Path(tmpdir) / "comp.json"

            # Test Markdown output
            monkeypatch.setattr(
                "sys.argv",
                [
                    "evaluate_iwr6843_significance.py",
                    "--output",
                    str(out_md),
                    "--format",
                    "markdown",
                ],
            )
            evaluate_iwr6843_significance.main()
            assert out_md.exists()
            assert "TI IWR6843" in out_md.read_text(encoding="utf-8")

            # Test JSON output
            monkeypatch.setattr(
                "sys.argv",
                ["evaluate_iwr6843_significance.py", "--output", str(out_json), "--format", "json"],
            )
            evaluate_iwr6843_significance.main()
            assert out_json.exists()
            data = json.loads(out_json.read_text(encoding="utf-8"))
            assert data["is_upgrade_recommended"] is True
            assert "iwr6843_specs" in data
