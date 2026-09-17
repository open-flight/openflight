"""K-LD7 angle radar tracker with ring buffer for shot correlation.

Deprecated: the K-LD7 angle radars are deprecated (superseded by a more
capable radar chip). Kept for existing builds only.
"""

import glob
import logging
import threading
import time
from collections import deque
from importlib.util import find_spec
from pathlib import Path
from typing import Optional

from ..clubs import ClubType
from ..serial_latency import log_usb_serial_latency_timer
from .radc import VERTICAL_FLIGHT_WINDOW_NET_DISTANCE_FT
from .types import KLD7Angle, KLD7Frame

logger = logging.getLogger(__name__)

_RADC_SELECTION_DIAGNOSTIC_KEYS = (
    "estimator",
    "selection_path",
    "selected_frame_indices",
    "selected_t_ms",
    "selected_bin_errors",
    "geom_fit_rmse_deg",
    "geom_single_frame_resid_deg",
    "weak_adjacent_frame_used",
    "dc_blind_zone",
    "fringe_metrics",
    "raw_angle_deg",
    "angle_offset_deg",
    "spectrum_source",
    "ball_speed_mph",
    "avg_snr_db",
    "angle_std_deg",
    "detection_count",
    "frame_count",
    "impact_frames",
    "confidence",
)


def _radc_selection_diagnostics(best: dict, *, relaxed_retry: bool) -> dict:
    """Return compact, JSON-safe RADC selection details for session replay."""
    diagnostics = {key: best.get(key) for key in _RADC_SELECTION_DIAGNOSTIC_KEYS if key in best}
    diagnostics["relaxed_retry"] = relaxed_retry
    return diagnostics


def _log_session_error(
    error: str,
    *,
    context: Optional[dict] = None,
    component: Optional[str] = None,
    exc: Optional[BaseException] = None,
) -> None:
    """Log to session JSONL without importing session_logger at module load."""
    from ..session_logger import log_session_error

    log_session_error(error, context=context, component=component, exc=exc)


def _is_recoverable_stream_error(error: BaseException) -> bool:
    """Return True for transient serial failures seen during live K-LD7 streaming."""
    message = str(error).lower()
    recoverable_fragments = (
        "serial read failed",
        "device reports readiness to read but returned no data",
        "multiple access on port",
        "device disconnected",
        "timeout waiting for reply",
        "short header read",
        "short payload read",
        "failed to read",
        "wrong length reply",
    )
    return any(fragment in message for fragment in recoverable_fragments)


def _find_port():
    """Auto-detect K-LD7 EVAL board USB serial port."""
    try:
        from serial.tools.list_ports import comports
    except ImportError:
        return None
    for port in comports():
        desc = (port.description or "").lower()
        mfg = (port.manufacturer or "").lower()
        if any(kw in desc for kw in ["ftdi", "cp210", "usb-serial", "uart"]):
            return port.device
        if any(kw in mfg for kw in ["ftdi", "silicon labs"]):
            return port.device
    return None


def _available_serial_device_summary() -> str:
    """Return a compact list of serial devices useful for K-LD7 port diagnostics."""
    candidates = []
    for pattern in ("/dev/kld7_*", "/dev/ttyUSB*", "/dev/serial/by-id/*"):
        candidates.extend(sorted(glob.glob(pattern)))
    if not candidates:
        return "none found"
    return ", ".join(candidates)


def _resolved_serial_port(port: str) -> str:
    """Resolve aliases like /dev/kld7_horizontal to the underlying serial device."""
    try:
        return str(Path(port).resolve(strict=False))
    except Exception:
        return str(port)


