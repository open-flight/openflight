"""Swing speed training monitor using OPS243 speed streaming."""

import logging
import statistics
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional

from .ops243 import Direction, OPS243Radar, SpeedReading
from .session_logger import log_session_error

logger = logging.getLogger("openflight.swing_speed")


@dataclass
class SwingSpeedEvent:
    """A single club-only swing speed training rep."""

    peak_speed_mph: float
    timestamp: datetime
    duration_ms: float
    reading_count: int
    trigger_speed_mph: float
    peak_magnitude: Optional[float] = None
    training_implement: str = "driver"
    training_implement_label: str = "Driver"
    profile_id: str = ""
    profile_name: str = ""
    unit: str = "mph"
    mode: str = "swing-speed"


class SwingSpeedMonitor:
    """Monitor OPS243 speed readings and emit club-only swing speed reps."""

    def __init__(
        self,
        port: Optional[str] = None,
        trigger_threshold_mph: float = 30.0,
        max_speed_mph: Optional[float] = 130.0,
        min_readings: int = 3,
        single_reading_peak_mph: float = 60.0,
        num_reports: int = 8,
        end_quiet_ms: float = 1000.0,
        cooldown_ms: float = 750.0,
        rejected_cooldown_ms: float = 100.0,
        poll_interval_ms: float = 2.0,
    ):
        self.radar = OPS243Radar(port=port)
        self.trigger_threshold_mph = trigger_threshold_mph
        self.max_speed_mph = max_speed_mph
        self.min_readings = min_readings
        self.single_reading_peak_mph = single_reading_peak_mph
        self.num_reports = num_reports
        self.end_quiet_ms = end_quiet_ms
        self.cooldown_ms = cooldown_ms
        self.rejected_cooldown_ms = rejected_cooldown_ms
        self.poll_interval_ms = poll_interval_ms

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._event_callback: Optional[Callable[[SwingSpeedEvent], None]] = None
        self._live_callback: Optional[Callable[[SpeedReading], None]] = None
        self._events: List[SwingSpeedEvent] = []
        self.training_implement = "driver"
        self.training_implement_label = "Driver"

    def connect(self) -> bool:
        """Connect to the radar and configure CW speed reporting for training.

        Uses the dedicated training path rather than the launch monitor's
        configure_for_speed_trigger(), which exists to detect a swing and hand
        off to the rolling buffer. Training never captures a rolling buffer.
        """
        self.radar.connect()
        self.radar.configure_for_swing_speed_training(
            min_speed_mph=self.trigger_threshold_mph,
            num_reports=self.num_reports,
        )
        logger.info(
            "[SWING_SPEED] Monitor ready (threshold=%.1f mph, max=%s, reports=%d, end_quiet=%.0fms)",
            self.trigger_threshold_mph,
            "%.1f mph" % self.max_speed_mph if self.max_speed_mph is not None else "off",
            self.num_reports,
            self.end_quiet_ms,
        )
        return True

    def disconnect(self):
        """Disconnect from the radar."""
        self.stop()
        try:
            self.radar.restore_rolling_buffer_mode()
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("[SWING_SPEED] Failed to restore rolling buffer mode: %s", exc)
        self.radar.disconnect()

    def get_radar_info(self) -> dict:
        """Get radar module information."""
        return self.radar.get_info()

    def start(
        self,
        event_callback: Optional[Callable[[SwingSpeedEvent], None]] = None,
        live_callback: Optional[Callable[[SpeedReading], None]] = None,
    ):
        """Start monitoring for swing speed reps."""
        self._event_callback = event_callback
        self._live_callback = live_callback
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info("[SWING_SPEED] Monitor started")

    def stop(self):
        """Stop monitoring."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("[SWING_SPEED] Monitor stopped")

    def get_events(self) -> List[SwingSpeedEvent]:
        """Return a copy of recorded swing speed events."""
        return list(self._events)

    def get_shots(self) -> list:
        """Return no launch-monitor shots for compatibility with server handlers."""
        return []

    def clear_session(self):
        """Clear recorded swing speed events."""
        self._events.clear()

    def set_club(self, _club):
        """Ignore club selection changes in club-only training mode."""
        return None

    def set_training_implement(self, implement: str, label: str):
        """Set the training implement stamped onto future swing speed reps."""
        self.training_implement = implement
        self.training_implement_label = label

    def get_session_stats(self) -> dict:
        """Get summary statistics for the current swing speed session."""
        if not self._events:
            return {
                "swing_count": 0,
                "best_speed_mph": 0,
                "avg_speed_mph": 0,
                "last_speed_mph": 0,
            }

        speeds = [event.peak_speed_mph for event in self._events]
        return {
            "swing_count": len(self._events),
            "best_speed_mph": round(max(speeds), 1),
            "avg_speed_mph": round(statistics.mean(speeds), 1),
            "last_speed_mph": round(speeds[-1], 1),
        }

    def _capture_loop(self):
        """Capture speed-stream readings and group them into swing events."""
        active_readings: List[SpeedReading] = []
        trigger_speed = 0.0
        swing_start = 0.0
        last_active = 0.0
        cooldown_until = 0.0

        while self._running:
            try:
                now = time.time()
                readings = self.radar.read_speed_candidates_nonblocking()
                if not readings:
                    single_reading = self.radar.read_speed_nonblocking()
                    readings = [single_reading] if single_reading else []

                reading = self._select_swing_reading(readings)
                if reading and self._live_callback:
                    self._live_callback(reading)

                qualifies = (
                    reading is not None
                    and reading.direction == Direction.OUTBOUND
                    and reading.speed >= self.trigger_threshold_mph
                    and self._within_max_speed(reading.speed)
                )

                if now < cooldown_until:
                    time.sleep(self.poll_interval_ms / 1000.0)
                    continue

                if qualifies:
                    if not active_readings:
                        swing_start = reading.timestamp or now
                        trigger_speed = reading.speed
                        logger.info("[SWING_SPEED] Swing started at %.1f mph", trigger_speed)
                    active_readings.append(reading)
                    last_active = reading.timestamp or now
                elif active_readings and (now - last_active) * 1000.0 >= self.end_quiet_ms:
                    peak_speed = max(reading.speed for reading in active_readings)
                    strong_single_peak = peak_speed >= self.single_reading_peak_mph
                    if len(active_readings) < self.min_readings and not strong_single_peak:
                        logger.info(
                            "[SWING_SPEED] Ignored short motion: %d readings, peak=%.1f mph",
                            len(active_readings),
                            peak_speed,
                        )
                        active_readings = []
                        cooldown_until = now + self.rejected_cooldown_ms / 1000.0
                        continue

                    event = self._build_event(
                        readings=active_readings,
                        trigger_speed=trigger_speed,
                        swing_start=swing_start,
                        swing_end=last_active,
                    )
                    self._events.append(event)
                    logger.info(
                        "[SWING_SPEED] Swing detected: peak=%.1f mph, readings=%d",
                        event.peak_speed_mph,
                        event.reading_count,
                    )
                    if self._event_callback:
                        self._event_callback(event)

                    active_readings = []
                    cooldown_until = now + self.cooldown_ms / 1000.0

                time.sleep(self.poll_interval_ms / 1000.0)
            except Exception as exc:  # pylint: disable=broad-except
                logger.error("[SWING_SPEED] Capture loop error: %s", exc, exc_info=True)
                log_session_error(
                    "Swing speed capture loop error",
                    component="swing_speed_monitor",
                    exc=exc,
                )
                time.sleep(1.0)

    def _select_swing_reading(self, readings: List[SpeedReading]) -> Optional[SpeedReading]:
        """Select the fastest outbound candidate for swing speed training."""
        outbound = [
            reading
            for reading in readings
            if reading
            and reading.direction == Direction.OUTBOUND
            and reading.speed >= self.trigger_threshold_mph
            and self._within_max_speed(reading.speed)
        ]
        if not outbound:
            return None
        return max(outbound, key=lambda reading: reading.speed)

    def _within_max_speed(self, speed_mph: float) -> bool:
        """Return true when a speed is below the configured plausible ceiling."""
        return self.max_speed_mph is None or speed_mph <= self.max_speed_mph

    def _build_event(
        self,
        readings: List[SpeedReading],
        trigger_speed: float,
        swing_start: float,
        swing_end: float,
    ) -> SwingSpeedEvent:
        """Create a SwingSpeedEvent from the completed swing window."""
        peak = max(readings, key=lambda item: item.speed)
        peak_magnitude = max(
            (reading.magnitude for reading in readings if reading.magnitude is not None),
            default=None,
        )
        return SwingSpeedEvent(
            peak_speed_mph=peak.speed,
            timestamp=datetime.fromtimestamp(peak.timestamp or time.time()),
            duration_ms=max(0.0, (swing_end - swing_start) * 1000.0),
            reading_count=len(readings),
            trigger_speed_mph=trigger_speed,
            peak_magnitude=peak_magnitude,
            training_implement=self.training_implement,
            training_implement_label=self.training_implement_label,
            unit=peak.unit,
        )
