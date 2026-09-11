"""Tests for session_logger module."""

import json
import threading
import time

import pytest

from openflight import session_logger as session_logger_module
from openflight.kld7.radc import RADC_PAYLOAD_BYTES
from openflight.session_logger import SessionLogger, log_session_error


class TestLogError:
    """Tests for session error logging."""

    def test_log_error_writes_entry_and_increments_stats(self, tmp_path):
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound")

        logger.log_error("capture loop failed", context={"component": "monitor"})

        entry = json.loads(logger.session_path.read_text().strip().split("\n")[-1])
        assert entry["type"] == "error"
        assert entry["error"] == "capture loop failed"
        assert entry["context"] == {"component": "monitor"}
        assert logger.stats["errors"] == 1

    def test_log_error_skipped_when_disabled(self, tmp_path):
        logger = SessionLogger(log_dir=tmp_path, enabled=False)
        logger.log_error("should not write")
        assert logger.stats["errors"] == 0
        assert logger.session_path is None


class TestLogSessionError:
    """Tests for the module-level session error helper."""

    def test_log_session_error_delegates_to_global_logger(self, tmp_path, monkeypatch):
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="mock", trigger_type="manual")
        monkeypatch.setattr(session_logger_module, "_session_logger", logger)

        log_session_error(
            "K-LD7 processing failed",
            component="server",
            context={"stage": "kld7"},
            exc=RuntimeError("boom"),
        )

        entry = json.loads(logger.session_path.read_text().strip().split("\n")[-1])
        assert entry["type"] == "error"
        assert entry["error"] == "K-LD7 processing failed"
        assert entry["context"]["component"] == "server"
        assert entry["context"]["stage"] == "kld7"
        assert entry["context"]["exception_type"] == "RuntimeError"
        assert entry["context"]["exception_message"] == "boom"

    def test_log_session_error_noop_without_global_logger(self, monkeypatch):
        monkeypatch.setattr(session_logger_module, "_session_logger", None)
        log_session_error("ignored")  # must not raise


