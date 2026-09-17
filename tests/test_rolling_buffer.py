"""Tests for rolling_buffer module."""

import json
import math
import threading
import time
from datetime import datetime
from unittest.mock import MagicMock

import numpy as np
import pytest

from openflight.clubs import ClubType
from openflight.launch_monitor import Shot
from openflight.rolling_buffer import (
    ImpactEstimate,
    IQCapture,
    ProcessedCapture,
    RollingBufferProcessor,
    SoundTrigger,
    SpeedReading,
    SpeedTimeline,
    SpeedTriggeredCapture,
    SpinCandidate,
    SpinResult,
    create_trigger,
    # Monitor functions
    estimate_carry_with_spin,
    get_optimal_spin_for_ball_speed,
)

# =============================================================================
# Tests for Optimal Spin Calculation
# =============================================================================


class TestGetOptimalSpinForBallSpeed:
    """Tests for the optimal spin rate calculation based on ball speed."""

    def test_high_ball_speed_180_mph(self):
        """180 mph ball speed should have ~2050 rpm optimal spin."""
        optimal = get_optimal_spin_for_ball_speed(180, ClubType.DRIVER)
        assert 2000 <= optimal <= 2100

    def test_tour_average_167_mph(self):
        """167 mph (PGA Tour avg) should have ~2450 rpm optimal spin."""
        optimal = get_optimal_spin_for_ball_speed(167, ClubType.DRIVER)
        assert 2300 <= optimal <= 2600

    def test_moderate_speed_160_mph(self):
        """160 mph ball speed should have ~2550 rpm optimal spin."""
        optimal = get_optimal_spin_for_ball_speed(160, ClubType.DRIVER)
        assert 2500 <= optimal <= 2600

    def test_amateur_speed_140_mph(self):
        """140 mph ball speed should have ~2700 rpm optimal spin."""
        optimal = get_optimal_spin_for_ball_speed(140, ClubType.DRIVER)
        assert 2650 <= optimal <= 2750

    def test_slower_speed_120_mph(self):
        """120 mph ball speed should have ~2900 rpm optimal spin."""
        optimal = get_optimal_spin_for_ball_speed(120, ClubType.DRIVER)
        assert 2850 <= optimal <= 2950

    def test_very_slow_speed_100_mph(self):
        """100 mph ball speed should have ~3100 rpm optimal spin."""
        optimal = get_optimal_spin_for_ball_speed(100, ClubType.DRIVER)
        assert 3050 <= optimal <= 3150

    def test_optimal_spin_decreases_with_ball_speed(self):
        """Higher ball speeds should require less spin."""
        spin_120 = get_optimal_spin_for_ball_speed(120, ClubType.DRIVER)
        spin_140 = get_optimal_spin_for_ball_speed(140, ClubType.DRIVER)
        spin_160 = get_optimal_spin_for_ball_speed(160, ClubType.DRIVER)
        spin_180 = get_optimal_spin_for_ball_speed(180, ClubType.DRIVER)

        assert spin_120 > spin_140 > spin_160 > spin_180

    def test_irons_need_more_spin_than_driver(self):
        """Irons should have higher optimal spin than driver at same speed."""
        driver_spin = get_optimal_spin_for_ball_speed(140, ClubType.DRIVER)
        iron_7_spin = get_optimal_spin_for_ball_speed(140, ClubType.IRON_7)
        pw_spin = get_optimal_spin_for_ball_speed(140, ClubType.PW)

        assert iron_7_spin > driver_spin
        assert pw_spin > iron_7_spin

    def test_club_spin_ordering(self):
        """Shorter clubs should require more spin."""
        ball_speed = 130
        driver = get_optimal_spin_for_ball_speed(ball_speed, ClubType.DRIVER)
        wood_3 = get_optimal_spin_for_ball_speed(ball_speed, ClubType.WOOD_3)
        iron_5 = get_optimal_spin_for_ball_speed(ball_speed, ClubType.IRON_5)
        iron_9 = get_optimal_spin_for_ball_speed(ball_speed, ClubType.IRON_9)
        pw = get_optimal_spin_for_ball_speed(ball_speed, ClubType.PW)

        assert driver < wood_3 < iron_5 < iron_9 < pw


# =============================================================================
# Tests for Carry Distance with Spin
# =============================================================================


class TestEstimateCarryWithSpin:
    """Tests for the spin-adjusted carry distance calculation."""

    def test_optimal_spin_gives_best_carry(self):
        """Spin at optimal rate should give highest carry."""
        ball_speed = 160
        optimal_spin = get_optimal_spin_for_ball_speed(ball_speed, ClubType.DRIVER)

        carry_optimal = estimate_carry_with_spin(ball_speed, optimal_spin, ClubType.DRIVER)
        carry_low = estimate_carry_with_spin(ball_speed, optimal_spin - 1000, ClubType.DRIVER)
        carry_high = estimate_carry_with_spin(ball_speed, optimal_spin + 1000, ClubType.DRIVER)

        assert carry_optimal >= carry_low
        assert carry_optimal >= carry_high

    def test_low_spin_penalty_more_severe_than_high_spin(self):
        """Low spin should hurt carry more than high spin."""
        ball_speed = 160
        optimal_spin = get_optimal_spin_for_ball_speed(ball_speed, ClubType.DRIVER)

        carry_optimal = estimate_carry_with_spin(ball_speed, optimal_spin, ClubType.DRIVER)
        carry_1000_low = estimate_carry_with_spin(ball_speed, optimal_spin - 1000, ClubType.DRIVER)
        carry_1000_high = estimate_carry_with_spin(ball_speed, optimal_spin + 1000, ClubType.DRIVER)

        low_penalty = carry_optimal - carry_1000_low
        high_penalty = carry_optimal - carry_1000_high

        # Low spin penalty should be larger
        assert low_penalty > high_penalty

    def test_tour_average_produces_expected_carry(self):
        """167 mph with ~2686 rpm should produce ~275 yards (Tour avg)."""
        carry = estimate_carry_with_spin(167, 2686, ClubType.DRIVER)
        # Allow some tolerance since we don't have launch angle
        assert 260 <= carry <= 290

    def test_very_low_spin_significant_penalty(self):
        """Very low spin (1500 rpm at 160 mph) should lose significant distance."""
        carry_optimal = estimate_carry_with_spin(160, 2550, ClubType.DRIVER)
        carry_low_spin = estimate_carry_with_spin(160, 1500, ClubType.DRIVER)

        # Should lose at least 10% carry
        assert carry_low_spin < carry_optimal * 0.90

    def test_very_high_spin_moderate_penalty(self):
        """Very high spin (4500 rpm at 160 mph) should lose moderate distance."""
        carry_optimal = estimate_carry_with_spin(160, 2550, ClubType.DRIVER)
        carry_high_spin = estimate_carry_with_spin(160, 4500, ClubType.DRIVER)

        # Should lose some but not as much as low spin
        assert carry_high_spin < carry_optimal
        assert carry_high_spin > carry_optimal * 0.85

    def test_smash_factor_penalty_for_poor_contact(self):
        """Poor smash factor should reduce carry estimate."""
        ball_speed = 150
        spin = 2600

        # Good contact: 150 mph ball / 100 mph club = 1.50 smash
        carry_good = estimate_carry_with_spin(ball_speed, spin, ClubType.DRIVER, club_speed_mph=100)

        # Poor contact: 150 mph ball / 115 mph club = 1.30 smash
        carry_poor = estimate_carry_with_spin(ball_speed, spin, ClubType.DRIVER, club_speed_mph=115)

        assert carry_poor < carry_good

    def test_no_club_speed_no_smash_penalty(self):
        """Without club speed, no smash factor penalty applied."""
        ball_speed = 150
        spin = 2600

        carry_no_club = estimate_carry_with_spin(ball_speed, spin, ClubType.DRIVER)
        carry_with_club = estimate_carry_with_spin(
            ball_speed,
            spin,
            ClubType.DRIVER,
            club_speed_mph=101,  # 1.48 smash - optimal
        )

        # Should be very close (club speed at optimal smash has minimal effect)
        assert abs(carry_no_club - carry_with_club) < 5

    def test_carry_increases_with_ball_speed(self):
        """Higher ball speed should always increase carry."""
        spin = 2600
        carry_120 = estimate_carry_with_spin(120, spin, ClubType.DRIVER)
        carry_140 = estimate_carry_with_spin(140, spin, ClubType.DRIVER)
        carry_160 = estimate_carry_with_spin(160, spin, ClubType.DRIVER)

        assert carry_120 < carry_140 < carry_160

    def test_realistic_carry_values(self):
        """Test that carry values are in realistic ranges."""
        # Amateur golfer: 140 mph ball speed, 2800 rpm
        amateur = estimate_carry_with_spin(140, 2800, ClubType.DRIVER)
        assert 220 <= amateur <= 250

        # Tour player: 170 mph ball speed, 2400 rpm
        tour = estimate_carry_with_spin(170, 2400, ClubType.DRIVER)
        assert 280 <= tour <= 320  # Widened range for slightly above optimal

        # Long drive: 190 mph ball speed, 2000 rpm
        long_drive = estimate_carry_with_spin(190, 2000, ClubType.DRIVER)
        assert 330 <= long_drive <= 380  # Widened for variation


# =============================================================================
# Tests for Rolling Buffer Types
# =============================================================================


class TestIQCapture:
    """Tests for IQCapture dataclass."""

    def test_create_iq_capture(self):
        """Basic IQCapture creation."""
        i_samples = [100] * 4096
        q_samples = [100] * 4096
        capture = IQCapture(
            sample_time=0.136,
            trigger_time=0.0,
            i_samples=i_samples,
            q_samples=q_samples,
            timestamp=1234567890.0,
        )
        assert capture.sample_time == 0.136
        assert len(capture.i_samples) == 4096
        assert len(capture.q_samples) == 4096


class TestSpeedReading:
    """Tests for rolling buffer SpeedReading dataclass."""

    def test_create_speed_reading(self):
        """Basic SpeedReading creation."""
        reading = SpeedReading(
            speed_mph=155.3,
            timestamp_ms=50.0,
            magnitude=500.0,
            direction="outbound",
        )
        assert reading.speed_mph == 155.3
        assert reading.is_outbound is True

    def test_inbound_direction(self):
        """Test inbound direction detection."""
        reading = SpeedReading(
            speed_mph=50.0,
            timestamp_ms=10.0,
            magnitude=100.0,
            direction="inbound",
        )
        assert reading.is_outbound is False


class TestSpeedTimeline:
    """Tests for SpeedTimeline dataclass."""

    def test_peak_speed(self):
        """Peak speed should return highest reading."""
        readings = [
            SpeedReading(speed_mph=100.0, timestamp_ms=10.0, magnitude=100, direction="outbound"),
            SpeedReading(speed_mph=155.0, timestamp_ms=20.0, magnitude=200, direction="outbound"),
            SpeedReading(speed_mph=120.0, timestamp_ms=30.0, magnitude=150, direction="outbound"),
        ]
        timeline = SpeedTimeline(readings=readings, sample_rate_hz=937.5)

        assert timeline.peak_speed is not None
        assert timeline.peak_speed.speed_mph == 155.0

    def test_speeds_property(self):
        """speeds property should return list of speed values."""
        readings = [
            SpeedReading(speed_mph=100.0, timestamp_ms=10.0, magnitude=100, direction="outbound"),
            SpeedReading(speed_mph=150.0, timestamp_ms=20.0, magnitude=200, direction="outbound"),
        ]
        timeline = SpeedTimeline(readings=readings, sample_rate_hz=937.5)

        assert timeline.speeds == [100.0, 150.0]

    def test_duration_ms(self):
        """Duration should be difference between first and last timestamp."""
        readings = [
            SpeedReading(speed_mph=100.0, timestamp_ms=10.0, magnitude=100, direction="outbound"),
            SpeedReading(speed_mph=150.0, timestamp_ms=60.0, magnitude=200, direction="outbound"),
        ]
        timeline = SpeedTimeline(readings=readings, sample_rate_hz=937.5)

        assert timeline.duration_ms == 50.0


class TestSpinResult:
    """Tests for SpinResult dataclass."""

    def test_quality_high(self):
        """High confidence should produce 'high' quality."""
        result = SpinResult(
            spin_rpm=2800,
            confidence=0.85,
            snr=5.0,
            quality="high",
        )
        assert result.quality == "high"

    def test_quality_low(self):
        """Low confidence should produce 'low' quality."""
        result = SpinResult(
            spin_rpm=2800,
            confidence=0.3,
            snr=2.0,
            quality="low",
        )
        assert result.quality == "low"

    def test_no_spin_detected_preserves_snr(self):
        """Rejected spin should keep the measured SNR for diagnostics."""
        result = SpinResult.no_spin_detected(
            "SNR too low",
            snr=2.96,
            peak_freq_hz=95.21484375,
            seam_cycles=4.8,
        )
        assert result.spin_rpm == 0
        assert result.snr == pytest.approx(2.96)
        assert result.peak_freq_hz == pytest.approx(95.21484375)
        assert result.rejection_reason == "SNR too low"

    def test_spin_candidate_serializes_for_logs(self):
        """Spin candidates should be JSON-friendly and rounded."""
        candidate = SpinCandidate(
            rank=1,
            rpm=5493.16,
            freq_hz=91.552734375,
            relative_magnitude=0.4567,
            snr=3.214,
            expected_spin_error_pct=8.765,
            selected=True,
        )

        assert candidate.to_dict() == {
            "rank": 1,
            "rpm": 5493,
            "freq_hz": 91.553,
            "relative_magnitude": 0.457,
            "snr": 3.21,
            "at_lower_rail": False,
            "at_upper_rail": False,
            "expected_spin_error_pct": 8.8,
            "selected": True,
        }


# =============================================================================
# Tests for Rolling Buffer Processor
# =============================================================================


