"""
WebSocket server for OpenFlight UI.

Provides real-time shot data to the web frontend via Flask-SocketIO.
"""

import json
import logging
import math
import os
import queue
import random
import statistics
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, fields, replace
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from flask import Flask, Response, jsonify, request, send_file, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO

from .ballistics import resolve_launch, simulate
from .clubs import ClubType
from .clubs.physics import (
    SHOT_SIMULATION_DEFAULTS,
    get_club_physics,
    get_club_simulation_profile,
)
from .launch_monitor import SPIN_CONFIDENCE_HIGH, SPIN_CONFIDENCE_RELIABLE, Shot, summarize_shots
from .ops243 import (
    UART_BAUD_COMMANDS,
    Direction,
    OPS243Radar,
    SpeedReading,
    set_show_raw_readings,
)
from .power import SUPPORTED_BATTERY_PROVIDERS, PowerMonitor, PowerStatus
from .profiles import ProfileStore
from .rolling_buffer.monitor import estimate_carry_with_spin, get_optimal_spin_for_ball_speed
from .session_logger import get_session_logger, init_session_logger, log_session_error
from .sim import (
    IncompleteShotError,
    PlayerState as SimPlayerState,
    PlayerUpdate,
    ShotAck,
    SimError,
    build_connectors,
    initial_shot_counter,
    load_sim_config,
    resolve_shot,
)
from .speed_correction import correct_ball_speed
from .spin_estimate import calculated_spin_rpm
from .startup_status import StartupStatusReporter, configured_startup_components
from .swing_speed import SwingSpeedEvent

# Configure logging
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST_DIR = REPO_ROOT / "ui" / "dist"
FRONTEND_SOURCE_DIR = REPO_ROOT / "ui"


app = Flask(__name__, static_folder=str(FRONTEND_DIST_DIR), static_url_path="")
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Global state
monitor = None
power_monitor: Optional[PowerMonitor] = None
battery_provider: str | None = None
mock_mode: bool = False
debug_mode: bool = False
mock_swing_speed_mode: bool = False
debug_log_file = None
debug_log_path: Optional[Path] = None
# Created lazily so importing the server (in tests, in tooling) never writes
# to the real config directory.
profile_store: Optional[ProfileStore] = None


def get_profile_store() -> ProfileStore:
    """The profile roster. Single source of truth for the active selection."""
    global profile_store  # pylint: disable=global-statement
    if profile_store is None:
        profile_store = ProfileStore()
    return profile_store


TRAINING_IMPLEMENT_LABELS = {
    "driver": "Driver",
    "superspeed-light": "SuperSpeed Light",
    "superspeed-medium": "SuperSpeed Medium",
    "superspeed-heavy": "SuperSpeed Heavy",
    "speed-stick-light": "SuperSpeed Light",
    "speed-stick-medium": "SuperSpeed Medium",
    "speed-stick-heavy": "SuperSpeed Heavy",
    "stack": "Stack",
    "stack-0g": "Stack 0g",
    "stack-60g": "Stack 60g",
    "stack-100g": "Stack 100g",
    "stack-120g": "Stack 120g",
    "stack-160g": "Stack 160g",
    "stack-180g": "Stack 180g",
    "stack-200g": "Stack 200g",
    "stack-220g": "Stack 220g",
    "stack-240g": "Stack 240g",
    "stack-260g": "Stack 260g",
    "stack-280g": "Stack 280g",
    "stack-300g": "Stack 300g",
    "rypstick": "Rypstick",
    "rypstick-0w": "Rypstick 0 Weights",
    "rypstick-1w": "Rypstick 1 Weight",
    "rypstick-2w": "Rypstick 2 Weights",
    "rypstick-3w": "Rypstick 3 Weights",
    "rypstick-3w-cw": "Rypstick 3 Weights + Counterweight",
    "custom": "Custom",
}

# K-LD7 angle radars (vertical = launch angle, horizontal = club path)
kld7_vertical = None
kld7_horizontal = None

# TI IWR6843 L3 rolling-buffer capture + LCMF-v1 launch angle.
iwr6843_runtime = None
iwr6843_runtime_config: dict = {"enabled": False}
camera_capture_runtime = None
camera_capture_config: dict = {"enabled": False}
camera_replay_manager = None
camera_reference_ball_tracker = None
camera_ball_flight_reference_tracker = None

# Optional LIS3DH enclosure orientation used to compensate TI mount tilt.
inclinometer_service = None
inclinometer_runtime_config: dict = {"enabled": False}

# Ballistic model toggle. Shot carry comes from the physics simulator whenever
# a vertical launch angle is available. Operators can explicitly disable it;
# missing launch inputs always fall back to the legacy table estimator.
ballistics_enabled: bool = True

# Simulator connectors (optional). Populated in main() from config/sim.json +
# CLI flags; shots fan out to every connected connector. Player/club state is
# shared across all of them.
sim_connectors: List = []
# Seed the shot counter from the clock so ShotNumber strictly increases across
# server restarts. Some sims (e.g. OpenGolfSim's Developer API) reject any
# ShotNumber <= the highest they've seen, and that counter persists across
# reconnects — a per-run reset to 1 would get every shot dropped. Uses epoch
# *seconds* (see initial_shot_counter): epoch millis overflow GSPro's 32-bit
# ShotNumber field and every shot comes back 501 "Bad format".
sim_player_state = SimPlayerState(shot_counter=initial_shot_counter())

shutdown_lock = threading.Lock()
shutdown_cleanup_started = False
# One active hardware job plus two waiting shots is enough for normal golf
# cadence without allowing a stuck peripheral to consume memory indefinitely.
_SHOT_ENRICHMENT_QUEUE_CAPACITY = 2
_SHOT_ENRICHMENT_DEADLINE_S = 20.0
# Active hardware work, the two queued jobs, and one overflow result. If more
# shots arrive, fail the oldest optional enrichment closed so OPS finalization
# can keep advancing without retaining an unbounded number of full Shot objects.
_SHOT_FINALIZATION_CAPACITY = _SHOT_ENRICHMENT_QUEUE_CAPACITY + 2
shot_enrichment_queue: queue.Queue[tuple[Shot, str, float | None]] = queue.Queue(
    maxsize=_SHOT_ENRICHMENT_QUEUE_CAPACITY
)
shot_enrichment_task = None
shot_enrichment_task_lock = threading.Lock()
_shot_sequence_number = 0
_shot_sequence_lock = threading.Lock()
_shot_callback_lock = threading.Lock()
_shot_finalization_lock = threading.Lock()
_shot_finalization_condition = threading.Condition(_shot_finalization_lock)
_shot_finalization_order: deque[int] = deque()
_shot_finalization_registered: dict[int, "_RegisteredShotFinalization"] = {}
_shot_finalization_ready: dict[int, "_PendingShotFinalization"] = {}
_shot_finalization_running = False
_shot_finalization_worker: threading.Thread | None = None


@dataclass(frozen=True)
class _ShotEnrichmentResult:
    """Optional hardware outputs needed by required shot finalization."""

    iwr6843_ms: float | None = None
    kld7_ms: float | None = None
    camera_capture_ms: float | None = None
    camera_analysis_ms: float | None = None


@dataclass(frozen=True)
class _PendingShotFinalization:
    """A completed enrichment waiting for its detection-order publication turn."""

    shot: Shot
    emit_event: str
    initial_ui_ms: float | None
    enrichment: _ShotEnrichmentResult


@dataclass(frozen=True)
class _RegisteredShotFinalization:
    """OPS-only fallback retained until optional enrichment reaches its deadline."""

    shot: Shot
    emit_event: str
    initial_ui_ms: float | None
    deadline_monotonic: float | None


def _assign_shot_number(shot: Shot) -> None:
    """Give a shot one stable session identity before any async work starts."""
    global _shot_sequence_number  # pylint: disable=global-statement

    with _shot_sequence_lock:
        if shot.shot_number is None:
            _shot_sequence_number += 1
            shot.shot_number = _shot_sequence_number
        else:
            _shot_sequence_number = max(_shot_sequence_number, shot.shot_number)


def _reset_shot_sequence() -> None:
    """Start shot identities at one for a newly started logging session."""
    global _shot_sequence_number  # pylint: disable=global-statement
    global _shot_finalization_running  # pylint: disable=global-statement

    with _shot_sequence_lock:
        _shot_sequence_number = 0
    with _shot_finalization_condition:
        _shot_finalization_order.clear()
        _shot_finalization_registered.clear()
        _shot_finalization_ready.clear()
        _shot_finalization_running = False
        _shot_finalization_condition.notify_all()


def _register_shot_for_finalization(
    shot: Shot,
    *,
    emit_event: str = "shot_update",
    initial_ui_ms: float | None = None,
    needs_watchdog: bool = False,
) -> None:
    """Record callback order and the bounded OPS-only fallback for a shot."""
    if shot.shot_number is None:
        raise ValueError("shot must have a stable number before registration")
    with _shot_finalization_condition:
        if shot.shot_number in _shot_finalization_registered:
            raise ValueError(f"shot #{shot.shot_number} is already registered")
        _shot_finalization_order.append(shot.shot_number)
        _shot_finalization_registered[shot.shot_number] = _RegisteredShotFinalization(
            shot=shot,
            emit_event=emit_event,
            initial_ui_ms=initial_ui_ms,
            deadline_monotonic=(
                time.monotonic() + _SHOT_ENRICHMENT_DEADLINE_S if needs_watchdog else None
            ),
        )
        _ensure_shot_finalization_worker_locked()
        _shot_finalization_condition.notify_all()


def _ensure_shot_finalization_worker_locked() -> None:
    """Start the exclusive finalization worker; caller holds the condition lock."""
    global _shot_finalization_worker  # pylint: disable=global-statement

    if _shot_finalization_worker is not None and _shot_finalization_worker.is_alive():
        return
    _shot_finalization_worker = threading.Thread(
        target=_shot_finalization_worker_loop,
        name="shot-finalization",
        daemon=True,
    )
    _shot_finalization_worker.start()


def _shot_finalization_worker_loop() -> None:
    """Exclusively finalize ready, overdue, or capacity-evicted shots in order."""
    global _shot_finalization_running  # pylint: disable=global-statement
    global _shot_finalization_worker  # pylint: disable=global-statement

    current_thread = threading.current_thread()
    try:
        while True:
            with _shot_finalization_condition:
                while True:
                    if not _shot_finalization_order:
                        _shot_finalization_running = False
                        if _shot_finalization_worker is current_thread:
                            _shot_finalization_worker = None
                        _shot_finalization_condition.notify_all()
                        return

                    next_shot_number = _shot_finalization_order[0]
                    registered = _shot_finalization_registered[next_shot_number]
                    pending = _shot_finalization_ready.get(next_shot_number)
                    deadline_expired = (
                        registered.deadline_monotonic is not None
                        and time.monotonic() >= registered.deadline_monotonic
                    )
                    over_capacity = len(_shot_finalization_order) > _SHOT_FINALIZATION_CAPACITY
                    if pending is not None or deadline_expired or over_capacity:
                        _shot_finalization_order.popleft()
                        del _shot_finalization_registered[next_shot_number]
                        _shot_finalization_ready.pop(next_shot_number, None)
                        _shot_finalization_running = True
                        _shot_finalization_condition.notify_all()
                        break

                    _shot_finalization_running = False
                    wait_timeout_s = None
                    if registered.deadline_monotonic is not None:
                        wait_timeout_s = max(
                            0.0,
                            registered.deadline_monotonic - time.monotonic(),
                        )
                    _shot_finalization_condition.wait(timeout=wait_timeout_s)

            if pending is None:
                reason = "deadline" if deadline_expired else "coordinator capacity"
                logger.warning(
                    "[SERVER] Shot #%d optional enrichment exceeded %s; finalizing OPS-only",
                    next_shot_number,
                    reason,
                )
                pending = _PendingShotFinalization(
                    shot=registered.shot,
                    emit_event=registered.emit_event,
                    initial_ui_ms=registered.initial_ui_ms,
                    enrichment=_ShotEnrichmentResult(),
                )
            elif pending.shot is not registered.shot:
                for shot_field in fields(Shot):
                    setattr(
                        registered.shot,
                        shot_field.name,
                        getattr(pending.shot, shot_field.name),
                    )
                pending = replace(pending, shot=registered.shot)

            try:
                _finalize_shot_detected(
                    pending.shot,
                    emit_event=pending.emit_event,
                    initial_ui_ms=pending.initial_ui_ms,
                    enrichment=pending.enrichment,
                )
            except Exception as error:  # pylint: disable=broad-exception-caught
                logger.error(
                    "[SERVER] Ordered shot finalization failed: %s",
                    error,
                    exc_info=True,
                )
                log_session_error(
                    "Ordered shot finalization failed",
                    component="server",
                    context={
                        "stage": "ordered_finalization",
                        "shot_number": pending.shot.shot_number,
                        "ball_speed_mph": pending.shot.ball_speed_mph,
                    },
                    exc=error,
                )
            finally:
                with _shot_finalization_condition:
                    _shot_finalization_running = False
                    _shot_finalization_condition.notify_all()
    finally:
        with _shot_finalization_condition:
            _shot_finalization_running = False
            if _shot_finalization_worker is current_thread:
                _shot_finalization_worker = None
            _shot_finalization_condition.notify_all()


def _shot_number_for_log(shot: Shot, session_log) -> int:
    """Use detection identity, retaining compatibility for direct helper calls."""
    if shot.shot_number is not None:
        return shot.shot_number
    return session_log.stats.get("shots_detected", 0) + 1


def _run_shutdown_step(name: str, callback) -> None:
    """Run one shutdown step without preventing later hardware cleanup."""
    started = time.monotonic()
    try:
        callback()
    except Exception:
        logger.warning("[SERVER] Shutdown cleanup failed during %s", name, exc_info=True)
    finally:
        elapsed_ms = (time.monotonic() - started) * 1000
        logger.info("[SERVER] Shutdown step %s completed in %.1fms", name, elapsed_ms)


def _cleanup_hardware_for_shutdown() -> bool:
    """Stop hardware resources and report whether this caller owned cleanup."""
    global shutdown_cleanup_started  # pylint: disable=global-statement

    with shutdown_lock:
        if shutdown_cleanup_started:
            logger.info("[SERVER] Shutdown cleanup already started")
            return False
        shutdown_cleanup_started = True

    if kld7_vertical:
        _run_shutdown_step("K-LD7 vertical stop", kld7_vertical.stop)
    if kld7_horizontal:
        _run_shutdown_step("K-LD7 horizontal stop", kld7_horizontal.stop)
    if inclinometer_service:
        _run_shutdown_step("inclinometer stop", inclinometer_service.stop)
    if iwr6843_runtime:
        _run_shutdown_step("IWR6843 stop", iwr6843_runtime.stop)
    if power_monitor:
        _run_shutdown_step("battery monitor stop", power_monitor.stop)
    if camera_capture_runtime:
        _run_shutdown_step("camera capture stop", camera_capture_runtime.stop)

    _run_shutdown_step("launch monitor stop", stop_monitor)

    for connector in sim_connectors:
        _run_shutdown_step(f"simulator connector stop ({connector.name})", connector.stop)

    return True


def _shutdown_process_after_delay(delay_s: float = 0.5) -> None:
    """Give the HTTP/WebSocket response time to flush, then clean up and exit."""
    time.sleep(delay_s)
    if not _cleanup_hardware_for_shutdown():
        return
    logger.info("[SERVER] Goodbye")
    os._exit(0)


# Max smash factor adjustment in degrees (clamped to prevent floor-dependence)
_MAX_SMASH_ADJ_LOW = -3.0  # max degrees to subtract for thin/toe hits
_MAX_SMASH_ADJ_HIGH = 2.0  # max degrees to add for high-face hits

# Degrees of launch angle change per 0.01 smash factor unit
_SMASH_DEG_PER_HUNDREDTH_LOW = 0.4  # below optimal (thin hits penalized more)
_SMASH_DEG_PER_HUNDREDTH_HIGH = 0.2  # above optimal

# Spin rate adjustment: degrees per 500 rpm deviation from optimal
_SPIN_DEG_PER_500RPM = 0.3
# Max spin adjustment in degrees (clamped like smash)
_MAX_SPIN_ADJ = 2.0

# Radar launch sanity guard. These windows are intentionally wide:
# they are meant to catch obvious K-LD7 false positives, not to micromanage
# normal shot-to-shot variation or mishits.
_RADAR_SANITY_LOW_CONF_BONUS_DEG = 5.0


def _react_app_dir() -> Path:
    """Return the best available directory containing the React index file."""
    candidates = [
        Path(app.static_folder) if app.static_folder else FRONTEND_DIST_DIR,
        FRONTEND_DIST_DIR,
        FRONTEND_SOURCE_DIR,
    ]
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return candidates[0]


def estimate_launch_angle(
    club: ClubType,
    ball_speed_mph: float,
    club_speed_mph: Optional[float] = None,
    spin_rpm: Optional[float] = None,
) -> tuple:
    """
    Estimate launch angle from club type, ball speed, and optional smash/spin data.

    Uses TrackMan averages as baseline, then adjusts for:
    - Ball speed deviation from club average
    - Smash factor deviation from optimal (if club_speed provided)
    - Spin rate deviation from optimal (if spin_rpm provided)

    Returns (vertical_angle, confidence).
    """
    physics = get_club_physics(club)

    # Slower than average → higher launch, faster → lower launch
    speed_delta = ball_speed_mph - physics.average_ball_speed_mph
    adjustment = -speed_delta * physics.launch_deg_per_mph

    confidence = 0.2

    # Smash factor adjustment: compare actual smash to optimal for this club
    if club_speed_mph is not None and club_speed_mph > 0:
        smash_factor = ball_speed_mph / club_speed_mph
        smash_delta = smash_factor - physics.optimal_smash

        if smash_delta < 0:
            smash_adj = max(_MAX_SMASH_ADJ_LOW, smash_delta * 100 * _SMASH_DEG_PER_HUNDREDTH_LOW)
        else:
            smash_adj = min(_MAX_SMASH_ADJ_HIGH, smash_delta * 100 * _SMASH_DEG_PER_HUNDREDTH_HIGH)
        adjustment += smash_adj

        confidence = 0.35

    # Spin rate adjustment: compare actual spin to optimal for this club/speed
    if spin_rpm is not None and spin_rpm > 0:
        optimal_spin = get_optimal_spin_for_ball_speed(ball_speed_mph, club)
        spin_delta = spin_rpm - optimal_spin
        spin_adj = (spin_delta / 500.0) * _SPIN_DEG_PER_500RPM
        spin_adj = max(-_MAX_SPIN_ADJ, min(_MAX_SPIN_ADJ, spin_adj))
        adjustment += spin_adj

        if confidence >= 0.35:
            confidence = 0.5
        else:
            confidence = 0.35

    launch_angle = max(5.0, round(physics.optimal_launch_deg + adjustment, 1))

    return (launch_angle, confidence)


def _radar_launch_base_delta_deg(club: ClubType) -> float:
    """Return a conservative club-family window for radar launch sanity checks."""
    if club in {ClubType.PW, ClubType.GW, ClubType.SW, ClubType.LW}:
        return 22.0
    if club in {ClubType.IRON_6, ClubType.IRON_7, ClubType.IRON_8, ClubType.IRON_9}:
        return 20.0
    return 18.0


def radar_launch_is_plausible(
    radar_angle_deg: Optional[float],
    club: ClubType,
    ball_speed_mph: float,
    club_speed_mph: Optional[float] = None,
    spin_rpm: Optional[float] = None,
) -> tuple[bool, dict]:
    """Check whether a radar launch angle is plausible for the shot profile.

    This is a wide guardrail meant to reject only obvious radar outliers. When
    the selected club is unknown or the angle is missing, we skip the guard.
    """
    if radar_angle_deg is None or club in {None, ClubType.UNKNOWN} or ball_speed_mph <= 0:
        return True, {
            "skipped": True,
            "expected_launch_deg": None,
            "allowed_delta_deg": None,
            "delta_deg": None,
        }

    expected_launch_deg, estimate_conf = estimate_launch_angle(
        club,
        ball_speed_mph,
        club_speed_mph=club_speed_mph,
        spin_rpm=spin_rpm,
    )
    allowed_delta_deg = (
        _radar_launch_base_delta_deg(club)
        + (1.0 - estimate_conf) * _RADAR_SANITY_LOW_CONF_BONUS_DEG
    )
    delta_deg = abs(radar_angle_deg - expected_launch_deg)
    if radar_angle_deg <= expected_launch_deg:
        plausible = 0.0 <= radar_angle_deg <= 45.0
    else:
        plausible = delta_deg <= allowed_delta_deg

    return plausible, {
        "skipped": False,
        "expected_launch_deg": round(expected_launch_deg, 1),
        "allowed_delta_deg": round(allowed_delta_deg, 1),
        "delta_deg": round(delta_deg, 1),
    }


def _vertical_soft_launch_lane_deg(club: ClubType) -> tuple[float, float]:
    """Return broad club-family lanes for low-confidence vertical radar candidates."""
    if club == ClubType.DRIVER:
        return (4.0, 22.0)
    if club in {ClubType.WOOD_3, ClubType.WOOD_5, ClubType.WOOD_7}:
        return (5.0, 24.0)
    if club in {ClubType.HYBRID_3, ClubType.HYBRID_5, ClubType.HYBRID_7, ClubType.HYBRID_9}:
        return (6.0, 26.0)
    if club in {ClubType.IRON_2, ClubType.IRON_3, ClubType.IRON_4, ClubType.IRON_5}:
        return (5.0, 25.0)
    if club in {ClubType.IRON_6, ClubType.IRON_7, ClubType.IRON_8, ClubType.IRON_9}:
        return (7.0, 28.0)
    if club in {ClubType.PW, ClubType.GW, ClubType.SW, ClubType.LW}:
        return (10.0, 45.0)
    return (5.0, 35.0)


