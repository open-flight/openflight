"""Tests for OPS243 radar driver."""

import inspect
import threading
import time

import pytest
import serial

from openflight.ops243 import Direction, OPS243Radar, SpeedReading


class TestParseReading:
    """Tests for radar reading parsing."""

    def setup_method(self):
        """Set up test radar instance."""
        self.radar = OPS243Radar.__new__(OPS243Radar)
        self.radar._json_mode = True
        self.radar._unit = "mph"
        self.radar._magnitude_enabled = True

    def test_parse_json_with_magnitude(self):
        """Parse JSON output with positive speed (inbound)."""
        # Positive speed = INBOUND (toward radar)
        line = '{"speed": 152.3, "magnitude": 1847}'
        reading = self.radar._parse_reading(line)

        assert reading is not None
        assert reading.speed == 152.3
        assert reading.magnitude == 1847
        assert reading.direction == Direction.INBOUND
        assert reading.unit == "mph"

    def test_parse_json_negative_speed(self):
        """Negative speed indicates outbound direction."""
        # Negative speed = OUTBOUND (away from radar - ball flight)
        line = '{"speed": -45.2, "magnitude": 500}'
        reading = self.radar._parse_reading(line)

        assert reading is not None
        assert reading.speed == 45.2  # Absolute value
        assert reading.direction == Direction.OUTBOUND

    def test_parse_json_without_magnitude(self):
        """Parse JSON without magnitude field."""
        line = '{"speed": 120.5}'
        reading = self.radar._parse_reading(line)

        assert reading is not None
        assert reading.speed == 120.5
        assert reading.magnitude is None

    def test_parse_plain_number(self):
        """Parse plain number output (non-JSON mode)."""
        # Positive speed = INBOUND (toward radar)
        self.radar._json_mode = False
        line = "145.7"
        reading = self.radar._parse_reading(line)

        assert reading is not None
        assert reading.speed == 145.7
        assert reading.direction == Direction.INBOUND

    def test_parse_plain_negative(self):
        """Parse plain negative number."""
        # Negative speed = OUTBOUND (away from radar - ball flight)
        self.radar._json_mode = False
        line = "-88.3"
        reading = self.radar._parse_reading(line)

        assert reading is not None
        assert reading.speed == 88.3
        assert reading.direction == Direction.OUTBOUND

    def test_parse_invalid_json(self):
        """Invalid JSON returns None."""
        line = '{"speed": invalid}'
        reading = self.radar._parse_reading(line)

        assert reading is None

    def test_parse_empty_line(self):
        """Empty line returns None."""
        reading = self.radar._parse_reading("")

        assert reading is None

    def test_parse_non_numeric(self):
        """Non-numeric line returns None."""
        self.radar._json_mode = False
        reading = self.radar._parse_reading("hello")

        assert reading is None

    def test_parse_json_zero_speed(self):
        """Zero speed should parse correctly."""
        line = '{"speed": 0, "magnitude": 100}'
        reading = self.radar._parse_reading(line)

        assert reading is not None
        assert reading.speed == 0

    def test_parse_json_high_speed(self):
        """Very high speeds should parse correctly."""
        line = '{"speed": 195.8, "magnitude": 2500}'
        reading = self.radar._parse_reading(line)

        assert reading is not None
        assert reading.speed == 195.8

    def test_parse_json_decimal_precision(self):
        """Decimal precision should be preserved."""
        line = '{"speed": 142.857, "magnitude": 1234}'
        reading = self.radar._parse_reading(line)

        assert reading is not None
        assert reading.speed == 142.857

    def test_parse_json_array_format(self):
        """Parse O4 multi-object array format."""
        # O4 mode outputs arrays for speed and magnitude
        line = '{"magnitude":[606.71, 352.58, 230.87], "speed":[-10.90, -12.26, -17.71]}'
        reading = self.radar._parse_reading(line)

        assert reading is not None
        # Should take first (strongest) reading
        assert reading.speed == 10.90  # Absolute value
        assert reading.magnitude == 606.71
        # Negative speed = OUTBOUND
        assert reading.direction == Direction.OUTBOUND

    def test_parse_json_array_inbound(self):
        """Parse array format with positive (inbound) speed."""
        line = '{"magnitude":[500.0, 300.0], "speed":[15.5, 12.3]}'
        reading = self.radar._parse_reading(line)

        assert reading is not None
        assert reading.speed == 15.5
        assert reading.magnitude == 500.0
        # Positive speed = INBOUND
        assert reading.direction == Direction.INBOUND