class TestRollingBufferProcessor:
    """Tests for the FFT-based rolling buffer processor."""

    @pytest.fixture
    def processor(self):
        """Create a processor instance for testing."""
        return RollingBufferProcessor()

    def test_processor_creation(self):
        """Processor should initialize with correct constants."""
        processor = RollingBufferProcessor()
        assert processor.WINDOW_SIZE == 128
        assert processor.FFT_SIZE == 4096
        assert processor.SAMPLE_RATE == 30000

    def test_parse_capture_valid_json(self, processor):
        """Parser should handle valid JSON response."""
        # Create a mock JSON response like the radar would return
        # The radar sends each field as a separate JSON line
        i_samples = [2048 + int(100 * math.sin(2 * math.pi * i / 128)) for i in range(4096)]
        q_samples = [2048 + int(100 * math.cos(2 * math.pi * i / 128)) for i in range(4096)]

        import json

        response = (
            '{"sample_time": 0.136}\n'
            '{"trigger_time": 0.0}\n'
            f'{{"I": {json.dumps(i_samples)}}}\n'
            f'{{"Q": {json.dumps(q_samples)}}}'
        )

        capture = processor.parse_capture(response)

        assert capture is not None
        assert len(capture.i_samples) == 4096
        assert len(capture.q_samples) == 4096
        assert capture.sample_time == 0.136
        assert capture.trigger_time == 0.0

    def test_parse_capture_records_first_byte_timestamp(self, processor):
        """Parser should preserve first-byte time and infer trigger epoch."""
        i_samples = [2048] * 4096
        q_samples = [2048] * 4096

        import json

        response = (
            '{"sample_time": 100.0}\n'
            '{"trigger_time": 100.068}\n'
            f'{{"I": {json.dumps(i_samples)}}}\n'
            f'{{"Q": {json.dumps(q_samples)}}}'
        )

        capture = processor.parse_capture(response, first_byte_timestamp=12345.678)

        assert capture is not None
        assert capture.first_byte_timestamp == pytest.approx(12345.678)
        expected_post_trigger_s = (capture.duration_ms - capture.trigger_offset_ms) / 1000.0
        assert capture.trigger_timestamp == pytest.approx(12345.678 - expected_post_trigger_s)

    def test_parse_capture_invalid_json(self, processor):
        """Parser should handle invalid JSON gracefully."""
        capture = processor.parse_capture("not valid json")
        assert capture is None

    def test_parse_capture_missing_fields(self, processor):
        """Parser should handle missing fields."""
        capture = processor.parse_capture('{"sample_time":0.136}')
        assert capture is None

    def test_parse_capture_rejects_mismatched_iq_lengths(self, processor):
        """Unequal I/Q arrays must be rejected, not paired index-wise.

        The J3 UART has no flow control, so a stalled reader can drop bytes
        mid-dump. Truncation usually breaks the JSON, but a short array that
        still parses must not reach the FFT stage as silently bad data.
        """
        response = "\n".join(
            [
                '{"sample_time":"964.003"}',
                '{"trigger_time":"964.105"}',
                json.dumps({"I": [1, 2, 3, 4]}),
                json.dumps({"Q": [1, 2]}),
            ]
        )
        assert processor.parse_capture(response) is None

    def test_parse_capture_rejects_empty_iq_arrays(self, processor):
        response = "\n".join(
            [
                '{"sample_time":"964.003"}',
                '{"trigger_time":"964.105"}',
                '{"I":[]}',
                '{"Q":[]}',
            ]
        )
        assert processor.parse_capture(response) is None

    def test_process_standard_returns_timeline(self, processor):
        """Standard processing should return a SpeedTimeline."""
        # Use a Doppler frequency above DC_MASK_BINS (150 bins ≈ 15 mph).
        # 1500 Hz → bin ~205 → ~20.9 mph, safely above the mask.
        # I=sin, Q=cos produces a negative-frequency (inbound) tone.
        doppler_freq = 1500  # Hz - corresponds to ~20.9 mph
        i_samples = [
            2048 + int(500 * math.sin(2 * math.pi * doppler_freq * i / 30000)) for i in range(4096)
        ]
        q_samples = [
            2048 + int(500 * math.cos(2 * math.pi * doppler_freq * i / 30000)) for i in range(4096)
        ]

        capture = IQCapture(
            sample_time=0.136,
            trigger_time=0.0,
            i_samples=i_samples,
            q_samples=q_samples,
            timestamp=1234567890.0,
        )

        timeline = processor.process_standard(capture)

        assert timeline is not None
        assert isinstance(timeline, SpeedTimeline)
        # With 4096 samples and 128 block size, we get 4096/128 = 32 readings
        assert len(timeline.readings) == 32

    def test_process_overlapping_higher_resolution(self, processor):
        """Overlapping processing should give more readings than standard."""
        doppler_freq = 1500  # Hz - ~20.9 mph, above DC mask
        i_samples = [
            2048 + int(500 * math.sin(2 * math.pi * doppler_freq * i / 30000)) for i in range(4096)
        ]
        q_samples = [
            2048 + int(500 * math.cos(2 * math.pi * doppler_freq * i / 30000)) for i in range(4096)
        ]

        capture = IQCapture(
            sample_time=0.136,
            trigger_time=0.0,
            i_samples=i_samples,
            q_samples=q_samples,
            timestamp=1234567890.0,
        )

        standard = processor.process_standard(capture)
        overlapping = processor.process_overlapping(capture)

        # Standard: 4096/128 = 32 readings
        # Overlapping: (4096-128)/32 + 1 = 125 readings (4x more)
        assert len(overlapping.readings) > len(standard.readings)
        assert len(standard.readings) == 32
        assert len(overlapping.readings) >= 120  # Allow some tolerance


# =============================================================================
# Tests for Trigger Strategies
# =============================================================================