class TestLogTriggerDiagnostic:
    """Tests for the trigger diagnostic logging method."""

    def test_accepted_diagnostic_writes_correct_entry(self, tmp_path):
        """Accepted trigger diagnostic should write all fields."""
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound-gpio")

        logger.log_trigger_diagnostic(
            trigger_type="sound-gpio",
            accepted=True,
            reason="accepted",
            response_bytes=32768,
            total_readings=32,
            outbound_readings=8,
            inbound_readings=24,
            peak_outbound_mph=155.3,
            peak_inbound_mph=45.0,
            all_outbound_speeds=[155.3, 140.2, 102.1],
            all_inbound_speeds=[45.0, 30.5],
            ball_speed_mph=155.3,
            club_speed_mph=103.2,
            spin_rpm=2800,
            carry_yards=265,
            latency_ms=12.5,
        )

        # Read back the JSONL file
        lines = logger.session_path.read_text().strip().split("\n")
        # Last line should be the trigger_diagnostic
        entry = json.loads(lines[-1])

        assert entry["type"] == "trigger_diagnostic"
        assert entry["trigger_type"] == "sound-gpio"
        assert entry["accepted"] is True
        assert entry["reason"] == "accepted"
        assert entry["response_bytes"] == 32768
        assert entry["total_readings"] == 32
        assert entry["outbound_readings"] == 8
        assert entry["inbound_readings"] == 24
        assert entry["peak_outbound_mph"] == 155.3
        assert entry["peak_inbound_mph"] == 45.0
        assert entry["ball_speed_mph"] == 155.3
        assert entry["club_speed_mph"] == 103.2
        assert entry["spin_rpm"] == 2800
        assert entry["carry_yards"] == 265
        assert entry["latency_ms"] == 12.5
        assert len(entry["all_outbound_speeds"]) == 3
        assert len(entry["all_inbound_speeds"]) == 2

    def test_rejected_diagnostic_writes_reason(self, tmp_path):
        """Rejected trigger diagnostic should include reason."""
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound-gpio")

        logger.log_trigger_diagnostic(
            trigger_type="sound-gpio",
            accepted=False,
            reason="no_outbound_speed",
            response_bytes=32768,
            total_readings=12,
            outbound_readings=0,
            inbound_readings=12,
            peak_outbound_mph=0.0,
            peak_inbound_mph=42.1,
        )

        lines = logger.session_path.read_text().strip().split("\n")
        entry = json.loads(lines[-1])

        assert entry["type"] == "trigger_diagnostic"
        assert entry["accepted"] is False
        assert entry["reason"] == "no_outbound_speed"
        assert entry["outbound_readings"] == 0
        assert entry["peak_inbound_mph"] == 42.1
        # Shot fields should be None/null
        assert entry["ball_speed_mph"] is None
        assert entry["club_speed_mph"] is None

    def test_no_response_diagnostic(self, tmp_path):
        """No-response trigger should log with minimal fields."""
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound-gpio")

        logger.log_trigger_diagnostic(
            trigger_type="sound-gpio",
            accepted=False,
            reason="no_response",
            response_bytes=0,
        )

        lines = logger.session_path.read_text().strip().split("\n")
        entry = json.loads(lines[-1])

        assert entry["type"] == "trigger_diagnostic"
        assert entry["accepted"] is False
        assert entry["reason"] == "no_response"
        assert entry["response_bytes"] == 0
        assert entry["total_readings"] == 0

    def test_stats_tracking(self, tmp_path):
        """Stats should track accepted/rejected counts."""
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound-gpio")

        logger.log_trigger_diagnostic(trigger_type="sound-gpio", accepted=True, reason="accepted")
        logger.log_trigger_diagnostic(
            trigger_type="sound-gpio", accepted=False, reason="no_response"
        )
        logger.log_trigger_diagnostic(
            trigger_type="sound-gpio", accepted=False, reason="no_outbound_speed"
        )

        assert logger.stats["triggers_total"] == 3
        assert logger.stats["triggers_accepted"] == 1
        assert logger.stats["triggers_rejected"] == 2

    def test_disabled_logger_skips_write(self, tmp_path):
        """Disabled logger should not write anything."""
        logger = SessionLogger(log_dir=tmp_path, enabled=False)

        logger.log_trigger_diagnostic(trigger_type="sound-gpio", accepted=True, reason="accepted")

        # No session file created when disabled
        assert logger.session_path is None

    def test_empty_speed_lists_default(self, tmp_path):
        """Speed lists should default to empty arrays."""
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound-gpio")

        logger.log_trigger_diagnostic(
            trigger_type="sound-gpio",
            accepted=False,
            reason="parse_failed",
        )

        lines = logger.session_path.read_text().strip().split("\n")
        entry = json.loads(lines[-1])

        assert entry["all_outbound_speeds"] == []
        assert entry["all_inbound_speeds"] == []