class _ChunkedSpeedSerial:
    """Serial stand-in that returns speed-stream chunks over multiple polls."""

    is_open = True

    def __init__(self, chunks):
        self._chunks = [chunk.encode("ascii") for chunk in chunks]

    @property
    def in_waiting(self):
        if not self._chunks:
            return 0
        return len(self._chunks[0])

    def read(self, _size):
        return self._chunks.pop(0)


class TestReadSpeedNonblocking:
    """Tests for non-blocking speed stream reads."""

    def test_buffers_partial_json_until_complete_line(self):
        """Split JSON lines should not be parsed until the newline arrives."""
        radar = OPS243Radar.__new__(OPS243Radar)
        radar.serial = _ChunkedSpeedSerial(
            [
                '{"magnitude":286.57, ',
                '"speed":-71.81}\n',
            ]
        )
        radar._json_mode = True
        radar._unit = "mph"
        radar._magnitude_enabled = True
        radar._speed_read_buffer = ""

        assert radar.read_speed_nonblocking() is None

        reading = radar.read_speed_nonblocking()

        assert reading is not None
        assert reading.speed == 71.81
        assert reading.direction == Direction.OUTBOUND
        assert reading.magnitude == 286.57

    def test_returns_last_valid_complete_line(self):
        """When several lines arrive together, the newest valid reading wins."""
        radar = OPS243Radar.__new__(OPS243Radar)
        radar.serial = _ChunkedSpeedSerial(
            [
                '{"magnitude":21.0, "speed":-21.22}\n'
                '{"magnitude":90.0, "speed":-44.07}\n'
            ]
        )
        radar._json_mode = True
        radar._unit = "mph"
        radar._magnitude_enabled = True
        radar._speed_read_buffer = ""

        reading = radar.read_speed_nonblocking()

        assert reading is not None
        assert reading.speed == 44.07
        assert reading.magnitude == 90.0

    def test_multi_candidate_reader_returns_all_array_speeds(self):
        """Multi-object JSON should expose all speed candidates."""
        radar = OPS243Radar.__new__(OPS243Radar)
        radar.serial = _ChunkedSpeedSerial(
            [
                '{"magnitude":[900.0, 120.0, 80.0], '
                '"speed":[-55.0, -101.0, 42.0]}\n'
            ]
        )
        radar._json_mode = True
        radar._unit = "mph"
        radar._magnitude_enabled = True
        radar._speed_read_buffer = ""

        readings = radar.read_speed_candidates_nonblocking()

        assert [reading.speed for reading in readings] == [55.0, 101.0, 42.0]
        assert readings[0].direction == Direction.OUTBOUND
        assert readings[1].direction == Direction.OUTBOUND
        assert readings[2].direction == Direction.INBOUND