class TestTriggerFactory:
    """Tests for the trigger factory function."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [("sound", SoundTrigger), ("speed", SpeedTriggeredCapture)],
    )
    def test_supported_triggers(self, name, expected):
        assert isinstance(create_trigger(name), expected)

    def test_default_trigger_is_sound(self):
        assert isinstance(create_trigger(), SoundTrigger)

    def test_invalid_trigger_type(self):
        """Factory should raise error for unknown trigger type."""
        with pytest.raises(ValueError):
            create_trigger("invalid_type")


class TestSoundTriggerTimestampPropagation:
    """Tests for hardware trigger timestamp propagation."""

    def test_ops_hardware_trigger_records_first_byte_timestamp(self):
        """OPS hardware wait should expose when the first serial byte arrived."""
        from openflight.ops243 import OPS243Radar

        class FakeSerial:
            is_open = True

            def __init__(self):
                self._response = (
                    b'{"sample_time": 1.0}\r\n{"trigger_time": 1.1}\r\n{"I": [1]}\r\n{"Q": [1]}'
                )

            @property
            def in_waiting(self):
                return len(self._response)

            def reset_input_buffer(self):
                pass

            def read(self, byte_count):
                chunk = self._response[:byte_count]
                self._response = self._response[byte_count:]
                return chunk

        radar = OPS243Radar(port="/dev/null")
        radar.serial = FakeSerial()

        response = radar.wait_for_hardware_trigger(timeout=1.0)

        assert response.endswith('{"Q": [1]}')
        assert radar.last_hardware_trigger_first_byte_timestamp is not None

    def test_sound_trigger_uses_buffer_offset_for_trigger_timestamp(self):
        """Hardware captures should translate first-byte time back to trigger time."""
        from openflight.rolling_buffer.trigger import SoundTrigger

        radar = MagicMock()
        radar.wait_for_hardware_trigger.return_value = '{"sample_time": 0.0}'
        radar.last_hardware_trigger_first_byte_timestamp = 12345.678

        capture = IQCapture(
            sample_time=100.000,
            trigger_time=100.068,
            i_samples=[2048] * 4096,
            q_samples=[2048] * 4096,
        )
        processor = MagicMock()
        processor.parse_capture.return_value = capture
        processor.process_standard.return_value = SpeedTimeline(
            readings=[
                SpeedReading(
                    speed_mph=100.0,
                    magnitude=1000.0,
                    timestamp_ms=68.0,
                    direction="outbound",
                )
            ],
            sample_rate_hz=937.5,
        )

        trigger = SoundTrigger(pre_trigger_segments=12)
        result = trigger.wait_for_trigger(radar, processor, timeout=1.0)

        assert result is capture
        assert result.first_byte_timestamp == pytest.approx(12345.678)
        expected_post_trigger_s = (capture.duration_ms - capture.trigger_offset_ms) / 1000.0
        assert result.trigger_timestamp == pytest.approx(12345.678 - expected_post_trigger_s)
        processor.parse_capture.assert_called_once_with(
            '{"sample_time": 0.0}',
            first_byte_timestamp=12345.678,
        )
        radar.rearm_rolling_buffer.assert_called_once_with(12)

    def test_sound_trigger_prefers_ops_clock_sync_for_trigger_timestamp(self):
        """Radar-clock trigger time should beat first-byte timing when available."""
        from openflight.rolling_buffer.trigger import SoundTrigger

        radar = MagicMock()
        radar.wait_for_hardware_trigger.return_value = '{"sample_time": 0.0}'
        radar.last_hardware_trigger_first_byte_timestamp = 12345.678
        radar.last_clock_sync = None
        radar.read_clock_sync.return_value = {
            "source": "per_shot",
            "samples": 3,
            "valid_samples": 3,
            "best_offset_s": 12000.0,
            "clock_sync_method": "integer_rollover",
            "usable_for_trigger_timestamps": True,
            "rollover_uncertainty_ms": 10.0,
            "reads": [
                {
                    "host_after": time.time(),
                    "host_mid": time.time(),
                    "radar_clock_s": 100.0,
                    "read_latency_ms": 1.0,
                }
            ],
        }

        capture = IQCapture(
            sample_time=100.000,
            trigger_time=100.068,
            i_samples=[2048] * 4096,
            q_samples=[2048] * 4096,
        )
        processor = MagicMock()
        processor.parse_capture.return_value = capture
        processor.process_standard.return_value = SpeedTimeline(
            readings=[
                SpeedReading(
                    speed_mph=100.0,
                    magnitude=1000.0,
                    timestamp_ms=68.0,
                    direction="outbound",
                )
            ],
            sample_rate_hz=937.5,
        )

        trigger = SoundTrigger(pre_trigger_segments=12)
        result = trigger.wait_for_trigger(radar, processor, timeout=1.0)

        assert result is capture
        assert result.trigger_timestamp == pytest.approx(12100.068)
        assert result.trigger_timestamp_source == "ops_clock_sync"
        assert radar.last_clock_sync is radar.read_clock_sync.return_value
        radar.read_clock_sync.assert_called_once_with(samples=36, store=False)

    def test_sound_trigger_uses_recent_previous_sync_when_fresh_sync_is_bad(self):
        """A bad per-shot C? read should not discard a recent valid sync."""
        from openflight.rolling_buffer.trigger import SoundTrigger

        now = time.time()
        radar = MagicMock()
        radar.wait_for_hardware_trigger.return_value = '{"sample_time": 0.0}'
        radar.last_hardware_trigger_first_byte_timestamp = 12345.678
        previous_sync = {
            "source": "startup",
            "samples": 3,
            "valid_samples": 3,
            "best_offset_s": 12000.0,
            "clock_sync_method": "integer_rollover",
            "usable_for_trigger_timestamps": True,
            "rollover_uncertainty_ms": 10.0,
            "reads": [
                {
                    "host_after": now,
                    "host_mid": now,
                    "radar_clock_s": 100.0,
                    "read_latency_ms": 1.0,
                }
            ],
        }
        radar.last_clock_sync = previous_sync
        radar.read_clock_sync.return_value = {
            "source": "per_shot",
            "samples": 4,
            "valid_samples": 2,
            "best_offset_s": 12100.0,
            "clock_sync_method": "integer_rollover",
            "usable_for_trigger_timestamps": True,
            "rollover_uncertainty_ms": 900.0,
            "reads": [
                {
                    "host_after": now,
                    "host_mid": now,
                    "radar_clock_s": None,
                    "read_latency_ms": 200.0,
                }
            ],
        }

        capture = IQCapture(
            sample_time=100.000,
            trigger_time=100.068,
            i_samples=[2048] * 4096,
            q_samples=[2048] * 4096,
        )
        processor = MagicMock()
        processor.parse_capture.return_value = capture
        processor.process_standard.return_value = SpeedTimeline(
            readings=[
                SpeedReading(
                    speed_mph=100.0,
                    magnitude=1000.0,
                    timestamp_ms=68.0,
                    direction="outbound",
                )
            ],
            sample_rate_hz=937.5,
        )

        trigger = SoundTrigger(pre_trigger_segments=12)
        result = trigger.wait_for_trigger(radar, processor, timeout=1.0)

        assert result is capture
        assert result.trigger_timestamp == pytest.approx(12100.068)
        assert result.trigger_timestamp_source == "ops_clock_sync"
        assert radar.last_clock_sync is previous_sync

    def test_sound_trigger_ignores_unusable_ops_clock_sync(self):
        """Whole-second-only clock sync should not override first-byte timing."""
        from openflight.rolling_buffer.trigger import SoundTrigger

        radar = MagicMock()
        radar.wait_for_hardware_trigger.return_value = '{"sample_time": 0.0}'
        radar.last_hardware_trigger_first_byte_timestamp = 12345.678
        radar.last_clock_sync = {
            "best_offset_s": 12000.0,
            "usable_for_trigger_timestamps": False,
            "clock_sync_method": "integer_unusable_no_rollover",
        }
        radar.read_clock_sync.return_value = {
            "samples": 1,
            "valid_samples": 0,
            "best_offset_s": None,
            "usable_for_trigger_timestamps": False,
            "clock_sync_method": "no_valid_reads",
            "reads": [],
        }

        capture = IQCapture(
            sample_time=100.000,
            trigger_time=100.068,
            i_samples=[2048] * 4096,
            q_samples=[2048] * 4096,
            first_byte_timestamp=12345.678,
        )
        processor = MagicMock()
        processor.parse_capture.return_value = capture
        processor.process_standard.return_value = SpeedTimeline(
            readings=[
                SpeedReading(
                    speed_mph=100.0,
                    magnitude=1000.0,
                    timestamp_ms=68.0,
                    direction="outbound",
                )
            ],
            sample_rate_hz=937.5,
        )

        trigger = SoundTrigger(pre_trigger_segments=12)
        result = trigger.wait_for_trigger(radar, processor, timeout=1.0)

        expected_post_trigger_s = (capture.duration_ms - capture.trigger_offset_ms) / 1000.0
        assert result is capture
        assert result.trigger_timestamp == pytest.approx(12345.678 - expected_post_trigger_s)
        assert result.trigger_timestamp_source == "first_byte"


# =============================================================================
# Tests for Shot with Spin Fields
# =============================================================================


class TestShotWithSpin:
    """Tests for Shot dataclass spin-related fields."""

    def test_shot_with_spin_data(self):
        """Shot should accept spin fields."""
        shot = Shot(
            ball_speed_mph=160.0,
            timestamp=datetime.now(),
            club_speed_mph=108.0,
            spin_rpm=2550.0,
            spin_confidence=0.85,
            carry_spin_adjusted=275.0,
        )
        assert shot.spin_rpm == 2550.0
        assert shot.spin_confidence == 0.85
        assert shot.carry_spin_adjusted == 275.0

    def test_shot_without_spin_data(self):
        """Shot should work without spin fields."""
        shot = Shot(
            ball_speed_mph=160.0,
            timestamp=datetime.now(),
        )
        assert shot.spin_rpm is None
        assert shot.spin_confidence is None
        assert shot.carry_spin_adjusted is None

    def test_has_spin_property(self):
        """has_spin should return True when spin_rpm is set."""
        shot_with_spin = Shot(
            ball_speed_mph=160.0,
            timestamp=datetime.now(),
            spin_rpm=2550.0,
        )
        shot_without_spin = Shot(
            ball_speed_mph=160.0,
            timestamp=datetime.now(),
        )

        assert shot_with_spin.has_spin is True
        assert shot_without_spin.has_spin is False

    def test_spin_quality_high(self):
        """High confidence should return 'high' quality."""
        shot = Shot(
            ball_speed_mph=160.0,
            timestamp=datetime.now(),
            spin_rpm=2550.0,
            spin_confidence=0.8,
        )
        assert shot.spin_quality == "high"

    def test_spin_quality_medium(self):
        """Medium confidence should return 'medium' quality."""
        shot = Shot(
            ball_speed_mph=160.0,
            timestamp=datetime.now(),
            spin_rpm=2550.0,
            spin_confidence=0.5,
        )
        assert shot.spin_quality == "medium"

    def test_spin_quality_low(self):
        """Low confidence should return 'low' quality."""
        shot = Shot(
            ball_speed_mph=160.0,
            timestamp=datetime.now(),
            spin_rpm=2550.0,
            spin_confidence=0.3,
        )
        assert shot.spin_quality == "low"

    def test_spin_quality_none_without_confidence(self):
        """No confidence should return None quality."""
        shot = Shot(
            ball_speed_mph=160.0,
            timestamp=datetime.now(),
        )
        assert shot.spin_quality is None

    def test_spin_quality_preserves_processor_label(self):
        """Shot quality should use the processor label when available."""
        shot = Shot(
            ball_speed_mph=160.0,
            timestamp=datetime.now(),
            spin_rpm=5054.0,
            spin_confidence=0.5,
            spin_result_quality="low",
        )
        assert shot.spin_quality == "low"

    def test_shot_accepts_rejected_spin_diagnostics(self):
        """Rejected spin diagnostics should be representable on a shot."""
        shot = Shot(
            ball_speed_mph=120.0,
            timestamp=datetime.now(),
            spin_snr=2.96,
            spin_peak_freq_hz=95.21484375,
            spin_seam_cycles=4.8,
            spin_rejection_reason="SNR too low (2.96, need 3.0)",
        )
        assert shot.spin_rpm is None
        assert shot.spin_snr == pytest.approx(2.96)
        assert shot.spin_peak_freq_hz == pytest.approx(95.21484375)
        assert shot.spin_rejection_reason == "SNR too low (2.96, need 3.0)"


# =============================================================================
# Tests for monitor spin plausibility
# =============================================================================


class TestRollingBufferMonitorSpinPlausibility:
    """Tests for club-aware filtering of spin artifacts."""

    def _processed_with_spin(self, spin: SpinResult) -> ProcessedCapture:
        capture = IQCapture(
            sample_time=0.0,
            trigger_time=0.068,
            i_samples=[2048] * 4096,
            q_samples=[2048] * 4096,
            first_byte_timestamp=12345.746,
            trigger_timestamp=12345.678,
        )
        return ProcessedCapture(
            timeline=SpeedTimeline(readings=[], sample_rate_hz=937.5),
            ball_speed_mph=100.0,
            ball_timestamp_ms=60.0,
            club_speed_mph=75.0,
            spin=spin,
            capture=capture,
        )

    def test_lower_rail_spin_withheld_for_high_spin_club(self):
        """PW/short-iron rail-low spin should be logged but not exposed."""
        from openflight.rolling_buffer import RollingBufferMonitor

        monitor = RollingBufferMonitor(port=None, trigger_type="sound")
        monitor.set_club(ClubType.PW)
        processed = self._processed_with_spin(
            SpinResult(
                spin_rpm=3296,
                confidence=0.8,
                snr=12.99,
                quality="high",
                peak_freq_hz=54.931640625,
                seam_cycles=3.9,
                at_lower_rail=True,
            )
        )

        shot = monitor._create_shot(processed)

        assert shot is not None
        assert shot.spin_rpm is None
        assert shot.spin_snr == pytest.approx(12.99)
        assert shot.spin_rejection_reason is not None
        assert "plausibility floor" in shot.spin_rejection_reason
        assert shot.impact_timestamp == pytest.approx(12345.678)

    def test_kld7_impact_timestamp_uses_hardware_trigger_timestamp(self):
        """K-LD7 geometry should use the trusted sound-trigger impact time."""
        from openflight.rolling_buffer import RollingBufferMonitor

        monitor = RollingBufferMonitor(port=None, trigger_type="sound")
        processed = self._processed_with_spin(
            SpinResult(spin_rpm=0, confidence=0.0, snr=0.0, quality="none")
        )

        shot = monitor._create_shot(processed)

        assert shot is not None
        assert shot.impact_timestamp == pytest.approx(12345.678)
        assert shot.impact_timestamp_kld7 == pytest.approx(12345.678)

    def test_lower_rail_driver_spin_kept_diagnostic_only(self):
        """Rail picks should be logged but not exposed as measured spin."""
        from openflight.rolling_buffer import RollingBufferMonitor

        monitor = RollingBufferMonitor(port=None, trigger_type="sound")
        monitor.set_club(ClubType.DRIVER)
        processed = self._processed_with_spin(
            SpinResult(
                spin_rpm=3296,
                confidence=0.8,
                snr=12.99,
                quality="high",
                peak_freq_hz=54.931640625,
                seam_cycles=3.9,
                at_lower_rail=True,
            )
        )

        shot = monitor._create_shot(processed)

        assert shot is not None
        assert shot.spin_rpm is None
        assert shot.spin_snr == pytest.approx(12.99)
        assert shot.spin_rejection_reason == (
            "Lower-rail spin candidate 3296 RPM kept as diagnostic only"
        )

    def test_low_quality_spin_withheld_from_shot_metrics(self):
        """Low-confidence rail spin should remain diagnostic, not user-facing."""
        from openflight.rolling_buffer import RollingBufferMonitor

        monitor = RollingBufferMonitor(port=None, trigger_type="sound")
        monitor.set_club(ClubType.DRIVER)
        processed = self._processed_with_spin(
            SpinResult(
                spin_rpm=3296,
                confidence=0.3,
                snr=2.88,
                quality="low",
                peak_freq_hz=54.931640625,
                seam_cycles=3.8,
                at_lower_rail=True,
            )
        )

        shot = monitor._create_shot(processed)

        assert shot is not None
        assert shot.spin_rpm is None
        assert shot.spin_snr == pytest.approx(2.88)
        assert shot.spin_peak_freq_hz == pytest.approx(54.931640625)
        assert shot.spin_rejection_reason == (
            "Lower-rail spin candidate 3296 RPM kept as diagnostic only"
        )

    def test_low_quality_non_rail_spin_remains_reportable(self):
        """Non-rail low-confidence spin can be shown but must not drive carry."""
        from openflight.rolling_buffer import RollingBufferMonitor

        monitor = RollingBufferMonitor(port=None, trigger_type="sound")
        monitor.set_club(ClubType.IRON_7)
        processed = self._processed_with_spin(
            SpinResult(
                spin_rpm=5493,
                confidence=0.3,
                snr=2.84,
                quality="low",
                peak_freq_hz=91.552734375,
                seam_cycles=4.1,
                at_lower_rail=False,
            )
        )

        shot = monitor._create_shot(processed)

        assert shot is not None
        assert shot.spin_rpm == 5493
        assert shot.spin_quality == "low"
        assert shot.spin_rejection_reason is None
        assert shot.carry_spin_adjusted is None

    def test_create_shot_uses_capture_trigger_epoch_for_impact_timestamp(self):
        """Rolling-buffer shots should carry the wall-clock sound trigger time."""
        from openflight.rolling_buffer import RollingBufferMonitor

        monitor = RollingBufferMonitor(port=None, trigger_type="sound")
        capture = IQCapture(
            sample_time=100.000,
            trigger_time=100.068,
            i_samples=[0] * 4096,
            q_samples=[0] * 4096,
            trigger_timestamp=1715000000.123,
        )
        processed = ProcessedCapture(
            timeline=SpeedTimeline(readings=[], sample_rate_hz=937.5, capture=capture),
            ball_speed_mph=100.0,
            ball_timestamp_ms=68.0,
            club_speed_mph=75.0,
            capture=capture,
        )

        shot = monitor._create_shot(processed)

        assert shot is not None
        assert shot.impact_timestamp == pytest.approx(1715000000.123)
        assert shot.impact_timestamp_kld7 == pytest.approx(1715000000.123)

    def test_create_shot_applies_ops_transition_impact_offset(self):
        """Transition impact timing should shift K-LD7 correlation per shot."""
        from openflight.rolling_buffer import RollingBufferMonitor

        monitor = RollingBufferMonitor(port=None, trigger_type="sound")
        capture = IQCapture(
            sample_time=100.000,
            trigger_time=100.068,
            i_samples=[0] * 4096,
            q_samples=[0] * 4096,
            trigger_timestamp=1715000000.123,
        )
        processed = ProcessedCapture(
            timeline=SpeedTimeline(readings=[], sample_rate_hz=937.5, capture=capture),
            ball_speed_mph=100.0,
            ball_timestamp_ms=68.0,
            club_speed_mph=75.0,
            capture=capture,
            impact=ImpactEstimate(
                timestamp_ms=60.0,
                source="ops_transition",
            ),
        )

        shot = monitor._create_shot(processed)

        assert shot is not None
        assert shot.impact_timestamp == pytest.approx(1715000000.123)
        assert shot.impact_timestamp_kld7 == pytest.approx(1715000000.115)


class TestRollingBufferShotIdentity:
    """Raw OPS captures and downstream shots must share a monotonic identity."""

    @staticmethod
    def _processed(impact_timestamp: float) -> ProcessedCapture:
        capture = IQCapture(
            sample_time=100.0,
            trigger_time=100.068,
            i_samples=[2048] * 16,
            q_samples=[2048] * 16,
            trigger_timestamp=impact_timestamp,
        )
        return ProcessedCapture(
            timeline=SpeedTimeline(readings=[], sample_rate_hz=937.5, capture=capture),
            ball_speed_mph=100.0,
            ball_timestamp_ms=68.0,
            club_speed_mph=75.0,
            capture=capture,
        )

    def test_clear_session_does_not_reuse_raw_ops_shot_number(self, monkeypatch):
        from openflight.rolling_buffer import RollingBufferMonitor, monitor as monitor_module

        raw_captures = []
        downstream_shots = []
        session_log = MagicMock()
        session_log.log_rolling_buffer_capture.side_effect = lambda **row: raw_captures.append(row)
        monkeypatch.setattr(monitor_module, "get_session_logger", lambda: session_log)

        monitor = RollingBufferMonitor(port=None, trigger_type="sound")
        monitor._diagnostic_callback = None
        monitor._shot_callback = downstream_shots.append

        def run_capture(impact_timestamp):
            processed = self._processed(impact_timestamp)

            class OneCaptureTrigger:
                calls = 0

                def wait_for_trigger(self, **_kwargs):
                    self.calls += 1
                    if self.calls == 1:
                        return processed.capture
                    monitor._running = False
                    return None

                @staticmethod
                def drain_diagnostics():
                    return []

                @staticmethod
                def reset():
                    return None

            monitor.trigger = OneCaptureTrigger()
            monitor.processor = MagicMock(process_capture=MagicMock(return_value=processed))
            monitor._running = True
            monitor._capture_loop()

        run_capture(1000.0)
        monitor.clear_session()
        run_capture(1001.0)

        assert [(shot.shot_number, shot.impact_timestamp) for shot in downstream_shots] == [
            (1, 1000.0),
            (2, 1001.0),
        ]
        assert [(row["shot_number"], row["trigger_timestamp"]) for row in raw_captures] == [
            (1, 1000.0),
            (2, 1001.0),
        ]


# =============================================================================
# Integration Tests
# =============================================================================


class TestCarryCalculationIntegration:
    """Integration tests for the complete carry calculation pipeline."""

    def test_full_shot_carry_calculation(self):
        """Test complete flow from ball speed + spin to carry distance."""
        # Simulate a Tour-quality shot
        ball_speed = 167  # Tour average
        club_speed = 113  # Tour average
        spin = 2686  # Tour average

        # Calculate carry
        carry = estimate_carry_with_spin(
            ball_speed, spin, ClubType.DRIVER, club_speed_mph=club_speed
        )

        # Should be close to Tour average (~275 yards)
        assert 265 <= carry <= 285

        # Smash factor check
        smash = ball_speed / club_speed
        assert 1.45 <= smash <= 1.52  # Tour range

    def test_amateur_shot_comparison(self):
        """Compare amateur vs Tour carry distances."""
        # Amateur: 140 mph ball, 95 mph club, 3000 rpm spin (slightly high)
        amateur_carry = estimate_carry_with_spin(140, 3000, ClubType.DRIVER, club_speed_mph=95)

        # Tour: 167 mph ball, 113 mph club, 2686 rpm spin (optimal)
        tour_carry = estimate_carry_with_spin(167, 2686, ClubType.DRIVER, club_speed_mph=113)

        # Tour should be significantly longer (at least 30 yards more)
        assert tour_carry > amateur_carry + 30

    def test_same_ball_speed_different_spin(self):
        """Same ball speed with different spins should produce different carries."""
        ball_speed = 155
        club_speed = 105

        carry_low_spin = estimate_carry_with_spin(
            ball_speed, 1800, ClubType.DRIVER, club_speed_mph=club_speed
        )
        carry_optimal_spin = estimate_carry_with_spin(
            ball_speed, 2650, ClubType.DRIVER, club_speed_mph=club_speed
        )
        carry_high_spin = estimate_carry_with_spin(
            ball_speed, 3500, ClubType.DRIVER, club_speed_mph=club_speed
        )

        # Optimal should be best
        assert carry_optimal_spin > carry_low_spin
        assert carry_optimal_spin > carry_high_spin

        # All should be positive and reasonable (widen ranges)
        assert 200 <= carry_low_spin <= 270
        assert 230 <= carry_optimal_spin <= 280
        assert 210 <= carry_high_spin <= 270


# =============================================================================
# Tests for Trigger Diagnostics
# =============================================================================


class TestTriggerStrategyDiagnostics:
    """Tests for the diagnostic accumulation in TriggerStrategy."""

    def test_drain_clears_diagnostics_and_defaults_lists(self):
        trigger = SoundTrigger()
        assert trigger.drain_diagnostics() == []
        trigger._append_diagnostic(accepted=False, reason="timeout")

        diagnostic = trigger.drain_diagnostics()[0]

        assert diagnostic["all_outbound_speeds"] == []
        assert diagnostic["all_inbound_speeds"] == []
        assert diagnostic["peak_outbound_magnitude"] == 0.0
        assert diagnostic["peak_inbound_magnitude"] == 0.0
        assert trigger.drain_diagnostics() == []

    def test_append_diagnostic_preserves_capture_details(self):
        trigger = SoundTrigger()
        trigger._append_diagnostic(
            accepted=True,
            reason="accepted",
            response_bytes=32768,
            total_readings=32,
            outbound_readings=8,
            inbound_readings=24,
            peak_outbound_mph=155.3,
            peak_inbound_mph=45.0,
            all_outbound_speeds=[155.3, 140.2],
            all_inbound_speeds=[45.0],
            peak_outbound_magnitude=245.5,
            peak_inbound_magnitude=180.3,
        )

        diag = trigger.drain_diagnostics()[0]
        assert diag["accepted"] is True
        assert diag["reason"] == "accepted"
        assert diag["response_bytes"] == 32768
        assert diag["total_readings"] == 32
        assert diag["outbound_readings"] == 8
        assert diag["peak_outbound_mph"] == 155.3
        assert diag["all_outbound_speeds"] == [155.3, 140.2]
        assert diag["peak_outbound_magnitude"] == 245.5
        assert diag["peak_inbound_magnitude"] == 180.3
        assert "timestamp" in diag

    def test_capture_activity_summary_counts_valid_outbound(self):
        trigger = SoundTrigger()

        class StubProcessor:
            def process_standard(self, capture):
                return SpeedTimeline(
                    readings=[
                        SpeedReading(
                            speed_mph=12.0,
                            magnitude=80.0,
                            timestamp_ms=0.0,
                            direction="outbound",
                        ),
                        SpeedReading(
                            speed_mph=68.0,
                            magnitude=250.0,
                            timestamp_ms=4.0,
                            direction="outbound",
                        ),
                        SpeedReading(
                            speed_mph=20.0,
                            magnitude=120.0,
                            timestamp_ms=8.0,
                            direction="inbound",
                        ),
                    ],
                    sample_rate_hz=56.0,
                )

        summary = trigger._summarize_capture_activity(StubProcessor(), object())

        assert summary["total_readings"] == 3
        assert summary["outbound_readings"] == 2
        assert summary["inbound_readings"] == 1
        assert summary["peak_outbound_mph"] == 68.0
        assert summary["peak_inbound_mph"] == 20.0
        assert summary["peak_outbound_magnitude"] == 250.0
        assert summary["valid_outbound_count"] == 1
        assert summary["valid_peak_outbound_mph"] == 68.0

    def test_activity_diagnostic_uses_summary_fields(self):
        trigger = SoundTrigger()
        summary = {
            "total_readings": 3,
            "outbound_readings": 2,
            "inbound_readings": 1,
            "peak_outbound_mph": 68.0,
            "peak_inbound_mph": 20.0,
            "all_outbound_speeds": [12.0, 68.0],
            "all_inbound_speeds": [20.0],
            "peak_outbound_magnitude": 250.0,
            "peak_inbound_magnitude": 120.0,
        }

        trigger._append_activity_diagnostic(
            summary,
            accepted=True,
            reason="accepted",
            response_bytes=32768,
            trigger_latency_ms=3.5,
        )
        diagnostics = trigger.drain_diagnostics()

        assert diagnostics[0]["accepted"] is True
        assert diagnostics[0]["response_bytes"] == 32768
        assert diagnostics[0]["all_outbound_speeds"] == [12.0, 68.0]
        assert diagnostics[0]["peak_outbound_magnitude"] == 250.0
        assert diagnostics[0]["trigger_latency_ms"] == 3.5


# =============================================================================
# Tests for FFT Dual-Peak Extraction and DC Mask
# =============================================================================


class TestDualPeakExtraction:
    """Tests for dual-peak FFT processing and DC mask."""

    @pytest.fixture
    def processor(self):
        """Create a processor instance for testing."""
        return RollingBufferProcessor()

    def test_dc_mask_bins_constant(self, processor):
        """DC_MASK_BINS should be 150 (~15 mph exclusion zone)."""
        assert processor.DC_MASK_BINS == 150

    def test_both_peaks_extracted_from_block(self, processor):
        """A signal with outbound + inbound tones should produce two peaks."""
        import numpy as np

        n = processor.WINDOW_SIZE
        t = np.arange(n) / processor.SAMPLE_RATE

        # Outbound tone at ~120 mph
        # speed = freq * wavelength / 2 => freq = speed / (wavelength/2)
        # freq = 120 / 2.23694 (m/s) * 2 / 0.01243 = ~8630 Hz
        outbound_freq = (120 / processor.MPS_TO_MPH) * 2 / processor.WAVELENGTH_M
        # Inbound tone at ~50 mph
        inbound_freq = (50 / processor.MPS_TO_MPH) * 2 / processor.WAVELENGTH_M

        # Outbound = positive frequency, Inbound = negative frequency
        # I + jQ: positive freq => I=cos, Q=sin; negative freq => I=cos, Q=-sin
        i_signal = 500 * np.cos(2 * np.pi * outbound_freq * t) + 400 * np.cos(
            2 * np.pi * inbound_freq * t
        )
        q_signal = 500 * np.sin(2 * np.pi * outbound_freq * t) - 400 * np.sin(
            2 * np.pi * inbound_freq * t
        )

        # Offset to simulate ADC midpoint
        i_block = (i_signal + 2048).astype(np.float64)
        q_block = (q_signal + 2048).astype(np.float64)

        results = processor._process_block(i_block, q_block)

        # Should find both peaks
        directions = [r[2] for r in results]
        assert "outbound" in directions, f"Expected outbound peak, got: {results}"
        assert "inbound" in directions, f"Expected inbound peak, got: {results}"

        # Check speeds are approximately correct
        for speed, mag, direction in results:
            if direction == "outbound":
                assert abs(speed - 120) < 5, f"Outbound speed {speed} not near 120 mph"
            elif direction == "inbound":
                assert abs(speed - 50) < 5, f"Inbound speed {speed} not near 50 mph"

    def test_dc_leakage_does_not_mask_real_signal(self, processor):
        """Strong DC offset should not prevent detection of real Doppler signal."""
        import numpy as np

        n = processor.WINDOW_SIZE
        t = np.arange(n) / processor.SAMPLE_RATE

        # Real outbound Doppler at ~80 mph
        real_freq = (80 / processor.MPS_TO_MPH) * 2 / processor.WAVELENGTH_M

        # Strong DC component (large offset that won't be fully removed by mean subtraction
        # due to windowing artifacts) plus real signal
        i_signal = 2048 + 300 * np.cos(2 * np.pi * real_freq * t)
        q_signal = 2048 + 300 * np.sin(2 * np.pi * real_freq * t)

        i_block = i_signal.astype(np.float64)
        q_block = q_signal.astype(np.float64)

        results = processor._process_block(i_block, q_block)

        # Should find the real signal, not a DC artifact
        outbound_results = [(s, m, d) for s, m, d in results if d == "outbound"]
        assert len(outbound_results) > 0, f"No outbound peak found, results: {results}"

        # The detected speed should be near 80 mph, not near 0
        speed = outbound_results[0][0]
        assert speed > 10, f"Detected speed {speed} mph is too low (DC artifact?)"
        assert abs(speed - 80) < 5, f"Outbound speed {speed} not near 80 mph"

    def test_two_outbound_peaks_extracted(self, processor):
        """Two outbound signals (club+ball) should both be extracted."""
        import numpy as np

        n = processor.WINDOW_SIZE
        t = np.arange(n) / processor.SAMPLE_RATE

        # Club at ~40 mph, Ball at ~120 mph — both outbound (positive freq)
        club_freq = (40 / processor.MPS_TO_MPH) * 2 / processor.WAVELENGTH_M
        ball_freq = (120 / processor.MPS_TO_MPH) * 2 / processor.WAVELENGTH_M

        i_signal = 400 * np.cos(2 * np.pi * club_freq * t) + 300 * np.cos(2 * np.pi * ball_freq * t)
        q_signal = 400 * np.sin(2 * np.pi * club_freq * t) + 300 * np.sin(2 * np.pi * ball_freq * t)

        i_block = (i_signal + 2048).astype(np.float64)
        q_block = (q_signal + 2048).astype(np.float64)

        results = processor._process_block(i_block, q_block)

        outbound = [(s, m, d) for s, m, d in results if d == "outbound"]
        assert len(outbound) >= 2, f"Expected 2+ outbound peaks, got {len(outbound)}: {results}"

        speeds = sorted([s for s, m, d in outbound])
        assert any(abs(s - 40) < 5 for s in speeds), f"No peak near 40 mph: {speeds}"
        assert any(abs(s - 120) < 5 for s in speeds), f"No peak near 120 mph: {speeds}"

    def test_single_outbound_peak_no_regression(self, processor):
        """Single outbound signal should still work correctly."""
        import numpy as np

        n = processor.WINDOW_SIZE
        t = np.arange(n) / processor.SAMPLE_RATE

        outbound_freq = (80 / processor.MPS_TO_MPH) * 2 / processor.WAVELENGTH_M
        i_signal = 500 * np.cos(2 * np.pi * outbound_freq * t)
        q_signal = 500 * np.sin(2 * np.pi * outbound_freq * t)

        i_block = (i_signal + 2048).astype(np.float64)
        q_block = (q_signal + 2048).astype(np.float64)

        results = processor._process_block(i_block, q_block)

        outbound = [(s, m, d) for s, m, d in results if d == "outbound"]
        assert len(outbound) >= 1
        assert abs(outbound[0][0] - 80) < 5

    def test_ball_found_when_backswing_stronger(self, processor):
        """Outbound ball should be found even when inbound backswing is stronger."""
        import numpy as np

        # Create full 4096-sample capture with strong inbound + weaker outbound
        n_samples = 4096
        t = np.arange(n_samples) / processor.SAMPLE_RATE

        # Strong inbound (backswing) at 50 mph, amplitude 800
        inbound_freq = (50 / processor.MPS_TO_MPH) * 2 / processor.WAVELENGTH_M
        # Weaker outbound (ball) at 120 mph, amplitude 300
        outbound_freq = (120 / processor.MPS_TO_MPH) * 2 / processor.WAVELENGTH_M

        i_signal = 300 * np.cos(2 * np.pi * outbound_freq * t) + 800 * np.cos(
            2 * np.pi * inbound_freq * t
        )
        q_signal = 300 * np.sin(2 * np.pi * outbound_freq * t) - 800 * np.sin(
            2 * np.pi * inbound_freq * t
        )

        i_samples = (i_signal + 2048).astype(int).tolist()
        q_samples = (q_signal + 2048).astype(int).tolist()

        capture = IQCapture(
            sample_time=0.136,
            trigger_time=0.0,
            i_samples=i_samples,
            q_samples=q_samples,
            timestamp=1234567890.0,
        )

        timeline = processor.process_standard(capture)

        # Should have outbound readings despite stronger inbound
        outbound = [r for r in timeline.readings if r.is_outbound]
        assert len(outbound) > 0, (
            f"No outbound readings found. Total readings: {len(timeline.readings)}, "
            f"directions: {[r.direction for r in timeline.readings]}"
        )

        # Peak outbound should be near 120 mph
        peak_outbound = max(r.speed_mph for r in outbound)
        assert peak_outbound > 100, f"Peak outbound {peak_outbound} mph too low"


# =============================================================================
# Tests for _find_peaks
# =============================================================================


class TestFindPeaks:
    """Tests for the _find_peaks local maxima finder."""

    @pytest.fixture
    def processor(self):
        return RollingBufferProcessor()

    def test_single_peak(self, processor):
        """Single peak above threshold should be found."""
        import numpy as np

        magnitude = np.zeros(2048)
        magnitude[500] = 100.0  # Clear peak
        magnitude[499] = 20.0  # Neighbors lower
        magnitude[501] = 20.0

        peaks = processor._find_peaks(magnitude, start=1, end=2048)
        assert len(peaks) >= 1
        assert peaks[0][0] == 500
        assert peaks[0][1] == 100.0

    def test_two_separated_peaks(self, processor):
        """Two well-separated peaks should both be found."""
        import numpy as np

        magnitude = np.zeros(2048)
        # Peak 1
        magnitude[300] = 80.0
        magnitude[299] = 10.0
        magnitude[301] = 10.0
        # Peak 2 — well separated (>50 bins apart)
        magnitude[600] = 120.0
        magnitude[599] = 10.0
        magnitude[601] = 10.0

        peaks = processor._find_peaks(magnitude, start=1, end=2048)
        assert len(peaks) >= 2
        bins = [p[0] for p in peaks]
        assert 300 in bins
        assert 600 in bins

    def test_close_peaks_merged(self, processor):
        """Two peaks within MIN_PEAK_SEPARATION_BINS should keep only the stronger."""
        import numpy as np

        magnitude = np.zeros(2048)
        # Peak 1 — weaker
        magnitude[300] = 50.0
        magnitude[299] = 10.0
        magnitude[301] = 10.0
        # Peak 2 — stronger, only 20 bins away (< 50)
        magnitude[320] = 80.0
        magnitude[319] = 10.0
        magnitude[321] = 10.0

        peaks = processor._find_peaks(magnitude, start=1, end=2048)
        # Should only keep the stronger peak at 320
        assert len(peaks) == 1
        assert peaks[0][0] == 320

    def test_below_threshold_rejected(self, processor):
        """Peaks below MAGNITUDE_THRESHOLD should be rejected."""
        import numpy as np

        magnitude = np.zeros(2048)
        magnitude[500] = 1.0  # Below threshold (3)
        magnitude[499] = 0.5
        magnitude[501] = 0.5

        peaks = processor._find_peaks(magnitude, start=1, end=2048)
        assert len(peaks) == 0

    def test_max_peaks_cap(self, processor):
        """Should return at most MAX_PEAKS_PER_DIRECTION peaks."""
        import numpy as np

        magnitude = np.zeros(2048)
        # Create 5 well-separated peaks
        for i, pos in enumerate([200, 400, 600, 800, 1000]):
            magnitude[pos] = 100.0 + i * 10
            magnitude[pos - 1] = 5.0
            magnitude[pos + 1] = 5.0

        peaks = processor._find_peaks(magnitude, start=1, end=2048)
        assert len(peaks) <= processor.MAX_PEAKS_PER_DIRECTION

    def test_flat_region_no_peaks(self, processor):
        """Flat constant signal should produce no peaks."""
        import numpy as np

        magnitude = np.ones(2048) * 50.0  # Flat — no local maxima

        peaks = processor._find_peaks(magnitude, start=1, end=2048)
        assert len(peaks) == 0

    def test_sorted_by_magnitude_descending(self, processor):
        """Returned peaks should be sorted by magnitude descending."""
        import numpy as np

        magnitude = np.zeros(2048)
        magnitude[200] = 50.0
        magnitude[199] = 5.0
        magnitude[201] = 5.0
        magnitude[400] = 100.0
        magnitude[399] = 5.0
        magnitude[401] = 5.0
        magnitude[600] = 75.0
        magnitude[599] = 5.0
        magnitude[601] = 5.0

        peaks = processor._find_peaks(magnitude, start=1, end=2048)
        mags = [p[1] for p in peaks]
        assert mags == sorted(mags, reverse=True)


# =============================================================================
# Tests for find_club_speed with concurrent readings
# =============================================================================


class TestFindClubSpeedOverlap:
    """Tests for find_club_speed searching concurrent timestamps."""

    @pytest.fixture
    def processor(self):
        return RollingBufferProcessor()

    def test_club_at_same_timestamp_as_ball(self, processor):
        """Club reading at the same timestamp as ball should be found."""
        readings = [
            SpeedReading(speed_mph=60.0, magnitude=200.0, timestamp_ms=10.0, direction="outbound"),
            SpeedReading(speed_mph=80.0, magnitude=300.0, timestamp_ms=10.0, direction="outbound"),
        ]
        timeline = SpeedTimeline(readings=readings, sample_rate_hz=937.5)

        club_speed, club_ts = processor.find_club_speed(
            timeline, ball_speed_mph=80.0, ball_timestamp_ms=10.0
        )

        assert club_speed == 60.0
        assert club_ts == 10.0

    def test_club_before_ball_still_works(self, processor):
        """Club at earlier timestamp should still be found."""
        readings = [
            SpeedReading(speed_mph=58.0, magnitude=200.0, timestamp_ms=5.0, direction="outbound"),
            SpeedReading(speed_mph=80.0, magnitude=300.0, timestamp_ms=10.0, direction="outbound"),
        ]
        timeline = SpeedTimeline(readings=readings, sample_rate_hz=937.5)

        club_speed, club_ts = processor.find_club_speed(
            timeline, ball_speed_mph=80.0, ball_timestamp_ms=10.0
        )

        assert club_speed == 58.0
        assert club_ts == 5.0

    def test_ball_not_returned_as_club(self, processor):
        """Ball reading itself should not be returned as club speed."""
        readings = [
            SpeedReading(speed_mph=80.0, magnitude=300.0, timestamp_ms=10.0, direction="outbound"),
        ]
        timeline = SpeedTimeline(readings=readings, sample_rate_hz=937.5)

        club_speed, club_ts = processor.find_club_speed(
            timeline, ball_speed_mph=80.0, ball_timestamp_ms=10.0
        )

        assert club_speed is None
        assert club_ts is None

    def test_speed_range_filtering(self, processor):
        """Only speeds within 67-85% of ball speed should be candidates."""
        readings = [
            # Too slow (< 67% of 100)
            SpeedReading(speed_mph=60.0, magnitude=200.0, timestamp_ms=10.0, direction="outbound"),
            # Too fast (> 85% of 100)
            SpeedReading(speed_mph=90.0, magnitude=200.0, timestamp_ms=10.0, direction="outbound"),
            # Just right (75% of 100)
            SpeedReading(speed_mph=75.0, magnitude=200.0, timestamp_ms=10.0, direction="outbound"),
            # Ball
            SpeedReading(speed_mph=100.0, magnitude=400.0, timestamp_ms=10.0, direction="outbound"),
        ]
        timeline = SpeedTimeline(readings=readings, sample_rate_hz=937.5)

        club_speed, _ = processor.find_club_speed(
            timeline, ball_speed_mph=100.0, ball_timestamp_ms=10.0
        )

        assert club_speed == 75.0

    def test_highest_magnitude_selected(self, processor):
        """Among valid candidates, highest magnitude should win."""
        readings = [
            SpeedReading(speed_mph=70.0, magnitude=100.0, timestamp_ms=10.0, direction="outbound"),
            SpeedReading(speed_mph=75.0, magnitude=250.0, timestamp_ms=10.0, direction="outbound"),
            SpeedReading(speed_mph=100.0, magnitude=400.0, timestamp_ms=10.0, direction="outbound"),
        ]
        timeline = SpeedTimeline(readings=readings, sample_rate_hz=937.5)

        club_speed, _ = processor.find_club_speed(
            timeline, ball_speed_mph=100.0, ball_timestamp_ms=10.0
        )

        assert club_speed == 75.0  # Higher magnitude

    def test_accelerating_head_branch_beats_stronger_shaft_return(self, processor):
        """Follow the upper pre-impact branch instead of the strongest reflector."""
        ball_timestamp_ms = 30.0
        readings = []
        head_speeds = [60.0, 68.0, 75.0, 80.0, 82.0, 83.0, 83.0, 78.0]
        for relative_ms, speed_mph in zip(
            [-20.0, -18.0, -16.0, -14.0, -12.0, -10.0, -8.0, -6.0],
            head_speeds,
        ):
            readings.append(
                SpeedReading(
                    speed_mph=speed_mph,
                    magnitude=25.0,
                    timestamp_ms=ball_timestamp_ms + relative_ms,
                    direction="outbound",
                )
            )

        # This slower shaft/body return is stronger than the club head and is
        # what the former maximum-magnitude selector chose.
        readings.append(
            SpeedReading(
                speed_mph=70.0,
                magnitude=1000.0,
                timestamp_ms=18.0,
                direction="outbound",
            )
        )
        readings.extend(
            [
                SpeedReading(98.0, 200.0, 26.0, "outbound"),
                SpeedReading(99.0, 250.0, 27.0, "outbound"),
                SpeedReading(100.0, 300.0, 30.0, "outbound"),
            ]
        )
        timeline = SpeedTimeline(readings=readings, sample_rate_hz=937.5)

        club_speed, club_ts = processor.find_club_speed(
            timeline,
            ball_speed_mph=100.0,
            ball_timestamp_ms=ball_timestamp_ms,
        )

        assert club_speed == pytest.approx(82.0)
        assert club_ts == 18.0

    @pytest.mark.parametrize("club_type", [ClubType.SW, ClubType.LW])
    def test_high_loft_wedge_uses_smooth_terminal_trace(self, processor, club_type):
        """A high-loft wedge trace can merge into the similarly fast ball trace."""
        ball_timestamp_ms = 30.0
        readings = [
            SpeedReading(speed, 30.0, ball_timestamp_ms + relative_ms, "outbound")
            for relative_ms, speed in [
                (-12.0, 47.0),
                (-10.0, 52.0),
                (-8.0, 57.0),
                (-6.0, 62.0),
                (-4.0, 66.0),
                (-2.0, 68.0),
                (-1.0, 69.0),
                (0.0, 70.0),
            ]
        ]
        timeline = SpeedTimeline(readings=readings, sample_rate_hz=937.5)

        club_speed, club_ts = processor.find_club_speed(
            timeline,
            ball_speed_mph=70.0,
            ball_timestamp_ms=ball_timestamp_ms,
            club_type=club_type,
        )

        assert club_speed == pytest.approx(69.0)
        assert club_ts == 29.0

    def test_ball_contaminated_plateau_falls_back_to_credible_candidate(self, processor):
        """A non-wedge plateau above 95% of ball speed is likely the ball."""
        ball_timestamp_ms = 30.0
        readings = [
            SpeedReading(75.0, 500.0, 10.0, "outbound"),
            SpeedReading(96.0, 20.0, 12.0, "outbound"),
            SpeedReading(97.0, 20.0, 14.0, "outbound"),
            SpeedReading(98.0, 20.0, 16.0, "outbound"),
            SpeedReading(98.0, 20.0, 18.0, "outbound"),
            SpeedReading(98.0, 20.0, 20.0, "outbound"),
            SpeedReading(99.0, 100.0, 26.0, "outbound"),
            SpeedReading(100.0, 200.0, 27.0, "outbound"),
            SpeedReading(100.0, 300.0, 30.0, "outbound"),
        ]
        timeline = SpeedTimeline(readings=readings, sample_rate_hz=937.5)

        club_speed, club_ts = processor.find_club_speed(
            timeline,
            ball_speed_mph=100.0,
            ball_timestamp_ms=ball_timestamp_ms,
            club_type=ClubType.IRON_9,
        )

        assert club_speed == 75.0
        assert club_ts == 10.0


class TestImpactEstimate:
    """Tests for OPS club-to-ball impact timing."""

    @pytest.fixture
    def processor(self):
        return RollingBufferProcessor()

    @pytest.fixture
    def capture(self):
        return IQCapture(
            sample_time=100.000,
            trigger_time=100.068,
            i_samples=[0] * 4096,
            q_samples=[0] * 4096,
        )

    def test_clear_transition_uses_midpoint_between_frame_centers(
        self,
        processor,
        capture,
    ):
        """A clear club-to-ball speed jump should define impact."""
        readings = [
            SpeedReading(
                speed_mph=60.0,
                magnitude=200.0,
                timestamp_ms=10.0,
                direction="outbound",
            ),
            SpeedReading(
                speed_mph=80.0,
                magnitude=300.0,
                timestamp_ms=11.0,
                direction="outbound",
            ),
        ]
        timeline = SpeedTimeline(readings=readings, sample_rate_hz=937.5)

        impact = processor.estimate_impact(
            timeline,
            ball_speed_mph=80.0,
            club_speed_mph=60.0,
            capture=capture,
        )

        center_offset_ms = (processor.WINDOW_SIZE / processor.SAMPLE_RATE) * 500.0
        assert impact.source == "ops_transition"
        assert impact.timestamp_ms == pytest.approx(10.5 + center_offset_ms)
        assert impact.speed_delta_mph == pytest.approx(20.0)
        assert impact.transition_gap_ms == pytest.approx(1.0)

    def test_small_speed_delta_falls_back_to_sound_trigger(
        self,
        processor,
        capture,
    ):
        """A weak transition is ambiguous, so keep hardware trigger timing."""
        readings = [
            SpeedReading(
                speed_mph=68.0,
                magnitude=200.0,
                timestamp_ms=10.0,
                direction="outbound",
            ),
            SpeedReading(
                speed_mph=80.0,
                magnitude=300.0,
                timestamp_ms=11.0,
                direction="outbound",
            ),
        ]
        timeline = SpeedTimeline(readings=readings, sample_rate_hz=937.5)

        impact = processor.estimate_impact(
            timeline,
            ball_speed_mph=80.0,
            club_speed_mph=68.0,
            capture=capture,
        )

        assert impact.source == "sound_trigger"
        assert impact.reason == "speed_delta_below_threshold"
        assert impact.timestamp_ms == pytest.approx(capture.trigger_offset_ms)
        assert impact.speed_delta_mph == pytest.approx(12.0)

    def test_missing_club_transition_falls_back_to_sound_trigger(
        self,
        processor,
        capture,
    ):
        """Without a club-like frame before first ball, sound trigger wins."""
        readings = [
            SpeedReading(
                speed_mph=80.0,
                magnitude=300.0,
                timestamp_ms=11.0,
                direction="outbound",
            ),
        ]
        timeline = SpeedTimeline(readings=readings, sample_rate_hz=937.5)

        impact = processor.estimate_impact(
            timeline,
            ball_speed_mph=80.0,
            club_speed_mph=None,
            capture=capture,
        )

        assert impact.source == "sound_trigger"
        assert impact.reason == "no_club_transition_candidate"
        assert impact.timestamp_ms == pytest.approx(capture.trigger_offset_ms)


# =============================================================================
# Tests for Multi-Peak Integration (end-to-end)
# =============================================================================


class TestMultiPeakIntegration:
    """End-to-end test: process_capture with synthetic club+ball I/Q."""

    @pytest.fixture
    def processor(self):
        return RollingBufferProcessor()

    def test_process_capture_finds_club_and_ball(self, processor):
        """process_capture should find both club and ball from dual-tone I/Q."""
        import numpy as np

        n_samples = 4096
        t = np.arange(n_samples) / processor.SAMPLE_RATE

        # Club at ~60 mph (outbound), Ball at ~80 mph (outbound)
        club_freq = (60 / processor.MPS_TO_MPH) * 2 / processor.WAVELENGTH_M
        ball_freq = (80 / processor.MPS_TO_MPH) * 2 / processor.WAVELENGTH_M

        i_signal = 400 * np.cos(2 * np.pi * club_freq * t) + 300 * np.cos(2 * np.pi * ball_freq * t)
        q_signal = 400 * np.sin(2 * np.pi * club_freq * t) + 300 * np.sin(2 * np.pi * ball_freq * t)

        i_samples = (i_signal + 2048).astype(int).tolist()
        q_samples = (q_signal + 2048).astype(int).tolist()

        capture = IQCapture(
            sample_time=0.0,
            trigger_time=0.0,
            i_samples=i_samples,
            q_samples=q_samples,
            timestamp=1234567890.0,
        )

        result = processor.process_capture(capture)

        assert result is not None
        assert abs(result.ball_speed_mph - 80) < 5, (
            f"Ball speed {result.ball_speed_mph} not near 80 mph"
        )
        assert result.club_speed_mph is not None, "Club speed not detected"
        assert abs(result.club_speed_mph - 60) < 5, (
            f"Club speed {result.club_speed_mph} not near 60 mph"
        )


class TestSpinDetectionIntegration:
    """End-to-end spin detection tests using synthetic I/Q seam modulation."""

    def _make_iq_with_seam_modulation(
        self,
        base_speed_mph: float,
        spin_rpm: float,
        modulation_depth: float = 0.03,
        sample_rate: int = 30000,
        num_samples: int = 4096,
    ):
        """Generate synthetic I/Q with amplitude modulation at 1x spin rate.

        The golf ball seam is a single great circle that crosses the radar beam
        once per revolution, creating amplitude modulation at the spin frequency.
        """
        wavelength = 0.01243
        speed_mps = base_speed_mph / 2.23694
        doppler_hz = 2 * speed_mps / wavelength
        seam_hz = spin_rpm / 60.0

        t = np.arange(num_samples) / sample_rate
        phase = 2 * np.pi * doppler_hz * t

        amplitude = 200 * (1.0 + modulation_depth * np.sin(2 * np.pi * seam_hz * t))

        i_samples = (amplitude * np.cos(phase) + 2048).astype(int).clip(0, 4095).tolist()
        q_samples = (amplitude * np.sin(phase) + 2048).astype(int).clip(0, 4095).tolist()

        return i_samples, q_samples

    def test_spin_detected_7iron(self):
        """7-iron at 7000 RPM should be reliably detected."""
        i_samples, q_samples = self._make_iq_with_seam_modulation(
            base_speed_mph=120,
            spin_rpm=7000,
            modulation_depth=0.03,
        )
        capture = IQCapture(
            sample_time=0.0,
            trigger_time=0.068,
            i_samples=i_samples,
            q_samples=q_samples,
        )
        processor = RollingBufferProcessor()
        result = processor.process_capture(capture)
        assert result is not None
        assert result.spin is not None
        assert result.spin.spin_rpm > 0, f"Should detect spin, got quality={result.spin.quality}"
        assert abs(result.spin.spin_rpm - 7000) < 500, f"Expected ~7000, got {result.spin.spin_rpm}"

    def test_spin_detected_driver(self):
        """Driver at 3000 RPM should still be detectable.

        At 160 mph the ball's Doppler is near the top of the FFT window, so
        process_capture finds the ball late in the synthetic capture. We call
        detect_spin directly with an explicit ball timestamp to exercise
        the spin algorithm independent of the speed timeline.
        """
        i_samples, q_samples = self._make_iq_with_seam_modulation(
            base_speed_mph=160,
            spin_rpm=3000,
            modulation_depth=0.03,
        )
        capture = IQCapture(
            sample_time=0.0,
            trigger_time=0.068,
            i_samples=i_samples,
            q_samples=q_samples,
        )
        processor = RollingBufferProcessor()
        result = processor.detect_spin(capture, ball_speed_mph=160, ball_timestamp_ms=5.0)
        assert result.spin_rpm > 0, f"Should detect spin, got quality={result.quality}"
        assert abs(result.spin_rpm - 3000) < 500, f"Expected ~3000, got {result.spin_rpm}"

    def test_spin_detected_wedge(self):
        """Wedge at 10000 RPM."""
        i_samples, q_samples = self._make_iq_with_seam_modulation(
            base_speed_mph=90,
            spin_rpm=10000,
            modulation_depth=0.05,
        )
        capture = IQCapture(
            sample_time=0.0,
            trigger_time=0.068,
            i_samples=i_samples,
            q_samples=q_samples,
        )
        processor = RollingBufferProcessor()
        result = processor.process_capture(capture)
        assert result is not None
        assert result.spin is not None
        assert result.spin.spin_rpm > 0
        assert abs(result.spin.spin_rpm - 10000) < 500

    def test_no_spin_with_constant_amplitude(self):
        """Constant amplitude should yield no spin."""
        sample_rate = 30000
        num_samples = 4096
        speed_mph = 150
        wavelength = 0.01243
        speed_mps = speed_mph / 2.23694
        freq = 2 * speed_mps / wavelength

        t = np.arange(num_samples) / sample_rate
        phase = 2 * np.pi * freq * t

        i_samples = (200 * np.cos(phase) + 2048).astype(int).clip(0, 4095).tolist()
        q_samples = (200 * np.sin(phase) + 2048).astype(int).clip(0, 4095).tolist()

        capture = IQCapture(
            sample_time=0.0,
            trigger_time=0.068,
            i_samples=i_samples,
            q_samples=q_samples,
        )
        processor = RollingBufferProcessor()
        result = processor.process_capture(capture)
        assert result is not None
        assert result.spin is not None
        assert result.spin.spin_rpm == 0 or result.spin.quality not in ("high", "medium"), (
            f"Unexpected spin: {result.spin.spin_rpm} RPM, quality={result.spin.quality}"
        )

    def test_spin_result_is_populated(self):
        """process_capture should always populate the spin field."""
        i_samples, q_samples = self._make_iq_with_seam_modulation(
            base_speed_mph=130,
            spin_rpm=5000,
        )
        capture = IQCapture(
            sample_time=0.0,
            trigger_time=0.068,
            i_samples=i_samples,
            q_samples=q_samples,
        )
        processor = RollingBufferProcessor()
        result = processor.process_capture(capture)
        assert result is not None
        assert result.spin is not None

    def test_marginal_snr_preserves_fft_pick(self):
        """Marginal-SNR captures must keep the FFT peak, not flip to upper rail.

        Regression test: a previous autocorrelation override (corr >= 0.4)
        would replace the FFT peak with the autocorr-derived frequency
        when they disagreed. Because the autocorr search region's peak
        commonly lands at minimum lag (~12000 RPM, the upper rail), real
        mid-range seam tones at marginal SNR were being flipped to ~12000
        RPM and then rejected as "upper-rail noise". This test pins the
        post-fix behavior: the FFT pick survives autocorr disagreement.
        """
        rng = np.random.default_rng(seed=42)
        # Build a clean seam-modulated capture at 5400 RPM (90 Hz),
        # then add white noise so envelope SNR drops into the marginal
        # band where the autocorrelation fallback used to run.
        i_clean, q_clean = self._make_iq_with_seam_modulation(
            base_speed_mph=120,
            spin_rpm=5400,
            modulation_depth=0.02,
        )
        noise_amp = 35
        i_samples = [int(np.clip(v + rng.normal(0, noise_amp), 0, 4095)) for v in i_clean]
        q_samples = [int(np.clip(v + rng.normal(0, noise_amp), 0, 4095)) for v in q_clean]
        capture = IQCapture(
            sample_time=0.0,
            trigger_time=0.068,
            i_samples=i_samples,
            q_samples=q_samples,
        )
        processor = RollingBufferProcessor()
        result = processor.detect_spin(
            capture,
            ball_speed_mph=120,
            ball_timestamp_ms=5.0,
        )
        if result.spin_rpm > 0:
            assert abs(result.spin_rpm - 5400) < 600, (
                f"FFT pick should win; got {result.spin_rpm} RPM (SNR={result.snr})"
            )
            assert result.spin_rpm < 10000, (
                f"Result must not be flipped to the upper rail (got {result.spin_rpm} RPM)"
            )


# =============================================================================
# Tests for Spin Validation Gates
# =============================================================================


class TestSpinValidationGates:
    """Tests for spin detection validation: RPM ceiling, confidence tiers, modulation floor."""

    def _make_iq_with_seam_modulation(
        self,
        base_speed_mph: float,
        spin_rpm: float,
        modulation_depth: float = 0.03,
        sample_rate: int = 30000,
        num_samples: int = 4096,
    ):
        """Generate synthetic I/Q with amplitude modulation at 1x spin rate."""
        wavelength = 0.01243
        speed_mps = base_speed_mph / 2.23694
        doppler_hz = 2 * speed_mps / wavelength
        seam_hz = spin_rpm / 60.0
        t = np.arange(num_samples) / sample_rate
        phase = 2 * np.pi * doppler_hz * t
        amplitude = 200 * (1.0 + modulation_depth * np.sin(2 * np.pi * seam_hz * t))
        i_samples = (amplitude * np.cos(phase) + 2048).astype(int).clip(0, 4095).tolist()
        q_samples = (amplitude * np.sin(phase) + 2048).astype(int).clip(0, 4095).tolist()
        return i_samples, q_samples

    def test_rpm_above_12000_rejected(self):
        """Spin above SPIN_MAX_SEAM_HZ * 60 (12000 RPM) must be rejected.

        Uses 18000 RPM (300 Hz) — far enough above the 200 Hz cap that
        spectral leakage from the Hann window can't produce a strong
        artifact inside the valid [33, 200] Hz range.
        """
        processor = RollingBufferProcessor()
        i_samples, q_samples = self._make_iq_with_seam_modulation(
            base_speed_mph=100,
            spin_rpm=18000,
            modulation_depth=0.05,
        )
        capture = IQCapture(
            sample_time=0.0,
            trigger_time=0.068,
            i_samples=i_samples,
            q_samples=q_samples,
        )
        result = processor.detect_spin(capture, ball_speed_mph=100, ball_timestamp_ms=5.0)
        assert result.spin_rpm == 0, f"18000 RPM should be rejected, got {result.spin_rpm} RPM"

    def test_medium_snr_low_cycles_gets_reduced_confidence(self):
        """SNR >= 5 but < 3 seam cycles should score 0.5, not 0.7."""
        processor = RollingBufferProcessor()
        i_samples, q_samples = self._make_iq_with_seam_modulation(
            base_speed_mph=130,
            spin_rpm=4000,
            modulation_depth=0.04,
            num_samples=700,
        )
        capture = IQCapture(
            sample_time=0.0,
            trigger_time=0.003,
            i_samples=i_samples,
            q_samples=q_samples,
        )
        result = processor.detect_spin(capture, ball_speed_mph=130, ball_timestamp_ms=1.0)
        if result.spin_rpm > 0:
            assert result.confidence <= 0.5, (
                f"Low-cycle detection should cap at 0.5, got {result.confidence}"
            )

    def test_weak_modulation_caps_confidence(self):
        """Modulation depth < 1% should cap confidence at 0.5 max."""
        processor = RollingBufferProcessor()
        i_samples, q_samples = self._make_iq_with_seam_modulation(
            base_speed_mph=130,
            spin_rpm=5000,
            modulation_depth=0.008,
        )
        capture = IQCapture(
            sample_time=0.0,
            trigger_time=0.068,
            i_samples=i_samples,
            q_samples=q_samples,
        )
        result = processor.detect_spin(capture, ball_speed_mph=130, ball_timestamp_ms=5.0)
        if result.spin_rpm > 0:
            assert result.confidence <= 0.5, (
                f"Weak modulation should cap at 0.5, got {result.confidence}"
            )

    def test_strong_spin_still_scores_high(self):
        """Clean spin with good SNR and many cycles should still score >= 0.8."""
        processor = RollingBufferProcessor()
        i_samples, q_samples = self._make_iq_with_seam_modulation(
            base_speed_mph=120,
            spin_rpm=7000,
            modulation_depth=0.03,
        )
        capture = IQCapture(
            sample_time=0.0,
            trigger_time=0.068,
            i_samples=i_samples,
            q_samples=q_samples,
        )
        result = processor.detect_spin(capture, ball_speed_mph=120, ball_timestamp_ms=5.0)
        assert result.spin_rpm > 0, f"Should detect spin, got quality={result.quality}"
        assert result.confidence >= 0.8, f"Strong spin should score >= 0.8, got {result.confidence}"


# =============================================================================
# Regression tests for "rail" failure modes observed on real driver captures
# (session_20260501_180406_range.jsonl): all 9 shots returned exactly 2637 RPM
# (lowest selectable bin, dominated by envelope-DC leakage) or ~12000 RPM
# (top of seam-frequency band, dominated by bandpass-shoulder noise) despite
# the user striking 7-irons (true spin ~5000-8000 RPM).
# =============================================================================


class TestSpinRailRejection:
    """Spin detection must reject peaks at the boundaries of the seam
    search range when the underlying signal is just envelope-DC leakage
    or bandpass-shoulder noise.
    """

    def _toneless_envelope_iq(
        self,
        base_speed_mph: float = 100.0,
        envelope_depth: float = 0.015,
        sample_rate: int = 30000,
        num_samples: int = 4096,
        seed: int = 0,
    ):
        """I/Q with a Doppler carrier whose amplitude wanders at very low
        frequencies (1-20 Hz) — no narrowband seam tone in the
        [33, 200] Hz search band.

        This reproduces the real-capture failure mode where envelope-DC
        leakage produces rail-low picks (~2637 RPM) and bandpass-shoulder
        noise produces rail-high picks (~12000 RPM). The envelope depth
        is large enough to clear the 0.5% modulation-floor gate but the
        spectral content is entirely outside the seam search band.
        """
        wavelength = 0.01243
        speed_mps = base_speed_mph / 2.23694
        doppler_hz = 2 * speed_mps / wavelength
        t = np.arange(num_samples) / sample_rate
        phase = 2 * np.pi * doppler_hz * t

        rng = np.random.default_rng(seed)
        # Heavily lowpassed Gaussian — energy concentrated below 50 Hz.
        env_noise = rng.standard_normal(num_samples)
        env_noise = np.convolve(env_noise, np.ones(300) / 300, mode="same")
        env_noise = env_noise / (np.std(env_noise) + 1e-9)
        amplitude = 200 * (1.0 + envelope_depth * env_noise)

        i = (amplitude * np.cos(phase) + 2048).astype(int).clip(0, 4095).tolist()
        q = (amplitude * np.sin(phase) + 2048).astype(int).clip(0, 4095).tolist()
        return i, q

    def _amplitude_modulated_iq(
        self,
        base_speed_mph: float,
        mod_freq_hz: float,
        modulation_depth: float = 0.05,
        sample_rate: int = 30000,
        num_samples: int = 4096,
    ):
        """Helper that lets us drive amplitude modulation at any frequency
        (including outside the seam search band).
        """
        wavelength = 0.01243
        speed_mps = base_speed_mph / 2.23694
        doppler_hz = 2 * speed_mps / wavelength
        t = np.arange(num_samples) / sample_rate
        phase = 2 * np.pi * doppler_hz * t
        amplitude = 200 * (1.0 + modulation_depth * np.sin(2 * np.pi * mod_freq_hz * t))
        i = (amplitude * np.cos(phase) + 2048).astype(int).clip(0, 4095).tolist()
        q = (amplitude * np.sin(phase) + 2048).astype(int).clip(0, 4095).tolist()
        return i, q

    def test_lower_rail_peak_rejected_with_toneless_envelope(self):
        """A toneless envelope must not be reported at rail-low values
        like 2637 / 2856 / 3076 RPM — the failure mode seen on every
        driver shot in the real capture.
        """
        processor = RollingBufferProcessor()
        accepted = []
        for seed in range(15):
            i, q = self._toneless_envelope_iq(
                base_speed_mph=100.0,
                envelope_depth=0.015,
                seed=seed,
            )
            capture = IQCapture(
                sample_time=0.0,
                trigger_time=0.068,
                i_samples=i,
                q_samples=q,
            )
            result = processor.detect_spin(
                capture,
                ball_speed_mph=100.0,
                ball_timestamp_ms=5.0,
            )
            if result.spin_rpm > 0:
                accepted.append((seed, result))
        # With the envelope detrend, 2000-3100 RPM is measurable for real
        # tones — but toneless noise must never produce a *reliable*
        # result there. Rail-zone picks additionally require medium SNR,
        # which envelope noise does not reach.
        for seed, r in accepted:
            if r.spin_rpm <= 3100:
                assert not r.is_reliable and r.confidence <= 0.5, (
                    f"seed={seed}: noise accepted as reliable low spin, got "
                    f"{r.spin_rpm} RPM (quality={r.quality}, snr={r.snr}, "
                    f"confidence={r.confidence})"
                )

    def test_upper_rail_peak_rejected_with_toneless_envelope(self):
        """A toneless envelope must not be reported as ~12000 RPM
        (rail-high, bandpass-shoulder bin).
        """
        processor = RollingBufferProcessor()
        accepted = []
        for seed in range(15):
            i, q = self._toneless_envelope_iq(
                base_speed_mph=105.0,
                envelope_depth=0.015,
                seed=seed + 100,
            )
            capture = IQCapture(
                sample_time=0.0,
                trigger_time=0.068,
                i_samples=i,
                q_samples=q,
            )
            result = processor.detect_spin(
                capture,
                ball_speed_mph=105.0,
                ball_timestamp_ms=5.0,
            )
            if result.spin_rpm > 0:
                accepted.append((seed, result))
        for seed, r in accepted:
            assert r.spin_rpm < 11500, (
                f"seed={seed}: upper rail not rejected, got "
                f"{r.spin_rpm} RPM (quality={r.quality}, snr={r.snr})"
            )

    def test_rail_flags_set_when_peak_near_boundary(self):
        """SpinResult must expose `at_lower_rail` and `at_upper_rail`
        flags so we can diagnose rail-hit failures from the JSONL.
        """
        processor = RollingBufferProcessor()
        i, q = self._toneless_envelope_iq(
            base_speed_mph=100.0,
            envelope_depth=0.015,
            seed=1,
        )
        capture = IQCapture(
            sample_time=0.0,
            trigger_time=0.068,
            i_samples=i,
            q_samples=q,
        )
        result = processor.detect_spin(
            capture,
            ball_speed_mph=100.0,
            ball_timestamp_ms=5.0,
        )
        assert hasattr(result, "at_lower_rail"), "SpinResult must expose at_lower_rail"
        assert hasattr(result, "at_upper_rail"), "SpinResult must expose at_upper_rail"

    def test_modulation_depth_exposed_in_result(self):
        """SpinResult must report `modulation_depth` so the JSONL
        captures the envelope quality of every detection attempt.
        """
        processor = RollingBufferProcessor()
        i, q = self._amplitude_modulated_iq(
            base_speed_mph=120.0,
            mod_freq_hz=100.0,
            modulation_depth=0.04,
        )
        capture = IQCapture(
            sample_time=0.0,
            trigger_time=0.068,
            i_samples=i,
            q_samples=q,
        )
        result = processor.detect_spin(
            capture,
            ball_speed_mph=120.0,
            ball_timestamp_ms=5.0,
        )
        assert hasattr(result, "modulation_depth"), "SpinResult must expose modulation_depth"
        assert result.modulation_depth is not None
        assert result.modulation_depth > 0.005, (
            f"modulation_depth should reflect envelope variation, got {result.modulation_depth}"
        )

    def test_real_seam_modulation_still_passes(self):
        """A clean 7-iron-class detection must still pass after the rail
        guards are added.
        """
        processor = RollingBufferProcessor()
        # 7-iron territory: 6500 RPM = 108 Hz seam frequency, comfortably
        # interior to the [33, 200] Hz search band.
        i, q = self._amplitude_modulated_iq(
            base_speed_mph=125.0,
            mod_freq_hz=6500.0 / 60.0,
            modulation_depth=0.03,
        )
        capture = IQCapture(
            sample_time=0.0,
            trigger_time=0.068,
            i_samples=i,
            q_samples=q,
        )
        result = processor.detect_spin(
            capture,
            ball_speed_mph=125.0,
            ball_timestamp_ms=5.0,
        )
        assert result.spin_rpm > 0, f"Clean 7-iron detection regressed: quality={result.quality}"
        assert abs(result.spin_rpm - 6500) < 500, f"Expected ~6500 RPM, got {result.spin_rpm}"
        assert result.at_lower_rail is False, "Interior peak should not flag at_lower_rail"
        assert result.at_upper_rail is False, "Interior peak should not flag at_upper_rail"

    def test_lower_rail_candidate_confidence_is_capped(self):
        """Lower-rail candidates may be visible but must not be reliable.

        The rail zone is the lowest 3 live bins of the seam range
        (~33-44 Hz, <=~2640 RPM) now that the detrend replaced the wide
        leakage guard; 2300 RPM sits inside it.
        """
        processor = RollingBufferProcessor()
        i, q = self._amplitude_modulated_iq(
            base_speed_mph=160.0,
            mod_freq_hz=2300.0 / 60.0,
            modulation_depth=0.03,
        )
        capture = IQCapture(
            sample_time=0.0,
            trigger_time=0.068,
            i_samples=i,
            q_samples=q,
        )

        result = processor.detect_spin(
            capture,
            ball_speed_mph=160.0,
            ball_timestamp_ms=5.0,
        )

        assert result.spin_rpm > 0
        assert abs(result.spin_rpm - 2300) < 250
        assert result.at_lower_rail is True
        assert result.confidence <= 0.5
        assert result.is_reliable is False

    def test_spin_prior_prefers_plausible_non_rail_peak(self):
        """Expected spin should break ties away from lower-rail artifacts."""
        processor = RollingBufferProcessor()
        valid_freqs = np.array([33.0, 36.0, 40.0, 55.0, 92.0, 108.0, 150.0, 180.0])
        valid_mag = np.array([0.0, 0.0, 100.0, 18.0, 45.0, 28.0, 10.0, 8.0])

        selected = processor._select_spin_peak(
            valid_mag,
            valid_freqs,
            leakage_bins=2,
            expected_spin_rpm=5600.0,
        )

        assert valid_freqs[selected] * 60 == pytest.approx(5520.0)

    def test_spin_prior_ignores_weak_plausible_peak(self):
        """A plausible prior match still needs enough spectral support."""
        processor = RollingBufferProcessor()
        valid_freqs = np.array([33.0, 36.0, 40.0, 55.0, 92.0, 108.0])
        valid_mag = np.array([0.0, 0.0, 100.0, 18.0, 35.0, 28.0])

        selected = processor._select_spin_peak(
            valid_mag,
            valid_freqs,
            leakage_bins=2,
            expected_spin_rpm=5600.0,
        )

        assert valid_freqs[selected] * 60 == pytest.approx(2400.0)

    def test_high_spin_prior_recovers_supported_peak_from_implausible_lower_rail(self):
        """Short irons should not let a bad lower-rail peak hide supported spin."""
        processor = RollingBufferProcessor()
        valid_freqs = np.array([33.0, 36.0, 40.0, 55.0, 120.0, 135.0, 150.0, 180.0])
        valid_mag = np.array([0.0, 0.0, 100.0, 18.0, 25.0, 8.0, 7.0, 5.0])

        selected = processor._select_spin_peak(
            valid_mag,
            valid_freqs,
            leakage_bins=2,
            expected_spin_rpm=7200.0,
        )

        assert valid_freqs[selected] * 60 == pytest.approx(7200.0)

    def test_high_spin_prior_keeps_lower_rail_when_alternative_is_too_weak(self):
        """Rail recovery still needs visible spectral support."""
        processor = RollingBufferProcessor()
        valid_freqs = np.array([33.0, 36.0, 40.0, 55.0, 120.0, 135.0, 150.0, 180.0])
        valid_mag = np.array([0.0, 0.0, 100.0, 18.0, 12.0, 8.0, 7.0, 5.0])

        selected = processor._select_spin_peak(
            valid_mag,
            valid_freqs,
            leakage_bins=2,
            expected_spin_rpm=7200.0,
        )

        assert valid_freqs[selected] * 60 == pytest.approx(2400.0)

    def test_spin_prior_keeps_strongest_without_expected_spin(self):
        """No prior means the detector preserves historical argmax behavior."""
        processor = RollingBufferProcessor()
        valid_freqs = np.array([33.0, 36.0, 40.0, 92.0])
        valid_mag = np.array([0.0, 0.0, 100.0, 35.0])

        selected = processor._select_spin_peak(
            valid_mag,
            valid_freqs,
            leakage_bins=2,
            expected_spin_rpm=None,
        )

        assert valid_freqs[selected] * 60 == pytest.approx(2400.0)

    def test_spin_candidates_include_selected_prior_peak(self):
        """Diagnostics should preserve the selected candidate even if not strongest."""
        processor = RollingBufferProcessor()
        valid_freqs = np.array([33.0, 36.0, 40.0, 55.0, 92.0, 108.0])
        valid_mag = np.array([0.0, 0.0, 100.0, 18.0, 45.0, 28.0])

        candidates = processor._build_spin_candidates(
            valid_mag,
            valid_freqs,
            leakage_bins=2,
            noise_floor=10.0,
            expected_spin_rpm=5600.0,
            selected_idx=4,
        )

        selected = [candidate for candidate in candidates if candidate.selected]
        assert len(selected) == 1
        assert selected[0].rpm == pytest.approx(5520.0)
        assert selected[0].expected_spin_error_pct == pytest.approx(1.4285714)
        assert candidates[0].relative_magnitude == pytest.approx(1.0)

    def test_phase_confirmation_accepts_matching_witness(self):
        """Phase is allowed to confirm a low-SNR envelope candidate."""
        processor = RollingBufferProcessor()
        samples = 2400
        envelope_spin_rpm = 7000.0
        seam_hz = envelope_spin_rpm / 60.0
        t = np.arange(samples) / processor.SAMPLE_RATE
        phase = 0.4 * np.sin(2 * np.pi * seam_hz * t)
        filtered_iq = np.exp(1j * phase)

        witness = processor._phase_spin_confirmation(
            filtered_iq,
            envelope_spin_rpm=envelope_spin_rpm,
            expected_spin_rpm=7000.0,
        )

        assert witness is not None
        assert witness["confirmed"] is True
        # Either phase witness may win the ranking; both are valid confirmations.
        assert witness["method"] in ("phase_residual", "instant_frequency")
        assert witness["rpm"] == pytest.approx(7031.25)
        assert witness["snr"] >= processor.SPIN_PHASE_SNR_MIN
        assert witness["agreement_pct"] <= processor.SPIN_PHASE_AGREEMENT_PCT

    def test_phase_confirmation_rejects_disagreeing_witness(self):
        """A strong phase candidate should not confirm if it disagrees."""
        processor = RollingBufferProcessor()
        samples = 2400
        phase_spin_rpm = 11000.0
        t = np.arange(samples) / processor.SAMPLE_RATE
        phase = 0.4 * np.sin(2 * np.pi * (phase_spin_rpm / 60.0) * t)
        filtered_iq = np.exp(1j * phase)

        witness = processor._phase_spin_confirmation(
            filtered_iq,
            envelope_spin_rpm=7000.0,
            expected_spin_rpm=7000.0,
        )

        assert witness is not None
        assert witness["confirmed"] is False
        assert witness["agreement_pct"] > processor.SPIN_PHASE_AGREEMENT_PCT


# =============================================================================
# Regression test for shutdown → restart sound-trigger failure.
#
# Symptom (reported on real hardware): clicking "Shutdown" in the UI and then
# re-running scripts/start-kiosk.sh leaves the OPS243-A unable to fire the
# HOST_INT sound trigger on subsequent shots.
#
# Root cause: monitor.disconnect() previously sent "GS" (return-to-CW)
# to the radar before the Python process exited. The OPS243-A firmware has
# a documented bug where the HOST_INT pin mode switches unexpectedly when
# transitioning between modes at runtime (see ops243.py:743 docstring and
# CLAUDE.md "Radar Setup"). The project's whole approach is to keep the
# radar in persistent rolling-buffer mode at all times — sending GS on
# shutdown breaks that and the next startup hits the buggy GS→GC runtime
# transition, so HOST_INT never fires.
#
# Fix: leave the radar in rolling-buffer mode on disconnect.
# =============================================================================


class TestShutdownPreservesRollingBuffer:
    """The shutdown / disconnect path must NOT take the OPS243-A out of
    rolling-buffer mode. Doing so triggers a documented HOST_INT firmware
    bug that requires a power cycle to recover from.
    """

    def _make_monitor_with_mock_radar(self):
        """Build a RollingBufferMonitor whose radar is fully mocked, so
        disconnect() can be exercised without serial hardware.
        """
        from openflight.rolling_buffer import RollingBufferMonitor

        monitor = RollingBufferMonitor(port=None, trigger_type="sound")
        monitor.radar = MagicMock()
        return monitor

    def test_disconnect_does_not_send_GS(self):
        """The shutdown path must not call disable_rolling_buffer (which
        sends 'GS' to the OPS243-A and triggers the HOST_INT firmware
        bug on the next runtime GS→GC transition).
        """
        monitor = self._make_monitor_with_mock_radar()
        monitor.disconnect()
        assert not monitor.radar.disable_rolling_buffer.called, (
            "disconnect() must not call radar.disable_rolling_buffer() — "
            "doing so leaves the OPS243-A in CW mode and the next "
            "start-kiosk.sh hits the HOST_INT firmware bug"
        )

    def test_disconnect_still_closes_serial(self):
        """disconnect() must still close the serial connection so the
        port is freed for the next process.
        """
        monitor = self._make_monitor_with_mock_radar()
        monitor.disconnect()
        assert monitor.radar.disconnect.called, (
            "disconnect() must close the serial port (radar.disconnect)"
        )

    def test_disconnect_stops_capture_thread(self):
        """disconnect() must stop the capture thread before closing the
        serial port.
        """
        monitor = self._make_monitor_with_mock_radar()
        # Mark the monitor as running so .stop() actually does work.
        monitor._running = True
        monitor.disconnect()
        assert monitor._running is False, (
            "disconnect() must call stop() so the capture thread shuts down"
        )

    def test_processing_failure_preserves_accepted_trigger_metadata(self, monkeypatch):
        """UI feedback must cover capture and FFT work, then clear on failure."""
        from openflight.rolling_buffer import RollingBufferMonitor, monitor as monitor_module

        monitor = RollingBufferMonitor(port=None, trigger_type="sound")
        capture = IQCapture(
            sample_time=0.0,
            trigger_time=0.068,
            i_samples=[2048] * 16,
            q_samples=[2048] * 16,
        )
        states = []

        class OneCaptureTrigger:
            calls = 0

            def wait_for_trigger(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    kwargs["capture_started_callback"]()
                    return capture
                monitor._running = False
                return None

            @staticmethod
            def drain_diagnostics():
                return [
                    {
                        "accepted": True,
                        "reason": "accepted",
                        "response_bytes": 32768,
                        "total_readings": 12,
                        "outbound_readings": 8,
                        "inbound_readings": 4,
                        "peak_outbound_mph": 150.0,
                        "peak_inbound_mph": 40.0,
                        "all_outbound_speeds": [150.0],
                        "all_inbound_speeds": [40.0],
                    }
                ]

            @staticmethod
            def reset():
                return None

        class FailingProcessor:
            @staticmethod
            def process_capture(*_args, **_kwargs):
                assert states == ["capturing", "calculating"]
                return None

        monitor.trigger = OneCaptureTrigger()
        monitor.processor = FailingProcessor()
        diagnostics = []

        def failing_diagnostic(event):
            diagnostics.append(event)
            raise RuntimeError("UI disconnected")

        monitor._diagnostic_callback = failing_diagnostic
        monitor._processing_callback = states.append
        monitor._running = True
        session_logger = MagicMock()
        session_logger.log_trigger_event.side_effect = RuntimeError("disk full")
        monkeypatch.setattr(monitor_module, "get_session_logger", lambda: session_logger)

        monitor._capture_loop()

        assert states == ["capturing", "calculating", "failed"]
        event = session_logger.log_trigger_event.call_args.kwargs
        assert event["accepted"] is False
        assert event["reason"] == "processing_failed"
        assert event["response_bytes"] == 32768
        assert event["total_readings"] == 12
        assert session_logger.log_trigger_event.call_count == 1
        assert len(diagnostics) == 1

    def test_processing_exception_records_the_physical_trigger(self, monkeypatch):
        from openflight.rolling_buffer import RollingBufferMonitor, monitor as monitor_module

        monitor = RollingBufferMonitor(port=None, trigger_type="sound")
        capture = IQCapture(
            sample_time=0.0,
            trigger_time=0.068,
            i_samples=[2048] * 16,
            q_samples=[2048] * 16,
        )

        class OneCaptureTrigger:
            calls = 0

            def wait_for_trigger(self, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    return capture
                monitor._running = False
                return None

            @staticmethod
            def drain_diagnostics():
                return [{"accepted": True, "reason": "accepted", "response_bytes": 4096}]

            @staticmethod
            def reset():
                return None

        class ExplodingProcessor:
            @staticmethod
            def process_capture(*_args, **_kwargs):
                raise RuntimeError("FFT failed")

        session_logger = MagicMock()
        monkeypatch.setattr(monitor_module, "get_session_logger", lambda: session_logger)
        monkeypatch.setattr(monitor_module.time, "sleep", lambda _delay: None)
        monitor.trigger = OneCaptureTrigger()
        monitor.processor = ExplodingProcessor()
        monitor._diagnostic_callback = None
        monitor._running = True

        monitor._capture_loop()

        event = session_logger.log_trigger_event.call_args.kwargs
        assert event["accepted"] is False
        assert event["reason"] == "processing_error"
        assert event["response_bytes"] == 4096

    @pytest.mark.parametrize("reason", ["parse_failed", "no_outbound_speed"])
    def test_rejected_started_capture_clears_processing_feedback(self, reason):
        """A dump rejected before FFT must not leave capture feedback stale."""
        from openflight.rolling_buffer import RollingBufferMonitor

        monitor = RollingBufferMonitor(port=None, trigger_type="sound")
        states = []

        class RejectedCaptureTrigger:
            calls = 0

            def wait_for_trigger(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    kwargs["capture_started_callback"]()
                    return None
                monitor._running = False
                return None

            @staticmethod
            def drain_diagnostics():
                return [{"accepted": False, "reason": reason}]

            @staticmethod
            def reset():
                return None

        monitor.trigger = RejectedCaptureTrigger()
        monitor._diagnostic_callback = None
        monitor._processing_callback = states.append
        monitor._running = True

        monitor._capture_loop()

        assert states == ["capturing", "failed"]

    def test_idle_sound_timeout_does_not_emit_processing_failure(self):
        """No feedback should appear or clear when no hardware dump began."""
        from openflight.rolling_buffer import RollingBufferMonitor

        monitor = RollingBufferMonitor(port=None, trigger_type="sound")
        states = []

        class IdleTrigger:
            calls = 0

            def wait_for_trigger(self, **_kwargs):
                self.calls += 1
                if self.calls > 1:
                    monitor._running = False
                return None

            @staticmethod
            def drain_diagnostics():
                return []

            @staticmethod
            def reset():
                return None

        monitor.trigger = IdleTrigger()
        monitor._diagnostic_callback = None
        monitor._processing_callback = states.append
        monitor._running = True

        monitor._capture_loop()

        assert states == []

    def test_stop_cancels_idle_sound_trigger_wait(self):
        """Default shutdown should wake the idle trigger thread immediately."""
        from openflight.rolling_buffer import RollingBufferMonitor

        waiting = threading.Event()

        class IdleRadar:
            def __init__(self):
                self.cancel_event = None

            def wait_for_hardware_trigger(
                self,
                timeout,
                cancel_event=None,
                on_first_byte=None,
            ):
                self.cancel_event = cancel_event
                waiting.set()
                if cancel_event is None:
                    time.sleep(0.25)
                else:
                    cancel_event.wait(timeout)
                return ""

        monitor = RollingBufferMonitor(port=None, trigger_type="sound")
        monitor.radar = IdleRadar()
        monitor.start()
        assert waiting.wait(timeout=1.0)

        started = time.monotonic()
        monitor.stop()

        assert monitor.radar.cancel_event is not None
        assert time.monotonic() - started < 0.2

    def test_stop_does_not_abandon_an_active_sound_capture(self):
        """A slow UART dump must finish before shutdown closes the serial port."""
        from openflight.rolling_buffer import RollingBufferMonitor

        class ActiveCaptureThread:
            def __init__(self):
                self.join_timeouts = []
                self.alive = True

            def join(self, timeout=None):
                self.join_timeouts.append(timeout)
                self.alive = False

            def is_alive(self):
                return self.alive

        monitor = RollingBufferMonitor(port=None, trigger_type="sound")
        active_thread = ActiveCaptureThread()
        monitor._capture_thread = active_thread
        monitor._running = True

        monitor.stop()

        assert active_thread.join_timeouts == [5.0]
        assert active_thread.is_alive() is False
        assert monitor._capture_thread is None


class TestRollingBufferStartupMode:
    """Startup should respect the persisted HOST_INT rolling-buffer workaround."""

    def test_sound_trigger_prepares_persisted_buffer_without_runtime_gc(self):
        from openflight.rolling_buffer import RollingBufferMonitor

        monitor = RollingBufferMonitor(port=None, trigger_type="sound", pre_trigger_segments=16)
        monitor.radar = MagicMock()

        assert monitor.connect() is True

        monitor.radar.connect.assert_called_once()
        monitor.radar.prepare_persisted_rolling_buffer.assert_called_once_with(
            pre_trigger_segments=16,
            sample_rate_ksps=30,
        )
        assert not monitor.radar.configure_for_rolling_buffer.called

    def test_speed_trigger_defers_runtime_configuration(self):
        from openflight.rolling_buffer import RollingBufferMonitor

        monitor = RollingBufferMonitor(port=None, trigger_type="speed", pre_trigger_segments=12)
        monitor.radar = MagicMock()

        assert monitor.connect() is True

        monitor.radar.connect.assert_called_once()
        assert not monitor.radar.configure_for_rolling_buffer.called
        assert not monitor.radar.prepare_persisted_rolling_buffer.called


class TestSpinWindowTrimming:
    """The spin window must end where ball signal ends (net impact), not at
    the end of the capture — the amplitude cliff plus dead air corrupts the
    envelope FFT."""

    def _make_capture_with_signal_loss(
        self,
        base_speed_mph=120,
        spin_rpm=6000,
        signal_end_ms=55.0,
        modulation_depth=0.03,
        sample_rate=30000,
        num_samples=4096,
    ):
        """Ball tone with seam modulation that dies mid-capture (net impact)."""
        rng = np.random.default_rng(7)
        wavelength = 0.01243
        doppler_hz = 2 * (base_speed_mph / 2.23694) / wavelength
        seam_hz = spin_rpm / 60.0

        t = np.arange(num_samples) / sample_rate
        phase = 2 * np.pi * doppler_hz * t
        amplitude = 200 * (1.0 + modulation_depth * np.sin(2 * np.pi * seam_hz * t))
        i_signal = amplitude * np.cos(phase)
        q_signal = amplitude * np.sin(phase)

        end_sample = min(int(signal_end_ms * sample_rate / 1000), num_samples)
        i_signal[end_sample:] = rng.normal(0, 2, num_samples - end_sample)
        q_signal[end_sample:] = rng.normal(0, 2, num_samples - end_sample)

        return IQCapture(
            sample_time=0.0,
            trigger_time=0.068,
            i_samples=(i_signal + 2048).astype(int).clip(0, 4095).tolist(),
            q_samples=(q_signal + 2048).astype(int).clip(0, 4095).tolist(),
        )

    def test_spin_correct_when_ball_signal_dies_mid_capture(self):
        capture = self._make_capture_with_signal_loss(spin_rpm=6000, signal_end_ms=55.0)
        processor = RollingBufferProcessor()
        result = processor.detect_spin(capture, ball_speed_mph=120, ball_timestamp_ms=5.0)
        assert result.spin_rpm > 0, f"Should detect spin, got: {result.rejection_reason}"
        assert abs(result.spin_rpm - 6000) < 300, (
            f"Expected ~6000 RPM, got {result.spin_rpm} (quality={result.quality})"
        )

    def test_full_length_signal_is_not_truncated(self):
        """A ball tone spanning the whole capture must keep its full window
        (seam_cycles reflects the full ~126 ms analysis window)."""
        capture = self._make_capture_with_signal_loss(spin_rpm=6000, signal_end_ms=10000.0)
        processor = RollingBufferProcessor()
        result = processor.detect_spin(capture, ball_speed_mph=120, ball_timestamp_ms=5.0)
        assert result.spin_rpm > 0
        assert abs(result.spin_rpm - 6000) < 300
        # 6000 RPM = 100 Hz; >= 11 cycles requires >= ~110 ms of window
        assert result.seam_cycles >= 11, (
            f"Window appears wrongly truncated: {result.seam_cycles} cycles"
        )


class TestSpinDriverDeadZone:
    """Typical driver backspin (2000-3000 RPM) must be measurable: the old
    DC-leakage guard zeroed 33-51.3 Hz (1980-3080 RPM) of the envelope FFT."""

    def _tone_capture(self, spin_rpm, modulation_depth=0.03, decay=None):
        sample_rate, num_samples = 30000, 4096
        wavelength = 0.01243
        doppler_hz = 2 * (160 / 2.23694) / wavelength
        t = np.arange(num_samples) / sample_rate
        phase = 2 * np.pi * doppler_hz * t
        amplitude = np.full(num_samples, 200.0)
        if spin_rpm:
            amplitude *= 1.0 + modulation_depth * np.sin(2 * np.pi * (spin_rpm / 60.0) * t)
        if decay is not None:
            amplitude *= np.linspace(1.0, decay, num_samples)
        return IQCapture(
            sample_time=0.0,
            trigger_time=0.068,
            i_samples=(amplitude * np.cos(phase) + 2048).astype(int).clip(0, 4095).tolist(),
            q_samples=(amplitude * np.sin(phase) + 2048).astype(int).clip(0, 4095).tolist(),
        )

    def test_low_driver_spin_measurable(self):
        """2400 RPM (40 Hz) sits inside the old zeroed band."""
        processor = RollingBufferProcessor()
        result = processor.detect_spin(
            self._tone_capture(2400), ball_speed_mph=160, ball_timestamp_ms=5.0
        )
        assert result.spin_rpm > 0, f"Should detect spin, got: {result.rejection_reason}"
        assert abs(result.spin_rpm - 2400) < 250, f"Expected ~2400 RPM, got {result.spin_rpm}"

    def test_range_falloff_decay_does_not_fake_driver_spin(self):
        """A smoothly decaying envelope with no seam modulation (ball flying
        away, no spin signal) must not produce a confident driver-band spin."""
        processor = RollingBufferProcessor()
        result = processor.detect_spin(
            self._tone_capture(spin_rpm=0, decay=0.4),
            ball_speed_mph=160,
            ball_timestamp_ms=5.0,
        )
        assert result.spin_rpm == 0 or result.quality == "low", (
            f"Decay ramp faked spin: {result.spin_rpm} RPM quality={result.quality}"
        )
