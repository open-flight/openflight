"""Tests for GPIO-triggered TI capture and OPS shot correlation."""

from __future__ import annotations

import threading
import time

import numpy as np

from openflight.iwr6843.dump import pack_dump
from openflight.iwr6843.monitor import IWR6843CaptureMonitor


class FakeRadar:
    """Small transport double with a complete L3 dump."""

    port = "/dev/fake-iwr6843"

    def __init__(self, raw: bytes, error: Exception | None = None):
        self.raw = raw
        self.error = error
        self.configs = []
        self.closed = False
        self.read_started_at = None
        self.shutdown_events = []

    def send_config(self, path: str):
        self.configs.append(path)

    def read_dump(self):
        self.read_started_at = time.monotonic()
        if self.error is not None:
            raise self.error
        return self.raw

    def close(self):
        self.shutdown_events.append("close")
        self.closed = True

    def stop_sensor(self):
        self.shutdown_events.append("sensorStop")


class FakeButton:
    """gpiozero-compatible button double."""

    def __init__(self, pin, pull_up, bounce_time):
        self.pin = pin
        self.pull_up = pull_up
        self.bounce_time = bounce_time
        self.when_pressed = None
        self.closed = False

    def close(self):
        self.closed = True


def _temperature_report() -> dict[str, int]:
    return {
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


def _raw_dump(temperature_report: dict[str, int] | None = None) -> bytes:
    cube = np.zeros((2, 4, 4, 8), dtype=complex)
    return pack_dump(
        cube,
        n_tx=2,
        version=5 if temperature_report is not None else 3,
        frame_period_us=6000,
        temperature_report=temperature_report,
    )


def test_capture_monitor_matches_gpio_edge_to_ops_impact(tmp_path):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    radar = FakeRadar(_raw_dump())
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=radar,
        button_factory=FakeButton,
        save_dumps=True,
    )
    monitor.start()

    edge = time.time()
    assert monitor.notify_trigger(edge)
    capture = monitor.capture_for_shot(edge + 0.012, timeout_s=1.0)

    assert capture is not None and capture.valid
    assert capture.trigger_timestamp == edge
    assert capture.path is not None and capture.path.read_bytes() == _raw_dump()
    assert radar.configs == [str(config)]
    assert monitor._button.bounce_time is None  # pylint: disable=protected-access

    monitor.stop()
    assert radar.closed


def test_capture_monitor_keeps_valid_raw_in_memory_without_writing_dump(tmp_path):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    raw = _raw_dump()
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=FakeRadar(raw),
        button_factory=FakeButton,
    )
    monitor.start()

    edge = time.time()
    assert monitor.notify_trigger(edge)
    capture = monitor.capture_for_shot(edge, timeout_s=1.0)

    assert capture is not None and capture.valid
    assert capture.raw == raw
    assert capture.path is None
    assert not (tmp_path / "dumps").exists()
    monitor.stop()


def test_capture_monitor_notifies_trigger_observers(tmp_path):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    raw = _raw_dump()
    observed = []
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=FakeRadar(raw),
        button_factory=FakeButton,
        trigger_observers=[observed.append],
    )
    monitor.start()

    edge = time.time()
    assert monitor.notify_trigger(edge)
    capture = monitor.capture_for_shot(edge, timeout_s=1.0)

    assert capture is not None and capture.valid
    assert observed == [edge]
    monitor.stop()


def test_capture_monitor_records_temperature_report_from_dump_header(tmp_path):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    report = _temperature_report()
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=FakeRadar(_raw_dump(temperature_report=report)),
        button_factory=FakeButton,
    )
    monitor.start()

    edge = time.time()
    assert monitor.notify_trigger(edge)
    capture = monitor.capture_for_shot(edge, timeout_s=1.0)

    assert capture is not None and capture.valid
    assert capture.temperature_report == report
    monitor.stop()