class TestPreparePersistedRollingBuffer:
    """Tests for returning from speed mode to sound-trigger launch mode."""

    def test_prepare_rearms_without_restoring_or_saving(self, monkeypatch):
        """Normal sound mode should not force GC/A! on a healthy persisted radar."""
        calls = []

        class FakeSerial:
            is_open = True

        radar = OPS243Radar.__new__(OPS243Radar)
        radar.serial = FakeSerial()

        monkeypatch.setattr(radar, "set_units", lambda unit: calls.append(("set_units", unit.value)))
        monkeypatch.setattr(radar, "set_transmit_power", lambda value: calls.append(("set_transmit_power", value)))
        monkeypatch.setattr(radar, "set_sample_rate", lambda value: calls.append(("set_sample_rate", value)))
        monkeypatch.setattr(
            radar,
            "rearm_rolling_buffer",
            lambda pre_trigger_segments: calls.append(("rearm_rolling_buffer", pre_trigger_segments)),
        )
        monkeypatch.setattr(
            radar,
            "restore_rolling_buffer_mode",
            lambda **kwargs: calls.append(("restore_rolling_buffer_mode", kwargs)),
        )

        radar.prepare_persisted_rolling_buffer(pre_trigger_segments=16, sample_rate_ksps=30)

        assert calls == [
            ("set_units", "US"),
            ("set_transmit_power", 3),
            ("set_sample_rate", 30000),
            ("rearm_rolling_buffer", 16),
        ]

    def test_restore_rolling_buffer_mode_does_not_save_to_flash(self, monkeypatch):
        """Runtime mode switching should not send A! or write persistent flash."""
        calls = []

        class FakeSerial:
            is_open = True

            def write(self, data):
                calls.append(("write", data))

            def flush(self):
                calls.append(("flush", None))

            def reset_input_buffer(self):
                calls.append(("reset_input_buffer", None))

        radar = OPS243Radar.__new__(OPS243Radar)
        radar.serial = FakeSerial()

        monkeypatch.setattr(radar, "set_units", lambda unit: calls.append(("set_units", unit.value)))
        monkeypatch.setattr(radar, "set_transmit_power", lambda value: calls.append(("set_transmit_power", value)))
        monkeypatch.setattr(
            radar,
            "enter_rolling_buffer_mode",
            lambda pre_trigger_segments, sample_rate_ksps: calls.append(
                ("enter_rolling_buffer_mode", pre_trigger_segments, sample_rate_ksps)
            ),
        )

        radar.restore_rolling_buffer_mode(pre_trigger_segments=16, sample_rate_ksps=30)

        assert ("write", b"A!") not in calls
        assert calls == [
            ("set_units", "US"),
            ("set_transmit_power", 3),
            ("enter_rolling_buffer_mode", 16, 30),
            ("reset_input_buffer", None),
        ]


class TestFFTSize:
    """Tests for FFT size configuration."""

    def test_set_fft_size_valid_values(self):
        """Valid FFT size values should be accepted."""
        radar = OPS243Radar.__new__(OPS243Radar)
        radar.serial = None
        # Just verify method exists and accepts valid values
        # (can't test actual command without hardware)
        valid_sizes = [1, 2, 4, 8, 16, 32]
        for size in valid_sizes:
            # Should not raise
            try:
                radar.set_fft_size(size)
            except ConnectionError:
                pass  # Expected - no serial connection

    def test_set_fft_size_invalid_value(self):
        """Invalid FFT size should raise ValueError."""
        radar = OPS243Radar.__new__(OPS243Radar)
        radar.serial = None
        with pytest.raises(ValueError):
            radar.set_fft_size(3)
        with pytest.raises(ValueError):
            radar.set_fft_size(64)


class TestSpeedReading:
    """Tests for SpeedReading dataclass."""

    def test_speed_reading_creation(self):
        """Create a basic speed reading."""
        reading = SpeedReading(
            speed=150.0,
            direction=Direction.OUTBOUND,
            magnitude=1500,
            timestamp=12345.67,
            unit="mph",
        )

        assert reading.speed == 150.0
        assert reading.direction == Direction.OUTBOUND
        assert reading.magnitude == 1500
        assert reading.timestamp == 12345.67
        assert reading.unit == "mph"

    def test_speed_reading_defaults(self):
        """Test default values."""
        reading = SpeedReading(speed=100.0, direction=Direction.OUTBOUND)

        assert reading.magnitude is None
        assert reading.timestamp is None
        assert reading.unit == "mph"


class _FakeClockSerial:
    """Minimal serial stand-in for read_clock_sync tests.

    Replies to a ``C?`` write with a JSON clock payload (or nothing, to
    simulate a missing/garbled reply).
    """

    def __init__(self, clock_value="137.429", respond=True, clock_values=None):
        self.is_open = True
        self._clock_value = clock_value
        self._clock_values = list(clock_values) if clock_values is not None else None
        self._respond = respond
        self._pending = b""
        self.writes = []

    def reset_input_buffer(self):
        self._pending = b""

    def write(self, data):
        self.writes.append(data)
        if self._respond:
            if self._clock_values:
                value = self._clock_values[min(len(self.writes) - 1, len(self._clock_values) - 1)]
            else:
                value = self._clock_value
            self._pending = ('{"Clock":"%s"}' % value).encode("ascii")
        return len(data)

    @property
    def in_waiting(self):
        return len(self._pending)

    def read(self, n):
        chunk, self._pending = self._pending[:n], self._pending[n:]
        return chunk


