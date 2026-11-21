"""SoundTrigger hands an accepted capture over before its post-dump housekeeping.

After the OPS dump, the trigger must clock-sync (while the radar is idle) and
then re-arm -- about 1.35 s of serial talk (clock sync 0.78 s, drain 0.20 s,
re-arm 0.37 s). None of it is needed to compute the shot,
so the capture is returned at once and the housekeeping runs on a helper
thread. The next wait must not start until that helper has re-armed, and clock
sync must still precede re-arm (see test_sound_trigger_serial_deadlock).
"""

import json
import threading
import time

from spin_synth import synth_capture

from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.rolling_buffer.trigger import SoundTrigger

CLOCK_SYNC_S = 0.4
REARM_S = 0.2


def _dump_response(i_samples, q_samples, trigger_time="964.105") -> str:
    return "\n".join(
        [
            '{"sample_time": "964.003"}',
            f'{{"trigger_time": "{trigger_time}"}}',
            json.dumps({"I": [int(v) for v in i_samples]}),
            json.dumps({"Q": [int(v) for v in q_samples]}),
        ]
    )


def _valid_sync(offset_s: float, read_host_time: float) -> dict:
    return {
        "clock_sync_method": "integer_rollover",
        "usable_for_trigger_timestamps": True,
        "best_offset_s": offset_s,
        "rollover_uncertainty_ms": 10.0,
        "valid_samples": 30,
        "reads": [{"host_after": read_host_time, "radar_clock_s": 1.0, "read_latency_ms": 1.0}],
    }


class SlowRadar:
    """Records serial-touching calls with timestamps; clock sync and re-arm take time."""

    def __init__(
        self, response: str, *, sync_offset_s: float = 1000.0, first_byte: float | None = None
    ):
        self.response = response
        self.sync_offset_s = sync_offset_s
        self.events = []
        self.lock = threading.Lock()
        self.last_clock_sync = None
        self.last_hardware_trigger_first_byte_timestamp = first_byte

    def _log(self, name):
        with self.lock:
            self.events.append((name, time.monotonic()))

    def names(self):
        with self.lock:
            return [name for name, _ in self.events]

    def wait_for_hardware_trigger(self, timeout, cancel_event=None, on_first_byte=None):
        self._log("wait")
        return self.response

    def read_clock_sync(self, samples=7, store=True, **kwargs):
        self._log("clock_sync_start")
        time.sleep(CLOCK_SYNC_S)
        self._log("clock_sync_end")
        return _valid_sync(self.sync_offset_s, time.time())

    def rearm_rolling_buffer(self, pre_trigger_segments):
        time.sleep(REARM_S)
        self._log("rearm")


def _swing():
    return synth_capture(rpm=3000, ball_speed_mph=80.0, amplitude=400.0)


def test_accepted_capture_is_returned_before_clock_sync_and_rearm_finish():
    radar = SlowRadar(_dump_response(*_swing()))
    trigger = SoundTrigger()
    started = time.monotonic()
    capture = trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=1.0)
    elapsed = time.monotonic() - started
    try:
        assert capture is not None
        assert elapsed < CLOCK_SYNC_S, f"shot waited {elapsed:.2f}s for housekeeping"
    finally:
        trigger.finish_housekeeping()
    names = radar.names()
    assert names.index("clock_sync_end") < names.index("rearm")


def test_next_wait_starts_only_after_rearm():
    radar = SlowRadar(_dump_response(*_swing()))
    trigger = SoundTrigger()
    processor = RollingBufferProcessor()
    trigger.wait_for_trigger(radar, processor, timeout=1.0)
    trigger.wait_for_trigger(radar, processor, timeout=1.0)
    trigger.finish_housekeeping()
    names = radar.names()
    second_wait = [i for i, name in enumerate(names) if name == "wait"][1]
    assert names.index("rearm") < second_wait, names


def test_capture_is_not_mutated_by_the_helper_thread():
    radar = SlowRadar(_dump_response(*_swing()), first_byte=time.time())
    trigger = SoundTrigger()
    capture = trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=1.0)
    before = (capture.trigger_timestamp, capture.trigger_timestamp_source)
    trigger.finish_housekeeping()
    assert (capture.trigger_timestamp, capture.trigger_timestamp_source) == before


def test_fresh_previous_clock_sync_times_the_shot_immediately():
    now = time.time()
    radar = SlowRadar(_dump_response(*_swing()), first_byte=now)
    radar.last_clock_sync = _valid_sync(2000.0, now - 5.0)  # 5 s old: still trusted
    trigger = SoundTrigger()
    capture = trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=1.0)
    trigger.finish_housekeeping()
    assert capture.trigger_timestamp_source == "ops_clock_sync"
    assert capture.trigger_timestamp == capture.trigger_time + 2000.0


def test_without_a_fresh_sync_the_first_byte_time_is_corrected_by_the_learned_bias():
    now = time.time()
    # Shot 1 has no previous sync: first-byte timing; its helper learns the bias.
    radar = SlowRadar(_dump_response(*_swing()), first_byte=now, sync_offset_s=0.0)
    trigger = SoundTrigger()
    first = trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=1.0)
    assert first.trigger_timestamp_source == "first_byte"
    trigger.finish_housekeeping()
    synced = first.trigger_time + 0.0
    bias = synced - first.infer_trigger_timestamp_from_first_byte()
    # Shot 2 after the sync has gone stale: corrected first-byte timing.
    radar.last_clock_sync["reads"][0]["host_after"] = time.time() - 600.0
    radar.last_hardware_trigger_first_byte_timestamp = now + 30.0
    second = trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=1.0)
    trigger.finish_housekeeping()
    assert second.trigger_timestamp_source == "first_byte_calibrated"
    expected = second.infer_trigger_timestamp_from_first_byte() + bias
    assert abs(second.trigger_timestamp - expected) < 1e-9


def test_cancel_while_housekeeping_returns_promptly():
    radar = SlowRadar(_dump_response(*_swing()))
    trigger = SoundTrigger()
    processor = RollingBufferProcessor()
    trigger.wait_for_trigger(radar, processor, timeout=1.0)
    cancel = threading.Event()
    cancel.set()
    started = time.monotonic()
    assert trigger.wait_for_trigger(radar, processor, timeout=1.0, cancel_event=cancel) is None
    assert time.monotonic() - started < 0.2
    trigger.finish_housekeeping()


def test_finish_housekeeping_times_out_while_the_helper_is_busy():
    radar = SlowRadar(_dump_response(*_swing()))
    trigger = SoundTrigger()
    trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=1.0)
    assert trigger.finish_housekeeping(timeout_s=0.05) is False
    assert trigger.finish_housekeeping(timeout_s=5.0) is True


def test_every_trigger_strategy_can_finish_housekeeping():
    from openflight.rolling_buffer.trigger import TriggerStrategy

    class Plain(TriggerStrategy):
        def wait_for_trigger(self, radar, processor, timeout=30.0):
            return None

        def reset(self):
            pass

    assert Plain().finish_housekeeping(timeout_s=0.0) is True


def test_failed_rearm_surfaces_on_the_next_wait():
    class FailingRearm(SlowRadar):
        def rearm_rolling_buffer(self, pre_trigger_segments):
            raise OSError("serial write failed")

    radar = FailingRearm(_dump_response(*_swing()))
    trigger = SoundTrigger()
    trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=1.0)
    try:
        trigger.wait_for_trigger(radar, RollingBufferProcessor(), timeout=1.0)
    except OSError as error:
        assert "serial write failed" in str(error)
    else:
        raise AssertionError("a failed re-arm must not be swallowed")