class TestLogShot:
    """Tests for shot logging."""

    def test_shot_uses_detection_time_shot_number(self, tmp_path):
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound")

        logger.log_shot(
            ball_speed_mph=150.0,
            club_speed_mph=100.0,
            smash_factor=1.5,
            estimated_carry_yards=250.0,
            club="driver",
            peak_magnitude=None,
            readings_count=0,
            shot_number=7,
            impact_timestamp=1234.5,
        )

        entry = json.loads(logger.session_path.read_text().strip().split("\n")[-1])
        assert entry["shot_number"] == 7
        assert entry["impact_timestamp"] == 1234.5
        assert logger.stats["shots_detected"] == 1

    def test_shot_logs_spin_diagnostics(self, tmp_path):
        """Shot entries should preserve rejected-spin diagnostics."""
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound")

        logger.log_shot(
            ball_speed_mph=120.0,
            club_speed_mph=85.0,
            smash_factor=1.41,
            estimated_carry_yards=165.0,
            club="7-iron",
            peak_magnitude=None,
            readings_count=0,
            spin_snr=2.96,
            spin_peak_freq_hz=95.21484375,
            spin_seam_cycles=4.8,
            spin_candidates=[
                {
                    "rank": 1,
                    "rpm": 5713,
                    "snr": 2.96,
                    "relative_magnitude": 1.0,
                    "selected": True,
                }
            ],
            spin_phase_method="phase_residual",
            spin_phase_rpm=5713,
            spin_phase_snr=3.2,
            spin_phase_agreement_pct=2.1,
            spin_phase_confirmed=True,
            spin_rejection_reason="SNR too low (2.96, need 3.0)",
            launch_angle_vertical=12.3,
            launch_angle_horizontal=-1.2,
            launch_angle_confidence=0.8,
            launch_angle_vertical_confidence=0.8,
            launch_angle_horizontal_confidence=0.6,
            launch_angle_vertical_source="radar",
            launch_angle_horizontal_source="estimated",
            experimental_attack_angle_deg=-4.9,
            experimental_attack_angle_status="candidate_available",
            experimental_club_path_deg=5.8,
            experimental_club_path_status="rejected_phase_span",
            iwr6843_horizontal_deg=17.9,
            iwr6843_horizontal_confidence=0.8,
            experimental_camera_horizontal_deg=0.6,
            experimental_camera_horizontal_confidence=0.75,
            experimental_camera_horizontal_status="camera_assisted_high",
            experimental_camera_iwr_delta_deg=-17.3,
            impact_timestamp=1234567890.25,
        )

        lines = logger.session_path.read_text().strip().split("\n")
        entry = json.loads(lines[-1])

        assert entry["type"] == "shot_detected"
        assert entry["spin_rpm"] is None
        assert entry["spin_snr"] == 2.96
        assert entry["spin_candidate_rpm"] == 5713
        assert entry["spin_candidates"][0]["rpm"] == 5713
        assert entry["spin_candidates"][0]["selected"] is True
        assert entry["spin_phase_method"] == "phase_residual"
        assert entry["spin_phase_rpm"] == 5713
        assert entry["spin_phase_snr"] == 3.2
        assert entry["spin_phase_agreement_pct"] == 2.1
        assert entry["spin_phase_confirmed"] is True
        assert entry["spin_rejection_reason"] == "SNR too low (2.96, need 3.0)"
        assert entry["launch_angle_vertical_confidence"] == 0.8
        assert entry["launch_angle_horizontal_confidence"] == 0.6
        assert entry["launch_angle_vertical_source"] == "radar"
        assert entry["launch_angle_horizontal_source"] == "estimated"
        assert entry["experimental_attack_angle_deg"] == -4.9
        assert entry["experimental_attack_angle_status"] == "candidate_available"
        assert entry["experimental_club_path_deg"] == 5.8
        assert entry["experimental_club_path_status"] == "rejected_phase_span"
        assert entry["iwr6843_horizontal_deg"] == 17.9
        assert entry["iwr6843_horizontal_confidence"] == 0.8
        assert entry["experimental_camera_horizontal_deg"] == 0.6
        assert entry["experimental_camera_horizontal_confidence"] == 0.75
        assert entry["experimental_camera_horizontal_status"] == "camera_assisted_high"
        assert entry["experimental_camera_iwr_delta_deg"] == -17.3
        assert entry["impact_timestamp"] == 1234567890.25

    def test_shot_logs_experimental_club_status_without_candidate(self, tmp_path):
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound")

        logger.log_shot(
            ball_speed_mph=100.0,
            club_speed_mph=80.0,
            smash_factor=1.25,
            estimated_carry_yards=130,
            club="9_iron",
            peak_magnitude=None,
            readings_count=0,
            experimental_attack_angle_status="rejected_no_club_track",
            experimental_club_path_status="rejected_no_pre_impact_frames",
        )

        entry = json.loads(logger.session_path.read_text().strip().split("\n")[-1])
        assert "experimental_attack_angle_deg" not in entry
        assert entry["experimental_attack_angle_status"] == "rejected_no_club_track"
        assert "experimental_club_path_deg" not in entry
        assert entry["experimental_club_path_status"] == "rejected_no_pre_impact_frames"

    def test_rolling_buffer_capture_logs_trigger_timing(self, tmp_path):
        """Rolling-buffer captures should preserve host trigger timing fields."""
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound")

        logger.log_rolling_buffer_capture(
            shot_number=1,
            sample_time=100.0,
            trigger_time=100.068,
            i_samples=[2048] * 4,
            q_samples=[2048] * 4,
            first_byte_timestamp=1234567890.25,
            trigger_timestamp=1234567890.182,
            trigger_timestamp_source="ops_clock_sync",
            clock_sync_offset_s=1234567790.114,
            post_trigger_duration_ms=68.0,
        )

        lines = logger.session_path.read_text().strip().split("\n")
        entry = json.loads(lines[-1])

        assert entry["type"] == "rolling_buffer_capture"
        assert entry["first_byte_timestamp"] == 1234567890.25
        assert entry["trigger_timestamp"] == 1234567890.182
        assert entry["trigger_timestamp_source"] == "ops_clock_sync"
        assert entry["trigger_timestamp_from_first_byte"] == 1234567890.182
        assert entry["trigger_timestamp_delta_from_first_byte_ms"] == 0.0
        assert entry["clock_sync_offset_s"] == 1234567790.114
        assert entry["post_trigger_duration_ms"] == 68.0