class TestParseOpsClock:
    """Tests for the C? clock reply parser."""

    def test_parse_quoted_decimal(self):
        from openflight.ops243 import _parse_ops_clock

        assert _parse_ops_clock('{"Clock":"137.429"}') == pytest.approx(137.429)

    def test_parse_whole_seconds(self):
        from openflight.ops243 import _parse_ops_clock

        assert _parse_ops_clock('{"Clock":"50"}') == pytest.approx(50.0)

    def test_parse_unquoted_value(self):
        from openflight.ops243 import _parse_ops_clock

        assert _parse_ops_clock('{"Clock": 12.5}') == pytest.approx(12.5)

    def test_parse_garbage_returns_none(self):
        from openflight.ops243 import _parse_ops_clock

        assert _parse_ops_clock("no clock here") is None
        assert _parse_ops_clock("") is None


class TestReadClockSync:
    """Tests for OPS243Radar.read_clock_sync."""

    def _radar(self, serial_obj):
        radar = OPS243Radar.__new__(OPS243Radar)
        radar.serial = serial_obj
        radar.last_clock_sync = None
        return radar

    def test_valid_reads_produce_offset(self):
        radar = self._radar(_FakeClockSerial(clock_value="137.429"))
        summary = radar.read_clock_sync(samples=3, per_read_timeout=0.05)

        assert summary["samples"] == 3
        assert summary["valid_samples"] == 3
        assert summary["best_offset_s"] is not None
        # offset = host_epoch - radar_clock, and host epoch >> 137s, so it is large.
        assert summary["best_offset_s"] > 1_000_000
        assert summary["best_read_latency_ms"] >= 0.0
        assert summary["clock_resolution"] == "fractional"
        assert summary["clock_sync_method"] == "fractional_clock"
        assert summary["usable_for_trigger_timestamps"] is True
        assert len(summary["reads"]) == 3
        assert all(r["radar_clock_s"] == pytest.approx(137.429) for r in summary["reads"])
        assert radar.last_clock_sync is summary

    def test_offset_spread_reported_for_multiple_reads(self):
        radar = self._radar(_FakeClockSerial())
        summary = radar.read_clock_sync(samples=4, per_read_timeout=0.05)
        assert summary["offset_spread_ms"] is not None
        assert summary["offset_spread_ms"] >= 0.0

    def test_store_false_does_not_replace_last_clock_sync(self):
        existing = {"best_offset_s": 42.0}
        radar = self._radar(_FakeClockSerial(clock_value="137.429"))
        radar.last_clock_sync = existing

        summary = radar.read_clock_sync(samples=2, per_read_timeout=0.05, store=False)

        assert summary["best_offset_s"] is not None
        assert radar.last_clock_sync is existing

    def test_no_response_is_handled(self):
        radar = self._radar(_FakeClockSerial(respond=False))
        summary = radar.read_clock_sync(samples=2, per_read_timeout=0.01)

        assert summary["valid_samples"] == 0
        assert summary["best_offset_s"] is None
        assert summary["offset_spread_ms"] is None
        assert summary["samples"] == 2

    def test_whole_second_clock_without_rollover_is_not_usable(self):
        radar = self._radar(_FakeClockSerial(clock_value="1882"))
        summary = radar.read_clock_sync(
            samples=3,
            per_read_timeout=0.01,
            max_sync_duration_s=0.0,
        )

        assert summary["clock_resolution"] == "integer"
        assert summary["clock_sync_method"] == "integer_unusable_no_rollover"
        assert summary["usable_for_trigger_timestamps"] is False
        assert summary["best_offset_s"] is None
        assert summary["raw_best_offset_s"] is not None

    def test_whole_second_clock_rollover_estimates_usable_offset(self):
        radar = self._radar(_FakeClockSerial(clock_values=["1882", "1882", "1883"]))
        summary = radar.read_clock_sync(
            samples=2,
            per_read_timeout=0.01,
            max_sync_duration_s=0.1,
            sample_interval_s=0.0,
        )

        assert summary["clock_resolution"] == "integer"
        assert summary["clock_sync_method"] == "integer_rollover"
        assert summary["usable_for_trigger_timestamps"] is True
        assert summary["best_offset_s"] is not None
        assert summary["rollover_uncertainty_ms"] is not None
        assert summary["samples"] == 3

    def test_raises_when_not_connected(self):
        radar = OPS243Radar.__new__(OPS243Radar)
        radar.serial = None
        with pytest.raises(ConnectionError):
            radar.read_clock_sync(samples=1)


