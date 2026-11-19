"""Tests for manual OPS243 radar recovery."""

from unittest.mock import MagicMock, Mock, call

import pytest

from openflight.rolling_buffer.monitor import RollingBufferMonitor


def make_monitor():
    """Create an isolated monitor without opening radar hardware."""
    monitor = object.__new__(RollingBufferMonitor)

    monitor.trigger_type = "sound"
    monitor._running = True
    monitor._capture_thread = None
    monitor.sample_rate_ksps = 30

    monitor._shot_callback = Mock()
    monitor._live_callback = Mock()
    monitor._diagnostic_callback = Mock()
    monitor._processing_callback = Mock()

    monitor.trigger = Mock()
    monitor.trigger.pre_trigger_segments = 16
    monitor.radar = Mock()

    monitor.stop = Mock()
    monitor.start = Mock()

    return monitor


def test_manual_recovery_restores_radar_and_callbacks():
    monitor = make_monitor()

    monitor.reinitialize_radar()

    monitor.stop.assert_called_once()

    monitor.radar.prepare_persisted_rolling_buffer.assert_called_once_with(
        pre_trigger_segments=16,
        sample_rate_ksps=30,
    )

    monitor.trigger.reset.assert_called_once()

    monitor.start.assert_called_once_with(
        shot_callback=monitor._shot_callback,
        live_callback=monitor._live_callback,
        diagnostic_callback=monitor._diagnostic_callback,
        processing_callback=monitor._processing_callback,
    )


def test_manual_recovery_rejects_other_trigger_modes():
    monitor = make_monitor()
    monitor.trigger_type = "hardware"

    with pytest.raises(RuntimeError, match="sound-trigger"):
        monitor.reinitialize_radar()

    monitor.stop.assert_not_called()
    monitor.radar.prepare_persisted_rolling_buffer.assert_not_called()


def test_manual_recovery_rejects_stopped_monitor():
    monitor = make_monitor()
    monitor._running = False

    with pytest.raises(RuntimeError, match="not running"):
        monitor.reinitialize_radar()

    monitor.stop.assert_not_called()


def test_manual_recovery_refuses_live_capture_thread():
    monitor = make_monitor()
    monitor._capture_thread = Mock()
    monitor._capture_thread.is_alive.return_value = True

    with pytest.raises(RuntimeError, match="did not stop"):
        monitor.reinitialize_radar()

    monitor.stop.assert_called_once()
    monitor.radar.prepare_persisted_rolling_buffer.assert_not_called()
    monitor.start.assert_not_called()