class TestLogCameraCapture:
    """Tests for passive high-speed camera capture logging."""

    def test_camera_capture_writes_path_and_timing(self, tmp_path):
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound")

        logger.log_camera_capture(
            shot_number=3,
            shot_timestamp=100.0,
            trigger_timestamp=100.012,
            capture_path="/tmp/camera_003",
            metadata={"frame_count": 48, "delivered_fps": 287.9},
        )

        entry = json.loads(logger.session_path.read_text().strip().split("\n")[-1])
        assert entry["type"] == "camera_capture"
        assert entry["shot_number"] == 3
        assert entry["capture_path"] == "/tmp/camera_003"
        assert entry["trigger_delta_ms"] == pytest.approx(12.0)
        assert entry["metadata"]["frame_count"] == 48


class TestLogKld7Buffer:
    """Tests for the K-LD7 ring buffer logging method."""

    def test_kld7_buffer_logs_ball_and_club_angles(self, tmp_path):
        """Both ball_angle and club_angle should round-trip through the JSONL log.

        Regression: server.py used to compute club_angle AFTER calling
        log_kld7_buffer, so club_angle in every horizontal kld7_buffer log
        entry was always None even when shot.club_path_deg was populated
        downstream. This test guards the logger's end of the contract.
        """
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound")

        ball = {
            "horizontal_deg": -3.5,
            "confidence": 0.82,
            "detection_class": "ball",
            "magnitude": 12.4,
            "num_frames": 3,
        }
        club = {
            "horizontal_deg": -2.1,
            "confidence": 0.65,
            "detection_class": "club",
            "magnitude": 8.7,
            "num_frames": 2,
        }
        logger.log_kld7_buffer(
            shot_number=1,
            shot_timestamp=1234567890.0,
            orientation="horizontal",
            buffer_frames=[
                {"timestamp": 1234567889.0, "has_radc": True},
                {"timestamp": 1234567889.05, "has_radc": True},
            ],
            ball_angle=ball,
            club_angle=club,
        )

        lines = logger.session_path.read_text().strip().split("\n")
        entry = json.loads(lines[-1])

        assert entry["type"] == "kld7_buffer"
        assert entry["orientation"] == "horizontal"
        assert entry["frame_count"] == 2
        assert entry["radc_frame_count"] == 2
        assert entry["radc_payload_count"] == 0
        assert entry["radc_payload_valid_count"] == 0
        assert entry["radc_payload_invalid_count"] == 0
        assert entry["radc_payload_expected"] is None
        assert entry["radc_payload_complete"] is False
        assert entry["ball_angle"] == ball
        assert entry["club_angle"] == club, (
            "club_angle must be preserved in the kld7_buffer log entry "
            "so offline analysis can correlate it with the ball angle."
        )

    def test_kld7_buffer_logs_raw_radc_payload_counts(self, tmp_path):
        """Top-level counts make TrackMan replay readiness obvious per shot."""
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound")

        logger.log_kld7_buffer(
            shot_number=1,
            shot_timestamp=1234567890.0,
            orientation="vertical",
            buffer_frames=[
                {"timestamp": 1.0, "has_radc": True, "radc_b64": "AQID"},
                {"timestamp": 2.0, "has_radc": True},
                {"timestamp": 3.0},
            ],
            raw_payload_expected=True,
        )

        entry = json.loads(logger.session_path.read_text().strip().split("\n")[-1])
        assert entry["frame_count"] == 3
        assert entry["radc_frame_count"] == 2
        assert entry["radc_payload_count"] == 1
        assert entry["radc_payload_valid_count"] == 0
        assert entry["radc_payload_invalid_count"] == 0
        assert entry["radc_payload_expected"] is True
        assert entry["radc_payload_complete"] is False

    def test_kld7_buffer_marks_complete_raw_radc_payloads(self, tmp_path):
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound")

        logger.log_kld7_buffer(
            shot_number=1,
            shot_timestamp=1234567890.0,
            orientation="vertical",
            buffer_frames=[
                {
                    "timestamp": 1.0,
                    "has_radc": True,
                    "radc_b64": "AQID",
                    "radc_payload_bytes": RADC_PAYLOAD_BYTES,
                },
                {
                    "timestamp": 2.0,
                    "has_radc": True,
                    "radc_b64": "BAUG",
                    "radc_payload_bytes": RADC_PAYLOAD_BYTES,
                },
            ],
            raw_payload_expected=True,
        )

        entry = json.loads(logger.session_path.read_text().strip().split("\n")[-1])
        assert entry["radc_payload_count"] == 2
        assert entry["radc_payload_valid_count"] == 2
        assert entry["radc_payload_invalid_count"] == 0
        assert entry["radc_payload_expected"] is True
        assert entry["radc_payload_complete"] is True

    def test_kld7_buffer_marks_wrong_size_payloads_incomplete(self, tmp_path):
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound")

        logger.log_kld7_buffer(
            shot_number=1,
            shot_timestamp=1234567890.0,
            orientation="vertical",
            buffer_frames=[
                {
                    "timestamp": 1.0,
                    "has_radc": True,
                    "radc_b64": "AQID",
                    "radc_payload_bytes": 3,
                },
            ],
            raw_payload_expected=True,
        )

        entry = json.loads(logger.session_path.read_text().strip().split("\n")[-1])
        assert entry["radc_payload_count"] == 1
        assert entry["radc_payload_valid_count"] == 0
        assert entry["radc_payload_invalid_count"] == 1
        assert entry["radc_payload_complete"] is False

    def test_kld7_buffer_club_angle_optional(self, tmp_path):
        """Missing club_angle is allowed (e.g. shot before club_speed available)."""
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound")

        logger.log_kld7_buffer(
            shot_number=1,
            shot_timestamp=1.0,
            orientation="vertical",
            buffer_frames=[],
            ball_angle={
                "vertical_deg": 12.5,
                "confidence": 0.9,
                "detection_class": "ball",
                "magnitude": 15.0,
                "num_frames": 2,
            },
        )

        entry = json.loads(logger.session_path.read_text().strip().split("\n")[-1])
        assert entry["ball_angle"]["vertical_deg"] == 12.5
        assert entry["club_angle"] is None