class _ScheduledSerial:
    """Serial stand-in that releases bytes on a wall-clock schedule.

    Models the rolling-buffer dump: nothing arrives until the hardware
    trigger fires, then ~46KB streams over several seconds. Schedule is
    a list of (seconds_after_reset, bytes) pairs.
    """

    def __init__(self, schedule):
        self.is_open = True
        self._schedule = sorted(schedule, key=lambda item: item[0])
        self._t0 = time.time()
        self._consumed = 0

    def reset_input_buffer(self):
        self._t0 = time.time()
        self._consumed = 0

    def _released(self):
        elapsed = time.time() - self._t0
        return b"".join(data for t, data in self._schedule if t <= elapsed)

    @property
    def in_waiting(self):
        return len(self._released()) - self._consumed

    def read(self, n):
        released = self._released()
        chunk = released[self._consumed : self._consumed + n]
        self._consumed += len(chunk)
        return chunk


class TestWaitForHardwareTrigger:
    """Tests for the hardware-trigger read loop (sound trigger path)."""

    # A complete dump ends with a closed Q array — the loop's completeness
    # check requires '"Q"' followed by ']}'.
    _DUMP = [
        b'{"sample_time":946.077}\r\n{"trigger_time":946.145}\r\n',
        b'{"I":[2168,2187,2155,2154]}\r\n',
        b'{"Q":[2048,2050,2047,2049]}',
    ]

    def _radar(self, serial_obj):
        radar = OPS243Radar.__new__(OPS243Radar)
        radar.serial = serial_obj
        radar.last_hardware_trigger_first_byte_timestamp = None
        return radar

    def test_trigger_near_timeout_still_reads_full_dump(self):
        """A trigger firing just before the timeout must not truncate the dump.

        Regression test: the wait loop bounded TOTAL elapsed time, so a
        trigger at 26s of a 30s window had its ~4.5s dump cut off at 30.0s
        ("Incomplete capture (missing: Q)"). Scaled down: trigger at 0.3s
        of a 0.4s window, dump completes at 0.7s.
        """
        schedule = [
            (0.30, self._DUMP[0]),
            (0.50, self._DUMP[1]),
            (0.70, self._DUMP[2]),
        ]
        radar = self._radar(_ScheduledSerial(schedule))
        response = radar.wait_for_hardware_trigger(timeout=0.4)
        assert response == b"".join(self._DUMP).decode("ascii")

    def test_no_trigger_returns_empty_after_timeout(self):
        """With no data at all, the wait still times out promptly."""
        radar = self._radar(_ScheduledSerial([]))
        start = time.time()
        response = radar.wait_for_hardware_trigger(timeout=0.3)
        assert response == ""
        assert time.time() - start < 1.5

    def test_idle_serial_noise_does_not_start_a_hardware_capture(self):
        """Whitespace and clock replies are not HOST_INT-triggered dumps."""
        noise = b'\n\n{"Clock":1786805707}\r\n\n'
        radar = self._radar(_ScheduledSerial([(0.02, noise)]))
        events = []

        response = radar.wait_for_hardware_trigger(
            timeout=0.12,
            dump_grace=1.0,
            on_first_byte=lambda: events.append("capture"),
        )

        assert response == ""
        assert events == []
        assert radar.last_hardware_trigger_first_byte_timestamp is None

    def test_idle_serial_noise_is_discarded_before_a_real_dump(self):
        """A later capture remains readable after unsolicited serial noise."""
        noise = b'\n\n{"Clock":1786805707}\r\n\n'
        dump = b"".join(self._DUMP)
        radar = self._radar(_ScheduledSerial([(0.02, noise), (0.08, dump)]))
        events = []

        response = radar.wait_for_hardware_trigger(
            timeout=0.2,
            dump_grace=1.0,
            on_first_byte=lambda: events.append("capture"),
        )

        assert response == dump.decode("ascii")
        assert events == ["capture"]
        assert radar.last_hardware_trigger_first_byte_timestamp is not None

    def test_complete_dump_returns_before_grace_expires(self):
        """A dump that finishes early returns immediately on completeness."""
        schedule = [(0.05, b"".join(self._DUMP))]
        radar = self._radar(_ScheduledSerial(schedule))
        start = time.time()
        response = radar.wait_for_hardware_trigger(timeout=5.0)
        assert response == b"".join(self._DUMP).decode("ascii")
        assert time.time() - start < 1.0

    def test_idle_wait_can_be_cancelled_for_fast_shutdown(self):
        """Shutdown should not wait for the normal 30-second trigger timeout."""
        parameters = inspect.signature(OPS243Radar.wait_for_hardware_trigger).parameters
        assert "cancel_event" in parameters

        radar = self._radar(_ScheduledSerial([]))
        cancel_event = threading.Event()
        cancel_event.set()
        start = time.monotonic()

        response = radar.wait_for_hardware_trigger(
            timeout=30.0,
            cancel_event=cancel_event,
        )

        assert response == ""
        assert time.monotonic() - start < 0.2

    def test_cancel_does_not_discard_a_dump_that_has_started_arriving(self):
        """Pending capture bytes win a race with the shutdown cancellation."""
        radar = self._radar(_ScheduledSerial([(0.0, b"".join(self._DUMP))]))
        cancel_event = threading.Event()
        cancel_event.set()

        response = radar.wait_for_hardware_trigger(
            timeout=30.0,
            cancel_event=cancel_event,
        )

        assert response == b"".join(self._DUMP).decode("ascii")

    def test_first_byte_callback_fires_when_capture_starts(self):
        """The UI can acknowledge impact before the complete dump arrives."""
        parameters = inspect.signature(OPS243Radar.wait_for_hardware_trigger).parameters
        assert "on_first_byte" in parameters

        radar = self._radar(_ScheduledSerial([(0.0, b"".join(self._DUMP))]))
        events = []

        response = radar.wait_for_hardware_trigger(
            timeout=5.0,
            on_first_byte=lambda: events.append("first-byte"),
        )

        assert response == b"".join(self._DUMP).decode("ascii")
        assert events == ["first-byte"]


