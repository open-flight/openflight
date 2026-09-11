"""
Session logging for OpenFlight field testing.

Provides structured logging of all radar data, shots, and metrics
for analysis and debugging.
"""

import json
import logging
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .ops243 import SpeedReading

# Expected byte length of a valid K-LD7 RADC payload.  Duplicated here
# (canonical value lives in kld7.radc) to avoid importing the deprecated
# kld7 package, whose __init__ emits a DeprecationWarning on every import.
RADC_PAYLOAD_BYTES = 3072

# Version of the session JSONL format itself. Bump on breaking changes to
# entry structure; additive changes (new fields, new entry types) do not
# require a bump. Consumed by offline analysis and (eventually) cloud sync.
SESSION_FORMAT_VERSION = 1


@dataclass
class SessionMetadata:
    """Metadata about a logging session."""

    session_id: str
    start_time: str
    radar_port: Optional[str]
    firmware_version: Optional[str]
    camera_enabled: bool
    camera_model: Optional[str]
    config: Dict[str, Any]
    mode: str  # "rolling-buffer" or "mock"
    trigger_type: Optional[str]  # For rolling-buffer mode: "polling", "threshold", etc.
    # Globally unique session identity for cloud sync dedupe. The
    # timestamp-based session_id stays for filenames and display; this
    # UUID travels inside the data so renamed/copied session files keep
    # their identity (see docs/cloud-sync-design.md).
    session_uuid: str = ""
    format_version: int = 1
    app_version: str = ""