def _select_vertical_radar_launch(kld7_angle, shot: Shot) -> tuple[bool, dict]:
    """Decide whether a vertical K-LD7 candidate should set the shot launch angle.

    High-confidence candidates keep the existing production behavior. Marginal
    candidates get a stricter second pass: they must agree with the launch
    estimator, sit inside a club-family lane, and avoid very long frame spans
    that often indicate clutter rather than the ball transit. Very low
    confidence candidates are still rejected, but near-threshold candidates
    that pass every other guard are allowed through so the UI can show them as
    low-confidence radar measurements instead of silently replacing them with
    the launch estimator.
    """
    details = {
        "accepted": False,
        "selection_reason": "no_candidate",
        "acceptance_path": None,
        "strict_min_confidence": _MIN_VERTICAL_RADAR_CONFIDENCE,
        "soft_min_confidence": _MIN_VERTICAL_SOFT_RADAR_CONFIDENCE,
        "low_confidence_min_confidence": _MIN_VERTICAL_LOW_CONFIDENCE_RADAR_CONFIDENCE,
        "soft_allowed_delta_deg": _VERTICAL_SOFT_ESTIMATE_DELTA_DEG,
        "soft_max_frame_count": _VERTICAL_SOFT_MAX_FRAME_COUNT,
    }
    if not kld7_angle or kld7_angle.vertical_deg is None:
        return False, details

    if _VERTICAL_RADAR_GATE_BYPASS:
        details["accepted"] = True
        details["selection_reason"] = "gate_bypassed"
        details["acceptance_path"] = "bypass"
        return True, details

    radar_angle_deg = kld7_angle.vertical_deg
    plausible, guard_details = radar_launch_is_plausible(
        radar_angle_deg=radar_angle_deg,
        club=shot.club,
        ball_speed_mph=shot.ball_speed_mph,
        club_speed_mph=shot.club_speed_mph,
        spin_rpm=shot.spin_rpm,
    )
    details.update(guard_details)
    if not plausible:
        details["selection_reason"] = "implausible_launch"
        return False, details

    if kld7_angle.confidence >= _MIN_VERTICAL_RADAR_CONFIDENCE:
        details["accepted"] = True
        details["selection_reason"] = "strict_accept"
        details["acceptance_path"] = "strict"
        return True, details

    if kld7_angle.confidence < _MIN_VERTICAL_LOW_CONFIDENCE_RADAR_CONFIDENCE:
        details["selection_reason"] = "low_confidence"
        return False, details

    if guard_details.get("skipped"):
        details["selection_reason"] = "soft_guard_unavailable"
        return False, details

    lane_min, lane_max = _vertical_soft_launch_lane_deg(shot.club)
    details["soft_lane_min_deg"] = lane_min
    details["soft_lane_max_deg"] = lane_max
    if radar_angle_deg < lane_min or radar_angle_deg > lane_max:
        details["accepted"] = True
        details["selection_reason"] = "marginal_accept:outside_soft_lane"
        details["acceptance_path"] = "marginal"
        return True, details

    delta_deg = guard_details.get("delta_deg")
    if delta_deg is None or delta_deg > _VERTICAL_SOFT_ESTIMATE_DELTA_DEG:
        details["accepted"] = True
        details["selection_reason"] = "marginal_accept:estimator_delta_too_large"
        details["acceptance_path"] = "marginal"
        return True, details

    if kld7_angle.num_frames <= 0:
        details["selection_reason"] = "no_candidate_frames"
        return False, details

    if (
        kld7_angle.num_frames > _VERTICAL_SOFT_MAX_FRAME_COUNT
        and delta_deg > _VERTICAL_SOFT_TIGHT_DELTA_FOR_LONG_FRAME_DEG
    ):
        details["accepted"] = True
        details["selection_reason"] = "marginal_accept:suspicious_frame_span"
        details["acceptance_path"] = "marginal"
        return True, details

    details["accepted"] = True
    if kld7_angle.confidence >= _MIN_VERTICAL_SOFT_RADAR_CONFIDENCE:
        details["selection_reason"] = "soft_accept"
        details["acceptance_path"] = "soft"
    else:
        details["selection_reason"] = "low_confidence_accept"
        details["acceptance_path"] = "low_confidence"
    return True, details


def _select_horizontal_radar_launch(kld7_angle, horizontal_limit: float) -> tuple[bool, dict]:
    """Decide whether a horizontal K-LD7 candidate should set the shot angle."""
    soft_limit = min(_HORIZONTAL_SOFT_ANGLE_LIMIT_DEG, max(horizontal_limit, 0.0))
    details = {
        "accepted": False,
        "selection_reason": "no_candidate",
        "acceptance_path": None,
        "horizontal_limit_deg": horizontal_limit,
        "strict_min_confidence": _MIN_HORIZONTAL_RADAR_CONFIDENCE,
        "soft_min_confidence": _MIN_HORIZONTAL_SOFT_RADAR_CONFIDENCE,
        "soft_angle_limit_deg": soft_limit,
        "soft_max_frame_count": _HORIZONTAL_SOFT_MAX_FRAME_COUNT,
        "near_limit_min_confidence": _HORIZONTAL_NEAR_LIMIT_MIN_CONFIDENCE,
        "near_limit_max_frame_count": _HORIZONTAL_NEAR_LIMIT_MAX_FRAMES,
    }
    if not kld7_angle or kld7_angle.horizontal_deg is None:
        return False, details

    abs_angle = abs(kld7_angle.horizontal_deg)
    if abs_angle > horizontal_limit:
        details["selection_reason"] = "outside_horizontal_limit"
        return False, details

    near_limit_angle = horizontal_limit * _HORIZONTAL_NEAR_LIMIT_FRACTION
    if (
        abs_angle >= near_limit_angle
        and kld7_angle.num_frames <= _HORIZONTAL_NEAR_LIMIT_MAX_FRAMES
        and kld7_angle.confidence < _HORIZONTAL_NEAR_LIMIT_MIN_CONFIDENCE
    ):
        details["selection_reason"] = "weak_near_limit"
        details["near_limit_angle_deg"] = round(near_limit_angle, 1)
        return False, details

    if kld7_angle.confidence >= _MIN_HORIZONTAL_RADAR_CONFIDENCE:
        details["accepted"] = True
        details["selection_reason"] = "strict_accept"
        details["acceptance_path"] = "strict"
        return True, details

    if kld7_angle.confidence < _MIN_HORIZONTAL_SOFT_RADAR_CONFIDENCE:
        details["selection_reason"] = "low_confidence"
        return False, details

    if abs_angle > soft_limit:
        details["selection_reason"] = "outside_soft_lane"
        return False, details

    if kld7_angle.num_frames <= 0:
        details["selection_reason"] = "no_candidate_frames"
        return False, details

    if kld7_angle.num_frames > _HORIZONTAL_SOFT_MAX_FRAME_COUNT:
        details["selection_reason"] = "suspicious_frame_span"
        return False, details

    details["accepted"] = True
    details["selection_reason"] = "soft_accept"
    details["acceptance_path"] = "soft"
    return True, details


def _ensure_user_facing_launch_angles(shot: Shot) -> None:
    """Provide a vertical estimate without inventing a horizontal measurement."""
    estimated: tuple[float, float] | None = None

    if shot.launch_angle_vertical is None:
        estimated = estimate_launch_angle(
            shot.club,
            shot.ball_speed_mph,
            club_speed_mph=shot.club_speed_mph,
            spin_rpm=shot.spin_rpm,
        )
        shot.launch_angle_vertical = estimated[0]
        shot.launch_angle_confidence = estimated[1]
        shot.launch_angle_vertical_confidence = estimated[1]
        shot.launch_angle_vertical_source = "estimated"
        shot.angle_source = "estimated"
        logger.info(
            "[SERVER] Angle source: estimated (%.1f°, conf=%.0f%%)",
            estimated[0],
            estimated[1] * 100,
        )

    if shot.launch_angle_horizontal is None:
        camera_capture_enabled = bool(camera_capture_config.get("enabled"))
        if iwr6843_runtime is not None or camera_capture_enabled:
            logger.info("[SERVER] Horizontal angle unavailable; no measured trajectory")
            return
        # Preserve the legacy neutral estimate for installations that have no
        # horizontal-capable TI/camera pipeline at all.
        shot.launch_angle_horizontal = 0.0
        if shot.launch_angle_horizontal_confidence is None:
            if estimated is None:
                estimated = estimate_launch_angle(
                    shot.club,
                    shot.ball_speed_mph,
                    club_speed_mph=shot.club_speed_mph,
                    spin_rpm=shot.spin_rpm,
                )
            shot.launch_angle_horizontal_confidence = estimated[1]
        shot.launch_angle_horizontal_source = "estimated"
        if shot.angle_source is None:
            shot.angle_source = "estimated"
        logger.info("[SERVER] Horizontal angle source: neutral estimate (0.0°)")


# K-LD7 produces ~34 RADC frames/sec at 3 Mbaud. With buffer_seconds=6
# the steady-state buffer is ~204 frames. If the snapshot at shot time
# is dramatically less than that, the radar's stream rate dropped.
# Surface as WARN so cabling/USB issues are visible without a replay.
_KLD7_FRAME_HZ = 34.0
_KLD7_BUFFER_SECONDS = 6.0
_KLD7_BUFFER_UNDERFILL_FRAC = 0.5
_KLD7_POST_SHOT_CAPTURE_DELAY_S = 0.18
_MIN_VERTICAL_RADAR_CONFIDENCE = 0.80
_MIN_VERTICAL_SOFT_RADAR_CONFIDENCE = 0.68
_MIN_VERTICAL_LOW_CONFIDENCE_RADAR_CONFIDENCE = 0.65
# Test-mode escape hatch (--kld7-vertical-raw): when True,
# _select_vertical_radar_launch accepts ANY vertical radar candidate and skips
# every guard, so the UI shows the radar's launch angle for every shot the
# estimator produces. Default False leaves production behavior unchanged.
_VERTICAL_RADAR_GATE_BYPASS = False
# Display confidence for radar measurements that pass the physics guard but
# trip a soft consistency guard (club lane / estimator delta / frame span):
# shown as radar with a single confidence dot (<0.4) instead of silently
# being replaced by the club estimate.
_VERTICAL_MARGINAL_DISPLAY_CONFIDENCE = 0.38
_VERTICAL_SOFT_ESTIMATE_DELTA_DEG = 4.5
_VERTICAL_SOFT_MAX_FRAME_COUNT = 40
_VERTICAL_SOFT_TIGHT_DELTA_FOR_LONG_FRAME_DEG = 2.0
_MIN_HORIZONTAL_RADAR_CONFIDENCE = 0.40
_MIN_HORIZONTAL_SOFT_RADAR_CONFIDENCE = 0.30
_HORIZONTAL_SOFT_ANGLE_LIMIT_DEG = 5.0
_HORIZONTAL_SOFT_MAX_FRAME_COUNT = 40
_HORIZONTAL_NEAR_LIMIT_FRACTION = 0.80
_HORIZONTAL_NEAR_LIMIT_MAX_FRAMES = 2
_HORIZONTAL_NEAR_LIMIT_MIN_CONFIDENCE = 0.80


def _maybe_wait_for_kld7_post_shot_frames(shot_timestamp: float) -> None:
    """Let the K-LD7 stream collect post-impact RADC frames for extraction.

    TrackMan test logs showed immediate snapshots ending at or just before
    the OPS impact timestamp, leaving the configured post-shot extraction
    window empty. Wait only until the bounded target time; if processing is
    already past that point, this adds no latency.
    """
    target_time = shot_timestamp + _KLD7_POST_SHOT_CAPTURE_DELAY_S
    delay_s = target_time - time.time()
    if delay_s <= 0:
        return
    logger.info(
        "[SERVER] Waiting %.0fms for post-impact K-LD7 RADC frames",
        delay_s * 1000.0,
    )
    time.sleep(delay_s)


def _warn_if_kld7_buffer_underfilled(orientation: str, frame_count: int) -> None:
    """Log a WARNING when the K-LD7 ring-buffer snapshot is far below
    the expected steady-state size at shot time.
    """
    expected = int(_KLD7_FRAME_HZ * _KLD7_BUFFER_SECONDS)
    if expected <= 0 or frame_count <= 0:
        return
    if frame_count < expected * _KLD7_BUFFER_UNDERFILL_FRAC:
        logger.warning(
            "[SERVER] K-LD7 %s buffer underfilled: %d/%d frames (%.0f%%) — "
            "stream rate dropped, check USB cabling and contention.",
            orientation,
            frame_count,
            expected,
            100.0 * frame_count / expected,
        )


def _kld7_angle_log_payload(
    angle,
    axis_field: str,
    selection_details: Optional[dict] = None,
) -> Optional[dict]:
    """Build the compact K-LD7 angle payload used in session logs."""
    if angle is None:
        return None

    payload = {
        axis_field: getattr(angle, axis_field),
        "confidence": angle.confidence,
        "detection_class": angle.detection_class,
        "magnitude": angle.magnitude,
        "num_frames": angle.num_frames,
        "frames_examined": angle.frames_examined,
        "frames_available": angle.frames_available,
        "frames_ignored_stale": angle.frames_ignored_stale,
    }
    radc_selection = getattr(angle, "radc_selection", None)
    if radc_selection:
        payload["radc_selection"] = radc_selection
    if selection_details:
        payload.update(selection_details)
    return payload


def _session_start_config() -> dict:
    """Return hardware configuration recorded at session start."""
    config = radar_config.copy()
    config["iwr6843"] = dict(iwr6843_runtime_config)
    config["camera_capture"] = dict(camera_capture_config)
    config["inclinometer"] = dict(inclinometer_runtime_config)
    config["power"] = {
        "enabled": battery_provider is not None,
        "provider": battery_provider,
    }
    return config


ball_speed_correction_enabled = False
ball_speed_correction_distance_ft = 5.5
ball_speed_correction_ball_above_radar_ft = -4.0 / 12.0
calculated_spin_enabled = False


def shot_to_dict(shot: Shot) -> dict:
    """Return the UI shot schema with display-oriented rounding."""
    data = shot.to_dict()
    for log_only_field in ("mode", "readings", "readings_count"):
        data.pop(log_only_field)
    for field, digits in {
        "ball_speed_mph": 1,
        "ball_speed_raw_mph": 1,
        "club_speed_mph": 1,
        "smash_factor": 2,
        "estimated_carry_yards": None,
        "spin_rpm": None,
        "spin_rpm_measured": None,
        "spin_confidence": 2,
        "spin_multipath_fade_hz": 2,
        "spin_snr": 2,
        "spin_modulation_depth": 4,
        "spin_peak_freq_hz": 2,
        "spin_candidate_rpm": None,
        "spin_seam_cycles": 2,
        "spin_phase_rpm": None,
        "spin_phase_snr": 2,
        "spin_phase_agreement_pct": 1,
        "carry_spin_adjusted": None,
    }.items():
        if data[field] is not None:
            data[field] = round(data[field], digits) if digits is not None else round(data[field])
    data["carry_range"] = [round(value) for value in data["carry_range"]]
    return data


@app.route("/")
def index():
    """Serve the React app."""
    return send_from_directory(_react_app_dir(), "index.html")


@app.route("/display", strict_slashes=False)
def display():
    """Serve the React app for TV display mode."""
    return send_from_directory(_react_app_dir(), "index.html")


@app.route("/<path:path>")
def static_files(path):
    """Serve static files."""
    return send_from_directory(app.static_folder, path)


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    """Cleanly shut down the server via REST API."""
    logger.info("[SERVER] Shutdown requested via REST API")
    threading.Thread(target=_shutdown_process_after_delay, daemon=True).start()
    return {"status": "shutting_down"}, 200


def init_camera_capture(
    *,
    output_dir: str | Path,
    gpio_pin: int,
    width: int,
    height: int,
    fps: float,
    pre_ms: float,
    post_ms: float,
    exposure_us: int,
    gain: float,
    stream: str,
    rotate_180: bool,
    mirror_horizontal: bool,
    roll_correction_deg: float,
    scaler_crop: tuple[int, int, int, int] | None,
    mount_height_m: float,
    lateral_offset_m: float,
    horizontal_offset_deg: float,
    use_gpio_trigger: bool,
    shot_analysis_enabled: bool = False,
) -> bool:
    """Initialize passive high-speed camera capture for offline alignment."""
    global camera_capture_runtime, camera_capture_config  # pylint: disable=global-statement
    global camera_replay_manager  # pylint: disable=global-statement
    global camera_reference_ball_tracker  # pylint: disable=global-statement
    global camera_ball_flight_reference_tracker  # pylint: disable=global-statement
    try:
        from .camera.capture_runtime import CameraCaptureRuntime, CameraCaptureSettings

        settings = CameraCaptureSettings(
            width=width,
            height=height,
            fps=fps,
            pre_ms=pre_ms,
            post_ms=post_ms,
            exposure_us=exposure_us,
            gain=gain,
            stream=stream,
            rotate_180=rotate_180,
            mirror_horizontal=mirror_horizontal,
            roll_correction_deg=roll_correction_deg,
            scaler_crop=scaler_crop,
            gpio_pin=gpio_pin,
            auto_exposure_state_path=(
                Path.home() / ".config" / "openflight" / "camera-exposure.json"
            ),
        )
        camera_capture_runtime = CameraCaptureRuntime(
            output_dir=output_dir,
            settings=settings,
            use_gpio_trigger=use_gpio_trigger,
        )
        camera_capture_runtime.start()
        settings = camera_capture_runtime.settings
        from .camera.replay import CameraReplayManager

        camera_replay_manager = CameraReplayManager(output_dir)
        from openflight.camera.club_delivery import (  # noqa: PLC0415
            ReferenceBallTracker,
        )

        camera_reference_ball_tracker = ReferenceBallTracker()
        camera_ball_flight_reference_tracker = ReferenceBallTracker()
        camera_capture_config = {
            "enabled": True,
            "shot_analysis_enabled": shot_analysis_enabled,
            "output_dir": str(Path(output_dir).expanduser()),
            "gpio_pin_bcm": gpio_pin,
            "trigger_source": "gpio" if use_gpio_trigger else "iwr6843_fanout",
            "width": settings.width,
            "height": settings.height,
            "fps": settings.fps,
            "pre_ms": settings.pre_ms,
            "post_ms": settings.post_ms,
            "pre_frames": settings.pre_frames,
            "post_frames": settings.post_frames,
            "exposure_us": settings.exposure_us,
            "gain": settings.gain,
            "auto_exposure_enabled": settings.auto_exposure,
            "stream": settings.stream,
            "rotate_180": settings.rotate_180,
            "mirror_horizontal": settings.mirror_horizontal,
            "roll_correction_deg": settings.roll_correction_deg,
            "scaler_crop": settings.scaler_crop,
            "mount_height_m": mount_height_m,
            "lateral_offset_m": lateral_offset_m,
            "horizontal_offset_deg": horizontal_offset_deg,
            "alignment_x_pct": 50.0,
            "alignment_y_pct": 50.0,
        }
        logger.info("[SERVER] Camera capture initialized: %s", camera_capture_config)
        return True
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] Camera capture initialization failed: %s", error, exc_info=True)
        log_session_error(
            "Camera capture initialization failed",
            component="camera_capture",
            context={"output_dir": str(output_dir)},
            exc=error,
        )
        camera_capture_runtime = None
        camera_replay_manager = None
        camera_reference_ball_tracker = None
        camera_ball_flight_reference_tracker = None
        camera_capture_config = {"enabled": False, "error": str(error)}
        return False


def init_iwr6843(
    *,
    port: str | None,
    config_path: str,
    calibration_path: str,
    output_dir: str | Path,
    trigger_pin: int,
    tee_range_m: float,
    net_range_m: float | None,
    tx_order: str,
    capture_timeout_s: float,
    tilt_deg: float | None = None,
    radar_height_m: float | None = None,
    ball_height_m: float = 0.04,
    azimuth_offset_deg: float = 0.0,
    horizontal_phase_reference_rad: float | None = None,
    save_dumps: bool = False,
) -> bool:
    """Initialize GPIO-triggered TI capture and the frozen LCMF-v1 estimator."""
    global iwr6843_runtime, iwr6843_runtime_config  # pylint: disable=global-statement
    try:
        from .iwr6843 import Calibration
        from .iwr6843.monitor import IWR6843CaptureMonitor, tx_order_from_config
        from .iwr6843.runtime import IWR6843Runtime

        configured_order = tx_order_from_config(config_path)
        resolved_order = configured_order if tx_order == "auto" else tx_order
        if resolved_order != configured_order:
            raise ValueError(
                f"--iwr6843-tx-order {resolved_order} conflicts with "
                f"{Path(config_path).name} ({configured_order})"
            )

        calibration = Calibration.load(calibration_path)
        calibration.tee_range_m = tee_range_m
        calibration.tee_ball_height_m = ball_height_m
        if tilt_deg is not None:
            calibration.tilt_rad = math.radians(tilt_deg)
        if radar_height_m is not None:
            calibration.meta["radar_height_m"] = radar_height_m

        capture_monitor = IWR6843CaptureMonitor(
            config_path=config_path,
            output_dir=output_dir,
            port=port,
            gpio_pin=trigger_pin,
            save_dumps=save_dumps,
            trigger_observers=(
                [camera_capture_runtime.notify_trigger]
                if camera_capture_runtime is not None
                else None
            ),
        )
        # OPS initialization can pulse the shared sound gate. Configure TI now,
        # but do not accept edges until the OPS trigger path is fully running.
        capture_monitor.start(armed=False)
        iwr6843_runtime = IWR6843Runtime(
            capture_monitor=capture_monitor,
            calibration=calibration,
            net_range_m=net_range_m,
            tx_order=resolved_order,
            capture_timeout_s=capture_timeout_s,
            azimuth_offset_deg=azimuth_offset_deg,
            horizontal_phase_reference_rad=horizontal_phase_reference_rad,
            # The supported normal-TX profiles have a measured positive TDM
            # registration. Auto sign selection can choose the mirror solution
            # in multipath and collapse the eight-element vertical channel.
            tdm_sign_policy="positive",
        )
        iwr6843_runtime_config = {
            "enabled": True,
            "estimator": "lcmf_v1",
            "port": capture_monitor.port,
            "config": str(config_path),
            "calibration": str(calibration_path),
            "trigger_pin_bcm": trigger_pin,
            "tee_slant_range_m": tee_range_m,
            "net_range_m": net_range_m,
            "tx_order": resolved_order,
            "tdm_sign_policy": iwr6843_runtime.tdm_sign_policy,
            "tilt_deg": math.degrees(calibration.tilt_rad),
            "radar_height_m": calibration.radar_height_m,
            "ball_height_m": calibration.tee_ball_height_m,
            "azimuth_offset_deg": azimuth_offset_deg,
            "horizontal_phase_reference_rad": horizontal_phase_reference_rad,
            "capture_timeout_s": capture_timeout_s,
            "freeze_delay_ms": 0.0,
            "raw_dump_saved": save_dumps,
            "output_dir": str(Path(output_dir).expanduser()),
        }
        logger.info(
            "[SERVER] IWR6843 initialized "
            "(port=%s, BCM%d, estimator=LCMF-v1, firmware boundary freeze)",
            capture_monitor.port,
            trigger_pin,
        )
        return True
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] IWR6843 initialization failed: %s", error, exc_info=True)
        log_session_error(
            "IWR6843 initialization failed",
            component="iwr6843",
            context={"config": config_path, "port": port or "auto"},
            exc=error,
        )
        iwr6843_runtime = None
        iwr6843_runtime_config = {"enabled": False, "error": str(error)}
        return False


def _iwr6843_startup_recovery(error: object) -> str:
    """Translate a known TI initialization failure into operator guidance."""
    normalized_error = str(error or "").casefold()
    if "press reset and retry" in normalized_error or "firmware may be wedged" in normalized_error:
        return "Press RESET on the TI radar, then relaunch OpenFlight."
    return "Check the TI radar USB and power connections, then relaunch OpenFlight."