def test_capture_monitor_can_configure_before_arming_gpio(tmp_path):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    radar = FakeRadar(_raw_dump())
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=radar,
        button_factory=FakeButton,
    )
    monitor.start(armed=False)

    edge = time.time()
    assert not monitor.notify_trigger(edge)
    assert monitor._button.when_pressed is None  # pylint: disable=protected-access

    monitor.arm()
    assert monitor._button.when_pressed == monitor.notify_trigger  # pylint: disable=protected-access
    assert monitor.notify_trigger(edge)
    assert monitor.capture_for_shot(edge, timeout_s=1.0).valid
    monitor.stop()


def test_capture_monitor_finishes_active_dump_before_closing_serial(tmp_path):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")

    class BlockingRadar(FakeRadar):
        def __init__(self, raw):
            super().__init__(raw)
            self.read_started = threading.Event()
            self.release_read = threading.Event()
            self.closed_before_read_finished = False

        def read_dump(self):
            self.read_started.set()
            self.release_read.wait(timeout=1.0)
            return self.raw

        def close(self):
            self.closed_before_read_finished = not self.release_read.is_set()
            # Unblock the old close-before-join implementation so this test fails fast.
            self.release_read.set()
            super().close()

    radar = BlockingRadar(_raw_dump())
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=radar,
        button_factory=FakeButton,
    )
    monitor.start()
    assert monitor.notify_trigger(time.time())
    assert radar.read_started.wait(timeout=0.5)

    stopper = threading.Thread(target=monitor.stop)
    stopper.start()
    time.sleep(0.05)
    radar.release_read.set()
    stopper.join(timeout=1.0)

    assert not stopper.is_alive()
    assert not radar.closed_before_read_finished
    assert radar.closed
    assert radar.shutdown_events == ["sensorStop", "close"]


def test_capture_monitor_closes_serial_when_sensor_stop_fails(tmp_path):
    """A failed firmware stop must not leak the host serial descriptor."""
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")

    class StopFailingRadar(FakeRadar):
        def stop_sensor(self):
            self.shutdown_events.append("sensorStop")
            raise RuntimeError("firmware remained active")

    radar = StopFailingRadar(_raw_dump())
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=radar,
        button_factory=FakeButton,
    )
    monitor.start()

    monitor.stop()

    assert radar.shutdown_events == ["sensorStop", "close"]
    assert radar.closed


def test_capture_monitor_discards_stale_false_trigger(tmp_path):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=FakeRadar(_raw_dump()),
        button_factory=FakeButton,
        match_tolerance_s=0.1,
    )
    monitor.start()
    assert monitor.notify_trigger(100.0)

    assert monitor.capture_for_shot(101.0, timeout_s=0.1) is None
    monitor.stop()


def test_capture_monitor_returns_quickly_when_matching_trigger_is_absent(tmp_path):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=FakeRadar(_raw_dump()),
        button_factory=FakeButton,
        match_tolerance_s=0.1,
    )
    monitor.start()

    start = time.monotonic()
    capture = monitor.capture_for_shot(time.time() - 1.0, timeout_s=1.0)

    assert capture is None
    assert time.monotonic() - start < 0.2
    monitor.stop()


def test_capture_monitor_surfaces_dump_failure_without_hanging(tmp_path):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=FakeRadar(b"", error=OSError("serial disconnected")),
        button_factory=FakeButton,
    )
    monitor.start()
    edge = time.time()
    assert monitor.notify_trigger(edge)

    capture = monitor.capture_for_shot(edge, timeout_s=1.0)

    assert capture is not None
    assert not capture.valid
    assert capture.error == "serial disconnected"
    monitor.stop()


def test_capture_monitor_closes_serial_when_gpio_setup_fails(tmp_path):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    radar = FakeRadar(_raw_dump())

    def failing_button(*_args, **_kwargs):
        raise RuntimeError("GPIO unavailable")

    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=radar,
        button_factory=failing_button,
    )

    try:
        monitor.start()
    except RuntimeError as error:
        assert str(error) == "GPIO unavailable"
    else:
        raise AssertionError("expected GPIO setup to fail")
    assert radar.shutdown_events == ["sensorStop", "close"]
    assert radar.closed
