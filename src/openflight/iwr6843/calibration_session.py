"""OPS+IWR6843 calibration-session helpers.

This module is intentionally production-shaped: OPS detects the shot and
provides radial speed, then the IWR6843 runtime matches the same sound-trigger
edge and runs LCMF-v1. The terminal script layers operator summaries on top.
"""

from __future__ import annotations

import copy
import json
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np

from openflight.clubs import ClubType
from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.lcmf import LCMFResult, estimate_lcmf_v1
from openflight.iwr6843.shot import ShotMeasurement
from openflight.launch_monitor import Shot


def parse_club(value: str | None) -> ClubType:
    """Parse UI/CLI club names into the production ClubType enum."""
    if value is None:
        return ClubType.UNKNOWN
    text = value.strip().lower().replace("_", "-")
    aliases = {
        "d": ClubType.DRIVER,
        "driver": ClubType.DRIVER,
        "3w": ClubType.WOOD_3,
        "3-wood": ClubType.WOOD_3,
        "5w": ClubType.WOOD_5,
        "5-wood": ClubType.WOOD_5,
        "7w": ClubType.WOOD_7,
        "7-wood": ClubType.WOOD_7,
        "3h": ClubType.HYBRID_3,
        "3-hybrid": ClubType.HYBRID_3,
        "5h": ClubType.HYBRID_5,
        "5-hybrid": ClubType.HYBRID_5,
        "7h": ClubType.HYBRID_7,
        "7-hybrid": ClubType.HYBRID_7,
        "9h": ClubType.HYBRID_9,
        "9-hybrid": ClubType.HYBRID_9,
        "2i": ClubType.IRON_2,
        "2-iron": ClubType.IRON_2,
        "3i": ClubType.IRON_3,
        "3-iron": ClubType.IRON_3,
        "4i": ClubType.IRON_4,
        "4-iron": ClubType.IRON_4,
        "5i": ClubType.IRON_5,
        "5-iron": ClubType.IRON_5,
        "6i": ClubType.IRON_6,
        "6-iron": ClubType.IRON_6,
        "7i": ClubType.IRON_7,
        "7-iron": ClubType.IRON_7,
        "8i": ClubType.IRON_8,
        "8-iron": ClubType.IRON_8,
        "9i": ClubType.IRON_9,
        "9-iron": ClubType.IRON_9,
        "pw": ClubType.PW,
        "gw": ClubType.GW,
        "sw": ClubType.SW,
        "sand-wedge": ClubType.SW,
        "lw": ClubType.LW,
        "unknown": ClubType.UNKNOWN,
    }
    try:
        return ClubType(text)
    except ValueError:
        return aliases.get(text, ClubType.UNKNOWN)


def clone_calibration(
    calibration: Calibration,
    *,
    tee_range_m: float | None = None,
    net_range_m: float | None = None,  # reserved for symmetric call sites
    tilt_deg: float | None = None,
    radar_height_m: float | None = None,
    ball_height_m: float | None = None,
) -> Calibration:
    """Copy calibration constants while replacing session geometry."""
    del net_range_m
    cloned = Calibration(
        elem_correction=np.array(calibration.elem_correction, dtype=complex, copy=True),
        tilt_rad=calibration.tilt_rad,
        range_bias_m=calibration.range_bias_m,
        source=calibration.source,
        tee_range_m=calibration.tee_range_m,
        tee_ball_height_m=calibration.tee_ball_height_m,
        meta=copy.deepcopy(calibration.meta),
    )
    if tee_range_m is not None:
        cloned.tee_range_m = tee_range_m
    if tilt_deg is not None:
        cloned.tilt_rad = math.radians(tilt_deg)
    if radar_height_m is not None:
        cloned.meta["radar_height_m"] = radar_height_m
    if ball_height_m is not None:
        cloned.tee_ball_height_m = ball_height_m
    return cloned


def estimated_track_start_range_m(
    shot_measurement: ShotMeasurement,
    calibration: Calibration,
) -> float | None:
    """Estimate ball-start slant range from the TI range walk at trigger time."""
    track = shot_measurement.track
    if track is None:
        return None
    measured_m = shot_measurement.geometry.range_res_m * track.bin_at(0.0)
    return calibration.true_range(measured_m)


def _median(values: Iterable[float]) -> float | None:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return statistics.median(clean) if clean else None


def _stdev(values: Iterable[float]) -> float | None:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return statistics.stdev(clean) if len(clean) > 1 else None