def init_inclinometer(*, zero_offset_deg: float, bus_number: int = 1, address: int = 0x18) -> bool:
    """Start the optional LIS3DH service without risking radar availability."""
    global inclinometer_service  # pylint: disable=global-statement
    global inclinometer_runtime_config  # pylint: disable=global-statement

    service = None
    try:
        from .inclinometer import LIS3DH, InclinometerService

        service = InclinometerService(
            LIS3DH(bus_number=bus_number, address=address),
            zero_offset_deg=zero_offset_deg,
        )
        service.start()
        startup = service.wait_for_stable(timeout_s=2.0)
        inclinometer_service = service
        inclinometer_runtime_config = {
            "enabled": True,
            "sensor": "lis3dh",
            "i2c_bus": bus_number,
            "i2c_address": f"0x{address:02x}",
            "sample_hz": service.sample_hz,
            "zero_offset_deg": zero_offset_deg,
            "startup": startup.to_dict(),
        }
        if startup.snapshot is None:
            logger.warning(
                "[SERVER] LIS3DH initialized but has no stable startup reading (%s)",
                startup.status,
            )
            print(f"Inclinometer enabled, waiting for a stable reading ({startup.status})")
            return True

        snapshot = startup.snapshot
        print(
            "Inclinometer enabled "
            f"(raw pitch {snapshot.raw_pitch_deg:+.2f}deg, "
            f"calibrated {snapshot.calibrated_pitch_deg:+.2f}deg)"
        )
        if iwr6843_runtime is not None:
            configured_tilt = math.degrees(iwr6843_runtime.calibration.tilt_rad)
            effective_tilt = configured_tilt + snapshot.calibrated_pitch_deg
            print(
                f"IWR6843 tilt: configured {configured_tilt:.2f}deg, "
                f"effective {effective_tilt:.2f}deg"
            )
        return True
    except Exception as error:  # pylint: disable=broad-exception-caught
        if service is not None:
            try:
                service.stop()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.debug("Failed to close LIS3DH after initialization error", exc_info=True)
        logger.warning("[SERVER] Inclinometer initialization failed: %s", error, exc_info=True)
        log_session_error(
            "Inclinometer initialization failed",
            component="inclinometer",
            context={"i2c_bus": bus_number, "i2c_address": f"0x{address:02x}"},
            exc=error,
        )
        inclinometer_service = None
        inclinometer_runtime_config = {
            "enabled": False,
            "requested": True,
            "sensor": "lis3dh",
            "i2c_bus": bus_number,
            "i2c_address": f"0x{address:02x}",
            "zero_offset_deg": zero_offset_deg,
            "error": str(error),
        }
        return False


def init_kld7(
    port=None,
    orientation="vertical",
    angle_offset_deg=0.0,
    base_freq=0,
    mount_tilt_deg=18.0,
    ball_distance_ft=5.5,
    vertical_flight_window_net_distance_ft=10.0,
) -> bool:
    """Initialize a single K-LD7 angle radar tracker.

    Returns True if the tracker connected and started successfully.
    Sets the appropriate global (kld7_vertical or kld7_horizontal).
    """
    global kld7_vertical, kld7_horizontal  # pylint: disable=global-statement
    try:
        from openflight.kld7 import KLD7Tracker

        tracker = KLD7Tracker(
            port=port,
            orientation=orientation,
            angle_offset_deg=angle_offset_deg,
            base_freq=base_freq,
            buffer_seconds=6.0,
            vertical_estimator="two_ray" if orientation == "vertical" else "naive",
            mount_tilt_deg=mount_tilt_deg,
            ball_distance_ft=ball_distance_ft,
            vertical_flight_window_net_distance_ft=vertical_flight_window_net_distance_ft,
        )
        if tracker.connect():
            tracker.start()
            logger.info(
                "[SERVER] K-LD7 %s initialized (port=%s, offset=%.1f°, RBFR=%d)",
                orientation,
                port or "auto",
                angle_offset_deg,
                base_freq,
            )
            session_log = get_session_logger()
            if session_log:
                session_log.log_connection(
                    device="kld7_%s" % orientation,
                    port=tracker.port or "auto",
                    baud=3000000,
                    radc_available=True,
                    base_freq=base_freq,
                )
            if orientation == "vertical":
                kld7_vertical = tracker
            else:
                kld7_horizontal = tracker
            return True
        else:
            return False
    except Exception as e:
        logger.warning("[SERVER] K-LD7 %s initialization failed: %s", orientation, e, exc_info=True)
        log_session_error(
            "K-LD7 initialization failed",
            component="kld7",
            context={"orientation": orientation},
            exc=e,
        )
        return False


@app.route("/api/camera/preview.jpg")
def camera_capture_preview():
    """Single still from the capture runtime's concurrent main stream.

    Served from the processed YUV stream while the raw rolling buffer keeps
    running, so shots are never missed while the camera tab polls this.
    """
    if camera_capture_runtime is None:
        return "Camera capture not enabled", 404
    jpeg = camera_capture_runtime.capture_preview_jpeg()
    if jpeg is None:
        return "Camera not running", 503
    return Response(jpeg, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})


@app.route("/api/camera/exposure-quality")
def camera_capture_exposure_quality():
    """Automatic exposure state derived from the latest impact-zone pixels."""
    if camera_capture_runtime is None:
        return jsonify({"sample_available": False, "status": "unavailable"}), 404
    quality = camera_capture_runtime.exposure_quality()
    quality["auto_exposure"] = camera_capture_runtime.auto_exposure_status()
    return jsonify(quality)


@app.route("/api/camera/replays/<replay_id>/prepare", methods=["GET", "POST"])
def prepare_camera_replay(replay_id: str):
    """Create a cached MP4 only after an explicit replay interaction."""
    from .camera.replay import ReplayNotFoundError, ReplayPreparationError

    if request.method != "POST":
        return jsonify({"error": "Use POST to prepare a camera replay"}), 405
    if camera_replay_manager is None:
        return jsonify({"error": "Camera replay was not found"}), 404
    try:
        prepared = camera_replay_manager.prepare(replay_id)
    except ReplayNotFoundError as error:
        return jsonify({"error": str(error)}), 404
    except ReplayPreparationError as error:
        logger.warning("[CAMERA] Could not prepare replay %s: %s", replay_id, error)
        log_session_error(
            "Camera replay preparation failed",
            component="camera_capture",
            context={"replay_id": replay_id},
            exc=error,
        )
        return jsonify({"error": str(error)}), 503
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.exception("[CAMERA] Unexpected replay preparation failure for %s", replay_id)
        log_session_error(
            "Camera replay preparation failed",
            component="camera_capture",
            context={"replay_id": replay_id},
            exc=error,
        )
        return jsonify({"error": "Camera replay could not be prepared"}), 500

    payload = dict(prepared.payload)
    payload["video_url"] = f"/api/camera/replays/{replay_id}/video"
    return jsonify(payload)


@app.route("/api/camera/replays/<replay_id>/video")
def camera_replay_video(replay_id: str):  # pylint: disable=too-many-return-statements
    """Stream a previously prepared replay with HTTP range support."""
    from .camera.replay import (
        ReplayNotFoundError,
        ReplayNotReadyError,
        ReplayPreparationError,
    )

    if camera_replay_manager is None:
        return jsonify({"error": "Camera replay was not found"}), 404
    try:
        video_path = camera_replay_manager.video_path(replay_id)
    except ReplayNotFoundError as error:
        return jsonify({"error": str(error)}), 404
    except ReplayNotReadyError as error:
        return jsonify({"error": str(error)}), 409
    except ReplayPreparationError as error:
        logger.warning("[CAMERA] Could not read replay %s: %s", replay_id, error)
        return jsonify({"error": str(error)}), 503
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.exception("[CAMERA] Unexpected replay lookup failure for %s", replay_id)
        log_session_error(
            "Camera replay lookup failed",
            component="camera_capture",
            context={"replay_id": replay_id},
            exc=error,
        )
        return jsonify({"error": "Camera replay video is unavailable"}), 500
    try:
        return send_file(video_path, mimetype="video/mp4", conditional=True, etag=True)
    except OSError as error:
        logger.warning("[CAMERA] Could not stream replay %s: %s", replay_id, error)
        return jsonify({"error": "Camera replay video is unavailable"}), 503
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.exception("[CAMERA] Unexpected replay streaming failure for %s", replay_id)
        log_session_error(
            "Camera replay streaming failed",
            component="camera_capture",
            context={"replay_id": replay_id},
            exc=error,
        )
        return jsonify({"error": "Camera replay video is unavailable"}), 500


def _camera_capture_settings_payload() -> dict:
    """Return camera controls and capture state for the Camera tab."""
    payload = dict(camera_capture_config)
    payload["available"] = camera_capture_runtime is not None
    payload.setdefault("alignment_x_pct", 50.0)
    payload.setdefault("alignment_y_pct", 50.0)
    if camera_capture_runtime is not None:
        payload.update(camera_capture_runtime.status())
        payload.update(camera_capture_runtime.vertical_crop_status())
        payload["exposure_us"] = getattr(
            camera_capture_runtime.settings,
            "exposure_us",
            payload.get("exposure_us"),
        )
        payload["gain"] = getattr(
            camera_capture_runtime.settings,
            "gain",
            payload.get("gain"),
        )
        frame_period_us = round(1_000_000 / camera_capture_runtime.settings.fps)
        payload["max_exposure_us"] = frame_period_us - 1
    return payload


@socketio.on("get_camera_capture_settings")
def handle_get_camera_capture_settings():
    """Send current high-speed capture settings to the requesting UI."""
    socketio.emit("camera_capture_settings", _camera_capture_settings_payload())


@socketio.on("set_camera_capture_settings")
def handle_set_camera_capture_settings(data):
    """Apply live-safe camera controls and alignment-guide position."""
    if camera_capture_runtime is None:
        socketio.emit(
            "camera_capture_settings_error",
            {"error": "High-speed camera capture is not running"},
        )
        return
    if not isinstance(data, dict):
        socketio.emit(
            "camera_capture_settings_error",
            {"error": "Camera settings must be an object"},
        )
        return

    try:
        if "exposure_us" in data or "gain" in data:
            raise ValueError("Camera exposure and gain are managed automatically")
        alignment_x_pct = float(
            data.get("alignment_x_pct", camera_capture_config.get("alignment_x_pct", 50.0))
        )
        alignment_y_pct = float(
            data.get("alignment_y_pct", camera_capture_config.get("alignment_y_pct", 50.0))
        )
        if not 0.0 <= alignment_x_pct <= 100.0:
            raise ValueError("horizontal alignment must be between 0 and 100 percent")
        if not 0.0 <= alignment_y_pct <= 100.0:
            raise ValueError("vertical alignment must be between 0 and 100 percent")

        crop_update = {}
        if "vertical_offset_px" in data:
            crop_update = camera_capture_runtime.update_vertical_crop(
                int(data["vertical_offset_px"])
            )

        camera_capture_config.update(
            {
                **crop_update,
                "alignment_x_pct": alignment_x_pct,
                "alignment_y_pct": alignment_y_pct,
            }
        )
        session_log = get_session_logger()
        if session_log:
            session_log.log_config_change(
                {"camera_capture": dict(camera_capture_config)},
                source="camera_ui",
            )
        socketio.emit("camera_capture_settings", _camera_capture_settings_payload())
    except (KeyError, TypeError, ValueError, RuntimeError) as error:
        logger.warning("[SERVER] Camera settings update rejected: %s", error)
        socketio.emit("camera_capture_settings_error", {"error": str(error)})


def start_debug_logging():
    """Start logging raw readings to a file."""
    global debug_log_file, debug_log_path  # pylint: disable=global-statement

    # Create logs directory
    log_dir = Path.home() / "openflight_logs"
    log_dir.mkdir(exist_ok=True)

    # Create timestamped log file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_log_path = log_dir / f"debug_{timestamp}.jsonl"
    debug_log_file = open(debug_log_path, "w")  # pylint: disable=consider-using-with

    # Enable radar raw logging
    radar_logger = logging.getLogger("ops243")
    radar_raw_logger = logging.getLogger("ops243.raw")
    radar_logger.setLevel(logging.DEBUG)
    radar_raw_logger.setLevel(logging.DEBUG)

    # Add file handler for raw radar data
    raw_log_path = log_dir / f"radar_raw_{timestamp}.log"
    file_handler = logging.FileHandler(raw_log_path)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
    radar_raw_logger.addHandler(file_handler)
    radar_logger.addHandler(file_handler)

    print(f"Debug logging to: {debug_log_path}")
    print(f"Raw radar logging to: {raw_log_path}")
    return str(debug_log_path)


def stop_debug_logging():
    """Stop logging and close the file."""
    global debug_log_file, debug_log_path  # pylint: disable=global-statement

    if debug_log_file:
        debug_log_file.close()
        debug_log_file = None
        print(f"Debug log saved: {debug_log_path}")


def log_debug_reading(reading: SpeedReading):
    """Log a raw reading to the debug file."""
    if debug_log_file:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "reading",
            "speed": reading.speed,
            "direction": reading.direction.value,
            "magnitude": reading.magnitude,
            "unit": reading.unit,
        }
        debug_log_file.write(json.dumps(entry) + "\n")
        debug_log_file.flush()

        # Also print to console for immediate feedback
        print(
            f"[RADAR] {reading.speed:.1f} mph {reading.direction.value} (mag={reading.magnitude})"
        )


def on_live_reading(reading: SpeedReading):
    """Callback for live radar readings - used in debug mode."""
    # Log ALL readings first (before filtering) so we can debug direction issues
    if debug_mode:
        log_debug_reading(reading)

        # Emit ALL readings to UI debug panel (including inbound)
        socketio.emit(
            "debug_reading",
            {
                "speed": reading.speed,
                "direction": reading.direction.value,
                "magnitude": reading.magnitude,
                "timestamp": datetime.now().isoformat(),
                "filtered": reading.direction != Direction.OUTBOUND,
            },
        )

    # Filter out inbound readings for shot detection
    # Note: shot filtering happens in launch_monitor.py but we also filter here
    # for any UI purposes that need only outbound readings
    if reading.direction != Direction.OUTBOUND:
        return


def _get_trigger_status() -> dict:
    """Build trigger status payload for the UI."""
    from .rolling_buffer import RollingBufferMonitor  # pylint: disable=import-outside-toplevel
    from .swing_speed import SwingSpeedMonitor  # pylint: disable=import-outside-toplevel

    is_rolling_buffer = isinstance(monitor, RollingBufferMonitor)
    is_swing_speed = isinstance(monitor, (SwingSpeedMonitor, MockSwingSpeedMonitor))
    session_logger = get_session_logger()
    stats = session_logger.stats if session_logger else {}

    if is_swing_speed:
        mode = "swing-speed"
    elif mock_mode:
        mode = "mock"
    else:
        mode = "rolling-buffer"
    trigger_type = None
    radar_port = None

    if is_rolling_buffer:
        trigger_type = monitor.trigger_type
    if is_rolling_buffer or is_swing_speed:
        if hasattr(monitor, "radar") and hasattr(monitor.radar, "port"):
            radar_port = monitor.radar.port

    return {
        "mode": mode,
        "trigger_type": trigger_type,
        "radar_connected": monitor is not None and not mock_mode,
        "radar_port": radar_port,
        "triggers_total": stats.get("triggers_total", 0),
        "triggers_accepted": stats.get("triggers_accepted", 0),
        "triggers_rejected": stats.get("triggers_rejected", 0),
    }


def _current_club_id() -> str:
    """Club id the kiosk should restore after a reload."""
    if monitor is None:
        return ClubType.DRIVER.value
    club = getattr(monitor, "_current_club", None)
    if club is None:
        return ClubType.DRIVER.value
    return club.value if hasattr(club, "value") else str(club)


def _session_state_payload(*, include_runtime_meta: bool = False) -> dict:
    """Build the session_state event the UI applies on connect and refresh."""
    payload = {
        "stats": monitor.get_session_stats() if monitor else {},
        "shots": _session_shots(),
        "club": _current_club_id(),
    }
    if include_runtime_meta:
        payload.update(
            {
                "mock_mode": mock_mode,
                "debug_mode": debug_mode,
            }
        )
    return payload


def _session_shots() -> list[dict]:
    """Return current session rows in the UI's shot-shaped payload format."""
    from .swing_speed import SwingSpeedMonitor  # pylint: disable=import-outside-toplevel

    if not monitor:
        return []
    if isinstance(monitor, (SwingSpeedMonitor, MockSwingSpeedMonitor)):
        return [swing_speed_to_shot_dict(event) for event in monitor.get_events()]
    return [shot_to_dict(shot) for shot in monitor.get_shots()]


def _unregister_camera_replay(shot: Shot) -> None:
    """Revoke live replay access while preserving the session artifacts."""
    replay = getattr(shot, "camera_replay", None)
    replay_id = replay.get("id") if isinstance(replay, dict) else None
    if not replay_id or camera_replay_manager is None:
        return
    try:
        camera_replay_manager.unregister(replay_id)
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[CAMERA] Could not unregister replay %s: %s", replay_id, error)


def _delete_session_row(timestamp: str) -> bool:
    """Delete one shot or swing-speed rep by UI timestamp."""
    from .swing_speed import SwingSpeedMonitor  # pylint: disable=import-outside-toplevel

    if not monitor or not timestamp:
        return False

    if isinstance(monitor, (SwingSpeedMonitor, MockSwingSpeedMonitor)):
        events = getattr(monitor, "_events", None)
        if events is None:
            return False
        for index, event in enumerate(events):
            if event.timestamp.isoformat() == timestamp:
                del events[index]
                return True
        return False

    shots = getattr(monitor, "_shots", None)
    if shots is None:
        return False
    for index, shot in enumerate(shots):
        if shot.timestamp.isoformat() == timestamp:
            _unregister_camera_replay(shot)
            del shots[index]
            return True
    return False


def _emit_sim_snapshot() -> None:
    """Emit the current status of every configured simulator connector.

    The UI builds its connector buttons from ``sim_status`` events, which
    otherwise only fire on connection-state *changes* (see _sim_on_status). A
    client that connects or refreshes after a connector already reached its
    state would miss those events and show no button — the intermittent
    "sometimes the sim status shows, sometimes it doesn't". Replaying a snapshot
    on connect guarantees a button for every enabled connector (sim_connectors
    holds only the enabled ones) carrying its live state.
    """
    for connector in sim_connectors:
        socketio.emit(
            "sim_status",
            {
                "target": connector.name,
                "state": connector.state.value,
                "host": connector.host,
                "port": connector.port,
            },
        )


def _on_power_status(status: PowerStatus) -> None:
    """Publish one battery reading to connected UI clients."""
    socketio.emit("power_status", status.to_dict())


def _log_power_status(status: PowerStatus) -> None:
    """Write throttled battery telemetry into the active session log."""
    session_log = get_session_logger()
    if session_log:
        session_log.log_power_status(status.to_dict())


def start_power_monitor(provider: str) -> None:
    """Start optional battery monitoring without blocking server startup."""
    global power_monitor  # pylint: disable=global-statement
    power_monitor = PowerMonitor(
        provider=provider,
        on_status=_on_power_status,
        on_log=_log_power_status,
    )
    power_monitor.start()
    logger.info("[POWER] Battery monitoring enabled with provider=%s", provider)


@socketio.on("connect")
def handle_connect():
    """Handle client connection."""
    print("Client connected")
    _emit_sim_snapshot()
    _emit_profiles()
    if power_monitor and power_monitor.status:
        socketio.emit("power_status", power_monitor.status.to_dict())
    if monitor:
        socketio.emit("session_state", _session_state_payload(include_runtime_meta=True))
        socketio.emit("trigger_status", _get_trigger_status())


@socketio.on("disconnect")
def handle_disconnect():
    """Handle client disconnection."""
    print("Client disconnected")


@socketio.on("get_trigger_status")
def handle_get_trigger_status():
    """Get current trigger/mode status for debug UI."""
    socketio.emit("trigger_status", _get_trigger_status())


@socketio.on("set_club")
def handle_set_club(data):
    """Handle club selection change."""
    club_name = data.get("club", "driver")
    try:
        club = ClubType(club_name)
        if monitor:
            monitor.set_club(club)
        socketio.emit("club_changed", {"club": club.value})
    except ValueError:
        pass


def _payload_dict(data) -> dict:
    """Normalize a socket payload to a dict, ignoring anything else."""
    return data if isinstance(data, dict) else {}


def _emit_profiles() -> None:
    """Broadcast the authoritative roster + selection.

    Sent after every mutation, including rejected ones, so a stale client
    self-heals on the next round trip instead of needing an error event.
    """
    socketio.emit("profiles", get_profile_store().snapshot())


@socketio.on("get_profiles")
def handle_get_profiles():
    """Send the roster to a client that asked for it."""
    _emit_profiles()


@socketio.on("set_active_profile")
def handle_set_active_profile(data=None):
    """Change which profile shots are attributed to."""
    get_profile_store().set_active(_payload_dict(data).get("profile_id"))
    _emit_profiles()


@socketio.on("add_profile")
def handle_add_profile(data=None):
    """Add a profile and make it active."""
    get_profile_store().add(_payload_dict(data).get("name"))
    _emit_profiles()


@socketio.on("rename_profile")
def handle_rename_profile(data=None):
    """Rename a profile. Its shots keep their id and stay attached."""
    payload = _payload_dict(data)
    get_profile_store().rename(payload.get("profile_id"), payload.get("name"))
    _emit_profiles()


@socketio.on("remove_profile")
def handle_remove_profile(data=None):
    """Delete a profile. Refused for the active, the last, or one with session rows."""
    profile_id = str(_payload_dict(data).get("profile_id") or "").strip()
    if profile_id and _profile_has_session_rows(profile_id):
        _emit_profiles()
        return
    get_profile_store().remove(profile_id)
    _emit_profiles()


@socketio.on("set_training_implement")
def handle_set_training_implement(data):
    """Handle swing speed training implement selection."""
    implement = data.get("implement", "driver") if isinstance(data, dict) else "driver"
    label = TRAINING_IMPLEMENT_LABELS.get(implement)
    if not label:
        socketio.emit("training_implement_error", {"error": "Unknown training implement"})
        return

    if monitor and hasattr(monitor, "set_training_implement"):
        monitor.set_training_implement(implement, label)
    socketio.emit(
        "training_implement_changed",
        {"implement": implement, "label": label},
    )


def _profile_has_session_rows(profile_id: str) -> bool:
    """True when the live session still has rows stamped with this profile."""
    from .swing_speed import SwingSpeedMonitor  # pylint: disable=import-outside-toplevel

    if not monitor or not profile_id:
        return False

    if isinstance(monitor, (SwingSpeedMonitor, MockSwingSpeedMonitor)):
        return any(getattr(event, "profile_id", "") == profile_id for event in monitor.get_events())

    if hasattr(monitor, "get_shots"):
        return any(getattr(shot, "profile_id", "") == profile_id for shot in monitor.get_shots())
    return False


