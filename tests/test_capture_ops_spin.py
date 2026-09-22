"""Contracts for the dedicated OPS spin diagnostic capture."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

from openflight.launch_monitor import ClubType
from openflight.rolling_buffer.types import (
    ImpactEstimate,
    IQCapture,
    ProcessedCapture,
    SpeedReading,
    SpeedTimeline,
    SpinCandidate,
    SpinResult,
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hardware-test" / "capture_ops_spin.py"
spec = importlib.util.spec_from_file_location("capture_ops_spin", SCRIPT)
capture_ops_spin = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = capture_ops_spin
spec.loader.exec_module(capture_ops_spin)


class MemoryWriter:
    def __init__(self):
        self.records = []

    def write(self, record):
        self.records.append(record)


class MalformedCaptureRadar:
    def __init__(self, response: str):
        self.response = response
        self.last_hardware_trigger_first_byte_timestamp = 1_700_000_000.25
        self.rearmed = []

    def wait_for_hardware_trigger(self, timeout):
        assert timeout == 0.1
        return self.response

    def rearm_rolling_buffer(self, pre_trigger_segments):
        self.rearmed.append(pre_trigger_segments)


class ValidCaptureRadar(MalformedCaptureRadar):
    def read_clock_sync(self, store=False):
        assert store is False
        return {
            "usable_for_trigger_timestamps": True,
            "best_offset_s": 1_699_999_990.0,
        }


class StubProcessor:
    def __init__(self, capture, processed, standard):
        self.capture = capture
        self.processed = processed
        self.standard = standard

    def parse_capture(self, response, first_byte_timestamp=None):
        assert response == "complete raw response"
        self.capture.first_byte_timestamp = first_byte_timestamp
        self.capture.apply_trigger_timestamp_from_first_byte()
        return self.capture

    def process_standard(self, capture):
        assert capture is self.capture
        return self.standard

    def process_capture(self, capture, club_type):
        assert capture is self.capture
        assert club_type is ClubType.IRON_7
        return self.processed

    def detect_spin(self, capture, ball_speed_mph, ball_timestamp_ms):
        assert capture is self.capture
        assert ball_speed_mph == 112.5
        assert ball_timestamp_ms == 60.0
        return SpinResult(
            spin_rpm=6300.0,
            confidence=0.5,
            snr=4.0,
            quality="medium",
        )


def _processed_capture(capture: IQCapture) -> tuple[ProcessedCapture, SpeedTimeline]:
    standard = SpeedTimeline(
        readings=[SpeedReading(112.5, 8.0, 60.0, "outbound")],
        sample_rate_hz=234.375,
        capture=capture,
    )
    overlapping = SpeedTimeline(
        readings=[
            SpeedReading(84.0, 5.0, 48.0, "inbound"),
            SpeedReading(112.5, 9.0, 60.0, "outbound"),
        ],
        sample_rate_hz=937.5,
        capture=capture,
    )
    spin = SpinResult(
        spin_rpm=6420.0,
        confidence=0.72,
        snr=6.4,
        quality="experimental",
        method="multitaper_ungated",
        modulation_depth=0.012,
        peak_freq_hz=107.0,
        seam_cycles=4.2,
        candidates=[
            SpinCandidate(
                rank=1,
                rpm=6420.0,
                freq_hz=107.0,
                relative_magnitude=1.0,
                snr=6.4,
                selected=True,
            )
        ],
    )
    processed = ProcessedCapture(
        timeline=overlapping,
        ball_speed_mph=112.5,
        ball_timestamp_ms=60.0,
        club_speed_mph=84.0,
        club_timestamp_ms=48.0,
        spin=spin,
        capture=capture,
        impact=ImpactEstimate(
            timestamp_ms=54.0,
            source="ops_transition",
            speed_delta_mph=28.5,
        ),
    )
    return processed, standard


def test_capture_record_preserves_raw_iq_and_all_processor_diagnostics():
    capture = IQCapture(
        sample_time=10.0,
        trigger_time=10.05,
        i_samples=[0, 100, 2048, 4095],
        q_samples=[4095, 3000, 2048, 0],
        first_byte_timestamp=1_700_000_000.25,
    )
    processed, standard = _processed_capture(capture)

    record = capture_ops_spin.build_capture_record(
        capture_number=3,
        capture=capture,
        processed=processed,
        standard_timeline=standard,
        club=ClubType.IRON_7,
        clock_sync={"usable_for_trigger_timestamps": True, "best_offset_s": 123.5},
    )

    assert record["type"] == "rolling_buffer_capture"
    assert record["capture_number"] == 3
    assert record["shot_number"] == 3
    assert record["club"] == "7-iron"
    assert record["i_samples"] == [0, 100, 2048, 4095]
    assert record["q_samples"] == [4095, 3000, 2048, 0]
    assert record["adc_summary"]["i"]["clipped_low"] == 1
    assert record["adc_summary"]["i"]["clipped_high"] == 1
    assert record["processing"]["spin"]["method"] == "multitaper_ungated"
    assert record["processing"]["spin"]["candidates"][0]["rpm"] == 6420.0
    assert record["processing"]["impact"]["source"] == "ops_transition"
    assert record["standard_timeline"][0]["magnitude"] == 8.0
    assert record["overlapping_timeline"][1]["speed_mph"] == 112.5
    assert record["clock_sync"]["best_offset_s"] == 123.5


def test_raw_record_keeps_verbatim_response_and_integrity_hash():
    response = '{"sample_time":"1.0"}\r\nnot-json\x00\xff'

    record = capture_ops_spin.build_raw_capture_record(
        capture_number=4,
        response=response,
        wait_started_at=1_700_000_000.0,
        response_received_at=1_700_000_002.0,
        first_byte_timestamp=1_700_000_001.0,
    )

    assert record["type"] == "ops_spin_raw_capture"
    assert record["raw_response"] == response
    assert record["response_characters"] == len(response)
    assert record["response_sha256"] == hashlib.sha256(response.encode("utf-8")).hexdigest()


def test_jsonl_writer_flushes_each_record_immediately(tmp_path):
    output = tmp_path / "ops-spin.jsonl"

    with capture_ops_spin.JsonlWriter(output) as writer:
        writer.write({"type": "first", "value": 1})
        lines = output.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[0])
        assert record["type"] == "first"
        assert record["value"] == 1
        assert record["schema_version"] == 1
        assert record["timestamp"].endswith("+00:00")


def test_parse_failure_is_saved_after_raw_response_and_radar_is_rearmed():
    raw_response = '{"sample_time":"12.0"}\n{"I":[1,2,3]}'
    radar = MalformedCaptureRadar(raw_response)
    writer = MemoryWriter()
    processor = capture_ops_spin.RollingBufferProcessor()

    outcome = capture_ops_spin.capture_once(
        radar=radar,
        processor=processor,
        writer=writer,
        capture_number=1,
        timeout=0.1,
        pre_trigger_segments=12,
        club=ClubType.DRIVER,
    )

    assert outcome == "parse_failed"
    assert [record["type"] for record in writer.records] == [
        "ops_spin_raw_capture",
        "ops_spin_capture_error",
    ]
    assert writer.records[0]["raw_response"] == raw_response
    assert writer.records[1]["reason"] == "parse_failed"
    assert radar.rearmed == [12]


def test_valid_capture_is_clock_synced_processed_and_replayable():
    capture = IQCapture(
        sample_time=10.0,
        trigger_time=10.05,
        i_samples=[0, 100, 2048, 4095],
        q_samples=[4095, 3000, 2048, 0],
    )
    processed, standard = _processed_capture(capture)
    radar = ValidCaptureRadar("complete raw response")
    processor = StubProcessor(capture, processed, standard)
    writer = MemoryWriter()

    outcome = capture_ops_spin.capture_once(
        radar=radar,
        processor=processor,
        writer=writer,
        capture_number=2,
        timeout=0.1,
        pre_trigger_segments=12,
        club=ClubType.IRON_7,
    )

    assert outcome == "processed"
    assert [record["type"] for record in writer.records] == [
        "ops_spin_raw_capture",
        "rolling_buffer_capture",
        "shot_detected",
    ]
    structured = writer.records[1]
    assert structured["trigger_timestamp"] == 1_700_000_000.05
    assert structured["trigger_timestamp_source"] == "ops_clock_sync"
    assert structured["processing"]["spin"]["spin_rpm"] == 6420.0
    assert structured["envelope_spin"]["spin_rpm"] == 6300.0
    assert writer.records[2]["data"]["shot_number"] == 2
    assert writer.records[2]["data"]["spin_candidate_rpm"] == 6420.0
    assert radar.rearmed == [12]


def test_parser_defaults_match_production_sound_capture():
    args = capture_ops_spin.build_parser().parse_args([])

    assert capture_ops_spin.SAMPLE_RATE_KSPS == 30
    assert args.pre_trigger == 12
    assert args.club == "unknown"
    assert args.environment == "outdoor-range"