class SessionLogger:
    """
    Comprehensive session logger for field testing.

    Creates structured log files with semantic naming:
    - session_YYYYMMDD_HHMMSS_<location>.jsonl - Main session log (JSON lines)
    - radar_raw_YYYYMMDD_HHMMSS.log - Raw radar serial data

    Log entry types:
    - session_start: Session metadata
    - session_end: Session summary
    - reading_accepted: Reading that passed all filters
    - shot_detected: A shot was recorded
    - shot_camera: Camera tracking data for a shot
    - config_change: Radar configuration changed
    - error: Processing failures (component, context, optional exception metadata)
    """

    DEFAULT_LOG_DIR = Path.home() / "openflight_sessions"

    def __init__(
        self, log_dir: Optional[Path] = None, location: str = "range", enabled: bool = True
    ):
        """
        Initialize session logger.

        Args:
            log_dir: Directory for log files (default: ~/openflight_sessions)
            location: Location identifier for file naming (e.g., "range", "course", "home")
            enabled: Whether logging is enabled
        """
        self.log_dir = Path(log_dir) if log_dir else self.DEFAULT_LOG_DIR
        self.location = location
        self.enabled = enabled

        self._session_id: Optional[str] = None
        self._session_file: Optional[Any] = None
        self._raw_file: Optional[Any] = None
        self._session_path: Optional[Path] = None
        self._raw_path: Optional[Path] = None

        # Serializes all access to ``_session_file``. The log_* methods are
        # called concurrently from the OPS243 capture thread, the K-LD7
        # stream thread, and Flask-SocketIO handlers; without this lock,
        # large entries' writes interleave (corrupting the JSONL replay
        # corpus) and a write can race end_session() closing the file.
        self._write_lock = threading.Lock()

        # Counters for session summary
        self._stats = {
            "readings_accepted": 0,
            "shots_detected": 0,
            "errors": 0,
        }

        # Setup Python logger for raw radar data
        self._raw_logger = logging.getLogger("ops243.raw")
        self._radar_logger = logging.getLogger("ops243")

    def start_session(
        self,
        radar_port: Optional[str] = None,
        firmware_version: Optional[str] = None,
        camera_enabled: bool = False,
        camera_model: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        mode: str = "rolling-buffer",
        trigger_type: Optional[str] = None,
    ) -> str:
        """
        Start a new logging session.

        Args:
            radar_port: Serial port for radar
            firmware_version: Radar firmware version
            camera_enabled: Whether camera is enabled
            camera_model: Camera/YOLO model being used
            config: Current radar configuration
            mode: Radar mode ("rolling-buffer" or "mock")
            trigger_type: Trigger strategy for rolling-buffer mode

        Returns:
            Session ID
        """
        if not self.enabled:
            return ""

        # Create log directory
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Generate session ID and filenames
        timestamp = datetime.now()
        self._session_id = timestamp.strftime("%Y%m%d_%H%M%S")

        # Semantic file naming: session_DATE_TIME_LOCATION.jsonl
        session_filename = f"session_{self._session_id}_{self.location}.jsonl"
        raw_filename = f"radar_raw_{self._session_id}.log"

        self._session_path = self.log_dir / session_filename
        self._raw_path = self.log_dir / raw_filename

        # Open log files
        self._session_file = open(self._session_path, "w")
        self._raw_file = open(self._raw_path, "w")

        # Setup raw radar logging to file
        self._setup_raw_logging()

        # Reset stats
        self._stats = {k: 0 for k in self._stats}

        # Write session start entry
        metadata = SessionMetadata(
            session_id=self._session_id,
            start_time=timestamp.isoformat(),
            radar_port=radar_port,
            firmware_version=firmware_version,
            camera_enabled=camera_enabled,
            camera_model=camera_model,
            config=config or {},
            mode=mode,
            trigger_type=trigger_type,
            session_uuid=str(uuid.uuid4()),
            format_version=SESSION_FORMAT_VERSION,
            app_version=__version__,
        )

        self._write_entry("session_start", asdict(metadata))

        print(f"[SESSION] Started logging: {self._session_path}")
        print(f"[SESSION] Mode: {mode}" + (f" (trigger: {trigger_type})" if trigger_type else ""))
        print(f"[SESSION] Raw radar log: {self._raw_path}")

        return self._session_id

    def log_connection(
        self,
        device: str,
        port: str,
        baud: int = 0,
        firmware: str = None,
        radc_available: bool = None,
        **kwargs,
    ):
        """Log device connection details."""
        if not self.enabled:
            return
        entry = {
            "device": device,
            "port": port,
            "baud": baud,
        }
        if firmware:
            entry["firmware"] = firmware
        if radc_available is not None:
            entry["radc_available"] = radc_available
        entry.update(kwargs)
        self._write_entry("connection", entry)

    def log_clock_sync(self, device: str, port: str, summary: Dict[str, Any]):
        """Log an OPS clock-sync block (radar-clock -> host-epoch mapping).

        The ``summary`` comes from OPS243Radar.read_clock_sync and carries the
        per-read offsets plus the best offset/latency, so the radar's internal
        trigger_time can be converted to a host epoch in live capture and
        offline analysis.
        """
        if not self.enabled:
            return
        entry = {"device": device, "port": port}
        if summary:
            entry.update(summary)
        self._write_entry("ops_clock_sync", entry)

    def _setup_raw_logging(self):
        """Configure Python logging for raw radar data."""
        # Remove existing handlers
        for handler in self._raw_logger.handlers[:]:
            self._raw_logger.removeHandler(handler)
        for handler in self._radar_logger.handlers[:]:
            self._radar_logger.removeHandler(handler)

        # Add file handler for raw data
        file_handler = logging.FileHandler(self._raw_path)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s.%(msecs)03d - %(message)s", datefmt="%H:%M:%S")
        )

        self._raw_logger.addHandler(file_handler)
        self._raw_logger.setLevel(logging.DEBUG)

        self._radar_logger.addHandler(file_handler)
        self._radar_logger.setLevel(logging.DEBUG)

    def end_session(self):
        """End the current logging session and write summary."""
        if not self.enabled or not self._session_file:
            return

        # Calculate session duration
        end_time = datetime.now()

        # Write session end with summary
        summary = {
            "end_time": end_time.isoformat(),
            "stats": self._stats.copy(),
            "shot_rate": (
                self._stats["shots_detected"] / max(1, self._stats["readings_accepted"])
                if self._stats["readings_accepted"] > 0
                else 0
            ),
        }

        self._write_entry("session_end", summary)

        # Close the session file under the write lock so it cannot be
        # closed out from under a concurrent _write_entry call.
        with self._write_lock:
            if self._session_file:
                self._session_file.close()
                self._session_file = None

        if self._raw_file:
            self._raw_file.close()
            self._raw_file = None

        # Remove logging handlers
        for handler in self._raw_logger.handlers[:]:
            handler.close()
            self._raw_logger.removeHandler(handler)
        for handler in self._radar_logger.handlers[:]:
            handler.close()
            self._radar_logger.removeHandler(handler)

        print(f"[SESSION] Ended. Total shots: {self._stats['shots_detected']}")
        print(f"[SESSION] Logs saved to: {self._session_path}")

    def _write_entry(self, entry_type: str, data: Dict[str, Any]):
        """Write a log entry to the session file.

        Serialized with ``_write_lock`` so concurrent log_* calls from
        different threads cannot interleave a partial line into the JSONL
        stream or write to a file that end_session() is closing. The entry
        is serialized outside the lock to keep the critical section to the
        write+flush.
        """
        if not self._session_file:
            return

        line = json.dumps({"ts": datetime.now().isoformat(), "type": entry_type, **data}) + "\n"

        with self._write_lock:
            if not self._session_file:
                return
            self._session_file.write(line)
            self._session_file.flush()

    def log_accepted_reading(self, reading: SpeedReading):
        """Log a reading that passed all filters and will be processed."""
        if not self.enabled:
            return

        self._stats["readings_accepted"] += 1

        self._write_entry(
            "reading_accepted",
            {
                "speed": reading.speed,
                "direction": reading.direction.value,
                "magnitude": reading.magnitude,
            },
        )

    def log_shot(
        self,
        ball_speed_mph: float,
        club_speed_mph: Optional[float],
        smash_factor: Optional[float],
        estimated_carry_yards: float,
        club: str,
        peak_magnitude: Optional[float],
        readings_count: int,
        readings: Optional[List[Dict]] = None,
        spin_rpm: Optional[float] = None,
        spin_confidence: Optional[float] = None,
        spin_method: Optional[str] = None,
        spin_quality: Optional[str] = None,
        spin_multipath_fade_hz: Optional[float] = None,
        spin_snr: Optional[float] = None,
        spin_modulation_depth: Optional[float] = None,
        spin_peak_freq_hz: Optional[float] = None,
        spin_seam_cycles: Optional[float] = None,
        spin_at_lower_rail: Optional[bool] = None,
        spin_at_upper_rail: Optional[bool] = None,
        spin_candidates: Optional[List[Dict]] = None,
        spin_phase_method: Optional[str] = None,
        spin_phase_rpm: Optional[float] = None,
        spin_phase_snr: Optional[float] = None,
        spin_phase_agreement_pct: Optional[float] = None,
        spin_phase_confirmed: Optional[bool] = None,
        spin_rejection_reason: Optional[str] = None,
        carry_spin_adjusted: Optional[float] = None,
        mode: str = "rolling-buffer",
        launch_angle_vertical: Optional[float] = None,
        launch_angle_horizontal: Optional[float] = None,
        launch_angle_confidence: Optional[float] = None,
        launch_angle_vertical_confidence: Optional[float] = None,
        launch_angle_horizontal_confidence: Optional[float] = None,
        launch_angle_vertical_source: Optional[str] = None,
        launch_angle_horizontal_source: Optional[str] = None,
        angle_source: Optional[str] = None,
        club_angle_deg: Optional[float] = None,
        club_path_deg: Optional[float] = None,
        experimental_attack_angle_deg: Optional[float] = None,
        experimental_attack_angle_status: Optional[str] = None,
        experimental_club_path_deg: Optional[float] = None,
        experimental_club_path_status: Optional[str] = None,
        experimental_fused_attack_angle_deg: Optional[float] = None,
        experimental_fused_club_path_deg: Optional[float] = None,
        experimental_fused_status: Optional[str] = None,
        experimental_fused_attack_angle_confidence: Optional[str] = None,
        experimental_fused_club_path_confidence: Optional[str] = None,
        experimental_camera_trace_deg: Optional[float] = None,
        experimental_aoa_offset_source: Optional[str] = None,
        iwr6843_horizontal_deg: Optional[float] = None,
        iwr6843_horizontal_confidence: Optional[float] = None,
        experimental_camera_horizontal_deg: Optional[float] = None,
        experimental_camera_horizontal_confidence: Optional[float] = None,
        experimental_camera_horizontal_status: Optional[str] = None,
        experimental_camera_iwr_delta_deg: Optional[float] = None,
        spin_axis_deg: Optional[float] = None,
        pipeline_ms: Optional[Dict] = None,
        impact_timestamp: Optional[float] = None,
        profile_id: Optional[str] = None,
        profile_name: Optional[str] = None,
        inclinometer: Optional[Dict] = None,
        shot_number: Optional[int] = None,
    ):
        """
        Log a detected shot with all metrics.

        Args:
            ball_speed_mph: Ball speed in MPH
            club_speed_mph: Estimated club speed
            smash_factor: Calculated smash factor
            estimated_carry_yards: Estimated carry distance
            club: Club type used
            peak_magnitude: Peak radar magnitude
            readings_count: Number of readings in the shot window
            shot_number: Stable sequence assigned when the shot was detected
            readings: Optional list of individual readings that comprised the shot
            spin_rpm: Spin rate in RPM (rolling buffer mode only)
            spin_confidence: Confidence of spin detection (rolling buffer mode only)
            spin_method: Estimator that produced the spin candidate
            spin_quality: Quality assessment ("high", "medium", "low", "experimental")
            spin_multipath_fade_hz: Fitted two-ray fade frequency
            spin_snr: Signal-to-noise ratio of spin detection
            spin_modulation_depth: Envelope std/mean ratio
            spin_peak_freq_hz: Frequency of the picked envelope-FFT peak
            spin_seam_cycles: Seam cycles in analysis window
            spin_at_lower_rail: True when peak landed near the low rail
            spin_at_upper_rail: True when peak landed near the high rail
            spin_candidates: Ranked envelope-FFT spin peaks for offline analysis
            spin_phase_method: Phase confirmation method, if attempted
            spin_phase_rpm: Phase-derived spin candidate, if available
            spin_phase_snr: Phase-derived candidate SNR
            spin_phase_agreement_pct: Envelope/phase agreement percentage
            spin_phase_confirmed: True when phase recovered a low-SNR spin
            spin_rejection_reason: Human-readable reason if spin was rejected
            carry_spin_adjusted: Carry distance adjusted for spin (rolling buffer mode only)
            mode: Radar mode ("rolling-buffer" or "mock")
            impact_timestamp: Host epoch timestamp aligned to impact/OPS trigger time
            inclinometer: Enclosure orientation and effective IWR tilt used for the shot
        """
        if not self.enabled:
            return

        self._stats["shots_detected"] += 1
        resolved_shot_number = (
            shot_number if shot_number is not None else self._stats["shots_detected"]
        )

        data = {
            "shot_number": resolved_shot_number,
            "ball_speed_mph": ball_speed_mph,
            "club_speed_mph": club_speed_mph,
            "smash_factor": smash_factor,
            "estimated_carry_yards": estimated_carry_yards,
            "club": club,
            "profile_id": profile_id,
            "profile_name": profile_name,
            "peak_magnitude": peak_magnitude,
            "readings_count": readings_count,
            "readings": readings,
            "spin_rpm": spin_rpm,
            "spin_confidence": spin_confidence,
            "spin_method": spin_method,
            "spin_quality": spin_quality,
            "spin_multipath_fade_hz": spin_multipath_fade_hz,
            "spin_snr": spin_snr,
            "spin_modulation_depth": spin_modulation_depth,
            "spin_peak_freq_hz": spin_peak_freq_hz,
            "spin_candidate_rpm": (
                round(spin_peak_freq_hz * 60) if spin_peak_freq_hz is not None else None
            ),
            "spin_seam_cycles": spin_seam_cycles,
            "spin_at_lower_rail": spin_at_lower_rail,
            "spin_at_upper_rail": spin_at_upper_rail,
            "spin_candidates": spin_candidates,
            "spin_phase_method": spin_phase_method,
            "spin_phase_rpm": spin_phase_rpm,
            "spin_phase_snr": spin_phase_snr,
            "spin_phase_agreement_pct": spin_phase_agreement_pct,
            "spin_phase_confirmed": spin_phase_confirmed,
            "spin_rejection_reason": spin_rejection_reason,
            "carry_spin_adjusted": carry_spin_adjusted,
            "mode": mode,
            "launch_angle_vertical": launch_angle_vertical,
            "launch_angle_horizontal": launch_angle_horizontal,
            "launch_angle_confidence": launch_angle_confidence,
            "launch_angle_vertical_confidence": launch_angle_vertical_confidence,
            "launch_angle_horizontal_confidence": launch_angle_horizontal_confidence,
            "launch_angle_vertical_source": launch_angle_vertical_source,
            "launch_angle_horizontal_source": launch_angle_horizontal_source,
            "impact_timestamp": impact_timestamp,
        }

        if angle_source is not None:
            data["angle_source"] = angle_source
        if club_angle_deg is not None:
            data["club_angle_deg"] = club_angle_deg
        if club_path_deg is not None:
            data["club_path_deg"] = club_path_deg
        if experimental_attack_angle_deg is not None:
            data["experimental_attack_angle_deg"] = experimental_attack_angle_deg
        if experimental_attack_angle_status is not None:
            data["experimental_attack_angle_status"] = experimental_attack_angle_status
        if experimental_club_path_deg is not None:
            data["experimental_club_path_deg"] = experimental_club_path_deg
        if experimental_club_path_status is not None:
            data["experimental_club_path_status"] = experimental_club_path_status
        if experimental_fused_attack_angle_deg is not None:
            data["experimental_fused_attack_angle_deg"] = experimental_fused_attack_angle_deg
        if experimental_fused_club_path_deg is not None:
            data["experimental_fused_club_path_deg"] = experimental_fused_club_path_deg
        if experimental_fused_status is not None:
            data["experimental_fused_status"] = experimental_fused_status
        if experimental_fused_attack_angle_confidence is not None:
            data["experimental_fused_attack_angle_confidence"] = (
                experimental_fused_attack_angle_confidence
            )
        if experimental_fused_club_path_confidence is not None:
            data["experimental_fused_club_path_confidence"] = (
                experimental_fused_club_path_confidence
            )
        if experimental_camera_trace_deg is not None:
            data["experimental_camera_trace_deg"] = experimental_camera_trace_deg
        if experimental_aoa_offset_source is not None:
            data["experimental_aoa_offset_source"] = experimental_aoa_offset_source
        if iwr6843_horizontal_deg is not None:
            data["iwr6843_horizontal_deg"] = iwr6843_horizontal_deg
        if iwr6843_horizontal_confidence is not None:
            data["iwr6843_horizontal_confidence"] = iwr6843_horizontal_confidence
        if experimental_camera_horizontal_deg is not None:
            data["experimental_camera_horizontal_deg"] = experimental_camera_horizontal_deg
        if experimental_camera_horizontal_confidence is not None:
            data["experimental_camera_horizontal_confidence"] = (
                experimental_camera_horizontal_confidence
            )
        if experimental_camera_horizontal_status is not None:
            data["experimental_camera_horizontal_status"] = experimental_camera_horizontal_status
        if experimental_camera_iwr_delta_deg is not None:
            data["experimental_camera_iwr_delta_deg"] = experimental_camera_iwr_delta_deg
        if spin_axis_deg is not None:
            data["spin_axis_deg"] = spin_axis_deg
        if pipeline_ms is not None:
            data["pipeline_ms"] = pipeline_ms
        if inclinometer is not None:
            data["inclinometer"] = inclinometer

        self._write_entry("shot_detected", data)

    def log_camera_data(
        self,
        shot_number: int,
        launch_angle_vertical: Optional[float],
        launch_angle_horizontal: Optional[float],
        confidence: Optional[float],
        positions_tracked: int,
        launch_detected: bool,
    ):
        """Log camera tracking data for a shot."""
        if not self.enabled:
            return

        self._write_entry(
            "shot_camera",
            {
                "shot_number": shot_number,
                "launch_angle_vertical": launch_angle_vertical,
                "launch_angle_horizontal": launch_angle_horizontal,
                "confidence": confidence,
                "positions_tracked": positions_tracked,
                "launch_detected": launch_detected,
            },
        )

    def log_camera_capture(
        self,
        *,
        shot_number: int,
        shot_timestamp: Optional[float],
        trigger_timestamp: Optional[float],
        capture_path: Optional[str],
        metadata: Optional[Dict] = None,
        capture_error: Optional[str] = None,
    ):
        """Log a high-speed camera clip saved for offline shot correlation."""
        if not self.enabled:
            return

        self._write_entry(
            "camera_capture",
            {
                "shot_number": shot_number,
                "shot_timestamp": shot_timestamp,
                "trigger_timestamp": trigger_timestamp,
                "trigger_delta_ms": (
                    (trigger_timestamp - shot_timestamp) * 1000.0
                    if shot_timestamp is not None and trigger_timestamp is not None
                    else None
                ),
                "capture_path": capture_path,
                "capture_error": capture_error,
                "metadata": metadata or {},
            },
        )

    def log_kld7_buffer(
        self,
        shot_number: int,
        shot_timestamp: float,
        orientation: str,
        buffer_frames: list,
        ball_angle: Optional[Dict] = None,
        club_angle: Optional[Dict] = None,
        raw_payload_expected: Optional[bool] = None,
    ):
        """Log raw K-LD7 ring buffer alongside OPS243 shot for correlation analysis."""
        if not self.enabled:
            return

        radc_frame_count = sum(
            1 for frame in buffer_frames if frame.get("has_radc") or frame.get("radc_b64")
        )
        radc_payload_count = sum(1 for frame in buffer_frames if frame.get("radc_b64"))
        radc_payload_valid_count = sum(
            1
            for frame in buffer_frames
            if frame.get("radc_b64") and frame.get("radc_payload_bytes") == RADC_PAYLOAD_BYTES
        )
        radc_payload_invalid_count = sum(
            1
            for frame in buffer_frames
            if frame.get("radc_b64")
            and frame.get("radc_payload_bytes") is not None
            and frame.get("radc_payload_bytes") != RADC_PAYLOAD_BYTES
        )
        radc_payload_complete = (
            radc_frame_count > 0
            and radc_payload_count == radc_frame_count
            and radc_payload_invalid_count == 0
        )
        self._write_entry(
            "kld7_buffer",
            {
                "shot_number": shot_number,
                "shot_timestamp": shot_timestamp,
                "orientation": orientation,
                "frame_count": len(buffer_frames),
                "radc_frame_count": radc_frame_count,
                "radc_payload_count": radc_payload_count,
                "radc_payload_valid_count": radc_payload_valid_count,
                "radc_payload_invalid_count": radc_payload_invalid_count,
                "radc_payload_expected": raw_payload_expected,
                "radc_payload_complete": radc_payload_complete,
                "frames": buffer_frames,
                "ball_angle": ball_angle,
                "club_angle": club_angle,
            },
        )

    def log_iwr6843_capture(
        self,
        *,
        shot_number: int,
        shot_timestamp: Optional[float],
        trigger_timestamp: Optional[float],
        capture_path: Optional[str],
        capture_bytes: int,
        dump_duration_s: Optional[float],
        capture_error: Optional[str],
        ball_speed_mph: float,
        measurement: Optional[Dict] = None,
        club_path: Optional[Dict] = None,
        temperature_report: Optional[Dict[str, Any]] = None,
    ):
        """Log the TI raw-dump reference and complete LCMF evidence."""
        if not self.enabled:
            return
        self._write_entry(
            "iwr6843_capture",
            {
                "shot_number": shot_number,
                "shot_timestamp": shot_timestamp,
                "trigger_timestamp": trigger_timestamp,
                "trigger_delta_ms": (
                    round((trigger_timestamp - shot_timestamp) * 1000.0, 3)
                    if trigger_timestamp is not None and shot_timestamp is not None
                    else None
                ),
                "capture_path": capture_path,
                "capture_bytes": capture_bytes,
                "dump_duration_s": dump_duration_s,
                "capture_error": capture_error,
                "ball_speed_source": "ops243",
                "ball_speed_mph": ball_speed_mph,
                "measurement": measurement,
                "club_path": club_path,
                "temperature_report": temperature_report,
            },
        )

    def log_config_change(self, config: Dict[str, Any], source: str = "user"):
        """Log a radar configuration change."""
        if not self.enabled:
            return

        self._write_entry(
            "config_change",
            {
                "config": config,
                "source": source,
            },
        )

    def log_sim_send(
        self,
        target: str,
        shot_number: int,
        provenance: Dict[str, str],
        values: Optional[Dict[str, Any]] = None,
    ):
        """Log a shot forwarded to a simulator connector."""
        if not self.enabled:
            return

        self._write_entry(
            "sim_send",
            {
                "target": target,
                "shot_number": shot_number,
                "provenance": provenance,
                "values": values or {},
            },
        )

    def log_sim_status(
        self,
        target: str,
        state: str,
        host: str = "",
        port: int = 0,
        message: str = "",
        attempt: int = 0,
        next_retry_in_s: float = 0.0,
    ):
        """Log a simulator connector connection-state change."""
        if not self.enabled:
            return

        self._write_entry(
            "sim_status",
            {
                "target": target,
                "state": state,
                "host": host,
                "port": port,
                "message": message,
                "attempt": attempt,
                "next_retry_in_s": next_retry_in_s,
            },
        )

    def log_sim_player(self, target: str, handed: str, club: str):
        """Log a player/club update pushed by a simulator."""
        if not self.enabled:
            return

        self._write_entry(
            "sim_player",
            {
                "target": target,
                "handed": handed,
                "club": club,
            },
        )

    def log_power_status(self, status: Dict[str, Any]) -> None:
        """Log a battery snapshot or power-state transition."""
        if not self.enabled:
            return
        self._write_entry("power_status", status)

    def log_iq_reading(
        self,
        speed_mph: float,
        direction: str,
        magnitude: float,
        snr: float,
        peak_bin: int,
        cfar_validated: bool,
        block_count: int,
    ):
        """
        Log a speed reading detected from I/Q streaming mode.

        Args:
            speed_mph: Detected speed in mph
            direction: "outbound" or "inbound"
            magnitude: Peak FFT magnitude
            snr: Signal-to-noise ratio
            peak_bin: FFT bin of the peak
            cfar_validated: Whether this was validated by CFAR
            block_count: Number of I/Q blocks processed
        """
        if not self.enabled:
            return

        self._write_entry(
            "iq_reading",
            {
                "speed_mph": speed_mph,
                "direction": direction,
                "magnitude": magnitude,
                "snr": snr,
                "peak_bin": peak_bin,
                "cfar_validated": cfar_validated,
                "block_count": block_count,
            },
        )

    def log_iq_blocks(self, shot_number: int, blocks: List[Dict[str, Any]]):
        """
        Log raw I/Q blocks for a shot (for post-session analysis).

        Args:
            shot_number: Shot number this data belongs to
            blocks: List of I/Q block data dicts with i_samples, q_samples, timestamp
        """
        if not self.enabled:
            return

        self._write_entry(
            "iq_blocks",
            {
                "shot_number": shot_number,
                "block_count": len(blocks),
                "blocks": blocks,
            },
        )

    def log_trigger_event(
        self,
        trigger_type: str,
        accepted: bool,
        reason: Optional[str] = None,
        peak_speed_mph: Optional[float] = None,
        readings_count: int = 0,
        latency_ms: Optional[float] = None,
    ):
        """
        Log a trigger event (accepted or rejected).

        Useful for diagnosing false triggers at driving ranges where
        nearby players can trip sound triggers.

        Args:
            trigger_type: Type of trigger (e.g., "sound", "sound-gpio")
            accepted: True if trigger led to valid shot detection
            reason: Reason for rejection (if not accepted)
            peak_speed_mph: Peak speed detected (if any)
            readings_count: Number of readings in capture
            latency_ms: Trigger latency in milliseconds (if measured)
        """
        if not self.enabled:
            return

        # Track stats
        if "triggers_total" not in self._stats:
            self._stats["triggers_total"] = 0
            self._stats["triggers_accepted"] = 0
            self._stats["triggers_rejected"] = 0

        self._stats["triggers_total"] += 1
        if accepted:
            self._stats["triggers_accepted"] += 1
        else:
            self._stats["triggers_rejected"] += 1

        self._write_entry(
            "trigger_event",
            {
                "trigger_type": trigger_type,
                "accepted": accepted,
                "reason": reason,
                "peak_speed_mph": peak_speed_mph,
                "readings_count": readings_count,
                "latency_ms": latency_ms,
            },
        )

    def log_trigger_diagnostic(
        self,
        trigger_type: str,
        accepted: bool,
        reason: str = "",
        # Capture metadata
        response_bytes: int = 0,
        total_readings: int = 0,
        outbound_readings: int = 0,
        inbound_readings: int = 0,
        peak_outbound_mph: float = 0.0,
        peak_inbound_mph: float = 0.0,
        all_outbound_speeds: Optional[List[float]] = None,
        all_inbound_speeds: Optional[List[float]] = None,
        # Shot result (if accepted and processed)
        ball_speed_mph: Optional[float] = None,
        club_speed_mph: Optional[float] = None,
        spin_rpm: Optional[float] = None,
        carry_yards: Optional[float] = None,
        # Timing
        latency_ms: Optional[float] = None,
    ):
        """
        Log a detailed trigger diagnostic entry.

        This provides rich diagnostic data for every trigger event,
        whether accepted or rejected. Used to diagnose why shots
        don't appear in the UI during field testing.

        Args:
            trigger_type: Type of trigger (e.g., "sound-gpio")
            accepted: Whether trigger led to a valid shot
            reason: Why accepted/rejected (e.g., "no_outbound_speed")
            response_bytes: Raw bytes received from radar
            total_readings: Total FFT readings extracted
            outbound_readings: Outbound readings count
            inbound_readings: Inbound readings count
            peak_outbound_mph: Peak outbound speed
            peak_inbound_mph: Peak inbound speed
            all_outbound_speeds: All outbound speed values
            all_inbound_speeds: All inbound speed values
            ball_speed_mph: Ball speed (if shot accepted)
            club_speed_mph: Club speed (if detected)
            spin_rpm: Spin rate (if detected)
            carry_yards: Estimated carry (if shot accepted)
            latency_ms: Trigger-to-capture latency
        """
        if not self.enabled:
            return

        # Track detailed stats
        if "triggers_total" not in self._stats:
            self._stats["triggers_total"] = 0
            self._stats["triggers_accepted"] = 0
            self._stats["triggers_rejected"] = 0

        self._stats["triggers_total"] += 1
        if accepted:
            self._stats["triggers_accepted"] += 1
        else:
            self._stats["triggers_rejected"] += 1

        self._write_entry(
            "trigger_diagnostic",
            {
                "trigger_type": trigger_type,
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
                "ball_speed_mph": ball_speed_mph,
                "club_speed_mph": club_speed_mph,
                "spin_rpm": spin_rpm,
                "carry_yards": carry_yards,
                "latency_ms": latency_ms,
            },
        )

    def log_rolling_buffer_capture(
        self,
        shot_number: int,
        sample_time: float,
        trigger_time: float,
        i_samples: List[int],
        q_samples: List[int],
        ball_speed_mph: Optional[float] = None,
        club_speed_mph: Optional[float] = None,
        ball_timestamp_ms: Optional[float] = None,
        club_timestamp_ms: Optional[float] = None,
        impact_timestamp_ms: Optional[float] = None,
        impact_source: Optional[str] = None,
        impact_reason: Optional[str] = None,
        impact_speed_delta_mph: Optional[float] = None,
        impact_transition_gap_ms: Optional[float] = None,
        impact_last_club_speed_mph: Optional[float] = None,
        impact_last_club_timestamp_ms: Optional[float] = None,
        impact_last_club_center_ms: Optional[float] = None,
        impact_first_ball_speed_mph: Optional[float] = None,
        impact_first_ball_timestamp_ms: Optional[float] = None,
        impact_first_ball_center_ms: Optional[float] = None,
        impact_min_transition_delta_mph: Optional[float] = None,
        trigger_latency_ms: Optional[float] = None,
        smash_factor: Optional[float] = None,
        spin_rpm: Optional[float] = None,
        spin_confidence: Optional[float] = None,
        spin_method: Optional[str] = None,
        spin_quality: Optional[str] = None,
        spin_multipath_fade_hz: Optional[float] = None,
        spin_snr: Optional[float] = None,
        spin_modulation_depth: Optional[float] = None,
        spin_peak_freq_hz: Optional[float] = None,
        spin_seam_cycles: Optional[float] = None,
        spin_at_lower_rail: Optional[bool] = None,
        spin_at_upper_rail: Optional[bool] = None,
        spin_candidates: Optional[List[Dict]] = None,
        spin_phase_method: Optional[str] = None,
        spin_phase_rpm: Optional[float] = None,
        spin_phase_snr: Optional[float] = None,
        spin_phase_agreement_pct: Optional[float] = None,
        spin_phase_confirmed: Optional[bool] = None,
        spin_rejection_reason: Optional[str] = None,
        first_byte_timestamp: Optional[float] = None,
        trigger_timestamp: Optional[float] = None,
        trigger_timestamp_source: Optional[str] = None,
        clock_sync_offset_s: Optional[float] = None,
        post_trigger_duration_ms: Optional[float] = None,
    ):
        """
        Log raw rolling buffer capture data for offline analysis.

        Args:
            shot_number: Shot number this capture belongs to
            sample_time: When sampling started (radar timestamp)
            trigger_time: When trigger fired (radar timestamp)
            i_samples: Raw I channel samples (4096 values)
            q_samples: Raw Q channel samples (4096 values)
            ball_speed_mph: Detected ball speed (if any)
            club_speed_mph: Detected club speed (if any)
            ball_timestamp_ms: Ball signal position in buffer (ms from start)
            club_timestamp_ms: Club signal position in buffer (ms from start)
            impact_timestamp_ms: Selected impact position in buffer (ms from start)
            impact_source: Source used for impact timing
            impact_reason: Fallback reason when source is not OPS transition
            impact_speed_delta_mph: Speed jump across club-to-ball transition
            impact_transition_gap_ms: Gap between transition frame centers
            impact_last_club_speed_mph: Last club-like frame speed
            impact_last_club_timestamp_ms: Last club-like frame start time
            impact_last_club_center_ms: Last club-like frame center time
            impact_first_ball_speed_mph: First ball-like frame speed
            impact_first_ball_timestamp_ms: First ball-like frame start time
            impact_first_ball_center_ms: First ball-like frame center time
            impact_min_transition_delta_mph: Minimum speed jump for transition
            trigger_latency_ms: Edge-to-S! latency (ms)
            smash_factor: Ball speed / club speed ratio
            spin_rpm: Detected spin rate in RPM
            spin_confidence: Confidence of spin detection (0-1)
            spin_method: Estimator that produced the spin candidate
            spin_quality: Quality assessment ("high", "medium", "low", "experimental")
            spin_multipath_fade_hz: Fitted two-ray fade frequency
            spin_snr: Signal-to-noise ratio of spin detection
            spin_modulation_depth: Envelope std/mean ratio (1-5% real seam,
                <0.5% noise floor, 0.5-1% suspicious)
            spin_peak_freq_hz: Frequency of the picked envelope-FFT peak
            spin_seam_cycles: Seam cycles in analysis window
            spin_at_lower_rail: True when peak landed at the bottom of
                the seam search range (envelope-DC leakage suspect)
            spin_at_upper_rail: True when peak landed at the top of the
                seam search range (bandpass-shoulder noise suspect)
            spin_candidates: Ranked envelope-FFT spin peaks for offline analysis
            spin_phase_method: Phase confirmation method, if attempted
            spin_phase_rpm: Phase-derived spin candidate, if available
            spin_phase_snr: Phase-derived candidate SNR
            spin_phase_agreement_pct: Envelope/phase agreement percentage
            spin_phase_confirmed: True when phase recovered a low-SNR spin
            spin_rejection_reason: Human-readable reason if spin was
                rejected (None on a clean accept)
            first_byte_timestamp: Host epoch timestamp when the first byte
                of the hardware-triggered rolling-buffer dump arrived
            trigger_timestamp: Host epoch timestamp of the inferred hardware trigger
            trigger_timestamp_source: Method used to infer trigger_timestamp
            clock_sync_offset_s: Host epoch minus OPS radar clock, when available
            post_trigger_duration_ms: Duration of the capture after trigger
        """
        if not self.enabled:
            return

        trigger_offset_ms = (trigger_time - sample_time) * 1000
        impact_offset_from_trigger_ms = (
            impact_timestamp_ms - trigger_offset_ms if impact_timestamp_ms is not None else None
        )

        self._write_entry(
            "rolling_buffer_capture",
            {
                "shot_number": shot_number,
                "sample_time": sample_time,
                "trigger_time": trigger_time,
                "trigger_offset_ms": trigger_offset_ms,
                "sample_count": len(i_samples),
                "i_samples": i_samples,
                "q_samples": q_samples,
                "ball_speed_mph": ball_speed_mph,
                "club_speed_mph": club_speed_mph,
                "ball_timestamp_ms": ball_timestamp_ms,
                "club_timestamp_ms": club_timestamp_ms,
                "impact_timestamp_ms": impact_timestamp_ms,
                "impact_offset_from_trigger_ms": impact_offset_from_trigger_ms,
                "impact_source": impact_source,
                "impact_reason": impact_reason,
                "impact_speed_delta_mph": impact_speed_delta_mph,
                "impact_transition_gap_ms": impact_transition_gap_ms,
                "impact_last_club_speed_mph": impact_last_club_speed_mph,
                "impact_last_club_timestamp_ms": impact_last_club_timestamp_ms,
                "impact_last_club_center_ms": impact_last_club_center_ms,
                "impact_first_ball_speed_mph": impact_first_ball_speed_mph,
                "impact_first_ball_timestamp_ms": impact_first_ball_timestamp_ms,
                "impact_first_ball_center_ms": impact_first_ball_center_ms,
                "impact_min_transition_delta_mph": impact_min_transition_delta_mph,
                "trigger_latency_ms": trigger_latency_ms,
                "first_byte_timestamp": first_byte_timestamp,
                "trigger_timestamp": trigger_timestamp,
                "trigger_timestamp_source": trigger_timestamp_source,
                "trigger_timestamp_from_first_byte": (
                    first_byte_timestamp - (post_trigger_duration_ms / 1000.0)
                    if first_byte_timestamp is not None and post_trigger_duration_ms is not None
                    else None
                ),
                "trigger_timestamp_delta_from_first_byte_ms": (
                    (trigger_timestamp - (first_byte_timestamp - post_trigger_duration_ms / 1000.0))
                    * 1000.0
                    if trigger_timestamp is not None
                    and first_byte_timestamp is not None
                    and post_trigger_duration_ms is not None
                    else None
                ),
                "clock_sync_offset_s": clock_sync_offset_s,
                "post_trigger_duration_ms": post_trigger_duration_ms,
                "smash_factor": smash_factor,
                "spin_rpm": spin_rpm,
                "spin_confidence": spin_confidence,
                "spin_method": spin_method,
                "spin_quality": spin_quality,
                "spin_multipath_fade_hz": spin_multipath_fade_hz,
                "spin_snr": spin_snr,
                "spin_modulation_depth": spin_modulation_depth,
                "spin_peak_freq_hz": spin_peak_freq_hz,
                "spin_candidate_rpm": (
                    round(spin_peak_freq_hz * 60) if spin_peak_freq_hz is not None else None
                ),
                "spin_seam_cycles": spin_seam_cycles,
                "spin_at_lower_rail": spin_at_lower_rail,
                "spin_at_upper_rail": spin_at_upper_rail,
                "spin_candidates": spin_candidates,
                "spin_phase_method": spin_phase_method,
                "spin_phase_rpm": spin_phase_rpm,
                "spin_phase_snr": spin_phase_snr,
                "spin_phase_agreement_pct": spin_phase_agreement_pct,
                "spin_phase_confirmed": spin_phase_confirmed,
                "spin_rejection_reason": spin_rejection_reason,
            },
        )

    def log_error(self, error: str, context: Optional[Dict] = None):
        """Log an error."""
        if not self.enabled:
            return

        self._stats["errors"] += 1

        self._write_entry(
            "error",
            {
                "error": error,
                "context": context or {},
            },
        )

    @property
    def session_path(self) -> Optional[Path]:
        """Get the current session log file path."""
        return self._session_path

    @property
    def raw_path(self) -> Optional[Path]:
        """Get the current raw radar log file path."""
        return self._raw_path

    @property
    def session_id(self) -> Optional[str]:
        """Get the current session ID."""
        return self._session_id

    @property
    def stats(self) -> Dict[str, int]:
        """Get current session statistics."""
        return self._stats.copy()


# Global session logger instance
_session_logger: Optional[SessionLogger] = None


def get_session_logger() -> Optional[SessionLogger]:
    """Get the global session logger instance."""
    return _session_logger


def log_session_error(
    error: str,
    *,
    context: Optional[Dict[str, Any]] = None,
    component: Optional[str] = None,
    exc: Optional[BaseException] = None,
) -> None:
    """Write an error entry to the active session JSONL log, if any."""
    session = get_session_logger()
    if session is None:
        return

    ctx: Dict[str, Any] = dict(context or {})
    if component:
        ctx["component"] = component
    if exc is not None:
        ctx["exception_type"] = type(exc).__name__
        ctx["exception_message"] = str(exc)

    session.log_error(error, context=ctx)


def init_session_logger(
    log_dir: Optional[Path] = None, location: str = "range", enabled: bool = True
) -> SessionLogger:
    """
    Initialize and return the global session logger.

    Args:
        log_dir: Directory for log files
        location: Location identifier
        enabled: Whether logging is enabled

    Returns:
        SessionLogger instance
    """
    global _session_logger
    _session_logger = SessionLogger(log_dir=log_dir, location=location, enabled=enabled)
    return _session_logger