class KLD7Tracker:
    """
    K-LD7 angle radar tracker.

    Streams RADC frames in a background thread into a ring buffer.
    When the OPS243 detects a shot, call get_angle_for_shot() to search
    the buffer for the ball pass and extract angle data via phase interferometry.
    """

    # Class-level defaults so __new__-constructed instances (tests) don't
    # fail with AttributeError when code accesses these.
    angle_offset_deg = 0.0
    base_freq = 0
    shot_window_after_s = 0.75
    radc_speed_tolerance_mph = 10.0
    radc_centroid_floor_frac = 0.5
    radc_spectrum_source = "f1a"
    radc_ops_bin_outlier_tol = 25
    radc_ops_bin_outlier_penalty = 10.0
    radc_ops_anchored_peak_min_snr = 5.0
    radc_vertical_impact_energy_threshold = 3.0
    radc_horizontal_impact_energy_threshold = 1.85
    radc_horizontal_retry_impact_energy_threshold = 0.5
    radc_horizontal_angle_limit_deg = 15.0
    vertical_estimator = "naive"
    mount_tilt_deg = 18.0
    ball_distance_ft = 5.5
    vertical_flight_window_net_distance_ft = VERTICAL_FLIGHT_WINDOW_NET_DISTANCE_FT

    def __init__(
        self,
        port: Optional[str] = None,
        range_m: int = 5,
        speed_kmh: int = 100,
        orientation: str = "vertical",
        buffer_seconds: float = 2.0,
        angle_offset_deg: float = 0.0,
        base_freq: int = 0,
        vertical_estimator: str = "naive",
        mount_tilt_deg: float = 18.0,
        ball_distance_ft: float = 5.5,
        vertical_flight_window_net_distance_ft: float = VERTICAL_FLIGHT_WINDOW_NET_DISTANCE_FT,
    ):
        self.port = port
        self.range_m = range_m
        self.speed_kmh = speed_kmh
        self.orientation = orientation
        self.buffer_seconds = buffer_seconds
        self.angle_offset_deg = angle_offset_deg
        self.base_freq = base_freq
        self.vertical_estimator = vertical_estimator
        self.mount_tilt_deg = mount_tilt_deg
        self.ball_distance_ft = ball_distance_ft
        self.vertical_flight_window_net_distance_ft = vertical_flight_window_net_distance_ft
        self.max_buffer_frames = int(34 * buffer_seconds)

        self._radar = None
        self._stream_thread: Optional[threading.Thread] = None
        self._running = False
        self._init_ring_buffer()

    def _init_ring_buffer(self):
        """Initialize or reset the ring buffer."""
        # Guards the buffer against the stream thread appending while the
        # shot path iterates it (deque iteration raises RuntimeError if the
        # deque is mutated mid-iteration). Readers copy under the lock and
        # do any per-frame work on the copy.
        self._buffer_lock = threading.Lock()
        self._ring_buffer: deque[KLD7Frame] = deque(maxlen=self.max_buffer_frames)

    def connect(self) -> bool:
        """Connect to K-LD7 and configure for golf."""
        if find_spec("kld7") is None:
            logger.error(
                "[KLD7] kld7 package not installed. Reinstall the project: "
                "uv pip install -e '.[ui]'"
            )
            return False

        port = self.port or _find_port()
        if not port:
            logger.error("[KLD7] No K-LD7 EVAL board detected")
            return False
        if str(port).startswith("/dev/") and not Path(port).exists():
            logger.error(
                "[KLD7] Configured K-LD7 port does not exist: %s. "
                "Available serial devices: %s. "
                "Check udev aliases in /etc/udev/rules.d/99-kld7.rules or pass "
                "--kld7-port/--kld7-horizontal-port with the current /dev/ttyUSB* device.",
                port,
                _available_serial_device_summary(),
            )
            return False
        resolved_port = _resolved_serial_port(str(port))
        log_usb_serial_latency_timer(logger, f"KLD7:{self.orientation}", resolved_port)

        # The kld7 library always opens at 115200, sends INIT to negotiate
        # up to 3Mbaud, then switches. If a prior session left the K-LD7 at
        # 3Mbaud (crashed before GBYE), the 115200-baud INIT is garbled
        # and the next command times out.
        #
        # `connect_with_recovery` retries with a GBYE-at-3Mbaud reset
        # between attempts, and applies the robust _read_packet patch.
        from .serial_io import connect_with_recovery

        try:
            self._radar = connect_with_recovery(port, baudrate=3000000, log=logger.info)
        except Exception:
            logger.error("[KLD7] Connection failed after retries — giving up", exc_info=True)
            return False
        actual_baud = (
            getattr(self._radar._port, "baudrate", "unknown")
            if hasattr(self._radar, "_port")
            else "unknown"
        )

        self._configure_for_golf()
        self.port = str(port)
        logger.info(
            "[KLD7] Ready: port=%s (resolved=%s), baud=%s, range=%dm, speed=%dkm/h, orientation=%s",
            port,
            resolved_port,
            actual_baud,
            self.range_m,
            self.speed_kmh,
            self.orientation,
        )
        return True

    def _configure_for_golf(self):
        """Configure K-LD7 parameters for golf ball detection."""
        range_settings = {5: 0, 10: 1, 30: 2, 100: 3}
        speed_settings = {12: 0, 25: 1, 50: 2, 100: 3}

        params = self._radar.params
        params.RRAI = range_settings.get(self.range_m, 0)
        params.RSPI = speed_settings.get(self.speed_kmh, 3)
        params.RBFR = self.base_freq
        params.DEDI = 2
        params.THOF = 10
        params.TRFT = 1
        params.MIAN = -90
        params.MAAN = 90
        params.MIRA = 0
        params.MARA = 100
        params.MISP = 0
        params.MASP = 100
        params.VISU = 0

        freq_labels = {0: "Low/24.05GHz", 1: "Mid/24.15GHz", 2: "High/24.25GHz"}
        logger.info(
            "[KLD7] Configured: range=%dm, speed=%dkm/h, orientation=%s, RBFR=%d (%s)",
            self.range_m,
            self.speed_kmh,
            self.orientation,
            self.base_freq,
            freq_labels.get(self.base_freq, "unknown"),
        )

    def start(self):
        """Start the background streaming thread."""
        if self._running:
            return
        self._running = True
        self._stream_thread = threading.Thread(target=self._stream_loop, daemon=True)
        self._stream_thread.start()
        logger.info("[KLD7] Streaming started (orientation=%s)", self.orientation)

    def stop(self):
        """Stop streaming and close connection."""
        self._running = False
        if self._stream_thread:
            self._stream_thread.join(timeout=5)
            self._stream_thread = None
        self._close_radar_safely()
        logger.info("[KLD7] Stopped")

    def _close_radar_safely(self):
        """Best-effort close that prevents the kld7 destructor from retrying a bad port."""
        if not self._radar:
            return
        try:
            self._radar.close()
        except Exception:
            pass
        try:
            self._radar._port = None
        except Exception:
            pass
        self._radar = None

    def _drain_after_stream_error(self) -> None:
        """Best-effort serial drain after a recoverable stream error."""
        try:
            self._radar._port.reset_input_buffer()
        except Exception:
            pass
        try:
            self._radar._drain_serial()
        except Exception:
            pass
        time.sleep(0.1)
        try:
            self._radar._port.reset_input_buffer()
        except Exception:
            pass

    def _reconnect_after_stream_errors(self, errors: int) -> bool:
        """Reconnect a K-LD7 whose stream is stuck in repeated command timeouts."""
        logger.warning(
            "[KLD7] Stream hit %d consecutive errors (%s); reconnecting",
            errors,
            self.orientation,
        )
        self._close_radar_safely()
        if not self._running:
            return False
        try:
            return self.connect()
        except Exception:
            logger.error("[KLD7] Stream reconnect failed (%s)", self.orientation, exc_info=True)
            return False

    def _stream_loop(self):
        """Background thread: stream RADC into ring buffer.

        Retries on packet errors (common when two K-LD7s start simultaneously).
        The kld7 library's stream_frames generator can fail if a stray packet
        from the prior GNFD cycle is still in the serial buffer.
        """
        from kld7 import FrameCode, KLD7Exception

        frame_codes = FrameCode.RADC
        frame_count = 0
        errors = 0
        max_errors = 10
        reconnects = 0
        max_reconnects = 3

        # Periodic stream-health logging. Both K-LD7 instances share
        # this loop, so per-orientation Hz asymmetries (one radar
        # delivering full 34 Hz, the other less due to USB contention)
        # surface clearly. Logged every HEALTH_INTERVAL_S seconds.
        HEALTH_INTERVAL_S = 10.0
        last_health_t = time.time()
        last_health_count = 0

        logger.info("[KLD7] Stream started: RADC only (3Mbaud, max rate, %s)", self.orientation)

        # Note: the robust _read_packet patch is applied during connect()
        # via serial_io.connect_with_recovery, so we don't need to
        # re-install it here.

        while self._running:
            try:
                for code, payload in self._radar.stream_frames(frame_codes, max_count=-1):
                    if not self._running:
                        break

                    if code == "RADC":
                        # Validate payload — USB short reads can truncate packets
                        if not isinstance(payload, bytes) or len(payload) != 3072:
                            continue
                        receive_complete_ts = time.time()
                        packet_timing = getattr(self._radar, "_openflight_last_packet_timing", {})
                        arrival_ts = (
                            packet_timing.get("arrival_timestamp")
                            if isinstance(packet_timing, dict)
                            else None
                        )
                        complete_ts = (
                            packet_timing.get("complete_timestamp")
                            if isinstance(packet_timing, dict)
                            else None
                        )
                        read_duration_ms = (
                            packet_timing.get("read_duration_ms")
                            if isinstance(packet_timing, dict)
                            else None
                        )
                        if arrival_ts is None:
                            arrival_ts = receive_complete_ts
                        if complete_ts is None:
                            complete_ts = receive_complete_ts
                        frame = KLD7Frame(
                            timestamp=float(arrival_ts),
                            arrival_timestamp=float(arrival_ts),
                            complete_timestamp=float(complete_ts),
                            read_duration_ms=(
                                float(read_duration_ms) if read_duration_ms is not None else None
                            ),
                        )
                        frame.radc = payload
                        self._add_frame(frame)
                        frame_count += 1
                        errors = 0  # reset on success
                        reconnects = 0

                        if frame_count == 1:
                            logger.info(
                                "[KLD7] First RADC frame received (%d bytes, %s)",
                                len(payload) if payload else 0,
                                self.orientation,
                            )
                        elif frame_count == 50:
                            logger.info(
                                "[KLD7] Stream health: %d RADC frames (%s)",
                                frame_count,
                                self.orientation,
                            )

                        # Periodic Hz log so per-orientation frame-rate
                        # imbalances are visible in production logs.
                        now = time.time()
                        elapsed = now - last_health_t
                        if elapsed >= HEALTH_INTERVAL_S:
                            hz = (frame_count - last_health_count) / elapsed
                            log_fn = logger.warning if hz < 25.0 else logger.info
                            log_fn(
                                "[KLD7] Stream health (%s): %.1f Hz over last %.0fs (total=%d)",
                                self.orientation,
                                hz,
                                elapsed,
                                frame_count,
                            )
                            last_health_t = now
                            last_health_count = frame_count

                if not self._running:
                    break
                logger.warning(
                    "[KLD7] Stream generator exited (frames=%d, %s)", frame_count, self.orientation
                )

            except Exception as e:
                # KLD7Exception is always treated as recoverable; other
                # exceptions only when they match known transient patterns.
                if isinstance(e, KLD7Exception) or _is_recoverable_stream_error(e):
                    errors += 1
                    label = (
                        "Stream error"
                        if isinstance(e, KLD7Exception)
                        else "Recoverable stream error"
                    )
                    logger.warning(
                        "[KLD7] %s %d/%d (%s): %s",
                        label,
                        errors,
                        max_errors,
                        self.orientation,
                        e,
                    )
                    if errors < max_errors:
                        self._drain_after_stream_error()
                        continue
                    reconnects += 1
                    if reconnects > max_reconnects:
                        break
                    if self._reconnect_after_stream_errors(errors):
                        errors = 0
                        continue
                    break

                logger.error(
                    "[KLD7] Stream crashed after %d frames (%s): %s",
                    frame_count,
                    self.orientation,
                    e,
                    exc_info=True,
                )
                _log_session_error(
                    "K-LD7 stream crashed",
                    component="kld7_tracker",
                    context={
                        "orientation": self.orientation,
                        "frame_count": frame_count,
                    },
                    exc=e,
                )
                break

        if errors >= max_errors:
            logger.error(
                "[KLD7] Stream gave up after %d consecutive errors and %d reconnects (%s)",
                max_errors,
                reconnects,
                self.orientation,
            )
            _log_session_error(
                "K-LD7 stream gave up after repeated errors",
                component="kld7_tracker",
                context={
                    "orientation": self.orientation,
                    "max_errors": max_errors,
                    "reconnects": reconnects,
                },
            )

    def _add_frame(self, frame: KLD7Frame):
        """Add a frame to the ring buffer."""
        with self._buffer_lock:
            self._ring_buffer.append(frame)

    def _radc_frames_for_extraction(
        self,
        shot_timestamp: Optional[float] = None,
    ) -> tuple[list[dict], int, int]:
        """Return RADC frames near the shot timestamp.

        The ring buffer is frame-count limited, so an underfilled/sparse
        stream can span far longer than `buffer_seconds`. Filter by wall
        time when a shot timestamp is available so stale frames from prior
        movement cannot influence this shot's angle.
        """
        with self._buffer_lock:
            buffered = list(self._ring_buffer)
        frames = [
            {
                "timestamp": f.timestamp,
                "radc": f.radc,
                "arrival_timestamp": f.arrival_timestamp,
                "complete_timestamp": f.complete_timestamp,
                "read_duration_ms": f.read_duration_ms,
            }
            for f in buffered
            if f.radc is not None
        ]
        frames_available = len(frames)

        if shot_timestamp is None:
            return frames, frames_available, 0

        window_before_s = max(float(self.buffer_seconds or 0.0), 0.0)
        window_after_s = max(float(self.shot_window_after_s or 0.0), 0.0)
        start = shot_timestamp - window_before_s
        end = shot_timestamp + window_after_s

        filtered = [frame for frame in frames if start <= float(frame["timestamp"]) <= end]
        ignored = frames_available - len(filtered)
        if ignored:
            logger.info(
                "[KLD7] RADC: shot timestamp window kept %d/%d frames "
                "(ignored %d outside %.2fs before / %.2fs after, %s)",
                len(filtered),
                frames_available,
                ignored,
                window_before_s,
                window_after_s,
                self.orientation,
            )
        return filtered, frames_available, ignored

    def _extract_ball_radc(
        self,
        ball_speed_mph: float,
        shot_timestamp: Optional[float] = None,
        impact_timestamp: Optional[float] = None,
        club: Optional[ClubType] = None,
    ) -> Optional[KLD7Angle]:
        """Extract ball launch angle via RADC phase interferometry.

        Uses the OPS243-measured ball speed to narrow the FFT velocity
        search band, then extracts angle from F1A/F2A phase difference.
        """
        from .radc import extract_launch_angle, select_best_shot_result

        frames, frames_available, frames_ignored_stale = self._radc_frames_for_extraction(
            shot_timestamp
        )

        if not frames:
            logger.info(
                "[KLD7] RADC: no frames with RADC data in extraction window "
                "(%d available, %d total frames, %s)",
                frames_available,
                len(self._ring_buffer),
                self.orientation,
            )
            return None

        logger.info(
            "[KLD7] RADC: examining %d frames, ball_speed=%.1f mph", len(frames), ball_speed_mph
        )

        # Horizontal radar sees weaker ball returns (narrower beam in
        # the horizontal plane), so use a lower primary impact threshold
        # and one low-energy retry before giving up. The retry is
        # horizontal-only because the captured miss pattern shows coherent
        # low-energy horizontal ball peaks; loosening vertical produced
        # less trustworthy candidates in replay.
        energy_attempts = (
            [self.radc_horizontal_impact_energy_threshold]
            if self.orientation == "horizontal"
            else [self.radc_vertical_impact_energy_threshold]
        )
        if self.orientation == "horizontal":
            energy_attempts.append(self.radc_horizontal_retry_impact_energy_threshold)

        results = []
        relaxed_retry = False
        impact_ts_for_rules = (
            float(impact_timestamp)
            if impact_timestamp is not None
            else (float(shot_timestamp) if shot_timestamp is not None else None)
        )
        for attempt_idx, energy_threshold in enumerate(energy_attempts):
            results = extract_launch_angle(
                frames,
                ops243_ball_speed_mph=ball_speed_mph,
                angle_offset_deg=self.angle_offset_deg,
                speed_tolerance_mph=self.radc_speed_tolerance_mph,
                impact_energy_threshold=energy_threshold,
                centroid_floor_frac=self.radc_centroid_floor_frac,
                spectrum_source=self.radc_spectrum_source,
                ops_bin_outlier_tol=self.radc_ops_bin_outlier_tol,
                ops_bin_outlier_penalty=self.radc_ops_bin_outlier_penalty,
                ops_anchored_peak_min_snr=self.radc_ops_anchored_peak_min_snr,
                horizontal_angle_limit_deg=self.radc_horizontal_angle_limit_deg,
                orientation=self.orientation,
                vertical_estimator=self.vertical_estimator,
                shot_timestamp=impact_ts_for_rules,
                impact_timestamp=impact_ts_for_rules,
                mount_deg=self.mount_tilt_deg,
                distance_ft=self.ball_distance_ft,
                vertical_flight_window_net_distance_ft=self.vertical_flight_window_net_distance_ft,
                club=club,
            )
            if results:
                best_attempt = select_best_shot_result(results)
                weak_horizontal_wall = (
                    self.orientation == "horizontal"
                    and attempt_idx == 0
                    and len(energy_attempts) > 1
                    and abs(float(best_attempt.get("launch_angle_deg", 0.0))) >= 12.0
                    and int(best_attempt.get("frame_count", 0)) <= 2
                    and float(best_attempt.get("avg_snr_db", 0.0)) < 3.0
                )
                if weak_horizontal_wall:
                    logger.info(
                        "[KLD7] RADC: rejecting weak horizontal wall candidate "
                        "(angle=%.1f°, snr=%.1f, frames=%d); retrying",
                        best_attempt["launch_angle_deg"],
                        best_attempt.get("avg_snr_db", 0.0),
                        best_attempt.get("frame_count", 0),
                    )
                    results = []
                    continue
                relaxed_retry = attempt_idx > 0
                if relaxed_retry:
                    logger.info(
                        "[KLD7] RADC: horizontal low-energy retry succeeded (threshold=%.2f)",
                        energy_threshold,
                    )
                break

        if not results:
            logger.info(
                "[KLD7] RADC: no ball detections for %.1f mph (%s, %d frames examined)",
                ball_speed_mph,
                self.orientation,
                len(frames),
            )
            return None

        best = dict(select_best_shot_result(results))
        if relaxed_retry:
            best["confidence"] = min(float(best.get("confidence", 0.0)), 0.45)
        radc_selection = _radc_selection_diagnostics(
            best,
            relaxed_retry=relaxed_retry,
        )
        logger.info(
            "[KLD7] RADC: angle=%.1f° speed=%.1f mph snr=%.1f conf=%.2f frames=%d "
            "est=%s path=%s selected_frames=%s selected_t_ms=%s fit_rmse=%s",
            best["launch_angle_deg"],
            best["ball_speed_mph"],
            best["avg_snr_db"],
            best["confidence"],
            best["frame_count"],
            best.get("estimator", "naive"),
            best.get("selection_path"),
            best.get("selected_frame_indices"),
            best.get("selected_t_ms"),
            best.get("geom_fit_rmse_deg"),
        )

        if self.orientation == "vertical":
            return KLD7Angle(
                vertical_deg=best["launch_angle_deg"],
                horizontal_deg=None,
                confidence=best["confidence"],
                num_frames=best["frame_count"],
                frames_examined=len(frames),
                frames_available=frames_available,
                frames_ignored_stale=frames_ignored_stale,
                magnitude=best["avg_snr_db"],
                detection_class="ball",
                radc_selection=radc_selection,
            )
        return KLD7Angle(
            vertical_deg=None,
            horizontal_deg=best["launch_angle_deg"],
            confidence=best["confidence"],
            num_frames=best["frame_count"],
            frames_examined=len(frames),
            frames_available=frames_available,
            frames_ignored_stale=frames_ignored_stale,
            magnitude=best["avg_snr_db"],
            detection_class="ball",
            radc_selection=radc_selection,
        )

    def get_angle_for_shot(
        self,
        shot_timestamp: Optional[float] = None,
        ball_speed_mph: Optional[float] = None,
        impact_timestamp: Optional[float] = None,
        club: Optional[ClubType] = None,
    ) -> Optional[KLD7Angle]:
        """Search the ring buffer for the ball launch angle using RADC phase interferometry.

        Requires ball_speed_mph from OPS243 to narrow the FFT velocity search.
        ``impact_timestamp`` is the corrected ball-contact instant used by the
        geometry estimator for per-frame flight time (falls back to naive when
        it is None). Returns None if RADC extraction fails or ball_speed_mph
        not provided.
        """
        logger.info(
            "[KLD7] Angle extraction: ball_speed=%s mph, buffer=%d frames",
            "%.1f" % ball_speed_mph if ball_speed_mph else "None",
            len(self._ring_buffer),
        )

        if ball_speed_mph is None:
            logger.info("[KLD7] No ball speed provided, cannot extract RADC angle")
            return None

        try:
            result = self._extract_ball_radc(
                ball_speed_mph,
                shot_timestamp=shot_timestamp,
                impact_timestamp=impact_timestamp,
                club=club,
            )
            if result is not None:
                return result
            logger.info(
                "[KLD7] RADC extraction returned None (no detections at %.1f mph)", ball_speed_mph
            )
        except Exception as e:
            logger.warning("[KLD7] RADC extraction failed: %s", e, exc_info=True)
            _log_session_error(
                "K-LD7 ball angle RADC extraction failed",
                component="kld7_tracker",
                context={
                    "orientation": self.orientation,
                    "ball_speed_mph": ball_speed_mph,
                },
                exc=e,
            )

        return None

    def get_club_angle(
        self,
        club_speed_mph: Optional[float] = None,
        shot_timestamp: Optional[float] = None,
    ) -> Optional[KLD7Angle]:
        """Extract club head angle from RADC using OPS243 club speed.

        Same approach as ball extraction — uses club speed to find the
        club's aliased velocity bin in the FFT, then phase interferometry.
        """
        if club_speed_mph is None:
            return None

        try:
            result = self._extract_ball_radc(club_speed_mph, shot_timestamp=shot_timestamp)
            if result is not None:
                # Re-tag as club detection
                result.detection_class = "club"
                logger.info(
                    "[KLD7] Club angle: %.1f° at %.1f mph (%s)",
                    result.vertical_deg or result.horizontal_deg,
                    club_speed_mph,
                    self.orientation,
                )
                return result
        except Exception as e:
            logger.debug("[KLD7] Club angle extraction failed: %s", e)
            _log_session_error(
                "K-LD7 club angle RADC extraction failed",
                component="kld7_tracker",
                context={
                    "orientation": self.orientation,
                    "club_speed_mph": club_speed_mph,
                },
                exc=e,
            )

        return None

    def snapshot_buffer(self) -> list:
        """Return a serializable snapshot of the current ring buffer.

        Call this BEFORE get_angle_for_shot/reset to capture raw data
        for offline analysis alongside OPS243 shot data.
        """
        with self._buffer_lock:
            buffered = list(self._ring_buffer)
        frames = []
        for frame in buffered:
            entry = {"timestamp": frame.timestamp}
            if frame.arrival_timestamp is not None:
                entry["arrival_timestamp"] = frame.arrival_timestamp
            if frame.complete_timestamp is not None:
                entry["complete_timestamp"] = frame.complete_timestamp
            if frame.read_duration_ms is not None:
                entry["read_duration_ms"] = frame.read_duration_ms
            if frame.radc is not None:
                entry["has_radc"] = True
            frames.append(entry)
        return frames

    def reset(self):
        """Clear the ring buffer after a shot is processed."""
        with self._buffer_lock:
            self._ring_buffer.clear()