class TestLogIWR6843Capture:
    """TI logs retain raw-file linkage and all frozen estimator evidence."""

    def test_iwr6843_capture_round_trips_lcmf_measurement(self, tmp_path):
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound")
        measurement = {
            "estimator": "lcmf_v1",
            "launch_angle_deg": 17.4,
            "components_deg": {"channel_two8_deg": 14.1},
        }
        temperature_report = {
            "device_time_ms": 123456,
            "rx0_c": 42,
            "rx1_c": 43,
            "rx2_c": 44,
            "rx3_c": 45,
            "tx0_c": 46,
            "tx1_c": 47,
            "tx2_c": 48,
            "pm_c": 49,
            "dig0_c": 50,
            "dig1_c": 51,
        }

        logger.log_iwr6843_capture(
            shot_number=2,
            shot_timestamp=100.0,
            trigger_timestamp=100.012,
            capture_path="/tmp/iwr6843_002.l3dump",
            capture_bytes=786452,
            dump_duration_s=7.56,
            capture_error=None,
            ball_speed_mph=101.2,
            measurement=measurement,
            temperature_report=temperature_report,
        )

        entry = json.loads(logger.session_path.read_text().strip().split("\n")[-1])
        assert entry["type"] == "iwr6843_capture"
        assert entry["shot_number"] == 2
        assert entry["trigger_delta_ms"] == 12.0
        assert entry["capture_bytes"] == 786452
        assert entry["ball_speed_source"] == "ops243"
        assert entry["measurement"] == measurement
        assert entry["temperature_report"] == temperature_report

    def test_iwr6843_capture_logs_club_path(self, tmp_path):
        """Club path evidence must be replayable from the session log alone."""
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound")

        logger.log_iwr6843_capture(
            shot_number=1,
            shot_timestamp=100.0,
            trigger_timestamp=100.002,
            capture_path="/tmp/x.l3dump",
            capture_bytes=549566,
            dump_duration_s=5.33,
            capture_error=None,
            ball_speed_mph=94.5,
            measurement={"status": "accepted", "track_span_s": 0.0334},
            club_path={"status": "accepted", "path_deg": 2.4, "confidence": 0.8},
        )

        entry = json.loads(logger.session_path.read_text().strip().split("\n")[-1])
        assert entry["type"] == "iwr6843_capture"
        assert entry["club_path"]["path_deg"] == 2.4
        assert entry["measurement"]["track_span_s"] == 0.0334
        assert entry["temperature_report"] is None

    def test_iwr6843_capture_club_path_defaults_to_none(self, tmp_path):
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound")

        logger.log_iwr6843_capture(
            shot_number=1,
            shot_timestamp=100.0,
            trigger_timestamp=None,
            capture_path=None,
            capture_bytes=0,
            dump_duration_s=None,
            capture_error="no capture",
            ball_speed_mph=94.5,
        )

        entry = json.loads(logger.session_path.read_text().strip().split("\n")[-1])
        assert entry["club_path"] is None