@dataclass
class CalibrationShotRecord:
    """One OPS shot plus its matched TI/LCMF result."""

    shot_number: int
    timestamp: str
    club: str
    ball_speed_mph: float
    club_speed_mph: float | None
    iwr_capture_path: str | None
    iwr_capture_error: str | None
    lcmf_status: str | None
    launch_angle_deg: float | None
    component_std_deg: float | None
    track_speed_mph: float | None
    track_rms_bins: float | None
    track_inliers: int | None
    track_start_range_m: float | None
    tilt_candidate_deg: float | None = None
    tilt_candidate_score: float | None = None

    @property
    def accepted(self) -> bool:
        """Whether LCMF produced a reportable launch angle."""
        return self.launch_angle_deg is not None

    def to_dict(self) -> dict:
        """Return JSON-safe calibration output."""
        return {
            "type": "iwr6843_calibration_shot",
            "shot_number": self.shot_number,
            "timestamp": self.timestamp,
            "club": self.club,
            "ball_speed_mph": self.ball_speed_mph,
            "club_speed_mph": self.club_speed_mph,
            "iwr_capture_path": self.iwr_capture_path,
            "iwr_capture_error": self.iwr_capture_error,
            "lcmf_status": self.lcmf_status,
            "launch_angle_deg": self.launch_angle_deg,
            "component_std_deg": self.component_std_deg,
            "track_speed_mph": self.track_speed_mph,
            "track_rms_bins": self.track_rms_bins,
            "track_inliers": self.track_inliers,
            "track_start_range_m": self.track_start_range_m,
            "tilt_candidate_deg": self.tilt_candidate_deg,
            "tilt_candidate_score": self.tilt_candidate_score,
        }


@dataclass
class CalibrationSummary:
    """Running session summary for operator feedback."""

    shots: list[CalibrationShotRecord] = field(default_factory=list)

    @property
    def accepted(self) -> list[CalibrationShotRecord]:
        """LCMF-accepted shots."""
        return [shot for shot in self.shots if shot.accepted]

    def to_dict(self) -> dict:
        """Return aggregate calibration statistics."""
        accepted = self.accepted
        return {
            "type": "iwr6843_calibration_summary",
            "shots": len(self.shots),
            "accepted_shots": len(accepted),
            "coverage": len(accepted) / len(self.shots) if self.shots else 0.0,
            "median_launch_angle_deg": _median(s.launch_angle_deg for s in accepted),
            "median_component_std_deg": _median(s.component_std_deg for s in accepted),
            "median_track_rms_bins": _median(s.track_rms_bins for s in accepted),
            "median_track_start_range_m": _median(s.track_start_range_m for s in accepted),
            "stdev_track_start_range_m": _stdev(s.track_start_range_m for s in accepted),
            "median_tilt_candidate_deg": _median(s.tilt_candidate_deg for s in accepted),
            "stdev_tilt_candidate_deg": _stdev(s.tilt_candidate_deg for s in accepted),
        }


class JsonlWriter:
    """Append calibration records to a JSONL file."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, payload: dict) -> None:
        """Append one JSON object."""
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")


def utc_now_iso() -> str:
    """Current UTC timestamp for JSONL records."""
    return datetime.now(timezone.utc).isoformat()


def tilt_consistency_sweep(
    *,
    raw: bytes,
    base_calibration: Calibration,
    ball_speed_mph: float,
    club: str | None,
    net_range_m: float | None,
    tx_order: str,
    center_tilt_deg: float,
    half_width_deg: float = 3.0,
    step_deg: float = 1.0,
) -> tuple[float | None, float | None]:
    """Pick the tilt whose LCMF model components agree best.

    This is a radar-consistency diagnostic, not a TrackMan truth solve. It is
    useful for spotting a mount that is several degrees away from the setting.
    """
    if step_deg <= 0:
        raise ValueError("step_deg must be positive")
    candidates = np.arange(
        center_tilt_deg - half_width_deg,
        center_tilt_deg + half_width_deg + step_deg / 2.0,
        step_deg,
    )
    scored: list[tuple[float, float]] = []
    for tilt_deg in candidates:
        calibration = clone_calibration(base_calibration, tilt_deg=float(tilt_deg))
        result = estimate_lcmf_v1(
            raw,
            calibration,
            ball_speed_mph=ball_speed_mph,
            club=club,
            net_range_m=net_range_m,
            tx_order=tx_order,
            tdm_sign_policy="positive",
            grid_step_deg=1.0,
        )
        if result.accepted and result.component_std_deg is not None:
            scored.append((float(tilt_deg), float(result.component_std_deg)))
    if not scored:
        return None, None
    return min(scored, key=lambda item: item[1])


def build_record(
    *,
    shot_number: int,
    shot: Shot,
    capture_path: str | None,
    capture_error: str | None,
    measurement: LCMFResult | None,
    track_measurement: ShotMeasurement | None,
    calibration: Calibration,
    tilt_candidate: tuple[float | None, float | None] = (None, None),
) -> CalibrationShotRecord:
    """Create a calibration record from production shot/runtime objects."""
    launch_angle = measurement.angle_deg if measurement and measurement.accepted else None
    return CalibrationShotRecord(
        shot_number=shot_number,
        timestamp=utc_now_iso(),
        club=shot.club.value,
        ball_speed_mph=shot.ball_speed_mph,
        club_speed_mph=shot.club_speed_mph,
        iwr_capture_path=capture_path,
        iwr_capture_error=capture_error,
        lcmf_status=measurement.status if measurement is not None else None,
        launch_angle_deg=launch_angle,
        component_std_deg=measurement.component_std_deg if measurement else None,
        track_speed_mph=measurement.track_speed_mph if measurement else None,
        track_rms_bins=measurement.track_rms_bins if measurement else None,
        track_inliers=measurement.track_inliers if measurement else None,
        track_start_range_m=(
            estimated_track_start_range_m(track_measurement, calibration)
            if track_measurement is not None
            else None
        ),
        tilt_candidate_deg=tilt_candidate[0],
        tilt_candidate_score=tilt_candidate[1],
    )