def _clear_profile_rows(profile_id: str) -> None:
    """Remove one profile's shots or swing-speed reps from the active monitor.

    Matching is exact on the id. The old name-keyed code folded case, so two
    profiles whose names differed only in case cleared each other.
    """
    from .swing_speed import SwingSpeedMonitor  # pylint: disable=import-outside-toplevel

    if not monitor or not profile_id:
        return

    if isinstance(monitor, (SwingSpeedMonitor, MockSwingSpeedMonitor)):
        events = getattr(monitor, "_events", None)
        if events is not None:
            events[:] = [
                event for event in events if getattr(event, "profile_id", "") != profile_id
            ]
        return

    shots = getattr(monitor, "_shots", None)
    if shots is not None:
        removed = [shot for shot in shots if getattr(shot, "profile_id", "") == profile_id]
        shots[:] = [shot for shot in shots if getattr(shot, "profile_id", "") != profile_id]
        for shot in removed:
            _unregister_camera_replay(shot)
        return

    if hasattr(monitor, "clear_session"):
        monitor.clear_session()


@socketio.on("clear_session")
def handle_clear_session(data=None):
    """Clear recorded rows for one profile only."""
    raw_id = _payload_dict(data).get("profile_id")
    profile_id = str(raw_id).strip() if raw_id else get_profile_store().get_active().id
    _clear_profile_rows(profile_id)
    socketio.emit(
        "session_cleared",
        {"profile_id": profile_id, "shots": _session_shots()},
    )


@socketio.on("upload_cloud")
def handle_upload_cloud():
    """Manually trigger upload of completed session logs."""
    threading.Thread(target=_run_cloud_push_for_ui, daemon=True).start()


@socketio.on("get_session")
def handle_get_session():
    """Get current session data."""
    if monitor:
        socketio.emit("session_state", _session_state_payload())


@socketio.on("delete_shot")
def handle_delete_shot(data):
    """Delete one recorded shot or swing-speed rep from the current session."""
    timestamp = data.get("timestamp") if isinstance(data, dict) else None
    deleted = _delete_session_row(timestamp)

    if not deleted:
        socketio.emit("delete_shot_error", {"error": "Shot not found"})
        return

    socketio.emit("session_state", _session_state_payload())


@socketio.on("simulate_shot")
def handle_simulate_shot():
    """Simulate a shot (only works in mock mode)."""
    if monitor and isinstance(monitor, (MockLaunchMonitor, MockSwingSpeedMonitor)):
        monitor.simulate_shot()


@socketio.on("toggle_debug")
def handle_toggle_debug():
    """Toggle debug mode on/off."""
    global debug_mode  # pylint: disable=global-statement

    debug_mode = not debug_mode

    if debug_mode:
        log_path = start_debug_logging()
        socketio.emit("debug_toggled", {"enabled": True, "log_path": log_path})
        print("Debug mode ENABLED")
    else:
        stop_debug_logging()
        socketio.emit("debug_toggled", {"enabled": False})
        print("Debug mode DISABLED")


@socketio.on("get_debug_status")
def handle_get_debug_status():
    """Get current debug mode status."""
    socketio.emit(
        "debug_status",
        {
            "enabled": debug_mode,
            "log_path": str(debug_log_path) if debug_log_path else None,
        },
    )


# Radar tuning state
radar_config = {
    "min_speed": 10,
    "max_speed": 220,
    "min_magnitude": 0,
    "transmit_power": 0,
}


@socketio.on("get_radar_config")
def handle_get_radar_config():
    """Get current radar configuration."""
    socketio.emit("radar_config", radar_config)


@socketio.on("set_radar_config")
def handle_set_radar_config(data):
    """Update radar configuration."""
    global radar_config  # pylint: disable=global-statement

    if not monitor or (mock_mode and not mock_swing_speed_mode):
        log_session_error(
            "Radar config update rejected: radar not connected",
            component="server",
            context={"stage": "set_radar_config", "mock_mode": mock_mode},
        )
        socketio.emit("radar_config_error", {"error": "Radar not connected"})
        return

    try:
        from .swing_speed import SwingSpeedMonitor  # pylint: disable=import-outside-toplevel

        is_swing_speed = isinstance(monitor, (SwingSpeedMonitor, MockSwingSpeedMonitor))

        # Update min speed filter
        if "min_speed" in data:
            new_min = int(data["min_speed"])
            monitor.radar.set_min_speed_filter(new_min)
            if is_swing_speed:
                monitor.trigger_threshold_mph = float(new_min)
            radar_config["min_speed"] = new_min
            print(f"Set min speed filter: {new_min} mph")

        # Update max speed filter. 0 must still be forwarded: AN-010-AD (p10)
        # defines "R<0 resets to no limit", so it is how the UI clears a
        # previously-set ceiling. Swallowing it would leave the old ceiling
        # active on the radar while radar_config claimed no limit.
        if "max_speed" in data:
            new_max = int(data["max_speed"])
            monitor.radar.set_max_speed_filter(new_max)
            if is_swing_speed:
                monitor.max_speed_mph = None if new_max <= 0 else float(new_max)
            radar_config["max_speed"] = new_max
            print(f"Set max speed filter: {new_max} mph")

        # Update magnitude filter
        if "min_magnitude" in data:
            new_mag = int(data["min_magnitude"])
            monitor.radar.set_magnitude_filter(min_mag=new_mag)
            radar_config["min_magnitude"] = new_mag
            print(f"Set min magnitude filter: {new_mag}")

        # Update transmit power (0=max, 7=min)
        if "transmit_power" in data:
            new_power = int(data["transmit_power"])
            if 0 <= new_power <= 7:
                monitor.radar.set_transmit_power(new_power)
                radar_config["transmit_power"] = new_power
                print(f"Set transmit power: {new_power}")

        # Log config change
        session_logger = get_session_logger()
        if session_logger:
            session_logger.log_config_change(radar_config.copy(), source="user")

        # Legacy debug logging
        if debug_mode and debug_log_file:
            entry = {
                "timestamp": datetime.now().isoformat(),
                "type": "config_change",
                "config": radar_config.copy(),
            }
            debug_log_file.write(json.dumps(entry) + "\n")
            debug_log_file.flush()

        socketio.emit("radar_config", radar_config)

    except Exception as e:
        logger.warning("[SERVER] Error setting radar config: %s", e, exc_info=True)
        log_session_error(
            "Radar config update failed",
            component="server",
            context={"stage": "set_radar_config", "requested": data},
            exc=e,
        )
        socketio.emit("radar_config_error", {"error": str(e)})


@socketio.on("shutdown")
def handle_shutdown():
    """Cleanly shut down the server and all hardware."""
    logger.info("[SERVER] Shutdown requested from UI (WebSocket)")
    socketio.emit("shutdown_ack", {"message": "Shutting down..."})
    threading.Thread(target=_shutdown_process_after_delay, daemon=True).start()


def on_shot_processing(state: str) -> None:
    """Forward the rolling-buffer processing lifecycle to the UI."""
    socketio.emit("shot_processing", {"state": state})


def _forward_shot_to_simulators(shot: Shot) -> None:
    """Resolve a shot once and fan it out to every connected simulator.

    The shot pipeline is untouched: this is called after the UI emit and never
    raises into it. A shot number is only allocated when at least one connector
    is connected, so the sequence doesn't drift while sims are offline.
    """
    if not any(c.is_connected() for c in sim_connectors):
        return
    try:
        resolved = resolve_shot(shot, sim_player_state)
    except IncompleteShotError as e:
        logger.warning("[sim] shot not sendable: %s", e)
        socketio.emit("sim_shot_dropped", {"reason": str(e)})
        return

    values = resolved.as_values()
    for connector in sim_connectors:
        if not connector.is_connected():
            continue
        try:
            # Sends are synchronous on this thread, in connector order. Local sims
            # (the only target today) ack in microseconds; if a remote or laggy sim
            # is ever added, move this behind a per-connector send queue so one slow
            # sim can't stall delivery to the others (PR #115 review #2).
            connector.send_shot(resolved)
        except OSError as e:
            logger.warning("[sim] %s send failed: %s", connector.name, e)
            socketio.emit("sim_send_failed", {"target": connector.name, "reason": str(e)})
            continue
        sl = get_session_logger()
        if sl:
            sl.log_sim_send(
                target=connector.name,
                shot_number=resolved.shot_number,
                provenance=resolved.provenance,
                values=values,
            )
        socketio.emit(
            "sim_shot",
            {
                "target": connector.name,
                "shot_number": resolved.shot_number,
                "fields": connector.codec.fields_for_target(),
                "values": values,
                "provenance": resolved.provenance,
            },
        )
        if debug_mode:
            measured = sum(1 for p in resolved.provenance.values() if p == "measured")
            estimated = len(resolved.provenance) - measured
            logger.info(
                "[sim] → %s shot #%d: ball=%.1f vla=%.1f hla=%.1f spin=%.0f axis=%.1f "
                "carry=%.1f (%dM/%dE)",
                connector.name,
                resolved.shot_number,
                resolved.ball_speed_mph,
                resolved.vla,
                resolved.hla,
                resolved.total_spin_rpm,
                resolved.spin_axis_deg,
                resolved.carry_yards,
                measured,
                estimated,
            )


def _sim_on_status(target: str, event) -> None:
    """Relay a connector status change to the UI and session log."""
    state = event.state.value
    if state == "connected":
        logger.info("[sim] %s connected (%s:%s)", target, event.host, event.port)
    elif state == "reconnecting":
        logger.info(
            "[sim] %s reconnecting — attempt %s, retry in %.0fs",
            target,
            event.attempt,
            event.next_retry_in_s,
        )
    elif state == "error":
        logger.warning("[sim] %s error: %s", target, event.message)
    elif debug_mode:
        logger.info("[sim] %s %s", target, state)
    socketio.emit(
        "sim_status",
        {
            "target": target,
            "state": event.state.value,
            "host": event.host,
            "port": event.port,
            "attempt": event.attempt,
            "next_retry_in_s": event.next_retry_in_s,
            "message": event.message,
        },
    )
    sl = get_session_logger()
    if sl:
        sl.log_sim_status(
            target=target,
            state=event.state.value,
            host=event.host,
            port=event.port,
            message=event.message,
            attempt=event.attempt,
            next_retry_in_s=event.next_retry_in_s,
        )


def _sim_on_inbound(target: str, event) -> None:
    """Apply an inbound simulator event (player/club update, error, ack)."""
    if isinstance(event, PlayerUpdate):
        sim_player_state.apply(event)
        club_value = sim_player_state.club.value
        logger.info("[sim] ← %s player update: club=%s", target, club_value)
        socketio.emit(
            "sim_player",
            {"target": target, "handed": sim_player_state.handed, "club": club_value},
        )
        sl = get_session_logger()
        if sl:
            sl.log_sim_player(target=target, handed=sim_player_state.handed, club=club_value)
        # The monitor owns current-club state for shot tagging and carry/spin
        # model selection; keep it in sync with the sim's canonical club.
        if monitor is not None:
            try:
                monitor.set_club(sim_player_state.club)
            except Exception:  # pylint: disable=broad-except
                logger.exception("[sim] monitor.set_club failed")
        socketio.emit("club_changed", {"club": club_value})
    elif isinstance(event, SimError):
        logger.warning("[sim] ← %s error: %s", target, event.message)
        socketio.emit("sim_status", {"target": target, "state": "error", "message": event.message})
    elif isinstance(event, ShotAck):
        if not event.ok:
            logger.info("[sim] ← %s rejected shot %s: %s", target, event.shot_number, event.message)
        elif debug_mode:
            logger.info("[sim] ← %s ack: shot %s ok", target, event.shot_number)


def _apply_calculated_spin(shot: Shot) -> bool:
    """Replace radar-measured spin with the kinematic estimate.

    The 24 GHz OPS return carries no usable spin line (see
    spin_estimate.py), so when the vertical launch angle was actually
    measured (radar/camera, not the club-table estimate), spin_rpm is
    rewritten with 170*v*sin(LA)^1.2. The displaced measured value is
    kept in spin_rpm_measured for offline scoring. Returns True when
    the shot was rewritten.
    """
    if shot.launch_angle_vertical is None:
        return False
    if shot.launch_angle_vertical_source not in ("radar", "camera"):
        return False
    spin_calc = calculated_spin_rpm(shot.ball_speed_mph, shot.launch_angle_vertical)
    if spin_calc is None:
        return False
    shot.spin_rpm_measured = shot.spin_rpm
    shot.spin_rpm = spin_calc
    shot.spin_confidence = SPIN_CONFIDENCE_HIGH
    shot.spin_source = "calculated"
    shot.spin_rejection_reason = None
    logger.info(
        "[SERVER] Calculated spin: %.0f rpm (v=%.1f mph, LA=%.1f deg, measured was %s)",
        spin_calc,
        shot.ball_speed_mph,
        shot.launch_angle_vertical,
        "%.0f rpm" % shot.spin_rpm_measured if shot.spin_rpm_measured else "none",
    )
    return True


# Confidence floor/ceiling for measured angles. A measured angle always beats
# the table fallback (0.5), but only a corroborated, tightly-agreeing estimate
# earns the top of the range.
ANGLE_CONFIDENCE_FLOOR = 0.55
ANGLE_CONFIDENCE_CEILING = 0.95
VERTICAL_SPREAD_FULL_CONFIDENCE_DEG = 2.0
VERTICAL_SPREAD_ZERO_CONFIDENCE_DEG = 10.0

# Spin axis is a difference of two experimental estimates (horizontal launch
# minus club path); it should not appear the moment club path becomes
# non-null, only once the weaker of the two legs (horizontal launch) clears
# this bar.
SPIN_AXIS_MIN_CONFIDENCE = 0.6


def vertical_confidence(measurement) -> float:
    """Vertical launch confidence from channel agreement and corroboration.

    ``component_std_deg`` measures cross-channel disagreement, which only
    means something when two channels were actually compared. When
    ``single_channel`` is set, that comparison never happened: the value is
    either ``None`` (no second component existed) or describes a
    disagreement that was already resolved by discarding the bad channel --
    reusing it as a spread penalty would double-count a fault this function
    already derates via SINGLE_CHANNEL_CONFIDENCE_FACTOR, and np.std of the
    one surviving value is spuriously 0.0 ("perfect agreement") in the
    discarded-channel case. So single-channel results never derive their
    agreement score from component_std_deg at all -- both single-channel
    states (no second channel, or one caught and discarded) get the same
    fixed "one channel, no corroboration" score before the derating factor
    is applied, which is the honest answer: in both cases we have exactly
    one channel's word for it.
    """
    single_channel = getattr(measurement, "single_channel", False)
    spread = getattr(measurement, "component_std_deg", None)
    if single_channel or spread is None:
        # The `spread is None` half of this is defensive only: reachable
        # today solely via single_channel (handled above), kept in case a
        # future corroborated estimator omits component_std_deg.
        score = 0.5
    else:
        span = VERTICAL_SPREAD_ZERO_CONFIDENCE_DEG - VERTICAL_SPREAD_FULL_CONFIDENCE_DEG
        score = 1.0 - (float(spread) - VERTICAL_SPREAD_FULL_CONFIDENCE_DEG) / span
        score = min(1.0, max(0.0, score))
    if single_channel:
        from .iwr6843.lcmf import (  # pylint: disable=import-outside-toplevel
            SINGLE_CHANNEL_CONFIDENCE_FACTOR,
        )

        score *= SINGLE_CHANNEL_CONFIDENCE_FACTOR
    span = ANGLE_CONFIDENCE_CEILING - ANGLE_CONFIDENCE_FLOOR
    return round(ANGLE_CONFIDENCE_FLOOR + span * score, 3)


def horizontal_confidence_from(coherence: float | None) -> float:
    """Horizontal launch confidence from HLCMF-v0 coherence."""
    if coherence is None:
        return 0.0
    return round(min(ANGLE_CONFIDENCE_CEILING, max(0.0, float(coherence))), 3)


def _snapshot_inclinometer_for_shot(shot: Shot) -> None:
    """Attach the stable pre-impact enclosure orientation used by this shot."""
    if inclinometer_service is None or shot.mode == "mock":
        return

    impact_timestamp = shot.impact_timestamp or time.time()
    selection = inclinometer_service.snapshot_for_impact(impact_timestamp)
    data = selection.to_dict()
    data["zero_offset_deg"] = inclinometer_runtime_config.get("zero_offset_deg", 0.0)
    snapshot = selection.snapshot
    if snapshot is not None and iwr6843_runtime is not None:
        configured_tilt = math.degrees(iwr6843_runtime.calibration.tilt_rad)
        effective_tilt = configured_tilt + snapshot.calibrated_pitch_deg
        data.update(
            {
                "applied": True,
                "configured_iwr_tilt_deg": round(configured_tilt, 3),
                "effective_iwr_tilt_deg": round(effective_tilt, 3),
            }
        )
        logger.info(
            "[SERVER] Inclinometer pitch: raw %+.2fdeg, calibrated %+.2fdeg, "
            "IWR tilt %.2fdeg (age %.0fms)",
            snapshot.raw_pitch_deg,
            snapshot.calibrated_pitch_deg,
            effective_tilt,
            (selection.age_s or 0.0) * 1000.0,
        )
    else:
        data["applied"] = False
        logger.warning("[SERVER] Inclinometer correction not applied: %s", selection.status)
    shot.inclinometer = data


def _process_iwr6843_angle(shot: Shot) -> float | None:
    """Apply a correlated LCMF-v1 result without risking the OPS shot."""
    if iwr6843_runtime is None or shot.mode == "mock":
        return None

    started = time.time()
    try:
        shot_result = iwr6843_runtime.process_shot(
            impact_timestamp=shot.impact_timestamp,
            ball_speed_mph=shot.ball_speed_mph,
            club=shot.club.value,
            club_speed_mph=shot.club_speed_mph,
            tilt_deg=(
                shot.inclinometer.get("effective_iwr_tilt_deg")
                if shot.inclinometer and shot.inclinometer.get("applied")
                else None
            ),
        )
        capture = shot_result.capture
        measurement = shot_result.measurement
        club_path = getattr(shot_result, "club_path", None)
        if measurement is not None:
            shot.iwr6843_ball_range_evidence = getattr(measurement, "range_evidence", None)
        session_log = get_session_logger()
        if session_log:
            session_log.log_iwr6843_capture(
                shot_number=_shot_number_for_log(shot, session_log),
                shot_timestamp=shot.impact_timestamp,
                trigger_timestamp=(capture.trigger_timestamp if capture is not None else None),
                capture_path=(str(capture.path) if capture and capture.path else None),
                capture_bytes=(len(capture.raw) if capture and capture.raw else 0),
                dump_duration_s=(capture.dump_duration_s if capture is not None else None),
                capture_error=(
                    capture.error
                    if capture is not None
                    else "no capture matched the OPS impact timestamp"
                ),
                ball_speed_mph=shot.ball_speed_mph,
                measurement=(measurement.to_dict() if measurement is not None else None),
                club_path=(club_path.to_dict() if club_path is not None else None),
                temperature_report=(
                    getattr(capture, "temperature_report", None) if capture is not None else None
                ),
            )

        if capture is None:
            logger.warning("[SERVER] IWR6843 capture timed out; preserving OPS shot")
            _emit_iwr6843_trigger_status(
                shot,
                state="error",
                reason="no capture matched the OPS impact timestamp",
            )
        elif not capture.valid:
            logger.warning(
                "[SERVER] IWR6843 capture #%d failed: %s; preserving OPS shot",
                capture.sequence,
                capture.error,
            )
            _emit_iwr6843_trigger_status(
                shot,
                state="error",
                reason=capture.error or "invalid IWR6843 capture",
            )
        elif measurement is None:
            logger.warning("[SERVER] IWR6843 capture had no LCMF measurement")
            _emit_iwr6843_trigger_status(
                shot,
                state="rejected",
                reason="no LCMF measurement",
            )
        elif measurement.accepted:
            shot.launch_angle_vertical = measurement.angle_deg
            # Device-level provenance is retained in iwr6843_capture. The
            # public Shot contract uses "radar" for all measured radar angles.
            shot.launch_angle_vertical_source = "radar"
            shot.launch_angle_vertical_confidence = vertical_confidence(measurement)
            shot.launch_angle_confidence = shot.launch_angle_vertical_confidence
            shot.angle_source = "radar"
            horizontal_deg = getattr(measurement, "horizontal_deg", None)
            horizontal_confidence = getattr(measurement, "horizontal_confidence", None)
            horizontal_status = getattr(measurement, "horizontal_status", None)
            if horizontal_deg is not None:
                shot.iwr6843_horizontal_deg = horizontal_deg
                shot.iwr6843_horizontal_confidence = horizontal_confidence_from(
                    horizontal_confidence
                )
                shot.launch_angle_horizontal = horizontal_deg
                shot.launch_angle_horizontal_confidence = shot.iwr6843_horizontal_confidence
                shot.launch_angle_horizontal_source = "radar"
                logger.info(
                    "[SERVER] IWR6843 TX2 horizontal proxy: %.2f° (coherence %.0f%%, status=%s)",
                    horizontal_deg,
                    (horizontal_confidence or 0.0) * 100,
                    horizontal_status,
                )
            logger.info(
                "[SERVER] IWR6843 LCMF-v1 launch: %.2f° "
                "(%d snapshots/%d frames, component std %.2f°)",
                measurement.angle_deg,
                measurement.n_snapshots,
                measurement.n_frames,
                measurement.component_std_deg,
            )
            _emit_iwr6843_trigger_status(
                shot,
                state="accepted",
                reason="accepted",
                angle_deg=measurement.angle_deg,
            )
        else:
            logger.warning(
                "[SERVER] IWR6843 LCMF-v1 withheld angle: %s",
                measurement.status,
            )
            _emit_iwr6843_trigger_status(
                shot,
                state="rejected",
                reason=measurement.status,
            )

        # IWR club path/AoA remain experimental even when their internal
        # quality gates accept them. Publish them through the normal UI path,
        # but never populate the canonical club fields or silently label them
        # as production radar measurements.
        if club_path is not None:
            shot.iwr6843_club_range_evidence = getattr(club_path, "range_evidence", None)
            accepted_path = club_path.path_deg if club_path.accepted else None
            candidate_path = (
                accepted_path
                if accepted_path is not None
                else getattr(club_path, "candidate_path_deg", None)
            )
            candidate_attack = getattr(club_path, "candidate_attack_angle_deg", None)
            candidate_path_status = getattr(club_path, "candidate_path_status", None)
            shot.experimental_club_path_status = (
                club_path.status
                if accepted_path is not None
                else (
                    candidate_path_status
                    if candidate_path_status not in (None, "candidate_available")
                    else club_path.status
                )
            )
            shot.experimental_attack_angle_status = (
                getattr(club_path, "attack_angle_status", None) or club_path.status
            )
            if candidate_path is not None:
                shot.experimental_club_path_deg = round(candidate_path, 1)
            if candidate_attack is not None:
                shot.experimental_attack_angle_deg = round(candidate_attack, 1)
            if accepted_path is not None:
                logger.info(
                    "[SERVER] Experimental IWR6843 club path: %.2f° (confidence %.2f, %d frames)",
                    accepted_path,
                    club_path.confidence or 0.0,
                    club_path.n_frames,
                )
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] IWR6843 processing error: %s", error, exc_info=True)
        log_session_error(
            "IWR6843 shot processing failed",
            component="server",
            context={
                "stage": "iwr6843",
                "ball_speed_mph": shot.ball_speed_mph,
                "club": shot.club.value,
            },
            exc=error,
        )
        _emit_iwr6843_trigger_status(shot, state="error", reason=str(error))
    return (time.time() - started) * 1000.0