class TestLogClockSync:
    """Tests for OPS clock-sync logging (H1 timing instrumentation)."""

    def _summary(self):
        return {
            "samples": 3,
            "valid_samples": 3,
            "best_offset_s": 1780000000.5,
            "best_read_latency_ms": 2.1,
            "offset_spread_ms": 0.8,
            "reads": [
                {
                    "radar_clock_s": 137.4,
                    "offset_s": 1780000000.5,
                    "read_latency_ms": 2.1,
                    "raw": '{"Clock":"137.4"}',
                },
            ],
        }

    def test_clock_sync_writes_entry(self, tmp_path):
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound")

        logger.log_clock_sync(device="ops243", port="/dev/ttyACM0", summary=self._summary())

        entry = json.loads(logger.session_path.read_text().strip().split("\n")[-1])
        assert entry["type"] == "ops_clock_sync"
        assert entry["device"] == "ops243"
        assert entry["port"] == "/dev/ttyACM0"
        assert entry["best_offset_s"] == 1780000000.5
        assert entry["valid_samples"] == 3
        assert entry["reads"][0]["raw"] == '{"Clock":"137.4"}'

    def test_clock_sync_disabled_skips_write(self, tmp_path):
        logger = SessionLogger(log_dir=tmp_path, enabled=False)
        logger.log_clock_sync(device="ops243", port="x", summary=self._summary())
        assert logger.session_path is None


class TestSessionIdentity:
    """session_start must carry a globally unique ID and format version so
    cloud sync can dedupe sessions by content, not filename."""

    def _start_entry(self, tmp_path):
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound")
        logger.end_session()
        session_file = next(tmp_path.glob("session_*.jsonl"))
        with session_file.open() as handle:
            first = json.loads(handle.readline())
        return first

    def test_session_start_has_uuid_and_format_version(self, tmp_path):
        import uuid

        import openflight

        entry = self._start_entry(tmp_path)
        assert entry["type"] == "session_start"
        # Valid UUID4, distinct from the timestamp-based session_id
        parsed = uuid.UUID(entry["session_uuid"])
        assert parsed.version == 4
        assert entry["session_uuid"] != entry["session_id"]
        assert entry["format_version"] == 1
        assert entry["app_version"] == openflight.__version__

    def test_session_uuid_is_unique_per_session(self, tmp_path):
        first = self._start_entry(tmp_path / "a")
        second = self._start_entry(tmp_path / "b")
        assert first["session_uuid"] != second["session_uuid"]


