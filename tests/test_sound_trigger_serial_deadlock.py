"""Regression tests for the net-impact serial deadlock (field failure).

A real shot produces a second loud sound ~1s after impact (ball hitting the
net). With the old ordering, the radar was re-armed immediately after its
dump was read, so the net impact re-asserted HOST_INT and started a new 41KB
dump exactly when the post-capture clock-sync exchange wrote C? commands to
the port. With no write_timeout on the serial port, serial.write() blocked
forever (confirmed by py-spy: capture thread stuck in serialposix write via
read_clock_sync), the radar wedged mid-dump, and the system never re-armed.

Fixes under test:
1. The serial port opens with a write_timeout so no write can hang a thread.
2. read_clock_sync survives a write timeout and returns an unusable summary.
3. SoundTrigger talks on the wire (clock sync) BEFORE re-arming — while the
   radar is idle after its dump and HOST_INT pulses are harmless — and
   re-arms last. Rejected/false triggers skip clock sync and re-arm fast.
"""

import json
from unittest.mock import patch

import serial as pyserial
from spin_synth import synth_capture

from openflight.ops243 import OPS243Radar
from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.rolling_buffer.trigger import SoundTrigger


def _dump_response(i_samples, q_samples) -> str:
    return "\n".join(
        [
            '{"sample_time": "964.003"}',
            '{"trigger_time": "964.105"}',
            json.dumps({"I": [int(v) for v in i_samples]}),
            json.dumps({"Q": [int(v) for v in q_samples]}),
        ]
    )


class ScriptedRadar:
    """Mock radar recording the order of serial-touching calls."""

    def __init__(self, response: str):
        self.response = response
        self.calls = []
        self.last_clock_sync = None
        self.last_hardware_trigger_first_byte_timestamp = None

    def wait_for_hardware_trigger(self, timeout, cancel_event=None, on_first_byte=None):
        self.calls.append("wait")
        return self.response

    def rearm_rolling_buffer(self, pre_trigger_segments):
        self.calls.append("rearm")

    def read_clock_sync(self, samples=7, store=True, **kwargs):
        self.calls.append("clock_sync")
        return {"clock_sync_method": "no_valid_reads", "valid_samples": 0}


class TimeoutWriteSerial:
    """Fake port whose writes time out (radar mid-dump, not reading)."""

    def __init__(self):
        self.is_open = True

    @property
    def in_waiting(self):
        return 0

    def read(self, n):
        return b""

    def reset_input_buffer(self):
        pass

    def write(self, data):
        raise pyserial.SerialTimeoutException("Write timeout")

    def flush(self):
        pass


def test_connect_sets_write_timeout():
    radar = OPS243Radar.__new__(OPS243Radar)
    radar.port = "/dev/ttyACM0"
    radar.baud = 57600
    radar.serial = None
    with patch("openflight.ops243.serial.Serial") as serial_cls:
        serial_cls.return_value.is_open = True
        with patch.object(OPS243Radar, "_verify_connection", return_value=True, create=True):
            try:
                radar.connect()
            except Exception:
                pass  # only the constructor kwargs matter here
        assert serial_cls.called, "connect() did not construct a Serial port"
        kwargs = serial_cls.call_args.kwargs
        assert kwargs.get("write_timeout") is not None, (
            "Serial port opened without write_timeout — a wedged radar "
            "blocks serial.write() forever and hangs the capture thread"
        )


def test_read_clock_sync_survives_write_timeout():
    radar = OPS243Radar.__new__(OPS243Radar)
    radar.serial = TimeoutWriteSerial()
    summary = radar.read_clock_sync(samples=3, store=False)
    assert summary["valid_samples"] == 0
    assert not summary["usable_for_trigger_timestamps"]


def test_accepted_capture_clock_syncs_before_rearm():
    # amplitude=400 ADC counts: process_standard scales to volts and gates on
    # MAGNITUDE_THRESHOLD=3; the synth default (40) lands under it.
    i_samples, q_samples = synth_capture(rpm=3000, ball_speed_mph=80.0, amplitude=400.0)
    radar = ScriptedRadar(_dump_response(i_samples, q_samples))
    trigger = SoundTrigger()
    capture = trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=1.0)
    trigger.finish_housekeeping()  # clock sync + re-arm run on a helper thread

    assert capture is not None, "synthetic swing should be accepted"
    assert "clock_sync" in radar.calls and "rearm" in radar.calls
    assert radar.calls.index("clock_sync") < radar.calls.index("rearm"), (
        f"clock sync must run while the radar is idle, BEFORE re-arm; got {radar.calls}"
    )


def test_rejected_capture_rearms_fast_without_clock_sync():
    # Silence (no ball tone) -> no outbound readings -> false trigger.
    i_samples, q_samples = synth_capture(rpm=3000, amplitude=0.0, noise_rms=1.0)
    radar = ScriptedRadar(_dump_response(i_samples, q_samples))
    trigger = SoundTrigger()
    capture = trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=1.0)

    assert capture is None, "silent capture should be rejected"
    assert "rearm" in radar.calls
    assert "clock_sync" not in radar.calls, (
        "false triggers must re-arm fast; clock sync is accepted-shots-only"
    )