class _InternalTriggerSerial:
    """Minimal serial stand-in for internal-trigger command tests."""

    is_open = True

    def __init__(self, fail_write=False):
        self.writes = []
        self.fail_write = fail_write

    @property
    def in_waiting(self):
        return 0

    def reset_input_buffer(self):
        pass

    def write(self, data):
        if self.fail_write:
            raise serial.SerialTimeoutException("radar busy")
        self.writes.append(data)
        return len(data)

    def flush(self):
        pass


class TestInternalSpeedTrigger:
    """Focused tests for the OPS243 board-managed speed trigger."""

    @staticmethod
    def _radar(serial_obj):
        radar = OPS243Radar.__new__(OPS243Radar)
        radar.serial = serial_obj
        return radar

    def test_configuration_uses_gc_trigger_order_and_six_pre_segments(self, monkeypatch):
        """Internal trigger setup must restore GC-reset settings in order."""
        radar = self._radar(_InternalTriggerSerial())
        commands = []
        monkeypatch.setattr(time, "sleep", lambda _seconds: None)

        def send_command(command):
            commands.append(command)
            return '{"Version":"1.3.2"}' if command == "?V" else ""

        monkeypatch.setattr(
            radar,
            "_send_command",
            send_command,
        )

        radar.configure_for_internal_speed_trigger(
            trigger_threshold_mph=25,
            pre_trigger_segments=6,
            trigger_magnitude=40,
            sample_rate_ksps=30,
        )

        assert commands == [
            "?V",
            "PI",
            "GC",
            "S=30",
            "US",
            "P0",
            "S(",
            "X=2",
            "R-",
            "R>25",
            "OJ",
            "OM",
            "W0",
            "S#6",
        ]
        assert radar.serial.writes == [b"ST-25\r", b"ST-25\r", b"SM40\r"]
        assert not {"GS", "PA", "S#0"} & set(commands)

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"trigger_threshold_mph": -1}, "non-negative"),
            ({"trigger_magnitude": 0}, "between 1 and 2000"),
            ({"trigger_magnitude": 2001}, "between 1 and 2000"),
            ({"sample_rate_ksps": 25}, "30 ksps"),
        ],
    )
    def test_configuration_validates_hardware_requirements(self, kwargs, message):
        """Unsafe threshold, magnitude, and sample-rate values fail early."""
        radar = self._radar(_InternalTriggerSerial())

        with pytest.raises(ValueError, match=message):
            radar.configure_for_internal_speed_trigger(**kwargs)

    @pytest.mark.parametrize(
        "version",
        ["1.3.1", "1.3.0", "1.2.9", "1.4.0", "unknown", "1.3", "v1.3.2", "1.3.2-beta", None],
    )
    def test_configuration_rejects_unsupported_ops243_firmware(self, monkeypatch, version):
        """Internal triggering must reject old, unknown, and other firmware trains."""
        radar = self._radar(_InternalTriggerSerial())
        monkeypatch.setattr(radar, "_probe_firmware_version", lambda: version)

        with pytest.raises(RuntimeError, match="requires OPS243-A firmware v1.3.2"):
            radar.configure_for_internal_speed_trigger()

    @pytest.mark.parametrize("version", ["1.3.2", "1.3.3", "1.3.99"])
    def test_configuration_accepts_compatible_ops243_firmware(self, monkeypatch, version):
        """The current and newer patch releases in the 1.3 train are accepted."""
        radar = self._radar(_InternalTriggerSerial())
        monkeypatch.setattr(radar, "_probe_firmware_version", lambda: version)

        assert radar.validate_internal_trigger_firmware() == version

    def test_rearm_uses_gc_and_restores_cached_settings(self, monkeypatch):
        """A completed dump is re-armed with GC without PA or S#0."""
        radar = self._radar(_InternalTriggerSerial())
        radar._internal_speed_trigger_config = (25.0, 6, 40, 30)
        commands = []
        monkeypatch.setattr(time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(radar, "_drain_rearm_serial", lambda: None)
        monkeypatch.setattr(
            radar,
            "_send_command",
            lambda command: commands.append(command) or "",
        )

        assert radar.rearm_internal_speed_trigger() is True
        assert radar.serial.writes == [b"GC", b"ST-25\r", b"SM40\r"]
        assert commands == [
            "S=30",
            "US",
            "P0",
            "S(",
            "X=2",
            "R-",
            "R>25",
            "OJ",
            "OM",
            "W0",
            "S#6",
        ]
        assert not {"PI", "GS", "PA", "S#0"} & set(commands)

    def test_rearm_recovers_from_serial_timeout_without_discarding_capture(self, monkeypatch):
        """A busy radar reports a retryable re-arm failure instead of raising."""
        radar = self._radar(_InternalTriggerSerial(fail_write=True))
        radar._internal_speed_trigger_config = (25.0, 6, 40, 30)
        monkeypatch.setattr(time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(radar, "_drain_rearm_serial", lambda: None)

        assert radar.rearm_internal_speed_trigger() is False
        assert radar._hardware_trigger_recovery_required is True

    def test_failed_rearm_discards_stale_output_before_next_dump(self):
        """Recovery must ignore trailing UART records before the next I/Q dump."""
        stale = b'{"speed":-12.0}\r\n'
        radar = self._radar(
            _ScheduledSerial(
                [
                    (0.0, stale),
                    (0.05, b"".join(TestWaitForHardwareTrigger._DUMP)),
                ]
            )
        )
        radar._hardware_trigger_recovery_required = True

        response = radar.wait_for_hardware_trigger(timeout=1.0)

        assert response == b"".join(TestWaitForHardwareTrigger._DUMP).decode("ascii")
        assert radar._hardware_trigger_recovery_required is False