class _ConcurrencyProbeStream:
    """Fake session file that detects overlapping writes deterministically.

    ``write`` widens its critical section with a short sleep, so if two
    threads are inside it at once (i.e. ``_write_entry`` is not serialized)
    the second one observes ``_active`` already set and records an overlap.
    The sleep releases the GIL, so with multiple writer threads and no lock
    the overlap is reproduced on every run rather than relying on a rare
    interleaving to surface.
    """

    def __init__(self):
        self.lines = []
        self.overlap_detected = False
        self.closed = False
        self._active = False

    def write(self, data):
        if self._active:
            self.overlap_detected = True
        self._active = True
        time.sleep(0.001)
        self.lines.append(data)
        self._active = False

    def flush(self):
        pass

    def close(self):
        self.closed = True


class TestWriteEntryThreadSafety:
    """Concurrency tests for the shared session-file writer.

    ``log_*`` methods are called from several threads at once (the OPS243
    capture thread, the K-LD7 stream thread, and Flask-SocketIO handlers).
    Without serialization, large entries' writes interleave and corrupt the
    JSONL replay corpus, and a write can race ``end_session`` closing the
    file.
    """

    def test_concurrent_writes_do_not_overlap_or_drop_entries(self, tmp_path):
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound")

        probe = _ConcurrencyProbeStream()
        logger._session_file = probe  # swap the real file for the probe

        threads_n = 8
        per_thread = 5

        def worker(idx):
            for seq in range(per_thread):
                logger._write_entry("probe", {"thread": idx, "seq": seq})

        threads = [threading.Thread(target=worker, args=(idx,)) for idx in range(threads_n)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert not any(thread.is_alive() for thread in threads)
        assert not probe.overlap_detected, (
            "Concurrent _write_entry calls overlapped inside the stream write; "
            "session JSONL lines can interleave and corrupt the replay corpus."
        )
        # Every entry was written exactly once and each line is intact JSON.
        assert len(probe.lines) == threads_n * per_thread
        for line in probe.lines:
            assert line.endswith("\n")
            json.loads(line)

    def test_end_session_does_not_close_during_an_active_write(self, tmp_path):
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound")

        in_write = threading.Event()
        let_write_finish = threading.Event()
        events = []

        class _BlockingFirstWriteStream:
            """Blocks the first write so a close can try to race it."""

            def __init__(self):
                self._first = True

            def write(self, data):
                if self._first:
                    self._first = False
                    in_write.set()
                    let_write_finish.wait(timeout=5)
                events.append("write")

            def flush(self):
                pass

            def close(self):
                events.append("close")

        logger._session_file = _BlockingFirstWriteStream()

        writer = threading.Thread(target=lambda: logger._write_entry("blocking", {}))
        writer.start()
        assert in_write.wait(timeout=5)  # writer is inside write(), holding the lock

        closer = threading.Thread(target=logger.end_session)
        closer.start()
        time.sleep(0.05)
        # The in-flight write holds the lock, so end_session must not have
        # closed the file yet. Without serialization it closes immediately.
        assert "close" not in events, "end_session closed the file during an active write"

        let_write_finish.set()
        writer.join(timeout=5)
        closer.join(timeout=5)

        # The close must land after the in-flight write completed.
        assert events[0] == "write"
        assert "close" in events
        assert events.index("close") == len(events) - 1


def test_power_status_writes_structured_session_entry(tmp_path):
    logger = SessionLogger(log_dir=tmp_path, enabled=True)
    logger.start_session(mode="rolling-buffer", trigger_type="sound")

    logger.log_power_status(
        {
            "available": True,
            "state": "on_battery",
            "battery_percent": 42.5,
            "battery_voltage_v": 3.72,
            "external_power": False,
            "updated_at": "2026-08-15T12:00:00+00:00",
            "error": None,
        }
    )

    entry = json.loads(logger.session_path.read_text().strip().split("\n")[-1])
    assert entry["type"] == "power_status"
    assert entry["state"] == "on_battery"
    assert entry["battery_percent"] == 42.5
    assert entry["external_power"] is False


def test_session_logger_import_does_not_trigger_kld7_deprecation():
    """Importing session_logger must not trigger the kld7 DeprecationWarning.

    Runs in a subprocess with ``-W error::DeprecationWarning`` so any
    DeprecationWarning becomes a hard failure.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-W",
            "error::DeprecationWarning",
            "-c",
            "from openflight.session_logger import RADC_PAYLOAD_BYTES",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"session_logger import raised DeprecationWarning:\n{result.stderr}"
    )