def _emit_iwr6843_trigger_status(
    shot: Shot,
    *,
    state: str,
    reason: str,
    angle_deg: float | None = None,
) -> None:
    """Enrich the existing OPS trigger row with the correlated TI result."""
    iwr_status = {"state": state, "reason": reason}
    if angle_deg is not None:
        iwr_status["angle_deg"] = round(angle_deg, 2)
    socketio.emit(
        "trigger_diagnostic_update",
        {
            "timestamp": shot.timestamp.isoformat(),
            "iwr6843": iwr_status,
        },
    )


_CAMERA_ARCHIVE_UNSET = object()


def _load_camera_capture_archive(camera_capture) -> dict[str, object] | None:
    """Load one camera clip into memory for all per-shot estimators."""
    if camera_capture is None or not camera_capture.valid or not camera_capture.path:
        return None
    frames_path = Path(camera_capture.path) / "frames.npz"
    if not frames_path.exists():
        return None

    import numpy as np  # noqa: PLC0415  pylint: disable=import-outside-toplevel

    try:
        with np.load(frames_path) as archive:
            return {name: archive[name] for name in archive.files}
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] Camera capture archive could not be loaded: %s", error)
        return None


def _fuse_camera_club_delivery(
    shot: Shot,
    camera_capture,
    camera_archive=_CAMERA_ARCHIVE_UNSET,
    *,
    reference_ball=None,
) -> None:
    """Impact-centered camera + IWR depth club delivery, experimentally."""
    try:
        from openflight.camera.club_delivery import (  # noqa: PLC0415
            CameraDeliveryGeometry,
            ChainedDelivery,
            estimate_chained_delivery,
        )

        fused = ChainedDelivery(status="rejected_no_camera_capture")
        if camera_capture is not None and camera_capture.valid and camera_capture.path:
            frames_path = Path(camera_capture.path) / "frames.npz"
            if frames_path.exists():
                archive = (
                    _load_camera_capture_archive(camera_capture)
                    if camera_archive is _CAMERA_ARCHIVE_UNSET
                    else camera_archive
                )
                if archive is None:
                    fused = ChainedDelivery(status="rejected_missing_camera_frames")
                else:
                    trigger_index = (
                        int(archive["pre_trigger_count"]) - 1
                        if "pre_trigger_count" in archive
                        else None
                    )
                    if iwr6843_runtime is None:
                        fused = ChainedDelivery(status="rejected_no_iwr_runtime")
                    else:
                        calibration = iwr6843_runtime.calibration
                        if calibration.tee_range_m is None:
                            fused = ChainedDelivery(status="rejected_missing_tee_geometry")
                        else:
                            fused = estimate_chained_delivery(
                                archive["frames"],
                                archive["host_timestamp_ns"],
                                trigger_index=trigger_index,
                                range_evidence=shot.iwr6843_club_range_evidence,
                                geometry=CameraDeliveryGeometry(
                                    camera_height_m=float(camera_capture_config["mount_height_m"]),
                                    radar_height_m=calibration.radar_height_m,
                                    tee_range_m=float(calibration.tee_range_m),
                                    ball_height_m=calibration.tee_ball_height_m,
                                    camera_lateral_offset_m=float(
                                        camera_capture_config.get("lateral_offset_m", 0.0)
                                    ),
                                    image_width_px=int(camera_capture_config["width"]),
                                    image_height_px=int(camera_capture_config["height"]),
                                    horizontal_pixel_sign=(
                                        -1.0
                                        if camera_capture_config.get("mirror_horizontal")
                                        else 1.0
                                    ),
                                    roll_correction_deg=float(
                                        camera_capture_config.get("roll_correction_deg", 0.0)
                                    ),
                                ),
                                ops_club_speed_mph=shot.club_speed_mph,
                                ball_tracker=camera_reference_ball_tracker,
                                reference_ball=reference_ball,
                            )
            else:
                fused = ChainedDelivery(status="rejected_missing_camera_frames")
        shot.experimental_fused_attack_angle_deg = fused.attack_angle_deg
        shot.experimental_fused_club_path_deg = fused.club_path_deg
        shot.experimental_fused_status = fused.status
        shot.experimental_fused_attack_angle_confidence = fused.attack_confidence_tier
        shot.experimental_fused_club_path_confidence = fused.path_confidence_tier
        shot.experimental_camera_trace_deg = None
        shot.experimental_aoa_offset_source = "none_chained_3d"
        logger.info(
            "[SERVER] Camera/IWR chained club delivery: AoA %s path %s "
            "(status=%s, features=%d, speed_ratio=%s, velocity_mad=%s mph, "
            "path_windows=%d, path_mad=%s deg, impact_frame=%s)",
            fused.attack_angle_deg,
            fused.club_path_deg,
            fused.status,
            fused.n_features,
            fused.speed_ratio_ops,
            fused.velocity_mad_mph,
            fused.path_window_count,
            fused.path_window_mad_deg,
            fused.impact_frame,
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        shot.experimental_fused_status = "error"
        logger.warning("[SERVER] Camera club-delivery fusion error: %s", error, exc_info=True)
        log_session_error(
            "Camera club-delivery fusion failed",
            component="camera_capture",
            context={"stage": "club_delivery_fusion", "ball_speed_mph": shot.ball_speed_mph},
            exc=error,
        )


def _fuse_camera_ball_flight(
    shot: Shot,
    camera_capture,
    camera_archive=_CAMERA_ARCHIVE_UNSET,
    *,
    reference_ball=None,
) -> None:
    """Select experimental camera horizontal while preserving IWR fallback."""
    try:
        from openflight.camera.ball_flight import (  # noqa: PLC0415
            CameraBallEstimate,
            CameraBallGeometry,
            estimate_camera_ball_flight,
            estimate_horizontal_launch,
            select_camera_assisted_horizontal,
        )

        estimate = CameraBallEstimate(status="rejected_no_camera_capture")
        if camera_capture is not None and camera_capture.valid and camera_capture.path:
            frames_path = Path(camera_capture.path) / "frames.npz"
            if not frames_path.exists():
                estimate = CameraBallEstimate(status="rejected_missing_camera_frames")
            elif iwr6843_runtime is None:
                estimate = CameraBallEstimate(status="rejected_no_iwr_runtime")
            else:
                calibration = iwr6843_runtime.calibration
                if calibration.tee_range_m is None:
                    estimate = CameraBallEstimate(status="rejected_missing_tee_geometry")
                else:
                    archive = (
                        _load_camera_capture_archive(camera_capture)
                        if camera_archive is _CAMERA_ARCHIVE_UNSET
                        else camera_archive
                    )
                    if archive is None:
                        estimate = CameraBallEstimate(status="rejected_missing_camera_frames")
                    else:
                        trigger_ns = int(archive["trigger_host_timestamp_ns"])
                        geometry = CameraBallGeometry(
                            camera_height_m=float(camera_capture_config["mount_height_m"]),
                            radar_height_m=calibration.radar_height_m,
                            tee_range_m=float(calibration.tee_range_m),
                            ball_height_m=calibration.tee_ball_height_m,
                            camera_lateral_offset_m=float(
                                camera_capture_config.get("lateral_offset_m", 0.0)
                            ),
                            horizontal_offset_deg=float(
                                camera_capture_config.get("horizontal_offset_deg", 0.0)
                            ),
                            roll_correction_deg=float(
                                camera_capture_config.get("roll_correction_deg", 0.0)
                            ),
                            horizontal_pixel_sign=(
                                -1.0 if camera_capture_config.get("mirror_horizontal") else 1.0
                            ),
                            image_width_px=int(camera_capture_config["width"]),
                            image_height_px=int(camera_capture_config["height"]),
                        )
                        if camera_capture_config.get("shot_analysis_enabled", False):
                            estimate = estimate_horizontal_launch(
                                archive["frames"],
                                archive["host_timestamp_ns"],
                                trigger_ns=trigger_ns,
                                geometry=geometry,
                                ops_ball_speed_mph=shot.ball_speed_raw_mph or shot.ball_speed_mph,
                                vertical_deg=shot.launch_angle_vertical,
                                vertical_source=shot.launch_angle_vertical_source,
                                reference_ball=reference_ball,
                            )
                        else:
                            estimate = estimate_camera_ball_flight(
                                archive["frames"],
                                archive["host_timestamp_ns"],
                                trigger_ns=trigger_ns,
                                range_evidence=shot.iwr6843_ball_range_evidence,
                                geometry=geometry,
                                ops_ball_speed_mph=shot.ball_speed_raw_mph or shot.ball_speed_mph,
                                iwr_vertical_deg=shot.launch_angle_vertical,
                                ball_tracker=camera_ball_flight_reference_tracker,
                            )

        decision = select_camera_assisted_horizontal(
            estimate,
            iwr_horizontal_deg=shot.iwr6843_horizontal_deg,
            iwr_confidence=shot.iwr6843_horizontal_confidence,
        )
        shot.experimental_camera_horizontal_deg = decision.camera_horizontal_deg
        shot.experimental_camera_horizontal_confidence = (
            decision.confidence
            if decision.source in ("camera_assisted_experimental", "camera_only_experimental")
            else None
        )
        shot.experimental_camera_horizontal_status = (
            decision.status
            if estimate.confidence_tier != "withheld"
            else f"{decision.status}:{estimate.status}"
        )
        shot.experimental_camera_iwr_delta_deg = decision.camera_iwr_delta_deg
        if decision.selected_deg is not None:
            shot.launch_angle_horizontal = decision.selected_deg
            shot.launch_angle_horizontal_confidence = decision.confidence
            shot.launch_angle_horizontal_source = decision.source
        logger.info(
            "[SERVER] Camera-assisted horizontal: selected=%s camera=%s IWR=%s "
            "delta=%s status=%s support=%d",
            decision.selected_deg,
            decision.camera_horizontal_deg,
            decision.iwr_horizontal_deg,
            decision.camera_iwr_delta_deg,
            decision.status,
            estimate.support,
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        shot.experimental_camera_horizontal_status = "error"
        logger.warning("[SERVER] Camera ball-flight fusion error: %s", error, exc_info=True)
        log_session_error(
            "Camera ball-flight fusion failed",
            component="camera_capture",
            context={"stage": "ball_flight_fusion", "ball_speed_mph": shot.ball_speed_mph},
            exc=error,
        )


def _fuse_camera_measurements(shot: Shot, camera_capture) -> float:
    """Decode one camera clip and share it across all live estimators."""
    analysis_started = time.perf_counter()
    captured_auto_exposure = (
        camera_capture.metadata.get("auto_exposure")
        if camera_capture is not None
        and isinstance(getattr(camera_capture, "metadata", None), dict)
        else None
    )
    analysis_eligible = (
        bool(captured_auto_exposure.get("analysis_eligible"))
        if isinstance(captured_auto_exposure, dict)
        else (
            camera_capture_runtime.camera_analysis_eligible
            if camera_capture_runtime is not None
            else True
        )
    )
    if not analysis_eligible:
        shot.experimental_camera_horizontal_status = "rejected_lighting_quality"
        shot.experimental_camera_horizontal_deg = None
        shot.experimental_camera_horizontal_confidence = None
        shot.experimental_camera_iwr_delta_deg = None
        shot.experimental_fused_attack_angle_deg = None
        shot.experimental_fused_club_path_deg = None
        shot.experimental_fused_status = "rejected_lighting_quality"
        shot.experimental_fused_attack_angle_confidence = "withheld"
        shot.experimental_fused_club_path_confidence = "withheld"
        logger.warning(
            "[SERVER] Camera analysis withheld for lighting quality; using radar fallback"
        )
        return (time.perf_counter() - analysis_started) * 1000.0
    camera_archive = _load_camera_capture_archive(camera_capture)
    reference_ball = None
    if camera_capture_config.get("shot_analysis_enabled", False) and camera_archive is not None:
        try:
            import numpy as np  # noqa: PLC0415  pylint: disable=import-outside-toplevel

            from openflight.camera.club_motion import (  # noqa: PLC0415
                detect_impact_reference_ball,
            )

            timestamps = np.asarray(camera_archive["host_timestamp_ns"], dtype=np.int64)
            trigger_ns = int(camera_archive["trigger_host_timestamp_ns"])
            trigger_frame = int(np.argmin(np.abs(timestamps - trigger_ns)))
            reference_ball = detect_impact_reference_ball(
                camera_archive["frames"],
                trigger_frame_index=trigger_frame,
            )
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.warning("[SERVER] Impact-aware reference ball unavailable: %s", error)
    _fuse_camera_ball_flight(
        shot,
        camera_capture,
        camera_archive,
        reference_ball=reference_ball,
    )
    _fuse_camera_club_delivery(
        shot,
        camera_capture,
        camera_archive,
        reference_ball=reference_ball,
    )
    elapsed_ms = (time.perf_counter() - analysis_started) * 1000.0
    logger.info("[SERVER] Camera analysis: %.1fms", elapsed_ms)
    return elapsed_ms


def _attach_camera_replay(shot: Shot, camera_capture) -> None:
    """Expose a matched raw clip without doing any video conversion."""
    if (
        camera_replay_manager is None
        or camera_capture is None
        or not camera_capture.valid
        or not camera_capture.path
    ):
        return
    try:
        shot.camera_replay = camera_replay_manager.register(
            camera_capture.path,
            camera_capture.metadata,
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[CAMERA] Replay registration failed: %s", error)
        log_session_error(
            "Camera replay registration failed",
            component="camera_capture",
            context={"stage": "replay_registration"},
            exc=error,
        )


def _enrich_shot_from_optional_hardware(shot: Shot) -> _ShotEnrichmentResult:
    """Mutate a shot with available radar/camera measurements and timings."""

    # Snapshot orientation before IWR capture can block, and select only data
    # timestamped before impact so impact vibration cannot bias the geometry.
    _snapshot_inclinometer_for_shot(shot)
    iwr6843_ms = _process_iwr6843_angle(shot)
    kld7_ms = None
    camera_capture_ms = None
    camera_analysis_ms = None
    # Process K-LD7 angle radars (vertical = launch angle, horizontal = club path)
    try:
        if shot.mode != "mock":
            kld7_start = time.time()
            shot_ts = shot.impact_timestamp or kld7_start
            session_log = get_session_logger()
            if kld7_vertical or kld7_horizontal:
                _maybe_wait_for_kld7_post_shot_frames(shot_ts)

            # --- Vertical K-LD7 (launch angle) ---
            if kld7_vertical:
                raw_buffer = kld7_vertical.snapshot_buffer()
                _warn_if_kld7_buffer_underfilled("vertical", len(raw_buffer))
                kld7_angle = kld7_vertical.get_angle_for_shot(
                    shot_timestamp=shot_ts,
                    ball_speed_mph=shot.ball_speed_mph,
                    impact_timestamp=shot.impact_timestamp_kld7,
                    club=shot.club,
                )
                vertical_selection_details = None
                if kld7_angle and kld7_angle.vertical_deg is not None:
                    accepted, vertical_selection_details = _select_vertical_radar_launch(
                        kld7_angle, shot
                    )
                    selection_reason = vertical_selection_details["selection_reason"]
                    if not accepted:
                        logger.warning(
                            "[SERVER] Vertical angle %.1f° rejected: %s "
                            "(expected=%s°, delta=%s°, conf=%.0f%%)",
                            kld7_angle.vertical_deg,
                            selection_reason,
                            vertical_selection_details.get("expected_launch_deg"),
                            vertical_selection_details.get("delta_deg"),
                            kld7_angle.confidence * 100,
                        )
                    else:
                        accepted_conf = kld7_angle.confidence
                        if vertical_selection_details.get("acceptance_path") == "marginal":
                            accepted_conf = min(
                                accepted_conf, _VERTICAL_MARGINAL_DISPLAY_CONFIDENCE
                            )
                        shot.launch_angle_vertical = kld7_angle.vertical_deg
                        shot.launch_angle_confidence = accepted_conf
                        shot.launch_angle_vertical_confidence = accepted_conf
                        shot.launch_angle_vertical_source = "radar"
                        shot.angle_source = "radar"
                        logger.info(
                            "[SERVER] Vertical angle: %.1f° (conf=%.0f%%, %d frames, %s)",
                            kld7_angle.vertical_deg,
                            kld7_angle.confidence * 100,
                            kld7_angle.num_frames,
                            selection_reason,
                        )
                # Club angle of attack (same RADC buffer, club speed from OPS).
                # Compute BEFORE logging the buffer so the log entry can
                # include club_angle alongside ball_angle for offline analysis.
                club_angle_v = None
                if shot.club_speed_mph:
                    club_angle_v = kld7_vertical.get_club_angle(
                        club_speed_mph=shot.club_speed_mph,
                        shot_timestamp=shot_ts,
                    )
                    if club_angle_v and club_angle_v.vertical_deg is not None:
                        # Negate: the radar sees where the club IS (above center = positive),
                        # but AoA is the club's attack direction (descending = negative).
                        candidate_aoa = -club_angle_v.vertical_deg
                        # Reject physically impossible AoA values.
                        # Real AoA ranges from ~-15° (steep iron) to ~+8° (ascending driver).
                        if -15.0 <= candidate_aoa <= 8.0:
                            shot.club_angle_deg = candidate_aoa
                            logger.info(
                                "[SERVER] Club AoA: %.1f° (conf=%.0f%%)",
                                shot.club_angle_deg,
                                club_angle_v.confidence * 100,
                            )
                        else:
                            logger.warning(
                                "[SERVER] Club AoA rejected: %.1f° outside plausible range",
                                candidate_aoa,
                            )

                if session_log and raw_buffer:
                    session_log.log_kld7_buffer(
                        shot_number=_shot_number_for_log(shot, session_log),
                        shot_timestamp=shot_ts,
                        orientation="vertical",
                        buffer_frames=raw_buffer,
                        ball_angle=_kld7_angle_log_payload(
                            kld7_angle,
                            "vertical_deg",
                            selection_details=vertical_selection_details,
                        ),
                        club_angle=_kld7_angle_log_payload(club_angle_v, "vertical_deg"),
                    )

                kld7_vertical.reset()

            # --- Horizontal K-LD7 (club path / aim direction) ---
            if kld7_horizontal:
                raw_buffer_h = kld7_horizontal.snapshot_buffer()
                _warn_if_kld7_buffer_underfilled("horizontal", len(raw_buffer_h))
                kld7_angle_h = kld7_horizontal.get_angle_for_shot(
                    shot_timestamp=shot_ts,
                    ball_speed_mph=shot.ball_speed_mph,
                )
                horizontal_selection_details = None
                if kld7_angle_h and kld7_angle_h.horizontal_deg is not None:
                    horizontal_limit = 15.0
                    accepted_h, horizontal_selection_details = _select_horizontal_radar_launch(
                        kld7_angle_h, horizontal_limit
                    )
                    selection_reason_h = horizontal_selection_details["selection_reason"]
                    if accepted_h:
                        shot.launch_angle_horizontal = kld7_angle_h.horizontal_deg
                        shot.launch_angle_horizontal_confidence = kld7_angle_h.confidence
                        shot.launch_angle_horizontal_source = "radar"
                        if shot.angle_source is None:
                            shot.angle_source = "radar"
                        if shot.launch_angle_confidence is None:
                            shot.launch_angle_confidence = kld7_angle_h.confidence
                        logger.info(
                            "[SERVER] Horizontal angle: %.1f° (conf=%.0f%%, %d frames, %s)",
                            kld7_angle_h.horizontal_deg,
                            kld7_angle_h.confidence * 100,
                            kld7_angle_h.num_frames,
                            selection_reason_h,
                        )
                    else:
                        logger.warning(
                            "[SERVER] Horizontal angle %.1f° rejected: %s "
                            "(limit=±%.0f°, conf=%.0f%%)",
                            kld7_angle_h.horizontal_deg,
                            selection_reason_h,
                            horizontal_limit,
                            kld7_angle_h.confidence * 100,
                        )
                # Club path (same RADC buffer, club speed from OPS).
                # Compute BEFORE logging the buffer so the log entry can
                # include club_angle alongside ball_angle for offline analysis.
                club_angle_h = None
                if shot.club_speed_mph:
                    club_angle_h = kld7_horizontal.get_club_angle(
                        club_speed_mph=shot.club_speed_mph,
                        shot_timestamp=shot_ts,
                    )
                    if club_angle_h and club_angle_h.horizontal_deg is not None:
                        shot.club_path_deg = club_angle_h.horizontal_deg
                        logger.info(
                            "[SERVER] Club path: %.1f° (conf=%.0f%%)",
                            club_angle_h.horizontal_deg,
                            club_angle_h.confidence * 100,
                        )

                if session_log and raw_buffer_h:
                    session_log.log_kld7_buffer(
                        shot_number=_shot_number_for_log(shot, session_log),
                        shot_timestamp=shot_ts,
                        orientation="horizontal",
                        buffer_frames=raw_buffer_h,
                        ball_angle=_kld7_angle_log_payload(
                            kld7_angle_h,
                            "horizontal_deg",
                            selection_details=horizontal_selection_details,
                        ),
                        club_angle=_kld7_angle_log_payload(club_angle_h, "horizontal_deg"),
                    )

                kld7_horizontal.reset()

            # Derive spin axis from face angle (H. launch) minus club path.
            # Both are experimental estimates; only trust the difference once
            # the weaker leg (horizontal launch) clears a confidence floor,
            # rather than emitting it the moment club path is non-null.
            # club_path_deg can come from IWR6843 (_process_iwr6843_angle,
            # above) rather than K-LD7, so a failure here is not necessarily
            # a K-LD7 failure -- see the except block below.
            if (
                shot.launch_angle_horizontal is not None
                and shot.club_path_deg is not None
                and (shot.launch_angle_horizontal_confidence or 0.0) >= SPIN_AXIS_MIN_CONFIDENCE
            ):
                shot.spin_axis_deg = round(shot.launch_angle_horizontal - shot.club_path_deg, 1)
                logger.info(
                    "[SERVER] Spin axis: %+.1f° (face=%+.1f° - path=%+.1f°)",
                    shot.spin_axis_deg,
                    shot.launch_angle_horizontal,
                    shot.club_path_deg,
                )

            if kld7_vertical or kld7_horizontal:
                kld7_ms = (time.time() - kld7_start) * 1000
                logger.info("[SERVER] K-LD7 processing: %.1fms", kld7_ms)
    except Exception as e:
        # Covers K-LD7 processing AND the spin-axis derivation above, which
        # reads club_path_deg regardless of whether it came from K-LD7 or
        # IWR6843 -- so this is not necessarily a K-LD7-specific failure.
        logger.warning("[SERVER] Angle/spin-axis post-processing error: %s", e, exc_info=True)
        log_session_error(
            "Angle/spin-axis post-processing failed",
            component="server",
            context={
                "stage": "angle_postprocessing",
                "ball_speed_mph": shot.ball_speed_mph,
                "club": shot.club.value,
            },
            exc=e,
        )

    camera_capture = None
    try:
        if camera_capture_runtime is not None and shot.mode != "mock":
            camera_capture_start = time.time()
            camera_capture = camera_capture_runtime.capture_for_shot(
                shot.impact_timestamp,
                timeout_s=2.0,
            )
            camera_capture_ms = (time.time() - camera_capture_start) * 1000.0
            session_log = get_session_logger()
            if session_log:
                shot_number = _shot_number_for_log(shot, session_log)
                if camera_capture is not None:
                    session_log.log_camera_capture(
                        shot_number=shot_number,
                        shot_timestamp=shot.impact_timestamp,
                        trigger_timestamp=camera_capture.trigger_timestamp,
                        capture_path=str(camera_capture.path) if camera_capture.path else None,
                        metadata=camera_capture.metadata,
                        capture_error=camera_capture.error,
                    )
                    if camera_capture.valid:
                        logger.info(
                            "[SERVER] Camera capture #%d matched -> %s",
                            camera_capture.sequence,
                            camera_capture.path,
                        )
                    else:
                        logger.warning(
                            "[SERVER] Camera capture #%d failed: %s",
                            camera_capture.sequence,
                            camera_capture.error,
                        )
                else:
                    session_log.log_camera_capture(
                        shot_number=shot_number,
                        shot_timestamp=shot.impact_timestamp,
                        trigger_timestamp=None,
                        capture_path=None,
                        capture_error="no_matching_camera_capture",
                    )
                    logger.warning("[SERVER] No camera capture matched this shot")
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] Camera capture matching error: %s", error, exc_info=True)
        log_session_error(
            "Camera capture matching failed",
            component="camera_capture",
            context={"stage": "camera_capture_match", "ball_speed_mph": shot.ball_speed_mph},
            exc=error,
        )

    _attach_camera_replay(shot, camera_capture)

    if shot.mode != "mock":
        camera_analysis_ms = _fuse_camera_measurements(shot, camera_capture)

    return _ShotEnrichmentResult(
        iwr6843_ms=iwr6843_ms,
        kld7_ms=kld7_ms,
        camera_capture_ms=camera_capture_ms,
        camera_analysis_ms=camera_analysis_ms,
    )


def _finalize_shot_detected(
    shot: Shot,
    *,
    emit_event: str,
    initial_ui_ms: float | None = None,
    enrichment: _ShotEnrichmentResult | None = None,
) -> None:
    """Apply required fallbacks, persist once, and publish the final shot."""
    enrichment = enrichment or _ShotEnrichmentResult()
    iwr6843_ms = enrichment.iwr6843_ms
    kld7_ms = enrichment.kld7_ms
    camera_capture_ms = enrichment.camera_capture_ms
    camera_analysis_ms = enrichment.camera_analysis_ms

    # Always emit user-facing launch angles. Radar/camera measurements win;
    # rejected or missing axes fall back to conservative estimates.
    _ensure_user_facing_launch_angles(shot)

    # Ball-speed cosine correction: the OPS reads the radial component of
    # a ball departing at the launch angle. Applied AFTER the K-LD7 (which
    # must anchor to the radial speed) and BEFORE carry/ballistics.
    if ball_speed_correction_enabled and shot.launch_angle_vertical is not None:
        raw_speed = shot.ball_speed_mph
        shot.ball_speed_raw_mph = raw_speed
        shot.ball_speed_mph = correct_ball_speed(
            raw_speed,
            shot.launch_angle_vertical,
            ball_speed_correction_distance_ft,
            ball_speed_correction_ball_above_radar_ft,
        )
        logger.info(
            "[SERVER] Ball speed cosine correction: %.1f -> %.1f mph (LA %.1f)",
            raw_speed,
            shot.ball_speed_mph,
            shot.launch_angle_vertical,
        )

    # Calculated spin runs AFTER the cosine correction (the model is
    # calibrated on true ball speed) and BEFORE carry/ballistics.
    if calculated_spin_enabled:
        _apply_calculated_spin(shot)

    # Compute carry. Prefer the physics simulator (drag + Magnus, RK4) when
    # ballistics is enabled and a vertical launch angle is available; fall
    # back to the table estimator otherwise (either ballistics disabled or
    # angle missing → resolve_launch returns None). This is the only place
    # that writes carry_spin_adjusted for a live shot.
    if shot.mode != "mock":
        conditions = resolve_launch(shot) if ballistics_enabled else None
        if conditions is not None:
            trajectory = simulate(conditions)
            shot.carry_spin_adjusted = trajectory.carry_yards
            logger.info(
                "[SERVER] Ballistic carry: %.0f yds (spin: %.0f rpm, source: %s)",
                shot.carry_spin_adjusted,
                conditions.spin_rpm,
                conditions.spin_source,
            )
        else:
            has_reliable_spin = (
                shot.spin_rpm
                and shot.spin_rpm > 0
                and shot.spin_confidence is not None
                and shot.spin_confidence >= SPIN_CONFIDENCE_RELIABLE
            )
            spin_for_carry = (
                shot.spin_rpm
                if has_reliable_spin
                else get_optimal_spin_for_ball_speed(shot.ball_speed_mph, shot.club)
            )
            shot.carry_spin_adjusted = estimate_carry_with_spin(
                shot.ball_speed_mph,
                spin_for_carry,
                shot.club,
                club_speed_mph=shot.club_speed_mph,
            )
            reason = "ballistics disabled" if not ballistics_enabled else "no launch angle"
            logger.info(
                "[SERVER] Table carry (%s): %.0f yds (spin: %.0f rpm%s)",
                reason,
                shot.carry_spin_adjusted,
                spin_for_carry,
                "" if shot.spin_rpm and shot.spin_rpm > 0 else " avg",
            )
    if shot.spin_rejection_reason:
        logger.info(
            "[SERVER] Spin unavailable: %s (snr=%s, candidate=%s rpm)",
            shot.spin_rejection_reason,
            "%.2f" % shot.spin_snr if shot.spin_snr is not None else "N/A",
            "%.0f" % (shot.spin_peak_freq_hz * 60) if shot.spin_peak_freq_hz is not None else "N/A",
        )

    # Log shot with all data (radar + spin + camera) in one entry
    try:
        session_log = get_session_logger()
        if session_log:
            session_log.log_shot(
                shot=shot,
                pipeline_ms={
                    "initial_ui": (round(initial_ui_ms, 1) if initial_ui_ms is not None else None),
                    "iwr6843": (round(iwr6843_ms, 1) if iwr6843_ms is not None else None),
                    "kld7": round(kld7_ms, 1) if kld7_ms is not None else None,
                    "camera_capture": (
                        round(camera_capture_ms, 1) if camera_capture_ms is not None else None
                    ),
                    "camera_analysis": (
                        round(camera_analysis_ms, 1) if camera_analysis_ms is not None else None
                    ),
                },
            )
    except Exception as e:
        logger.warning("[SERVER] Failed to log shot: %s", e, exc_info=True)
        log_session_error(
            "Session shot logging failed",
            component="server",
            context={"stage": "session_log_shot", "ball_speed_mph": shot.ball_speed_mph},
            exc=e,
        )

    # Emit shot with launch angle data included
    try:
        shot_data = shot_to_dict(shot)
        stats = monitor.get_session_stats() if monitor else {}
        socketio.emit(emit_event, {"shot": shot_data, "stats": stats})

        # Log shot info
        angle_str = ""
        if shot.launch_angle_vertical is not None:
            angle_str = ", Launch: %.1f°" % shot.launch_angle_vertical
        logger.info(
            "[SERVER] Shot: ball=%.1f mph, carry=%.0f yds%s",
            shot.ball_speed_mph,
            shot.estimated_carry_yards,
            angle_str,
        )
    except Exception as e:
        logger.error("[SERVER] Failed to emit shot: %s", e, exc_info=True)
        log_session_error(
            "WebSocket shot emit failed",
            component="server",
            context={"stage": f"emit_{emit_event}", "ball_speed_mph": shot.ball_speed_mph},
            exc=e,
        )
        return

    # Forward to simulator connectors (optional)
    _forward_shot_to_simulators(shot)

    # Debug logging (optional)
    if debug_mode:
        try:
            debug_log_entry = {
                "type": "shot",
                "timestamp": datetime.now().isoformat(),
                "radar": {
                    "ball_speed_mph": shot_data["ball_speed_mph"],
                    "club_speed_mph": shot_data["club_speed_mph"],
                    "smash_factor": shot_data["smash_factor"],
                    "peak_magnitude": shot_data["peak_magnitude"],
                },
                "club": shot_data["club"],
            }

            if debug_log_file:
                debug_log_file.write(json.dumps(debug_log_entry) + "\n")
                debug_log_file.flush()

            socketio.emit("debug_shot", debug_log_entry)
        except Exception as e:
            print(f"[WARN] Debug logging error: {e}")


def _finish_shot_detected(
    shot: Shot,
    *,
    emit_event: str,
    initial_ui_ms: float | None = None,
) -> None:
    """Attempt optional enrichment, then always perform required finalization."""
    # Optional hardware may return after the coordinator has timed out this
    # shot. Mutate a shallow dataclass copy so a late result cannot change the
    # already-finalized OPS object retained by the monitor.
    enriched_shot = replace(shot) if _has_slow_shot_enrichment(shot) else shot
    enrichment = _ShotEnrichmentResult()
    try:
        enrichment = _enrich_shot_from_optional_hardware(enriched_shot)
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.error("[SERVER] Deferred shot enrichment failed: %s", error, exc_info=True)
        log_session_error(
            "Deferred shot enrichment failed",
            component="server",
            context={"stage": "deferred_enrichment", "ball_speed_mph": shot.ball_speed_mph},
            exc=error,
        )

    _queue_shot_finalization(
        shot,
        emit_event=emit_event,
        initial_ui_ms=initial_ui_ms,
        enrichment=enrichment,
        final_shot=enriched_shot,
    )


def _queue_shot_finalization(
    shot: Shot,
    *,
    emit_event: str,
    initial_ui_ms: float | None,
    enrichment: _ShotEnrichmentResult | None = None,
    final_shot: Shot | None = None,
) -> None:
    """Record a ready shot for the exclusive finalization worker."""
    _queue_ordered_shot_finalization(
        _PendingShotFinalization(
            shot=final_shot if final_shot is not None else shot,
            emit_event=emit_event,
            initial_ui_ms=initial_ui_ms,
            enrichment=(enrichment if enrichment is not None else _ShotEnrichmentResult()),
        ),
        source_shot=shot,
    )


def _queue_ordered_shot_finalization(
    pending: _PendingShotFinalization,
    *,
    source_shot: Shot,
) -> None:
    """Make a completed shot visible to the ordered finalization worker."""

    shot_number = pending.shot.shot_number
    if shot_number is None:
        raise ValueError("shot must have a stable number before finalization")

    with _shot_finalization_condition:
        registered = _shot_finalization_registered.get(shot_number)
        if registered is None or registered.shot is not source_shot:
            logger.info(
                "[SERVER] Ignoring late enrichment for finalized shot #%d",
                shot_number,
            )
            return
        if shot_number in _shot_finalization_ready:
            logger.warning("[SERVER] Shot #%d is already waiting for finalization", shot_number)
            return
        _shot_finalization_ready[shot_number] = pending
        _ensure_shot_finalization_worker_locked()
        _shot_finalization_condition.notify_all()


def _emit_initial_ops_shot(shot: Shot) -> bool:
    """Publish immediately available OPS metrics before slow enrichments."""
    try:
        shot_data = shot_to_dict(shot)
        stats = monitor.get_session_stats() if monitor else {}
        pending = {}
        if iwr6843_runtime is not None:
            pending["iwr6843"] = True
        if camera_capture_runtime is not None:
            pending["camera"] = True
        socketio.emit(
            "shot",
            {
                "shot": shot_data,
                "stats": stats,
                "pending": pending,
            },
        )
        return True
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.error("[SERVER] Failed to emit initial OPS shot: %s", error, exc_info=True)
        log_session_error(
            "Initial WebSocket shot emit failed",
            component="server",
            context={"stage": "emit_initial_shot", "ball_speed_mph": shot.ball_speed_mph},
            exc=error,
        )
        return False


def _emit_ops_enrichment_skipped(shot: Shot, *, reason: str) -> None:
    """Immediately clear provisional hardware progress when admission fails."""
    skipped_hardware = []
    if iwr6843_runtime is not None:
        skipped_hardware.append("iwr6843")
    if camera_capture_runtime is not None:
        skipped_hardware.append("camera")
    try:
        socketio.emit(
            "shot_update",
            {
                "shot": shot_to_dict(shot),
                "stats": monitor.get_session_stats() if monitor else {},
                "pending": {},
                "enrichment": {
                    "status": "skipped",
                    "reason": reason,
                    "hardware": skipped_hardware,
                },
            },
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning(
            "[SERVER] Failed to clear skipped shot enrichment status: %s",
            error,
            exc_info=True,
        )


def _has_slow_shot_enrichment(shot: Shot) -> bool:
    """Whether optional hardware can add seconds to this shot callback."""
    return shot.mode != "mock" and (
        iwr6843_runtime is not None or camera_capture_runtime is not None
    )


def _drain_shot_enrichment_queue() -> None:
    """Finish deferred shots in detection order, one hardware consumer at a time."""
    global shot_enrichment_task  # pylint: disable=global-statement

    while True:
        try:
            shot, emit_event, initial_ui_ms = shot_enrichment_queue.get_nowait()
        except queue.Empty:
            with shot_enrichment_task_lock:
                if shot_enrichment_queue.empty():
                    shot_enrichment_task = None
                    return
            continue

        try:
            _finish_shot_detected(
                shot,
                emit_event=emit_event,
                initial_ui_ms=initial_ui_ms,
            )
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.error("[SERVER] Deferred shot finalization failed: %s", error, exc_info=True)
            log_session_error(
                "Deferred shot finalization failed",
                component="server",
                context={"stage": "deferred_finalization", "ball_speed_mph": shot.ball_speed_mph},
                exc=error,
            )
        finally:
            shot_enrichment_queue.task_done()


def _defer_shot_enrichment(
    shot: Shot,
    *,
    emit_event: str,
    initial_ui_ms: float | None,
) -> None:
    """Queue optional hardware work without blocking the OPS capture callback."""
    global shot_enrichment_task  # pylint: disable=global-statement

    with shot_enrichment_task_lock:
        shot_enrichment_queue.put_nowait((shot, emit_event, initial_ui_ms))
        if shot_enrichment_task is not None:
            return
        try:
            # Reserve the slot while the task starts. The worker needs the same
            # lock before clearing it, which closes the fast-finish race.
            shot_enrichment_task = True
            task = socketio.start_background_task(_drain_shot_enrichment_queue)
            shot_enrichment_task = task
        except Exception:
            shot_enrichment_task = None
            queued_shot, _event, _latency = shot_enrichment_queue.get_nowait()
            shot_enrichment_queue.task_done()
            if queued_shot is not shot:
                raise RuntimeError("shot enrichment queue lost FIFO ordering") from None
            raise


def on_shot_detected(shot: Shot) -> None:
    """Serialize detection order before publishing or queueing a shot."""
    with _shot_callback_lock:
        _handle_shot_detected(shot)


def _handle_shot_detected(shot: Shot) -> None:
    """Publish OPS metrics promptly, then enrich optional hardware data."""
    _assign_shot_number(shot)
    active_profile = get_profile_store().get_active()
    shot.profile_id = active_profile.id
    shot.profile_name = active_profile.name
    logger.info("[SERVER] Shot callback: %.1f mph", shot.ball_speed_mph)

    if not _has_slow_shot_enrichment(shot):
        _register_shot_for_finalization(
            shot,
            emit_event="shot",
            initial_ui_ms=None,
        )
        _finish_shot_detected(shot, emit_event="shot")
        return

    emitted = _emit_initial_ops_shot(shot)
    initial_ui_ms = None
    if emitted and shot.impact_timestamp is not None:
        initial_ui_ms = max(0.0, (time.time() - shot.impact_timestamp) * 1000.0)
        logger.info(
            "[SERVER] Initial OPS metrics emitted %.0fms after impact; "
            "hardware enrichment continues in background",
            initial_ui_ms,
        )
    final_event = "shot_update" if emitted else "shot"
    _register_shot_for_finalization(
        shot,
        emit_event=final_event,
        initial_ui_ms=initial_ui_ms,
        needs_watchdog=True,
    )
    try:
        _defer_shot_enrichment(
            shot,
            emit_event=final_event,
            initial_ui_ms=initial_ui_ms,
        )
    except queue.Full:
        logger.warning(
            "[SERVER] Shot enrichment queue is full (%d waiting); "
            "skipping optional hardware for shot #%d",
            _SHOT_ENRICHMENT_QUEUE_CAPACITY,
            shot.shot_number,
        )
        if emitted:
            _emit_ops_enrichment_skipped(shot, reason="queue_full")
        _queue_shot_finalization(
            shot,
            emit_event=final_event,
            initial_ui_ms=initial_ui_ms,
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning(
            "[SERVER] Could not defer shot enrichment: %s",
            error,
            exc_info=True,
        )
        if emitted:
            _emit_ops_enrichment_skipped(shot, reason="worker_unavailable")
        _queue_shot_finalization(
            shot,
            emit_event=final_event,
            initial_ui_ms=initial_ui_ms,
        )


def swing_speed_to_dict(event: SwingSpeedEvent) -> dict:
    """Convert a swing speed training event to a UI payload."""
    return {
        "peak_speed_mph": round(event.peak_speed_mph, 1),
        "timestamp": event.timestamp.isoformat(),
        "duration_ms": round(event.duration_ms),
        "reading_count": event.reading_count,
        "trigger_speed_mph": round(event.trigger_speed_mph, 1),
        "peak_magnitude": event.peak_magnitude,
        "training_implement": event.training_implement,
        "training_implement_label": event.training_implement_label,
        "profile_id": event.profile_id,
        "profile_name": event.profile_name,
        "unit": event.unit,
        "mode": event.mode,
    }


def swing_speed_to_shot_dict(event: SwingSpeedEvent) -> dict:
    """Convert a swing speed event to the existing shot UI shape."""
    peak_speed = round(event.peak_speed_mph, 1)
    return {
        "ball_speed_mph": peak_speed,
        "ball_speed_raw_mph": None,
        "club_speed_mph": peak_speed,
        "smash_factor": None,
        "estimated_carry_yards": 0,
        "carry_range": [0, 0],
        "club": event.training_implement_label,
        "profile_id": event.profile_id,
        "profile_name": event.profile_name,
        "timestamp": event.timestamp.isoformat(),
        "peak_magnitude": event.peak_magnitude,
        "launch_angle_vertical": None,
        "launch_angle_horizontal": None,
        "launch_angle_confidence": None,
        "launch_angle_vertical_confidence": None,
        "launch_angle_horizontal_confidence": None,
        "launch_angle_vertical_source": None,
        "launch_angle_horizontal_source": None,
        "angle_source": None,
        "club_angle_deg": None,
        "club_path_deg": None,
        "spin_axis_deg": None,
        "spin_rpm": None,
        "spin_rpm_measured": None,
        "spin_source": None,
        "spin_confidence": None,
        "spin_quality": None,
        "spin_snr": None,
        "spin_modulation_depth": None,
        "spin_peak_freq_hz": None,
        "spin_peak_freq_rpm": None,
        "spin_seam_cycles": None,
        "spin_at_lower_rail": None,
        "spin_at_upper_rail": None,
        "spin_candidates": None,
        "spin_phase_method": None,
        "spin_phase_rpm": None,
        "spin_phase_snr": None,
        "spin_phase_agreement_pct": None,
        "spin_phase_confirmed": None,
        "spin_rejection_reason": None,
        "carry_spin_adjusted": None,
        "mode": event.mode,
        "swing_speed_duration_ms": round(event.duration_ms),
        "swing_speed_reading_count": event.reading_count,
        "swing_speed_trigger_mph": round(event.trigger_speed_mph, 1),
        "training_implement": event.training_implement,
        "training_implement_label": event.training_implement_label,
    }


def on_swing_speed_detected(event: SwingSpeedEvent):
    """Handle swing speed training reps and emit them to connected clients."""
    active_profile = get_profile_store().get_active()
    event.profile_id = active_profile.id
    event.profile_name = active_profile.name
    event_data = swing_speed_to_dict(event)
    shot_data = swing_speed_to_shot_dict(event)
    stats = monitor.get_session_stats() if monitor else {}
    socketio.emit("swing_speed", {"event": event_data, "stats": stats})
    socketio.emit("shot", {"shot": shot_data, "stats": stats})
    logger.info(
        "[SERVER] Swing speed event emitted: peak=%.1f mph, readings=%d",
        event.peak_speed_mph,
        event.reading_count,
    )


def start_monitor(
    port: Optional[str] = None,
    mock: bool = False,
    trigger_type: str = "sound",
    debug: bool = False,
    trigger_kwargs: Optional[dict] = None,
    sample_rate_ksps: int = 30,
    swing_speed_mode: bool = False,
    swing_speed_kwargs: Optional[dict] = None,
    ops_baud: Optional[int] = None,
):
    """
    Start the monitor in launch monitor or swing speed mode.

    Args:
        port: Serial port for radar
        mock: Run in mock mode without radar
        trigger_type: Trigger strategy (sound or speed)
        debug: Enable verbose debug output
        ops_baud: Target UART baud when the OPS243 is on the GPIO header
    """
    global monitor, mock_mode, mock_swing_speed_mode, debug_mode, radar_config

    # Stop any existing monitor first
    if monitor is not None:
        print("[MONITOR] Stopping existing monitor before starting new one")
        stop_monitor()

    mock_mode = mock
    mock_swing_speed_mode = bool(mock and swing_speed_mode)
    debug_mode = debug
    if mock and swing_speed_mode:
        monitor = MockSwingSpeedMonitor(**(swing_speed_kwargs or {}))
        print("[MODE] Mock swing speed training mode")
    elif mock:
        # Mock mode for testing without radar
        monitor = MockLaunchMonitor()
    elif swing_speed_mode:
        from .swing_speed import SwingSpeedMonitor

        monitor = SwingSpeedMonitor(
            port=port,
            **(swing_speed_kwargs or {}),
        )
        print("[MODE] Swing speed training mode")
    else:
        from .rolling_buffer import RollingBufferMonitor

        monitor = RollingBufferMonitor(
            port=port,
            trigger_type=trigger_type,
            sample_rate_ksps=sample_rate_ksps,
            ops_baud=ops_baud,
            **(trigger_kwargs or {}),
        )
        print(
            "[MODE] Rolling buffer mode "
            f"(trigger: {trigger_type}, sample_rate: {sample_rate_ksps}ksps)"
        )

    monitor.connect()
    _reset_shot_sequence()

    if swing_speed_mode:
        swing_config = swing_speed_kwargs or {}
        radar_config = {
            **radar_config,
            "min_speed": int(swing_config.get("trigger_threshold_mph", 30)),
            "max_speed": int(swing_config.get("max_speed_mph") or 0),
        }

    logger.info(
        "[SERVER] Starting monitor: mode=%s, trigger=%s, sample_rate=%dksps",
        "swing-speed" if swing_speed_mode else ("mock" if mock else "rolling-buffer"),
        trigger_type,
        sample_rate_ksps,
    )

    # Start session logging
    session_logger = get_session_logger()
    if session_logger:
        radar_info = monitor.get_radar_info() if not mock else {}
        session_logger.start_session(
            radar_port=port if not mock else "mock",
            firmware_version=radar_info.get("Version"),
            camera_enabled=camera_capture_runtime is not None,
            camera_model="capture" if camera_capture_runtime is not None else None,
            config=_session_start_config(),
            mode="swing-speed" if swing_speed_mode else ("mock" if mock else "rolling-buffer"),
            trigger_type=None if swing_speed_mode or mock else trigger_type,
        )
        if not mock and radar_info:
            session_logger.log_connection(
                device="ops243",
                port=port or "auto",
                baud=getattr(monitor.radar, "baud", 0) if hasattr(monitor, "radar") else 0,
                firmware=radar_info.get("Version"),
            )
            # Capture the OPS radar-clock -> host-epoch offset once at startup
            # before the trigger loop runs. Sound-triggered captures use this to
            # anchor K-LD7 correlation to the OPS trigger_time instead of the
            # USB first-byte arrival time.
            radar = getattr(monitor, "radar", None)
            if not swing_speed_mode and radar is not None and hasattr(radar, "read_clock_sync"):
                try:
                    clock_sync = radar.read_clock_sync()
                    session_logger.log_clock_sync(
                        device="ops243",
                        port=port or "auto",
                        summary=clock_sync,
                    )
                except Exception:  # pylint: disable=broad-except
                    # Instrumentation must never break startup.
                    logger.warning("[SERVER] OPS clock sync read failed", exc_info=True)
        if not mock and iwr6843_runtime is not None:
            session_logger.log_connection(
                device="iwr6843",
                port=iwr6843_runtime.capture_monitor.port,
                baud=getattr(iwr6843_runtime.capture_monitor.radar, "baud", 1_041_667),
                firmware="custom-l3-dump",
                estimator="lcmf_v1",
                trigger_pin_bcm=iwr6843_runtime_config.get("trigger_pin_bcm"),
            )
        if not mock and inclinometer_service is not None:
            session_logger.log_connection(
                device="lis3dh",
                port=f"i2c-{inclinometer_runtime_config.get('i2c_bus', 1)}",
                baud=0,
                address=inclinometer_runtime_config.get("i2c_address", "0x18"),
                sample_hz=inclinometer_runtime_config.get("sample_hz", 10.0),
            )

    if swing_speed_mode:
        monitor.start(
            event_callback=on_swing_speed_detected,
            live_callback=on_live_reading,
        )
    elif not mock:

        def on_trigger_diagnostic(data: dict):
            """Forward trigger diagnostics to connected UI clients."""
            socketio.emit("trigger_diagnostic", data)

        monitor.start(  # pylint: disable=unexpected-keyword-arg
            shot_callback=on_shot_detected,
            live_callback=on_live_reading,
            diagnostic_callback=on_trigger_diagnostic,
            processing_callback=on_shot_processing,
        )
        if iwr6843_runtime is not None:
            iwr6843_runtime.capture_monitor.arm()
    else:
        monitor.start(shot_callback=on_shot_detected, live_callback=on_live_reading)


def _fire_cloud_push(session_logger):
    """Best-effort, non-blocking cloud push on session end.

    Fully guarded: the uploader is opt-in and must never delay or break the
    shot/session path. The systemd timer is the safety net if this no-ops.
    """
    try:
        from .cloud.config import load_config
        from .cloud.trigger import fire_push_async

        config = load_config()
        if config is None or not config.is_active():
            return
        log_dir = getattr(session_logger, "log_dir", None)
        if log_dir is not None:
            fire_push_async(config, log_dir=log_dir)
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def _run_cloud_push_for_ui():
    """Run a manual cloud push and report the result to connected UI clients."""
    socketio.emit("cloud_upload_status", {"state": "running", "message": "Uploading..."})
    try:
        from .cloud import commands
        from .cloud.client import CloudClient
        from .cloud.config import CloudConfig, load_config

        config = load_config() or CloudConfig()
        session_logger = get_session_logger()
        log_dir = getattr(session_logger, "log_dir", None)
        if log_dir is None:
            log_dir = session_logger.DEFAULT_LOG_DIR if session_logger else None
        if log_dir is None:
            log_dir = Path.home() / "openflight_sessions"

        messages = []
        summary = commands.cmd_push(
            config,
            Path(log_dir),
            CloudClient(config.endpoint, token=config.device_token or None),
            out=messages.append,
        )

        if summary.get("needs_relink"):
            state = "error"
            message = "Cloud token rejected. Re-link this Pi."
        elif summary.get("skipped") == "inactive":
            state = "error"
            message = "Cloud uploader is not linked."
        elif summary.get("offline"):
            state = "error"
            message = "Cloud unreachable."
        elif summary.get("uploaded", 0) > 0:
            state = "complete"
            message = f"Uploaded {summary['uploaded']} session(s)."
        elif messages:
            state = "complete"
            message = messages[-1]
        else:
            state = "complete"
            message = "Nothing to upload."

        socketio.emit(
            "cloud_upload_status",
            {"state": state, "message": message, "summary": summary},
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] Manual cloud upload failed: %s", exc, exc_info=True)
        socketio.emit(
            "cloud_upload_status",
            {"state": "error", "message": str(exc)},
        )


def stop_monitor():
    """Stop the launch monitor."""
    global monitor, mock_swing_speed_mode  # pylint: disable=global-statement

    # End session logging
    session_logger = get_session_logger()
    if session_logger:
        session_logger.end_session()
        _fire_cloud_push(session_logger)

    if monitor:
        monitor.stop()
        monitor.disconnect()
        monitor = None
    mock_swing_speed_mode = False


class MockLaunchMonitor:
    """Mock launch monitor for UI development without radar hardware."""

    def __init__(self):
        """Initialize mock monitor."""
        self._shots: List[Shot] = []
        self._running = False
        self._shot_callback = None
        self._current_club = ClubType.DRIVER

    def connect(self):
        """Connect to mock radar (no-op)."""
        return True

    def disconnect(self):
        """Disconnect from mock radar."""
        self.stop()

    def start(self, shot_callback=None, live_callback=None):  # pylint: disable=unused-argument
        """Start mock monitoring."""
        self._shot_callback = shot_callback
        self._running = True
        print("Mock monitor started - simulate shots via WebSocket")

    def stop(self):
        """Stop mock monitoring."""
        self._running = False

    def simulate_shot(self, ball_speed: float = None):
        """Simulate a shot for testing using realistic TrackMan-based values."""
        physics = get_club_physics(self._current_club)
        profile = get_club_simulation_profile(self._current_club)
        defaults = SHOT_SIMULATION_DEFAULTS

        if ball_speed is None:
            ball_speed = max(
                defaults.min_ball_speed_mph,
                min(
                    defaults.max_ball_speed_mph,
                    random.gauss(
                        physics.average_ball_speed_mph,
                        profile.ball_speed_std_dev_mph,
                    ),
                ),
            )

        smash_factor = profile.average_smash + random.uniform(
            -defaults.smash_variation, defaults.smash_variation
        )
        club_speed = ball_speed / smash_factor

        spin_rpm = max(
            defaults.min_spin_rpm,
            random.gauss(profile.average_spin_rpm, profile.spin_std_dev_rpm),
        )

        launch_v = max(
            defaults.min_launch_deg,
            random.gauss(physics.optimal_launch_deg, profile.launch_std_dev_deg),
        )
        launch_h = random.gauss(0, defaults.horizontal_launch_std_dev_deg)
        launch_confidence = round(
            random.uniform(defaults.confidence_min, defaults.confidence_max), 2
        )

        club_aoa = round(
            random.gauss(
                defaults.angle_of_attack_mean_deg,
                defaults.angle_of_attack_std_dev_deg,
            ),
            1,
        )

        shot = Shot(
            ball_speed_mph=ball_speed,
            club_speed_mph=club_speed,
            timestamp=datetime.now(),
            club=self._current_club,
            spin_rpm=spin_rpm,
            spin_confidence=random.choice(defaults.spin_confidence_choices),
            launch_angle_vertical=round(launch_v, 1),
            launch_angle_horizontal=round(launch_h, 1),
            launch_angle_confidence=launch_confidence,
            launch_angle_vertical_confidence=launch_confidence,
            launch_angle_horizontal_confidence=launch_confidence,
            launch_angle_vertical_source="mock",
            launch_angle_horizontal_source="mock",
            angle_source="mock",
            club_angle_deg=club_aoa,
            club_path_deg=round(
                random.uniform(
                    -defaults.club_path_max_abs_deg,
                    defaults.club_path_max_abs_deg,
                ),
                1,
            ),
            spin_axis_deg=round(
                launch_h
                - random.uniform(
                    -defaults.spin_axis_error_max_abs_deg,
                    defaults.spin_axis_error_max_abs_deg,
                ),
                1,
            ),
            mode="mock",
        )

        self._shots.append(shot)

        if self._shot_callback:
            self._shot_callback(shot)

        return shot

    def get_shots(self) -> List[Shot]:
        """Get all recorded shots."""
        return self._shots.copy()

    def get_session_stats(self) -> dict:
        """Get session statistics."""
        return summarize_shots(self._shots, mode="mock")

    def clear_session(self):
        """Clear all recorded shots."""
        self._shots = []

    def set_club(self, club: ClubType):
        """Set the current club for future shots."""
        self._current_club = club


class _MockSwingRadar:
    """Tiny radar facade so UI tuning can exercise the swing speed controls."""

    port = "mock"
    baud = 0

    def set_min_speed_filter(self, value):  # pylint: disable=unused-argument
        """Accept mock lower speed updates."""

    def set_max_speed_filter(self, value):  # pylint: disable=unused-argument
        """Accept mock upper speed updates."""

    def set_magnitude_filter(self, min_mag=0, max_mag=0):  # pylint: disable=unused-argument
        """Accept mock magnitude updates."""

    def set_transmit_power(self, level):  # pylint: disable=unused-argument
        """Accept mock transmit power updates."""


class MockSwingSpeedMonitor:
    """Mock swing speed monitor for UI development without OPS hardware."""

    def __init__(
        self,
        trigger_threshold_mph: float = 30.0,
        max_speed_mph: Optional[float] = 130.0,
        min_readings: int = 3,
        single_reading_peak_mph: float = 60.0,
        **kwargs,  # pylint: disable=unused-argument
    ):
        self.trigger_threshold_mph = float(trigger_threshold_mph)
        self.max_speed_mph = None if max_speed_mph is None else float(max_speed_mph)
        self.min_readings = int(min_readings)
        self.single_reading_peak_mph = float(single_reading_peak_mph)
        self.radar = _MockSwingRadar()
        self._events: List[SwingSpeedEvent] = []
        self._running = False
        self._event_callback = None
        self.training_implement = "driver"
        self.training_implement_label = "Driver"

    def connect(self):
        """Connect to mock radar."""
        return True

    def disconnect(self):
        """Disconnect from mock radar."""
        self.stop()

    def start(self, event_callback=None, live_callback=None):  # pylint: disable=unused-argument
        """Start mock swing speed monitoring."""
        self._event_callback = event_callback
        self._running = True
        print("Mock swing speed monitor started - simulate swings via WebSocket")

    def stop(self):
        """Stop mock monitoring."""
        self._running = False

    def get_radar_info(self):
        """Return mock radar metadata."""
        return {"Version": "mock-swing-speed"}

    def simulate_shot(self, peak_speed: float = None):
        """Simulate a club-only swing speed rep."""
        lower = max(20.0, float(self.trigger_threshold_mph))
        upper = float(self.max_speed_mph) if self.max_speed_mph is not None else 130.0
        upper = max(lower + 1.0, upper)

        if peak_speed is None:
            center = min(max(lower + 35.0, 95.0), upper - 4.0)
            peak_speed = random.gauss(center, 6.0)

        peak_speed = max(lower, min(upper, float(peak_speed)))
        trigger_speed = max(lower, min(peak_speed, peak_speed - random.uniform(12.0, 24.0)))
        reading_count = random.randint(max(1, self.min_readings), max(self.min_readings + 3, 8))

        event = SwingSpeedEvent(
            peak_speed_mph=peak_speed,
            timestamp=datetime.now(),
            duration_ms=random.uniform(850.0, 1800.0),
            reading_count=reading_count,
            trigger_speed_mph=trigger_speed,
            peak_magnitude=random.uniform(80.0, 450.0),
            training_implement=self.training_implement,
            training_implement_label=self.training_implement_label,
        )
        self._events.append(event)

        if self._event_callback:
            self._event_callback(event)

        return event

    def get_shots(self) -> List[Shot]:
        """Swing speed mode has no ball-flight shots."""
        return []

    def get_events(self) -> List[SwingSpeedEvent]:
        """Get all simulated swing speed reps."""
        return list(self._events)

    def get_session_stats(self) -> dict:
        """Get swing speed session statistics."""
        if not self._events:
            return {
                "shot_count": 0,
                "avg_ball_speed": 0,
                "max_ball_speed": 0,
                "min_ball_speed": 0,
                "avg_club_speed": None,
                "avg_smash_factor": None,
                "avg_carry_est": 0,
            }

        speeds = [event.peak_speed_mph for event in self._events]
        return {
            "shot_count": len(self._events),
            "avg_ball_speed": statistics.mean(speeds),
            "max_ball_speed": max(speeds),
            "min_ball_speed": min(speeds),
            "std_dev": statistics.stdev(speeds) if len(speeds) > 1 else 0,
            "avg_club_speed": statistics.mean(speeds),
            "avg_smash_factor": None,
            "avg_carry_est": 0,
        }

    def clear_session(self):
        """Clear all simulated swing speed reps."""
        self._events = []

    def set_club(self, club: ClubType):  # pylint: disable=unused-argument
        """Accept club changes for API compatibility."""

    def set_training_implement(self, implement: str, label: str):
        """Set the training implement stamped onto future mock reps."""
        self.training_implement = implement
        self.training_implement_label = label


def _add_ballistics_arguments(parser):
    """Add the preferred ballistic carry model and its explicit opt-out."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--ballistics",
        action="store_true",
        dest="ballistics",
        help=(
            "Use the physics-based carry simulator (drag + Magnus, RK4). "
            "This is the default; shots without a vertical launch angle "
            "fall back to the legacy table estimator."
        ),
    )
    group.add_argument(
        "--no-ballistics",
        action="store_false",
        dest="ballistics",
        help="Disable the physics simulator and use the legacy carry table for all shots.",
    )
    parser.set_defaults(ballistics=True)


def _add_battery_arguments(parser):
    """Add explicit battery-provider selection."""
    parser.add_argument(
        "--battery",
        choices=SUPPORTED_BATTERY_PROVIDERS,
        default=None,
        help="Show battery and external-power status using the selected provider",
    )


def _apply_kld7_device_defaults(args, dev_root: Path = Path("/dev")) -> None:
    """Preserve kiosk symlink discovery while keeping CLI policy in the server."""
    vertical = dev_root / "kld7_vertical"
    horizontal = dev_root / "kld7_horizontal"
    if args.kld7 and args.kld7_port is None and vertical.exists():
        args.kld7_port = str(vertical)
    if args.kld7 and horizontal.exists():
        args.kld7_horizontal = True
    if args.kld7_horizontal and args.kld7_horizontal_port is None and horizontal.exists():
        args.kld7_horizontal_port = str(horizontal)


def main():
    """Run the server."""
    import argparse  # pylint: disable=import-outside-toplevel

    parser = argparse.ArgumentParser(description="OpenFlight UI Server")
    parser.add_argument("--port", "-p", help="Serial port for radar")
    parser.add_argument(
        "--ops-baud",
        type=int,
        default=None,
        help=(
            "Target UART baud for the OPS243 on the GPIO header "
            f"(default {OPS243Radar.DEFAULT_UART_BAUD}). Only meaningful when "
            "--port is a UART device such as /dev/ttyAMA0; drop to 115200 if "
            "230400 proves unreliable on your board."
        ),
    )
    parser.add_argument("--mock", "-m", action="store_true", help="Run in mock mode without radar")
    parser.add_argument(
        "--mock-swing-speed",
        action="store_true",
        help="Run swing speed training mode with simulated reps and no OPS radar",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    parser.add_argument(
        "--web-port", type=int, default=8080, help="Web server port (default: 8080)"
    )
    parser.add_argument(
        "--startup-status-file",
        default=None,
        help="Write structured initialization progress for the optional kiosk splash",
    )
    parser.add_argument(
        "--debug", "-d", action="store_true", help="Enable verbose FFT/CFAR debug output"
    )
    parser.add_argument(
        "--radar-log", action="store_true", help="Log raw radar data to console (Python logging)"
    )
    parser.add_argument(
        "--show-raw", action="store_true", help="Show raw radar readings in console (signed values)"
    )
    parser.add_argument(
        "--camera-capture",
        action="store_true",
        help="Enable high-speed camera rolling-buffer capture and replay",
    )
    parser.add_argument(
        "--camera-shot-analysis",
        action="store_true",
        help=(
            "Enable camera capture with experimental impact-aware ball and club analysis "
            "instead of the legacy camera analyzer"
        ),
    )
    parser.add_argument("--camera-capture-width", type=int, default=640)
    parser.add_argument("--camera-capture-height", type=int, default=400)
    parser.add_argument("--camera-capture-fps", type=float, default=300.0)
    parser.add_argument("--camera-capture-pre-ms", type=float, default=150.0)
    parser.add_argument("--camera-capture-post-ms", type=float, default=50.0)
    parser.add_argument(
        "--camera-capture-exposure-us",
        type=int,
        default=1000,
        help="Exposure seed used by the one-time startup calibration.",
    )
    parser.add_argument(
        "--camera-capture-gain",
        type=float,
        default=4.0,
        help="Analogue-gain seed used by the one-time startup calibration.",
    )
    parser.add_argument(
        "--camera-capture-mount-height-m",
        type=float,
        default=0.20955,
        help="Camera optical-center height above the hitting surface (default: 8.25 in).",
    )
    parser.add_argument(
        "--camera-capture-horizontal-offset-deg",
        type=float,
        default=0.0,
        help="Measured camera target-line correction added to horizontal launch angles.",
    )
    parser.add_argument(
        "--camera-capture-lateral-offset-m",
        type=float,
        default=0.0,
        help=(
            "Camera optical-center lateral position relative to radar center in meters; "
            "positive is target-right when looking downrange."
        ),
    )
    parser.add_argument(
        "--camera-capture-roll-deg",
        type=float,
        default=0.0,
        help=(
            "Clockwise image-roll correction applied to camera preview and geometry "
            "without modifying saved raw frames."
        ),
    )
    parser.add_argument(
        "--camera-capture-stream",
        choices=("raw", "main-y"),
        default="raw",
        help="Camera stream to persist (raw preserves OV9281 R8 detail; main-y is smaller).",
    )
    parser.add_argument(
        "--camera-capture-scaler-crop",
        default=None,
        help="Optional Picamera2 ScalerCrop as X,Y,W,H.",
    )
    parser.add_argument(
        "--camera-capture-rotate-180",
        action="store_true",
        help="Rotate saved camera frames 180 degrees.",
    )
    parser.add_argument(
        "--camera-capture-mirror-horizontal",
        action="store_true",
        help="Mirror saved frames left-to-right after mount rotation.",
    )
    parser.add_argument(
        "--session-location",
        "-l",
        default="range",
        help="Location identifier for session logs (e.g., 'range', 'course', 'home')",
    )
    parser.add_argument(
        "--log-dir", help="Directory for session logs (default: ~/openflight_sessions)"
    )
    parser.add_argument(
        "--profiles-path",
        default=None,
        help=(
            "Path to profiles.json (default: OPENFLIGHT_PROFILES_PATH or "
            "~/.config/openflight/profiles.json)"
        ),
    )
    parser.add_argument("--no-logging", action="store_true", help="Disable session logging")
    _add_battery_arguments(parser)
    parser.add_argument(
        "--sim",
        action="store_true",
        help="Enable simulator connectors from config/sim.json (GSPro / OpenGolfSim / PAR-TEE). "
        "Off by default.",
    )
    _add_ballistics_arguments(parser)
    parser.add_argument(
        "--trigger",
        choices=["sound", "speed"],
        default="sound",
        help="Trigger strategy (default: sound)",
    )
    parser.add_argument(
        "--swing-speed",
        action="store_true",
        help="Run club-only swing speed training mode (no impact or ball required)",
    )
    parser.add_argument(
        "--swing-speed-threshold",
        type=float,
        default=30.0,
        help="Outbound speed threshold that starts a swing speed rep (default: 30 mph)",
    )
    parser.add_argument(
        "--swing-speed-max",
        type=float,
        default=130.0,
        help="Maximum plausible swing speed accepted from OPS reports; use 0 to disable (default: 130 mph)",
    )
    parser.add_argument(
        "--swing-speed-min-readings",
        type=int,
        default=3,
        help="Minimum qualifying radar readings required to count a swing speed rep (default: 3)",
    )
    parser.add_argument(
        "--swing-speed-single-peak",
        type=float,
        default=60.0,
        help="Peak speed that can count as a swing from one radar reading (default: 60 mph)",
    )
    parser.add_argument(
        "--swing-speed-num-reports",
        type=int,
        default=8,
        help="Number of OPS speed candidates to report per sample cycle (default: 8)",
    )
    parser.add_argument(
        "--swing-speed-end-ms",
        type=float,
        default=1000.0,
        help="Milliseconds below threshold before ending a swing speed rep (default: 1000)",
    )
    parser.add_argument(
        "--swing-speed-cooldown-ms",
        type=float,
        default=750.0,
        help="Cooldown after a swing speed rep before accepting another (default: 750)",
    )
    parser.add_argument(
        "--swing-speed-rejected-cooldown-ms",
        type=float,
        default=100.0,
        help="Cooldown after an ignored short motion before re-arming (default: 100)",
    )
    parser.add_argument(
        "--sound-pre-trigger",
        type=int,
        default=16,
        help=(
            "Pre-trigger segments S#n, 0-32 "
            "(default: 16 = 50/50 split, each segment ~4.27ms at 30ksps)"
        ),
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=30,
        help=(
            "Radar sample rate in ksps (default: 30). "
            "Lower = longer buffer but lower max speed. "
            "25=174mph/164ms, 27=187mph/152ms"
        ),
    )
    parser.add_argument(
        "--iwr6843",
        action="store_true",
        help="Enable TI IWR6843 L3 capture and LCMF-v1 vertical launch angle",
    )
    parser.add_argument(
        "--inclinometer",
        action="store_true",
        help="Enable LIS3DH enclosure pitch compensation for IWR6843 tilt",
    )
    parser.add_argument(
        "--inclinometer-zero-offset",
        type=float,
        default=0.0,
        help="Degrees added to raw LIS3DH pitch (default: 0)",
    )
    parser.add_argument(
        "--iwr6843-port", default=None, help="TI serial port (auto-detect by default)"
    )
    parser.add_argument(
        "--iwr6843-config",
        default="config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg",
        help="TI RF config matching the flashed L3 firmware",
    )
    parser.add_argument(
        "--iwr6843-cal",
        default="config/iwr6843_calibration_reference.json",
        help="TI complex array/range calibration JSON",
    )
    parser.add_argument(
        "--iwr6843-trigger-pin",
        type=int,
        default=17,
        help="BCM GPIO receiving the shared sound-trigger edge (default: 17)",
    )
    parser.add_argument(
        "--iwr6843-tee-m",
        type=float,
        default=1.575,
        help="Antenna-center to tee slant range in metres (default: 1.575)",
    )
    parser.add_argument(
        "--iwr6843-net-m",
        type=float,
        default=4.6,
        help="Antenna-center to net range in metres (default: 4.6)",
    )
    parser.add_argument(
        "--iwr6843-tilt-deg",
        type=float,
        default=None,
        help="Override mount tilt from the TI calibration JSON",
    )
    parser.add_argument(
        "--iwr6843-radar-height-m",
        type=float,
        default=None,
        help="Override antenna-center height from the TI calibration JSON",
    )
    parser.add_argument(
        "--iwr6843-ball-height-m",
        type=float,
        default=0.040,
        help="Ball-center height above the floor/mat (default: 0.040)",
    )
    parser.add_argument(
        "--iwr6843-tx-order",
        choices=("auto", "normal", "reversed"),
        default="auto",
        help="TI TDM chirp order; auto reads the chirp masks from the cfg",
    )
    parser.add_argument(
        "--iwr6843-capture-timeout",
        type=float,
        default=16.0,
        help=(
            "Maximum seconds an OPS shot waits for its TI UART dump "
            "(default: 16). A 25-frame ring is 763,200 bytes, which takes "
            "7.4 s at the saturated 1,041,667 baud link."
        ),
    )
    parser.add_argument(
        "--iwr6843-output-dir",
        default=None,
        help=("Raw TI dump directory when --debug is enabled (default: <session-log-dir>/iwr6843)"),
    )
    parser.add_argument(
        "--iwr6843-azimuth-offset-deg",
        type=float,
        default=0.0,
        help=(
            "Azimuth of the radar boresight relative to the target line, in degrees. "
            "Positive means boresight points right of the target line. Added to "
            "measured horizontal launch and club path; 0 reports both relative "
            "to boresight."
        ),
    )
    parser.add_argument(
        "--iwr6843-horizontal-phase-reference-rad",
        type=float,
        default=None,
        help=(
            "Static target-line phase measured by horizontal aim calibration. "
            "Subtracted from the TX2 horizontal proxy before angle conversion."
        ),
    )
    parser.add_argument(
        "--kld7",
        action="store_true",
        help="[DEPRECATED] Enable K-LD7 vertical angle radar (launch angle)",
    )
    parser.add_argument(
        "--kld7-port",
        default=None,
        help="K-LD7 vertical serial port (auto-detect if not specified)",
    )
    parser.add_argument(
        "--kld7-angle-offset",
        type=float,
        default=1.5,
        help=(
            "K-LD7 vertical boresight offset in degrees. Not user-measurable "
            "without a corner reflector; 1.5 is the calibrated default for the "
            "standard mount (default: 1.5)"
        ),
    )
    parser.add_argument(
        "--calculated-spin",
        action="store_true",
        help=(
            "Replace radar-measured spin with the kinematic estimate "
            "(170*v*sin(LA)^1.2) when the launch angle was measured. The 24 GHz "
            "OPS return carries no usable spin line (see "
            "src/openflight/spin_estimate.py); the measured value is kept in "
            "spin_rpm_measured for offline scoring"
        ),
    )
    parser.add_argument(
        "--kld7-mount-tilt",
        type=float,
        default=os.getenv("KLD7_MOUNT_TILT"),
        help=(
            "K-LD7 vertical radar mount tilt in degrees. REQUIRED with --kld7 — "
            "measure it with a phone inclinometer against the radar face; there is "
            "no default because a wrong tilt silently corrupts the launch angle"
        ),
    )
    parser.add_argument(
        "--kld7-ball-distance",
        type=float,
        default=5.0,
        help="Radar-to-tee distance in feet (default: 5.0)",
    )
    parser.add_argument(
        "--net-distance",
        dest="net_distance",
        type=float,
        default=10.0,
        help=(
            "Ball-to-net/screen distance in feet (two_ray). For nets beyond the "
            "~11ft FSK range wrap, far-flight frames are de-aliased and kept "
            "instead of dropped (default: 10.0; nets at/inside the wrap are "
            "unaffected)."
        ),
    )
    parser.add_argument(
        "--kld7-radar-height-inches",
        dest="kld7_radar_height_inches",
        type=float,
        default=4.0,
        help=(
            "K-LD7 radar height above the ball in inches, used by the ball-speed "
            "cosine correction geometry (default: 4.0)"
        ),
    )
    parser.add_argument(
        "--kld7-vertical-raw",
        dest="kld7_vertical_raw",
        action="store_true",
        help=(
            "TEST MODE: show the raw vertical launch angle for every shot the "
            "estimator produces, bypassing all display guardrails (plausibility, "
            "soft-lane, estimator-agreement, confidence floor). Default off."
        ),
    )
    parser.add_argument(
        "--kld7-horizontal",
        action="store_true",
        help="[DEPRECATED] Enable K-LD7 horizontal angle radar (club path)",
    )
    parser.add_argument("--kld7-horizontal-port", default=None, help="K-LD7 horizontal serial port")
    parser.add_argument(
        "--kld7-horizontal-offset",
        type=float,
        default=0.0,
        help="K-LD7 horizontal angle offset in degrees (default: 0.0)",
    )
    args = parser.parse_args()
    _apply_kld7_device_defaults(args)
    if args.camera_shot_analysis:
        args.camera_capture = True

    # Mount tilt cannot be defaulted safely (a wrong value silently biases the
    # launch angle), so require it whenever the K-LD7 radars are enabled.
    if args.kld7 and args.kld7_mount_tilt is None:
        parser.error("--kld7-mount-tilt is required when --kld7 is passed")
    if args.mock_swing_speed:
        args.mock = True
        args.swing_speed = True
    elif args.swing_speed and args.mock:
        parser.error(
            "--swing-speed requires real OPS243 radar hardware; use --mock-swing-speed for UI testing"
        )

    if args.iwr6843 and args.kld7:
        parser.error("--iwr6843 and vertical --kld7 cannot both own launch angle")
    if args.iwr6843 and args.kld7_horizontal:
        parser.error("--iwr6843 and horizontal --kld7 cannot both own club path")
    if args.inclinometer and not args.iwr6843:
        parser.error("--inclinometer requires --iwr6843")
    if args.iwr6843 and args.mock:
        parser.error("--iwr6843 cannot be used with --mock")
    if args.camera_shot_analysis and args.mock:
        parser.error("--camera-shot-analysis cannot be used with --mock")
    if args.camera_capture and args.mock:
        parser.error("--camera-capture cannot be used with --mock")
    if args.iwr6843 and (args.iwr6843_tee_m <= 0 or args.iwr6843_net_m <= 0):
        parser.error("--iwr6843-tee-m and --iwr6843-net-m must be positive")
    if args.camera_capture and (
        args.camera_capture_width <= 0
        or args.camera_capture_height <= 0
        or args.camera_capture_fps <= 0
        or args.camera_capture_pre_ms <= 0
        or args.camera_capture_post_ms <= 0
        or args.camera_capture_exposure_us <= 0
        or args.camera_capture_gain <= 0
        or args.camera_capture_mount_height_m <= 0
    ):
        parser.error("--camera-capture dimensions, timing, exposure, and gain must be positive")
    camera_capture_scaler_crop = None
    if args.camera_capture_scaler_crop:
        try:
            from .camera.capture_runtime import parse_scaler_crop

            camera_capture_scaler_crop = parse_scaler_crop(args.camera_capture_scaler_crop)
        except ValueError as exc:
            parser.error(f"--camera-capture-scaler-crop: {exc}")
    # The radar can only be moved to a rate it has an API command for, so an
    # unsupported value is refused by the hardware and leaves the link at
    # whatever answered -- a silent slow link, which presents as an
    # unresponsive app rather than a bad flag. Fail at the CLI instead.
    if args.ops_baud is not None and args.ops_baud not in UART_BAUD_COMMANDS:
        supported = ", ".join(str(b) for b in sorted(UART_BAUD_COMMANDS))
        parser.error(f"--ops-baud must be one of {supported} (got {args.ops_baud})")
    global ballistics_enabled
    global battery_provider
    global profile_store
    global ball_speed_correction_enabled
    global ball_speed_correction_distance_ft
    global ball_speed_correction_ball_above_radar_ft
    # Cosine correction rides on whichever vertical radar supplies launch.
    # LCMF itself always receives the original OPS radial speed first.
    ball_speed_correction_enabled = args.kld7 or args.iwr6843
    ball_speed_correction_distance_ft = args.kld7_ball_distance
    ball_speed_correction_ball_above_radar_ft = -args.kld7_radar_height_inches / 12.0
    global _VERTICAL_RADAR_GATE_BYPASS
    _VERTICAL_RADAR_GATE_BYPASS = args.kld7_vertical_raw
    global calculated_spin_enabled
    calculated_spin_enabled = args.calculated_spin
    ballistics_enabled = args.ballistics
    battery_provider = args.battery
    profile_store = ProfileStore(args.profiles_path)
    startup_status = StartupStatusReporter(
        args.startup_status_file,
        configured_startup_components(
            mock=args.mock,
            camera=args.camera_capture,
            iwr6843=args.iwr6843,
            inclinometer=args.inclinometer,
            kld7=args.kld7,
            kld7_horizontal=args.kld7_horizontal,
            battery=bool(args.battery),
            simulators=args.sim,
        ),
    )
    startup_status.start("server", "Preparing OpenFlight server")

    # Configure logging - always show INFO and above for openflight modules
    # This ensures trigger events and important messages are visible
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    # Set rolling buffer logger to INFO so trigger events are visible
    logging.getLogger("openflight.rolling_buffer").setLevel(logging.INFO)
    logging.getLogger("openflight.rolling_buffer.trigger").setLevel(logging.INFO)
    logging.getLogger("openflight.rolling_buffer.monitor").setLevel(logging.INFO)

    print("=" * 50)
    print("  OpenFlight UI Server")
    print("=" * 50)
    print()

    # Initialize session logger (enabled for both real and mock modes)
    if not args.no_logging:
        log_dir = Path(args.log_dir) if args.log_dir else None
        init_session_logger(log_dir=log_dir, location=args.session_location, enabled=True)
        print(f"Session logging enabled (location: {args.session_location})")
    else:
        init_session_logger(enabled=False)
        print("Session logging DISABLED")

    if ballistics_enabled:
        print("Ballistic carry model: ENABLED (simulator + drag/Magnus)")
    else:
        print("Ballistic carry model: DISABLED (table fallback for all shots)")

    # Configure radar logging if requested
    if args.radar_log:
        logging.basicConfig(
            level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        radar_logger = logging.getLogger("ops243")
        radar_raw_logger = logging.getLogger("ops243.raw")
        radar_logger.setLevel(logging.DEBUG)
        radar_raw_logger.setLevel(logging.DEBUG)
        print("Radar raw logging ENABLED - all readings will be logged")

    # Enable raw reading console output if requested
    if args.show_raw:
        set_show_raw_readings(True)
        print("Raw radar readings display ENABLED - signed speed values will be shown")

    # Start the monitor
    # Build trigger-specific kwargs (pre_trigger_segments always passed)
    trigger_kwargs = {"pre_trigger_segments": args.sound_pre_trigger}
    swing_speed_kwargs = {
        "trigger_threshold_mph": args.swing_speed_threshold,
        "max_speed_mph": None if args.swing_speed_max <= 0 else args.swing_speed_max,
        "min_readings": args.swing_speed_min_readings,
        "single_reading_peak_mph": args.swing_speed_single_peak,
        "num_reports": args.swing_speed_num_reports,
        "end_quiet_ms": args.swing_speed_end_ms,
        "cooldown_ms": args.swing_speed_cooldown_ms,
        "rejected_cooldown_ms": args.swing_speed_rejected_cooldown_ms,
    }

    if args.camera_capture:
        startup_status.start("camera", "Connecting high-speed camera")
        camera_capture_base = (
            Path(args.log_dir).expanduser() if args.log_dir else Path.home() / "openflight_sessions"
        )
        camera_capture_output_dir = camera_capture_base / args.session_location / "camera"
        if not init_camera_capture(
            output_dir=camera_capture_output_dir,
            gpio_pin=args.iwr6843_trigger_pin,
            width=args.camera_capture_width,
            height=args.camera_capture_height,
            fps=args.camera_capture_fps,
            pre_ms=args.camera_capture_pre_ms,
            post_ms=args.camera_capture_post_ms,
            exposure_us=args.camera_capture_exposure_us,
            gain=args.camera_capture_gain,
            mount_height_m=args.camera_capture_mount_height_m,
            lateral_offset_m=args.camera_capture_lateral_offset_m,
            horizontal_offset_deg=args.camera_capture_horizontal_offset_deg,
            roll_correction_deg=args.camera_capture_roll_deg,
            stream=args.camera_capture_stream,
            rotate_180=args.camera_capture_rotate_180,
            mirror_horizontal=args.camera_capture_mirror_horizontal,
            scaler_crop=camera_capture_scaler_crop,
            use_gpio_trigger=not args.iwr6843,
            shot_analysis_enabled=args.camera_shot_analysis,
        ):
            print("Camera capture unavailable - running without high-speed camera capture")
            startup_status.skip("camera", "High-speed camera unavailable; continuing")
        else:
            print(f"Camera capture enabled: {camera_capture_output_dir}")
            if args.camera_shot_analysis:
                print("Impact-aware camera shot analysis: ENABLED")
            startup_status.ready("camera", "High-speed camera connected")

    if args.iwr6843:
        startup_status.start("ti", "Connecting TI radar")
        iwr_output_dir = (
            Path(args.iwr6843_output_dir).expanduser()
            if args.iwr6843_output_dir
            else (
                Path(args.log_dir).expanduser()
                if args.log_dir
                else Path.home() / "openflight_sessions"
            )
            / "iwr6843"
        )
        if init_iwr6843(
            port=args.iwr6843_port,
            config_path=args.iwr6843_config,
            calibration_path=args.iwr6843_cal,
            output_dir=iwr_output_dir,
            trigger_pin=args.iwr6843_trigger_pin,
            tee_range_m=args.iwr6843_tee_m,
            net_range_m=args.iwr6843_net_m,
            tx_order=args.iwr6843_tx_order,
            capture_timeout_s=args.iwr6843_capture_timeout,
            tilt_deg=args.iwr6843_tilt_deg,
            radar_height_m=args.iwr6843_radar_height_m,
            ball_height_m=args.iwr6843_ball_height_m,
            azimuth_offset_deg=args.iwr6843_azimuth_offset_deg,
            horizontal_phase_reference_rad=args.iwr6843_horizontal_phase_reference_rad,
            save_dumps=args.debug,
        ):
            calibration = iwr6843_runtime.calibration
            ball_speed_correction_distance_ft = args.iwr6843_tee_m * 3.28084
            ball_speed_correction_ball_above_radar_ft = (
                calibration.tee_ball_height_m - calibration.radar_height_m
            ) * 3.28084
            print(
                "IWR6843 enabled (LCMF-v1 launch angle, "
                f"BCM{args.iwr6843_trigger_pin}, {iwr6843_runtime.tx_order} TX order)"
            )
            if args.debug:
                print(f"IWR6843 raw dumps enabled: {iwr_output_dir}")
            startup_status.ready("ti", "TI radar connected")
        else:
            startup_status.error(
                "ti",
                "TI radar failed to initialize",
                _iwr6843_startup_recovery(iwr6843_runtime_config.get("error")),
            )
            print("ERROR: IWR6843 requested but failed to initialize. Exiting.")
            _cleanup_hardware_for_shutdown()
            sys.exit(1)

    if args.inclinometer:
        startup_status.start("inclinometer", "Connecting inclinometer")
        if not init_inclinometer(zero_offset_deg=args.inclinometer_zero_offset):
            print("WARNING: Inclinometer unavailable; continuing with configured IWR6843 tilt")
            startup_status.skip("inclinometer", "Inclinometer unavailable; continuing")
        else:
            startup_status.ready("inclinometer", "Inclinometer connected")

    # Initialize K-LD7 angle radars (if enabled)
    if args.kld7:
        startup_status.start("kld7_vertical", "Connecting K-LD7 launch radar")
        if init_kld7(
            port=args.kld7_port,
            orientation="vertical",
            angle_offset_deg=args.kld7_angle_offset,
            base_freq=0,
            mount_tilt_deg=args.kld7_mount_tilt,
            ball_distance_ft=args.kld7_ball_distance,
            vertical_flight_window_net_distance_ft=args.net_distance,
        ):
            offset_str = (
                f", offset: {args.kld7_angle_offset:+.1f}°" if args.kld7_angle_offset else ""
            )
            print(f"K-LD7 vertical radar enabled (launch angle{offset_str})")
            startup_status.ready("kld7_vertical", "K-LD7 launch radar connected")
        else:
            startup_status.error(
                "kld7_vertical",
                "K-LD7 launch radar failed to connect",
                "Check the K-LD7 USB connection and power, then relaunch OpenFlight.",
            )
            print("ERROR: K-LD7 vertical requested but failed to connect. Exiting.")
            _cleanup_hardware_for_shutdown()
            sys.exit(1)

    if args.kld7_horizontal:
        startup_status.start("kld7_horizontal", "Connecting K-LD7 path radar")
        if init_kld7(
            port=args.kld7_horizontal_port,
            orientation="horizontal",
            angle_offset_deg=args.kld7_horizontal_offset,
            base_freq=2,
        ):
            offset_str = (
                f", offset: {args.kld7_horizontal_offset:+.1f}°"
                if args.kld7_horizontal_offset
                else ""
            )
            print(f"K-LD7 horizontal radar enabled (club path{offset_str})")
            startup_status.ready("kld7_horizontal", "K-LD7 path radar connected")
        else:
            startup_status.error(
                "kld7_horizontal",
                "K-LD7 path radar failed to connect",
                "Check the K-LD7 USB connection and power, then relaunch OpenFlight.",
            )
            print("ERROR: K-LD7 horizontal requested but failed to connect. Exiting.")
            _cleanup_hardware_for_shutdown()
            sys.exit(1)

    monitor_component = "monitor" if args.mock else "ops"
    monitor_label = "shot simulator" if args.mock else "OPS radar"
    startup_status.start(monitor_component, f"Starting {monitor_label}")
    try:
        start_monitor(
            port=args.port,
            mock=args.mock,
            trigger_type=args.trigger,
            debug=args.debug,
            trigger_kwargs=trigger_kwargs,
            sample_rate_ksps=args.sample_rate,
            swing_speed_mode=args.swing_speed,
            swing_speed_kwargs=swing_speed_kwargs,
            ops_baud=args.ops_baud,
        )
    except Exception:
        monitor_recovery = (
            "Relaunch OpenFlight and check the terminal log."
            if args.mock
            else "Check the OPS radar USB and power connections, then relaunch OpenFlight."
        )
        startup_status.error(
            monitor_component,
            f"{'Shot simulator' if args.mock else 'OPS radar'} failed to initialize",
            monitor_recovery,
        )
        _cleanup_hardware_for_shutdown()
        raise
    startup_status.ready(monitor_component, f"{monitor_label.capitalize()} ready")

    if battery_provider:
        startup_status.start("battery", "Starting power monitor")
        start_power_monitor(battery_provider)
        print(f"Battery monitoring: ENABLED ({battery_provider})")
        startup_status.ready("battery", "Power monitor ready")

    # Simulator connectors (off unless --sim). Started after the monitor exists
    # so inbound club updates can call monitor.set_club().
    global sim_connectors  # pylint: disable=global-statement
    if args.sim:
        startup_status.start("simulators", "Connecting golf simulators")
    sim_cfgs = load_sim_config() if args.sim else []
    sim_connectors = build_connectors(
        sim_cfgs, on_status=_sim_on_status, on_inbound=_sim_on_inbound
    )
    for connector in sim_connectors:
        connector.start()
        print(f"Simulator connector enabled: {connector.name} -> {connector.host}:{connector.port}")
    if args.sim and not sim_connectors:
        print("Simulator connectors enabled (--sim) but none are enabled in config/sim.json")
        startup_status.skip("simulators", "No simulator connections are configured")
    elif args.sim:
        startup_status.ready("simulators", "Simulator connections started")

    if args.mock:
        print("Running in MOCK mode - no radar required")
        print("Simulate shots via WebSocket or API")
    if args.swing_speed:
        print("Running in SWING SPEED mode - no ball impact trigger required")

    print(f"Server starting at http://{args.host}:{args.web_port}")
    print()
    startup_status.start("server", "Starting OpenFlight server")

    try:
        # Note: Flask debug mode (reloader) is disabled to prevent duplicate processes
        # fighting over the serial port. OpenFlight --debug enables verbose logging only.
        socketio.run(
            app, host=args.host, port=args.web_port, debug=False, allow_unsafe_werkzeug=True
        )
    finally:
        _cleanup_hardware_for_shutdown()


if __name__ == "__main__":
    main()
