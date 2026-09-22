"""
Trigger strategies for rolling buffer capture.

Defines different methods for determining when to capture the rolling buffer.
"""

import logging
import math
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, List, Optional

from .processor import RollingBufferProcessor
from .types import IQCapture

if TYPE_CHECKING:
    from ..ops243 import OPS243Radar

# Incremented per capture for log sequencing (not shot number)

logger = logging.getLogger("openflight.rolling_buffer.trigger")


class TriggerStrategy(ABC):
    """
    Base class for trigger strategies.

    A trigger strategy determines when to capture the rolling buffer.
    Different strategies trade off between simplicity, reliability, and efficiency.
    """

    MIN_VALID_OUTBOUND_MPH = 15.0

    def __init__(self, pre_trigger_segments: int = 12):
        self._diagnostics: List[dict] = []
        self.pre_trigger_segments = pre_trigger_segments

    def drain_diagnostics(self) -> List[dict]:
        """Return and clear accumulated diagnostic entries.

        Diagnostics are accumulated during wait_for_trigger() calls.
        The monitor drains these after each call to log and emit them.
        """
        diagnostics = self._diagnostics
        self._diagnostics = []
        return diagnostics

    def _append_diagnostic(
        self,
        accepted: bool,
        reason: str,
        response_bytes: int = 0,
        total_readings: int = 0,
        outbound_readings: int = 0,
        inbound_readings: int = 0,
        peak_outbound_mph: float = 0.0,
        peak_inbound_mph: float = 0.0,
        all_outbound_speeds: Optional[List[float]] = None,
        all_inbound_speeds: Optional[List[float]] = None,
        peak_outbound_magnitude: float = 0.0,
        peak_inbound_magnitude: float = 0.0,
        trigger_latency_ms: Optional[float] = None,
    ):
        """Append a diagnostic entry for the current trigger event."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "accepted": accepted,
            "reason": reason,
            "response_bytes": response_bytes,
            "total_readings": total_readings,
            "outbound_readings": outbound_readings,
            "inbound_readings": inbound_readings,
            "peak_outbound_mph": peak_outbound_mph,
            "peak_inbound_mph": peak_inbound_mph,
            "all_outbound_speeds": all_outbound_speeds or [],
            "all_inbound_speeds": all_inbound_speeds or [],
            "peak_outbound_magnitude": peak_outbound_magnitude,
            "peak_inbound_magnitude": peak_inbound_magnitude,
        }
        if trigger_latency_ms is not None:
            entry["trigger_latency_ms"] = trigger_latency_ms
        self._diagnostics.append(entry)

    def _summarize_capture_activity(
        self,
        processor: RollingBufferProcessor,
        capture: IQCapture,
    ) -> dict:
        """Summarize movement in a capture before accepting a sound trigger."""
        timeline = processor.process_standard(capture)
        all_readings = timeline.readings
        all_outbound = [r for r in all_readings if r.is_outbound]
        all_inbound = [r for r in all_readings if not r.is_outbound]
        outbound_speeds = [r.speed_mph for r in all_outbound]
        inbound_speeds = [r.speed_mph for r in all_inbound]
        valid_outbound = [r for r in all_outbound if r.speed_mph >= self.MIN_VALID_OUTBOUND_MPH]

        return {
            "total_readings": len(all_readings),
            "outbound_readings": len(all_outbound),
            "inbound_readings": len(all_inbound),
            "peak_outbound_mph": max(outbound_speeds, default=0),
            "peak_inbound_mph": max(inbound_speeds, default=0),
            "all_outbound_speeds": outbound_speeds,
            "all_inbound_speeds": inbound_speeds,
            "peak_outbound_magnitude": max((r.magnitude for r in all_outbound), default=0),
            "peak_inbound_magnitude": max((r.magnitude for r in all_inbound), default=0),
            "valid_outbound_count": len(valid_outbound),
            "valid_peak_outbound_mph": max(
                (r.speed_mph for r in valid_outbound),
                default=0,
            ),
        }

    def _append_activity_diagnostic(
        self,
        summary: dict,
        *,
        accepted: bool,
        reason: str,
        response_bytes: int,
        trigger_latency_ms: Optional[float] = None,
    ):
        """Append a diagnostic entry using capture-activity summary fields."""
        self._append_diagnostic(
            accepted=accepted,
            reason=reason,
            response_bytes=response_bytes,
            total_readings=summary["total_readings"],
            outbound_readings=summary["outbound_readings"],
            inbound_readings=summary["inbound_readings"],
            peak_outbound_mph=summary["peak_outbound_mph"],
            peak_inbound_mph=summary["peak_inbound_mph"],
            all_outbound_speeds=summary["all_outbound_speeds"],
            all_inbound_speeds=summary["all_inbound_speeds"],
            peak_outbound_magnitude=summary["peak_outbound_magnitude"],
            peak_inbound_magnitude=summary["peak_inbound_magnitude"],
            trigger_latency_ms=trigger_latency_ms,
        )

    @abstractmethod
    def wait_for_trigger(
        self,
        radar: "OPS243Radar",
        processor: RollingBufferProcessor,
        timeout: float = 30.0,
    ) -> Optional[IQCapture]:
        """
        Wait for trigger condition and capture buffer.

        Args:
            radar: Connected OPS243Radar instance in rolling buffer mode
            processor: Processor for parsing capture response
            timeout: Maximum time to wait for trigger

        Returns:
            IQCapture if triggered and captured, None if timeout or error
        """
        pass

    @abstractmethod
    def reset(self):
        """Reset trigger state for next capture."""
        pass


class SpeedTriggeredCapture(TriggerStrategy):
    """
    Speed-triggered rolling buffer capture per OmniPreSense recommendation.

    This implements the manufacturer's recommended approach for golf:
    1. Run in fast speed detection mode (~150-200Hz report rate)
    2. When outbound speed >20mph detected, immediately switch to rolling buffer
    3. Capture ball impact and flight with S#0 (no pre-trigger history)

    Advantages over polling:
    - Much faster trigger response (~5-6ms vs 300ms+ polling)
    - Captures club speed in speed mode, ball in rolling buffer
    - Minimal data loss during mode switch

    Per manufacturer:
    "You'll lose a little data (~5-6ms) from the initial club speed detection
    time while the sensor determines there's probably a golf swing event to
    capture with the Rolling Buffer. But assuming the club speed detected to
    ball impact is around 20-40ms, that should be ok."
    """

    def __init__(
        self,
        min_trigger_speed_mph: float = 20.0,
        min_ball_speed_mph: float = 35.0,
        trigger_to_capture_delay_ms: float = 15.0,
        pre_trigger_segments: int = 0,  # Speed trigger defaults to 0 (no pre-trigger)
    ):
        """
        Initialize speed-triggered capture.

        Args:
            min_trigger_speed_mph: Minimum speed to trigger capture (default 20mph)
            min_ball_speed_mph: Minimum ball speed to consider valid shot (default 35mph)
            trigger_to_capture_delay_ms: Delay after trigger before capture (default 15ms)
                This allows ball impact to happen before we dump the buffer.
            pre_trigger_segments: Pre-trigger segments (default 0 for speed trigger)
        """
        super().__init__(pre_trigger_segments=pre_trigger_segments)
        self.min_trigger_speed_mph = min_trigger_speed_mph
        self.min_ball_speed_mph = min_ball_speed_mph
        self.trigger_to_capture_delay_ms = trigger_to_capture_delay_ms
        self._last_trigger_speed: float = 0
        self._needs_reconfigure = True

    def wait_for_trigger(
        self,
        radar: "OPS243Radar",
        processor: RollingBufferProcessor,
        timeout: float = 30.0,
    ) -> Optional[IQCapture]:
        """
        Wait for speed trigger, switch to rolling buffer, and capture.

        Flow:
        1. Configure radar for fast speed detection (if needed)
        2. Poll for speed readings at ~150-200Hz
        3. When speed >= threshold detected:
           a. Record club speed
           b. Switch to rolling buffer mode (GC + S#0)
           c. Wait for ball impact (~15-25ms)
           d. Trigger capture (S!)
        4. Return to speed detection mode for next shot
        """
        # Configure for speed trigger mode if needed
        if self._needs_reconfigure:
            radar.configure_for_speed_trigger()
            self._needs_reconfigure = False
            # Clear any buffered data from mode switch
            if radar.serial:
                radar.serial.reset_input_buffer()
            time.sleep(0.1)

        start_time = time.time()
        logger.info(
            "[TRIGGER] Waiting for speed trigger >= %.1f mph...", self.min_trigger_speed_mph
        )

        while (time.time() - start_time) < timeout:
            # Non-blocking speed read
            reading = radar.read_speed_nonblocking()

            if reading and reading.speed >= self.min_trigger_speed_mph:
                # Speed detected - this is likely the club
                self._last_trigger_speed = reading.speed
                trigger_time = time.time()

                logger.info(
                    "[TRIGGER] Speed trigger: %.1f mph %s detected, switching to rolling buffer...",
                    reading.speed,
                    reading.direction.value,
                )

                # Immediately switch to rolling buffer mode
                radar.switch_to_rolling_buffer()

                # Wait for ball impact
                # Club to ball is typically 20-40ms, we wait a portion of that
                delay_sec = self.trigger_to_capture_delay_ms / 1000.0
                time.sleep(delay_sec)

                # Capture the rolling buffer
                response = radar.trigger_capture(timeout=5.0)
                capture = processor.parse_capture(response)

                # Calculate timing
                capture_time = time.time()
                total_delay_ms = (capture_time - trigger_time) * 1000
                logger.info("[TRIGGER] Buffer captured %.1fms after trigger", total_delay_ms)

                if capture:
                    # Validate capture has ball speed
                    timeline = processor.process_standard(capture)
                    outbound = [
                        r
                        for r in timeline.readings
                        if r.is_outbound and r.speed_mph >= self.min_ball_speed_mph
                    ]

                    if outbound:
                        peak = max(r.speed_mph for r in outbound)
                        logger.info("[TRIGGER] Ball detected: %.1f mph in capture", peak)

                        # Mark for reconfigure on next call
                        self._needs_reconfigure = True
                        return capture
                    else:
                        logger.info(
                            "[TRIGGER] No speed >= %.1f mph in capture", self.min_ball_speed_mph
                        )

                # Reconfigure for speed mode and continue
                self._needs_reconfigure = True
                radar.configure_for_speed_trigger()
                if radar.serial:
                    radar.serial.reset_input_buffer()

            # Brief sleep to avoid busy-waiting but stay responsive
            time.sleep(0.002)  # 2ms = 500Hz poll rate

        logger.info("[TRIGGER] Speed trigger timeout")
        return None

    def reset(self):
        """Reset trigger state and mark for reconfiguration."""
        self._last_trigger_speed = 0
        self._needs_reconfigure = True

    @property
    def last_trigger_speed(self) -> float:
        """Get the speed that triggered the last capture (likely club speed)."""
        return self._last_trigger_speed


class SoundTrigger(TriggerStrategy):
    """
    Hardware sound trigger using SparkFun SEN-14262.

    IMPORTANT: Rolling buffer mode must be configured BEFORE using this trigger.
    Call radar.configure_for_rolling_buffer() or radar.enter_rolling_buffer_mode()
    before calling wait_for_trigger().

    Wiring: SEN-14262 GATE → OPS243-A J3 Pin 3 (HOST_INT)
    The GATE output goes HIGH on loud sound (club impact).
    OPS243-A uses rising edge detection on HOST_INT as trigger.

    No software trigger (S!) needed — the radar triggers itself
    via hardware. We just need to wait for data to appear on serial.

    """

    CLOCK_SYNC_SAMPLES = 36
    CLOCK_SYNC_MAX_ROLLOVER_UNCERTAINTY_MS = 40.0
    CLOCK_SYNC_MAX_TIMEOUT_READ_MS = 50.0
    CLOCK_SYNC_MAX_FALLBACK_AGE_S = 60.0

    def __init__(
        self,
        pre_trigger_segments: int = 12,
    ):
        """
        Initialize sound trigger.

        Args:
            pre_trigger_segments: Number of pre-trigger segments for S# command.
                Each segment = 128 samples = ~4.27ms at 30ksps.
                Default 12 gives ~51ms pre-trigger, ~85ms post-trigger.
                NOTE: This is passed to enter_rolling_buffer_mode() by the caller.
                The trigger does NOT configure rolling buffer mode itself.
        """
        super().__init__(pre_trigger_segments=pre_trigger_segments)

    @staticmethod
    def _clock_sync_last_read_host_time(clock_sync: dict) -> Optional[float]:
        """Return the host time of the last C? read in a clock-sync summary."""
        reads = clock_sync.get("reads") or []
        if not reads or not isinstance(reads[-1], dict):
            return None
        return reads[-1].get("host_after") or reads[-1].get("host_mid")

    @classmethod
    def _clock_sync_age_s(cls, clock_sync: dict) -> Optional[float]:
        """Return age in seconds for a clock-sync summary."""
        last_host_time = cls._clock_sync_last_read_host_time(clock_sync)
        if last_host_time is None:
            return None
        try:
            return time.time() - float(last_host_time)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _clock_sync_quality(cls, clock_sync: object) -> tuple[bool, str]:
        """Return whether a clock sync is trustworthy enough for shot timing."""
        if not isinstance(clock_sync, dict):
            return False, "missing"

        if not clock_sync.get("usable_for_trigger_timestamps"):
            return False, f"unusable_method:{clock_sync.get('clock_sync_method', 'unknown')}"

        if clock_sync.get("best_offset_s") is None:
            return False, "missing_best_offset"

        reads = clock_sync.get("reads") or []
        slow_invalid_reads = [
            read
            for read in reads
            if isinstance(read, dict)
            and read.get("radar_clock_s") is None
            and (read.get("read_latency_ms") or 0.0) >= cls.CLOCK_SYNC_MAX_TIMEOUT_READ_MS
        ]
        if slow_invalid_reads:
            return False, f"timeout_reads:{len(slow_invalid_reads)}"

        method = clock_sync.get("clock_sync_method")
        if method == "integer_rollover":
            uncertainty = clock_sync.get("rollover_uncertainty_ms")
            if uncertainty is None:
                return False, "missing_rollover_uncertainty"
            try:
                if float(uncertainty) > cls.CLOCK_SYNC_MAX_ROLLOVER_UNCERTAINTY_MS:
                    return False, f"rollover_uncertainty:{float(uncertainty):.1f}ms"
            except (TypeError, ValueError):
                return False, "invalid_rollover_uncertainty"
            return True, "valid_integer_rollover"

        if method == "fractional_clock":
            return True, "valid_fractional_clock"

        return False, f"unsupported_method:{method or 'unknown'}"

    @classmethod
    def _clock_sync_summary_for_log(cls, clock_sync: object) -> Optional[dict]:
        """Return a compact JSONL-safe summary of a clock-sync candidate."""
        if not isinstance(clock_sync, dict):
            return None
        valid, reason = cls._clock_sync_quality(clock_sync)
        return {
            "valid": valid,
            "reason": reason,
            "source": clock_sync.get("source"),
            "samples": clock_sync.get("samples"),
            "valid_samples": clock_sync.get("valid_samples"),
            "clock_sync_method": clock_sync.get("clock_sync_method"),
            "best_offset_s": clock_sync.get("best_offset_s"),
            "raw_best_offset_s": clock_sync.get("raw_best_offset_s"),
            "best_read_latency_ms": clock_sync.get("best_read_latency_ms"),
            "offset_spread_ms": clock_sync.get("offset_spread_ms"),
            "rollover_uncertainty_ms": clock_sync.get("rollover_uncertainty_ms"),
            "age_s": (
                round(cls._clock_sync_age_s(clock_sync), 3)
                if cls._clock_sync_age_s(clock_sync) is not None
                else None
            ),
        }

    def _select_clock_sync_for_capture(
        self,
        radar: "OPS243Radar",
        capture: IQCapture,
    ) -> Optional[dict]:
        """Choose and apply the best OPS clock sync for this capture."""
        previous_sync = getattr(radar, "last_clock_sync", None)
        previous_valid, previous_reason = self._clock_sync_quality(previous_sync)
        previous_age_s = (
            self._clock_sync_age_s(previous_sync) if isinstance(previous_sync, dict) else None
        )

        fresh_sync = None
        fresh_error = None
        if hasattr(radar, "read_clock_sync"):
            try:
                fresh_sync = radar.read_clock_sync(
                    samples=self.CLOCK_SYNC_SAMPLES,
                    store=False,
                )
                if isinstance(fresh_sync, dict):
                    fresh_sync["source"] = "per_shot"
            except Exception as exc:  # pylint: disable=broad-except
                fresh_error = str(exc)
                logger.warning("[TRIGGER] Per-shot OPS clock sync failed: %s", exc, exc_info=True)

        fresh_valid, fresh_reason = self._clock_sync_quality(fresh_sync)

        selected_sync = None
        selected_source = "first_byte"
        selected_reason = "no_valid_clock_sync"

        if fresh_valid and isinstance(fresh_sync, dict):
            selected_sync = fresh_sync
            selected_source = "fresh"
            selected_reason = fresh_reason
            radar.last_clock_sync = fresh_sync
        elif (
            previous_valid
            and isinstance(previous_sync, dict)
            and previous_age_s is not None
            and previous_age_s <= self.CLOCK_SYNC_MAX_FALLBACK_AGE_S
        ):
            selected_sync = previous_sync
            selected_source = "previous"
            selected_reason = f"fresh_rejected:{fresh_reason};previous_age:{previous_age_s:.1f}s"
        elif previous_valid and previous_age_s is not None:
            selected_reason = (
                f"fresh_rejected:{fresh_reason};previous_too_old:{previous_age_s:.1f}s"
            )
        elif previous_reason != "missing":
            selected_reason = f"fresh_rejected:{fresh_reason};previous_rejected:{previous_reason}"
        elif fresh_error:
            selected_reason = f"fresh_error:{fresh_error}"
        else:
            selected_reason = f"fresh_rejected:{fresh_reason}"

        selected_offset_s = None
        selected_age_s = None
        if isinstance(selected_sync, dict):
            selected_offset_s = selected_sync.get("best_offset_s")
            selected_age_s = self._clock_sync_age_s(selected_sync)
            try:
                capture.apply_trigger_timestamp_from_clock_sync(float(selected_offset_s))
            except (TypeError, ValueError):
                logger.warning(
                    "[TRIGGER] Ignoring invalid selected OPS clock-sync offset: %r",
                    selected_offset_s,
                )
                selected_sync = None
                selected_source = "first_byte"
                selected_reason = "selected_offset_invalid"
                selected_offset_s = None

        previous_offset_s = (
            previous_sync.get("best_offset_s") if isinstance(previous_sync, dict) else None
        )
        fresh_offset_s = fresh_sync.get("best_offset_s") if isinstance(fresh_sync, dict) else None

        selection_log = {
            "selection": selected_source,
            "selection_reason": selected_reason,
            "selected_offset_s": selected_offset_s,
            "selected_age_s": round(selected_age_s, 3) if selected_age_s is not None else None,
            "fresh": self._clock_sync_summary_for_log(fresh_sync),
            "previous": self._clock_sync_summary_for_log(previous_sync),
            "fresh_error": fresh_error,
            "fresh_delta_from_previous_ms": (
                round((fresh_offset_s - previous_offset_s) * 1000.0, 3)
                if fresh_offset_s is not None and previous_offset_s is not None
                else None
            ),
        }

        if selected_sync is not None:
            logger.info(
                "[TRIGGER] OPS clock sync selected: %s offset=%.6fs age=%sms "
                "(fresh=%s, previous=%s, delta=%sms)",
                selected_source,
                selected_offset_s,
                "n/a" if selected_age_s is None else f"{selected_age_s:.1f}",
                fresh_reason,
                previous_reason,
                "n/a"
                if selection_log["fresh_delta_from_previous_ms"] is None
                else f"{selection_log['fresh_delta_from_previous_ms']:.1f}",
            )
        else:
            logger.info(
                "[TRIGGER] OPS clock sync fallback to first-byte timing: %s",
                selected_reason,
            )

        return selection_log

    def wait_for_trigger(
        self,
        radar: "OPS243Radar",
        processor: RollingBufferProcessor,
        timeout: float = 30.0,
        cancel_event: Optional[threading.Event] = None,
        capture_started_callback: Optional[Callable[[], None]] = None,
    ) -> Optional[IQCapture]:
        """
        Wait for hardware sound trigger and capture buffer.

        PREREQUISITE: Rolling buffer mode must already be configured via
        radar.configure_for_rolling_buffer() or radar.enter_rolling_buffer_mode().

        Unlike other triggers, no S! command is sent. The radar's
        HOST_INT pin receives the trigger from the SEN-14262 GATE
        output, causing the radar to dump its rolling buffer automatically.
        We just block on serial read waiting for the I/Q data to arrive.
        """
        logger.info("[TRIGGER] Waiting for sound trigger (timeout=%.0fs)...", timeout)

        response = radar.wait_for_hardware_trigger(
            timeout=timeout,
            cancel_event=cancel_event,
            on_first_byte=capture_started_callback,
        )

        if not response:
            logger.info("[TRIGGER] Sound trigger timeout — no hardware trigger received")
            return None

        response_len = len(response)
        logger.info("[TRIGGER] Sound trigger fired, %d bytes received", response_len)
        first_byte_timestamp = getattr(
            radar,
            "last_hardware_trigger_first_byte_timestamp",
            None,
        )

        # ORDERING MATTERS: after its dump the radar sits idle, where
        # HOST_INT pulses are harmless. A real shot produces a second loud
        # sound ~1s later (ball hitting the net); if we re-arm first, that
        # sound starts a new dump exactly when the clock-sync exchange
        # writes to the port — a two-way serial deadlock observed in the
        # field (capture thread wedged in serial.write, radar frozen
        # mid-dump). So: do all wire-talk (clock sync) while idle, and
        # re-arm LAST, when we are about to go back to reading.

        capture = processor.parse_capture(
            response,
            first_byte_timestamp=first_byte_timestamp,
        )

        if not capture:
            radar.rearm_rolling_buffer(self.pre_trigger_segments)
            logger.warning("[TRIGGER] Sound trigger parse failed (%d bytes received)", response_len)
            self._append_diagnostic(
                accepted=False,
                reason="parse_failed",
                response_bytes=response_len,
            )
            return None

        if first_byte_timestamp is not None and capture.first_byte_timestamp is None:
            capture.first_byte_timestamp = float(first_byte_timestamp)

        # Quick validation: does the capture contain any real swing data?
        # At a driving range, a nearby player's impact sound can trip the
        # trigger even though nothing was moving in front of our radar.
        # Discard these false triggers immediately so we re-arm fast.
        summary = self._summarize_capture_activity(processor, capture)

        if not summary["valid_outbound_count"]:
            # False trigger: re-arm immediately (no clock sync) so the
            # next real swing isn't missed.
            radar.rearm_rolling_buffer(self.pre_trigger_segments)
            logger.info(
                "[TRIGGER] Sound trigger rejected — no outbound speed >= %.0f mph "
                "(peak=%.1f mph, %d readings)",
                self.MIN_VALID_OUTBOUND_MPH,
                summary["peak_outbound_mph"],
                summary["total_readings"],
            )
            self._append_activity_diagnostic(
                summary,
                accepted=False,
                reason="no_outbound_speed",
                response_bytes=response_len,
            )
            return None

        # Accepted: talk on the wire while the radar is still idle, then
        # re-arm as the last serial action before returning to the reader.
        self._select_clock_sync_for_capture(radar, capture)
        radar.rearm_rolling_buffer(self.pre_trigger_segments)

        if capture.first_byte_timestamp is not None and capture.trigger_timestamp is None:
            capture.apply_trigger_timestamp_from_first_byte()

        if capture.trigger_timestamp is not None and capture.first_byte_timestamp is not None:
            logger.info(
                "[TRIGGER] Sound trigger wall time %.3f "
                "(source=%s, first byte %.3f, post-trigger %.1fms)",
                capture.trigger_timestamp,
                capture.trigger_timestamp_source or "unknown",
                capture.first_byte_timestamp,
                capture.post_trigger_duration_ms,
            )

        logger.info(
            "[TRIGGER] Sound trigger accepted — peak %.1f mph, %d outbound readings",
            summary["valid_peak_outbound_mph"],
            summary["valid_outbound_count"],
        )
        self._append_activity_diagnostic(
            summary,
            accepted=True,
            reason="accepted",
            response_bytes=response_len,
        )

        return capture

    def reset(self):
        """Reset trigger state."""
        pass  # No state to reset


class HardwareTriggeredCapture(TriggerStrategy):
    """Capture using the OPS243's internal speed trigger."""

    def __init__(
        self,
        trigger_threshold_mph: float = 25.0,
        min_ball_speed_mph: float = 35.0,
        pre_trigger_segments: int = 6,
        trigger_magnitude: int = 25,
        sample_rate_ksps: int = 30,
    ):
        super().__init__(pre_trigger_segments=pre_trigger_segments)

        threshold = float(trigger_threshold_mph)
        if not math.isfinite(threshold) or threshold < 0:
            raise ValueError("Trigger threshold must be non-negative")
        if not isinstance(pre_trigger_segments, int) or not 0 <= pre_trigger_segments <= 32:
            raise ValueError("Pre-trigger segments must be an integer from 0 to 32")
        if not isinstance(trigger_magnitude, int) or not 1 <= trigger_magnitude <= 2000:
            raise ValueError("Trigger magnitude must be between 1 and 2000")
        if sample_rate_ksps != 30:
            raise ValueError("Internal speed trigger requires a 30 ksps sample rate")

        self.trigger_threshold_mph = threshold
        self.min_ball_speed_mph = float(min_ball_speed_mph)
        self.trigger_magnitude = trigger_magnitude
        self.sample_rate_ksps = sample_rate_ksps

    def wait_for_trigger(
        self,
        radar: "OPS243Radar",
        processor: RollingBufferProcessor,
        timeout: float = 30.0,
    ) -> Optional[IQCapture]:
        """Wait for one internal-trigger dump and return a valid capture."""
        logger.info(
            "[TRIGGER] Waiting for OPS hardware trigger >= %.1f mph (timeout=%.0fs)...",
            self.trigger_threshold_mph,
            timeout,
        )

        response = radar.wait_for_hardware_trigger(timeout=timeout)
        if not response:
            logger.info("[TRIGGER] OPS hardware trigger timeout — no dump received")
            return None

        response_bytes = len(response)
        first_byte_timestamp = getattr(
            radar,
            "last_hardware_trigger_first_byte_timestamp",
            None,
        )
        capture = None
        parse_error = None
        started_at = time.time()
        try:
            capture = processor.parse_capture(
                response,
                first_byte_timestamp=first_byte_timestamp,
            )
        except Exception as error:  # pylint: disable=broad-exception-caught
            parse_error = error
            logger.warning("[TRIGGER] Hardware capture parse failed: %s", error, exc_info=True)
        finally:
            try:
                rearmed = radar.rearm_internal_speed_trigger(self.sample_rate_ksps)
            except Exception as error:  # pylint: disable=broad-exception-caught
                rearmed = False
                logger.warning("[TRIGGER] Internal trigger re-arm failed: %s", error, exc_info=True)

        trigger_latency_ms = (time.time() - started_at) * 1000.0
        if not rearmed:
            logger.warning("[TRIGGER] Hardware capture retained while radar re-arm is pending")

        if capture is None or parse_error is not None:
            self._append_diagnostic(
                accepted=False,
                reason="parse_failed",
                response_bytes=response_bytes,
                trigger_latency_ms=trigger_latency_ms,
            )
            return None

        summary = self._summarize_capture_activity(processor, capture)
        valid_outbound = [
            speed for speed in summary["all_outbound_speeds"] if speed >= self.min_ball_speed_mph
        ]
        if not valid_outbound:
            self._append_activity_diagnostic(
                summary,
                accepted=False,
                reason="no_ball_speed",
                response_bytes=response_bytes,
                trigger_latency_ms=trigger_latency_ms,
            )
            logger.info(
                "[TRIGGER] OPS hardware capture rejected — no outbound speed >= %.1f mph",
                self.min_ball_speed_mph,
            )
            return None

        logger.info(
            "[TRIGGER] OPS hardware capture accepted — peak %.1f mph",
            max(valid_outbound),
        )
        return capture

    def reset(self):
        """Reset trigger state; the radar owns the armed state."""
        pass


def create_trigger(trigger_type: str = "sound", **kwargs) -> TriggerStrategy:
    """
    Factory function to create trigger strategy.

    Args:
        trigger_type: "sound" (production), "hardware", or "speed" (fallback)
        **kwargs: Arguments passed to trigger constructor

    Returns:
        Configured TriggerStrategy instance

    Trigger types:
        - "sound": Hardware sound trigger via SparkFun SEN-14262 GATE → HOST_INT.
                   Requires GATE voltage to reach 3.3V threshold.
        - "speed": Fast speed detection triggers rolling buffer capture.
                   Recommended fallback by OmniPreSense. ~5-6ms response time.
        - "hardware": OPS243 internal speed trigger with rolling-buffer capture.
    """
    triggers = {
        "hardware": HardwareTriggeredCapture,
        "speed": SpeedTriggeredCapture,
        "sound": SoundTrigger,
    }

    if trigger_type not in triggers:
        raise ValueError(
            f"Unknown trigger type: {trigger_type}. Available: {list(triggers.keys())}"
        )

    return triggers[trigger_type](**kwargs)
