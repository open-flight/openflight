"""Camera-assisted experimental club delivery (attack angle + club path).

The live estimator tracks clubhead image features across short intervals around
impact. Its preferred path spans the final approach through the first
post-impact frame, combining camera transverse motion, IWR6843 depth, and OPS
club speed. A fully pre-impact interval is the next choice so launched-ball or
deflected-head pixels cannot silently replace a missing impact track. When IWR
club range is unavailable, camera perspective flow plus OPS speed closes a
lower-confidence 3D fallback. Both paths remain experimental pending broader
source-of-truth validation.

The older radar-AoA/camera-trace functions remain below for replay comparisons,
but the OpenFlight server no longer uses their per-club correction offsets.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from openflight.camera.club_motion import (
    ReferenceBall,
    detect_impact_frame,
    detect_reference_ball,
)
from openflight.camera.geometry import deroll_normalized_offsets
from openflight.clubs import ClubType
from openflight.clubs.physics import get_club_physics

# --- scene / mask constants -------------------------------------------------
# Scene brightness gate: background 99.5th percentile. The 2026-08-07 session
# measured p99 ~147 (9-iron block, clean traces), ~115 (5-iron, mask started
# locking onto the ball) and ~66 (driver block, unusable).
SCENE_P995_MIN = 105.0
# Over-exposure gates. Midday sun (2026-08-08 session) saturated large
# scene regions; the reference-ball detector then locks onto arbitrary
# bright blobs and impact detection never sees a departure. Dappled
# background saturation is harmless (same day, 250 us: far-field 5%%
# saturated, trace clean), so the hard gate is LOCAL to the hitting zone;
# the global fraction only attributes cause when ball detection fails.
BALL_ZONE_RADIUS_PX = 150
BALL_ZONE_SATURATION_MAX = 0.02
GLOBAL_SATURATION_HINT = 0.05
# A real impact must sit near the sound-trigger frame; a far-off "impact"
# means the ball patch never departed (wrong blob detected).
IMPACT_PRE_TRIGGER_MAX = 8
IMPACT_POST_TRIGGER_MAX = 10
# Moving-bright mask (adaptive): pixels saturated NOW but dark in the
# pre-swing background isolate the chrome shaft from static bright clutter.
BRIGHT_NOW_FLOOR = 110.0
BRIGHT_NOW_SCALE = 0.85  # fraction of the background's brightest content
DARK_BG_MARGIN = 25.0
BALL_THRESHOLD_FLOOR = 120.0
BALL_THRESHOLD_SCALE = 0.9

BALL_PATCH_RADIUS_FRAC = 0.4
BALL_PRESENT_DELTA = 30.0
MIN_CLUB_COMPONENT_PX = 40
MAX_COMPONENT_BALL_DIST_PX = 320.0
TIP_BAND_PX = 12.0
PATCH_HALF_PX = 45
SEARCH_HALF_PX = 110
MIN_MATCH_SCORE = 0.30
MIN_PAIR_STEP_PX = 2.0
MIN_PAIRS = 2
PAIR_OFFSETS = (-2, -1, 0, 1)

# --- fusion constants ---------------------------------------------------------
# A trace near 0° or ±90° makes tan(path) = tan(AoA)/tan(trace) explode or
# collapse; require it in a physically plausible band.
TRACE_ABS_RANGE_DEG = (15.0, 85.0)

# Impact-centered 3D fusion gates. These were frozen without TrackMan truth
# after the 2026-08-08 17-shot replay. They decide whether an experimental
# number is displayed; they do not calibrate or shift either angle.
CHAINED_SPEED_RATIO_RANGE = (0.7, 1.3)
CHAINED_VELOCITY_MAD_MAX_MPH = 10.0
CHAINED_PATH_RANGE_DEG = (-25.0, 25.0)
CHAINED_AOA_RANGE_DEG = (-20.0, 10.0)
CHAINED_INTERVAL_AGREEMENT_DEG = 5.0
CHAINED_MIN_IMAGE_MOTION_PX = 1e-6
CHAINED_FEATURE_SPEED_RANGE_MPH = (25.0, 140.0)
CHAINED_FEATURE_PATH_RANGE_DEG = (-60.0, 60.0)
CHAINED_FEATURE_AOA_RANGE_DEG = (-45.0, 30.0)
APPROACH_AOA_RANGE_DEG = (-12.0, 8.0)
APPROACH_PATH_WINDOW_MAD_MAX_DEG = 5.0
APPROACH_MIN_PATH_WINDOWS = 3
APPROACH_ATTACK_VELOCITY_MAD_MAX_MPH = 12.0
APPROACH_MEDIUM_AOA_RANGE_DEG = (-18.0, 12.0)
APPROACH_MEDIUM_SPEED_RATIO_RANGE = (0.6, 1.4)
APPROACH_MEDIUM_VELOCITY_MAD_MAX_MPH = 18.0
PREFERRED_PATH_OFFSETS = ((-2, 1), (-4, -1))
APPROACH_PATH_OFFSETS = (*PREFERRED_PATH_OFFSETS, (-1, 0), (-2, 0), (-3, -1), (-3, 0))
GOLF_BALL_DIAMETER_M = 0.04267
REFERENCE_IMAGE_SIZE = (640, 400)


def _image_scale(shape: tuple[int, ...]) -> float:
    """Scale legacy 640x400 pixel windows to the active camera crop."""
    height, width = shape[-2:]
    return min(width / REFERENCE_IMAGE_SIZE[0], height / REFERENCE_IMAGE_SIZE[1])


@dataclass(frozen=True)
class CameraDeliveryGeometry:
    """Measured camera/radar geometry for rear-view club reconstruction."""

    camera_height_m: float
    radar_height_m: float
    tee_range_m: float
    ball_height_m: float
    # Camera optical-center position relative to radar center. Positive is
    # target-right when viewed from behind the sensors looking downrange.
    camera_lateral_offset_m: float = 0.0
    ball_diameter_m: float = GOLF_BALL_DIAMETER_M
    image_width_px: int = 640
    image_height_px: int = 400
    # Saved-frame mirroring changes image handedness. Keep public club path
    # positive in-to-out by restoring physical lateral orientation here.
    horizontal_pixel_sign: float = 1.0
    roll_correction_deg: float = 0.0

    @property
    def ball_forward_m(self) -> float:
        """Horizontal camera-to-ball distance from radar slant geometry."""
        vertical = self.ball_height_m - self.radar_height_m
        return math.sqrt(max(self.tee_range_m**2 - vertical**2, 1e-9))

    @property
    def camera_origin(self) -> np.ndarray:
        """Camera origin in the radar-centered world coordinate system."""
        return np.array([self.camera_lateral_offset_m, 0.0, self.camera_height_m])


@dataclass(frozen=True)
class ChainedDelivery:
    """Camera clubhead motion plus IWR depth over the impact interval."""

    status: str
    attack_angle_deg: float | None = None
    club_path_deg: float | None = None
    confidence_tier: str = "withheld"
    speed_mph: float | None = None
    speed_ratio_ops: float | None = None
    velocity_mad_mph: float | None = None
    n_features: int = 0
    pre_path_deg: float | None = None
    pre_attack_angle_deg: float | None = None
    cross_path_deg: float | None = None
    cross_attack_angle_deg: float | None = None
    impact_frame: int | None = None
    impact_vs_trigger_ms: float | None = None
    scene_p995: float | None = None
    head_thickness_px: float | None = None
    path_window_count: int = 0
    path_window_mad_deg: float | None = None
    path_confidence_tier: str = "withheld"
    attack_confidence_tier: str = "withheld"


@dataclass(frozen=True)
class ApproachPairEstimate:
    """One short camera/IWR velocity interval around impact."""

    path_deg: float
    attack_angle_deg: float
    speed_ratio_ops: float
    velocity_mad_mph: float
    n_features: int


class ReferenceBallTracker:
    """Retain a robust session tee anchor when one frame finds a false blob."""

    def __init__(self, max_samples: int = 15, min_fallback_samples: int = 3):
        self.max_samples = max_samples
        self.min_fallback_samples = min_fallback_samples
        self._samples: list[ReferenceBall] = []

    def _anchor(self) -> ReferenceBall | None:
        if not self._samples:
            return None
        return ReferenceBall(
            x=float(np.median([ball.x for ball in self._samples])),
            y=float(np.median([ball.y for ball in self._samples])),
            diameter_px=float(np.median([ball.diameter_px for ball in self._samples])),
            area_px=int(round(np.median([ball.area_px for ball in self._samples]))),
        )

    def resolve(self, candidate: ReferenceBall) -> tuple[ReferenceBall, str]:
        """Accept a consistent observation or return the established anchor."""
        anchor = self._anchor()
        plausible_size = 9.0 <= candidate.diameter_px <= 30.0
        consistent = plausible_size
        if anchor is not None:
            distance_px = math.hypot(candidate.x - anchor.x, candidate.y - anchor.y)
            size_ratio = candidate.diameter_px / anchor.diameter_px
            consistent = (
                plausible_size
                and distance_px <= max(40.0, 4.0 * anchor.diameter_px)
                and 0.65 <= size_ratio <= 1.55
            )
        if consistent:
            self._samples.append(candidate)
            self._samples = self._samples[-self.max_samples :]
            return candidate, "detected"
        if anchor is not None and len(self._samples) >= self.min_fallback_samples:
            return anchor, "session_anchor"
        return candidate, "unverified"

    def resolve_stable(self, candidate: ReferenceBall) -> tuple[ReferenceBall, str]:
        """Return a rolling session anchor once enough valid observations exist."""
        resolved, source = self.resolve(candidate)
        anchor = self._anchor()
        if anchor is not None and len(self._samples) >= self.min_fallback_samples:
            return anchor, "session_anchor"
        return resolved, "warming" if source == "detected" else source

    def fallback(self) -> ReferenceBall | None:
        """Return an established session anchor without adding an observation."""
        if len(self._samples) < self.min_fallback_samples:
            return None
        return self._anchor()


def _pixels_to_world(
    points_px: np.ndarray,
    radar_range_m: float,
    *,
    ball,
    geometry: CameraDeliveryGeometry,
) -> np.ndarray:
    """Rear-camera pixels + IWR slant range -> lateral/height/forward meters.

    The reference ball supplies focal scale and camera pitch. Each pixel ray is
    intersected with the IWR slant-range sphere, accounting for the camera and
    radar sitting at different heights.
    """
    camera_ball_range_m = math.sqrt(
        geometry.camera_lateral_offset_m**2
        + geometry.ball_forward_m**2
        + (geometry.ball_height_m - geometry.camera_height_m) ** 2
    )
    focal_px = ball.diameter_px * camera_ball_range_m / geometry.ball_diameter_m
    if not math.isfinite(focal_px) or focal_px <= 0.0:
        raise ValueError("invalid camera focal scale from reference ball")
    center_x = geometry.image_width_px / 2.0
    center_y = geometry.image_height_px / 2.0
    ball_x = geometry.horizontal_pixel_sign * (ball.x - center_x) / focal_px
    ball_z = -(ball.y - center_y) / focal_px
    _ball_x, ball_z = deroll_normalized_offsets(
        ball_x,
        ball_z,
        geometry.roll_correction_deg,
    )
    pitch_rad = math.atan2(
        geometry.ball_height_m - geometry.camera_height_m,
        geometry.ball_forward_m,
    ) - math.atan2(ball_z, 1.0)
    image_x = geometry.horizontal_pixel_sign * (points_px[:, 0] - center_x) / focal_px
    image_z = -(points_px[:, 1] - center_y) / focal_px
    image_x, image_z = deroll_normalized_offsets(
        image_x,
        image_z,
        geometry.roll_correction_deg,
    )
    rays = np.column_stack(
        (
            image_x,
            math.cos(pitch_rad) - image_z * math.sin(pitch_rad),
            math.sin(pitch_rad) + image_z * math.cos(pitch_rad),
        )
    )
    rays /= np.linalg.norm(rays, axis=1, keepdims=True)
    radar_from_camera = geometry.camera_origin - np.array([0.0, 0.0, geometry.radar_height_m])
    ray_offset = rays @ radar_from_camera
    discriminant = ray_offset**2 - (np.dot(radar_from_camera, radar_from_camera) - radar_range_m**2)
    if np.any(discriminant < 0.0):
        raise ValueError("camera ray does not intersect IWR range sphere")
    distance = -ray_offset + np.sqrt(discriminant)
    xyz = geometry.camera_origin + distance[:, None] * rays
    # Public ordering remains lateral, vertical, forward.
    return xyz[:, (0, 2, 1)]


def _velocity_angles(velocity: np.ndarray) -> tuple[float, float]:
    """Club path (azimuth) and attack angle (elevation) of a 3D velocity.

    Attack angle divides by the TOTAL horizontal speed, not by the forward
    component alone. The two agree only at zero club path; otherwise the
    forward-only form inflates the attack angle by a factor of 1/cos(path).
    Measured on session 20260825_181734 that is +0.052 deg mean and 0.347 deg
    max at the observed paths, rising to 0.60 deg at a 30 deg path -- small,
    but systematic and correlated with path.
    """
    lateral, vertical, forward = (float(value) for value in velocity)
    return (
        math.degrees(math.atan2(lateral, forward)),
        math.degrees(math.atan2(vertical, math.hypot(lateral, forward))),
    )


def _bounded_angles(path_deg: float, attack_angle_deg: float) -> bool:
    return (
        CHAINED_PATH_RANGE_DEG[0] <= path_deg <= CHAINED_PATH_RANGE_DEG[1]
        and CHAINED_AOA_RANGE_DEG[0] <= attack_angle_deg <= CHAINED_AOA_RANGE_DEG[1]
    )


def combine_approach_estimates(
    path_estimates: list[ApproachPairEstimate],
    *,
    attack_estimate: ApproachPairEstimate | None,
    preferred_path_estimate: ApproachPairEstimate | None = None,
    timing_plausible: bool,
) -> ChainedDelivery:
    """Combine independent impact windows without coupling path and AoA."""
    path_candidates = [
        estimate
        for estimate in path_estimates
        if CHAINED_SPEED_RATIO_RANGE[0] <= estimate.speed_ratio_ops <= CHAINED_SPEED_RATIO_RANGE[1]
        and estimate.velocity_mad_mph <= CHAINED_VELOCITY_MAD_MAX_MPH
        and CHAINED_PATH_RANGE_DEG[0] <= estimate.path_deg <= CHAINED_PATH_RANGE_DEG[1]
    ]
    path_deg = None
    path_mad = None
    path_confidence = "withheld"
    if preferred_path_estimate is not None:
        path_deg = preferred_path_estimate.path_deg
        values = np.asarray([estimate.path_deg for estimate in path_candidates])
        path_mad = float(np.median(np.abs(values - path_deg))) if len(values) else None
        preferred_quality = (
            CHAINED_SPEED_RATIO_RANGE[0]
            <= preferred_path_estimate.speed_ratio_ops
            <= CHAINED_SPEED_RATIO_RANGE[1]
            and preferred_path_estimate.velocity_mad_mph <= CHAINED_VELOCITY_MAD_MAX_MPH
            and CHAINED_PATH_RANGE_DEG[0]
            <= preferred_path_estimate.path_deg
            <= CHAINED_PATH_RANGE_DEG[1]
        )
        if preferred_quality and timing_plausible:
            path_confidence = "high" if path_mad is not None and path_mad <= 2.0 else "medium"
        else:
            path_confidence = "low"
    elif len(path_candidates) >= APPROACH_MIN_PATH_WINDOWS:
        values = np.asarray([estimate.path_deg for estimate in path_candidates])
        median = float(np.median(values))
        path_mad = float(np.median(np.abs(values - median)))
        if path_mad <= APPROACH_PATH_WINDOW_MAD_MAX_DEG:
            path_deg = median
            path_confidence = "high" if timing_plausible and path_mad <= 2.0 else "medium"
    if path_deg is None and path_estimates:
        values = np.asarray([estimate.path_deg for estimate in path_estimates])
        path_deg = float(np.median(values))
        path_mad = float(np.median(np.abs(values - path_deg)))
        path_confidence = "low"

    attack_angle_deg = attack_estimate.attack_angle_deg if attack_estimate else None
    attack_confidence = "withheld"
    strict_attack = (
        attack_estimate is not None
        and CHAINED_SPEED_RATIO_RANGE[0]
        <= attack_estimate.speed_ratio_ops
        <= CHAINED_SPEED_RATIO_RANGE[1]
        and attack_estimate.velocity_mad_mph <= APPROACH_ATTACK_VELOCITY_MAD_MAX_MPH
        and APPROACH_AOA_RANGE_DEG[0]
        <= attack_estimate.attack_angle_deg
        <= APPROACH_AOA_RANGE_DEG[1]
    )
    medium_attack = (
        attack_estimate is not None
        and APPROACH_MEDIUM_SPEED_RATIO_RANGE[0]
        <= attack_estimate.speed_ratio_ops
        <= APPROACH_MEDIUM_SPEED_RATIO_RANGE[1]
        and attack_estimate.velocity_mad_mph <= APPROACH_MEDIUM_VELOCITY_MAD_MAX_MPH
        and APPROACH_MEDIUM_AOA_RANGE_DEG[0]
        <= attack_estimate.attack_angle_deg
        <= APPROACH_MEDIUM_AOA_RANGE_DEG[1]
    )
    if strict_attack and timing_plausible:
        attack_confidence = "high"
    elif medium_attack and timing_plausible:
        attack_confidence = "medium"
    elif attack_estimate is not None:
        attack_confidence = "low"

    common = {
        "attack_angle_deg": (round(attack_angle_deg, 2) if attack_angle_deg is not None else None),
        "club_path_deg": round(path_deg, 2) if path_deg is not None else None,
        "path_window_count": len(path_candidates),
        "path_window_mad_deg": round(path_mad, 2) if path_mad is not None else None,
        "path_confidence_tier": path_confidence,
        "attack_confidence_tier": attack_confidence,
        "speed_mph": None,
        "speed_ratio_ops": (round(attack_estimate.speed_ratio_ops, 3) if attack_estimate else None),
        "velocity_mad_mph": (
            round(attack_estimate.velocity_mad_mph, 2) if attack_estimate else None
        ),
        "n_features": attack_estimate.n_features if attack_estimate else 0,
    }
    if path_deg is not None and attack_angle_deg is not None:
        high = path_confidence == "high" and attack_confidence == "high"
        return ChainedDelivery(
            status="approach_high" if high else "approach_mixed",
            confidence_tier="high" if high else "experimental",
            **common,
        )
    if path_deg is not None:
        return ChainedDelivery(
            status="approach_path_only",
            confidence_tier="experimental",
            **common,
        )
    if attack_angle_deg is not None:
        return ChainedDelivery(
            status="approach_aoa_only",
            confidence_tier="experimental",
            **common,
        )
    return ChainedDelivery(status="rejected_no_stable_approach", **common)


def _preferred_path_estimate(
    pair_estimates: dict[tuple[int, int], ApproachPairEstimate],
) -> ApproachPairEstimate | None:
    """Select the interval nearest TrackMan's instantaneous-impact definition."""
    for offsets in PREFERRED_PATH_OFFSETS:
        estimate = pair_estimates.get(offsets)
        if estimate is not None:
            return estimate
    return None


def delivery_from_feature_tracks(
    feature_pixels: np.ndarray,
    timestamps_s: np.ndarray,
    radar_ranges_m: np.ndarray,
    *,
    ball,
    geometry: CameraDeliveryGeometry,
    ops_club_speed_mph: float,
    timing_plausible: bool,
) -> ChainedDelivery:
    """Reconstruct impact delivery from identical features in three frames.

    ``feature_pixels`` is ``[feature, pre/impact/post, xy]``. The central
    estimate spans pre-to-post; the adjacent intervals are independent quality
    checks. Tracking identity is established before this geometry function so
    the launched ball cannot silently replace the clubhead after impact.
    """
    pixels = np.asarray(feature_pixels, dtype=float)
    times = np.asarray(timestamps_s, dtype=float)
    ranges = np.asarray(radar_ranges_m, dtype=float)
    if pixels.ndim != 3 or pixels.shape[1:] != (3, 2):
        return ChainedDelivery(status="rejected_invalid_feature_shape")
    if pixels.shape[0] < 3:
        return ChainedDelivery(
            status="rejected_insufficient_features",
            n_features=int(pixels.shape[0]),
        )
    if times.shape != (3,) or ranges.shape != (3,) or np.any(np.diff(times) <= 0.0):
        return ChainedDelivery(status="rejected_invalid_timing", n_features=len(pixels))
    if not ops_club_speed_mph or not math.isfinite(ops_club_speed_mph):
        return ChainedDelivery(status="rejected_no_ops_speed", n_features=len(pixels))

    # IWR supplies one depth history for the club candidate. Never project
    # static camera texture through that changing depth: it would acquire a
    # physically convincing but entirely artificial forward velocity.
    image_motion = np.linalg.norm(pixels[:, 2] - pixels[:, 0], axis=1)
    moving = image_motion >= CHAINED_MIN_IMAGE_MOTION_PX
    pixels = pixels[moving]
    if len(pixels) < 3:
        return ChainedDelivery(
            status="rejected_insufficient_moving_features",
            n_features=int(len(pixels)),
        )

    positions = np.stack(
        [
            _pixels_to_world(pixels[:, frame], ranges[frame], ball=ball, geometry=geometry)
            for frame in range(3)
        ],
        axis=1,
    )
    pre_velocity = (positions[:, 1] - positions[:, 0]) / (times[1] - times[0])
    cross_velocity = (positions[:, 2] - positions[:, 1]) / (times[2] - times[1])
    central_velocity = (positions[:, 2] - positions[:, 0]) / (times[2] - times[0])

    feature_speeds_mph = np.linalg.norm(central_velocity, axis=1) * 2.23694
    feature_path = np.array([_velocity_angles(value)[0] for value in central_velocity])
    feature_aoa = np.array([_velocity_angles(value)[1] for value in central_velocity])
    plausible = (
        (feature_speeds_mph > CHAINED_FEATURE_SPEED_RANGE_MPH[0])
        & (feature_speeds_mph < CHAINED_FEATURE_SPEED_RANGE_MPH[1])
        & (feature_path > CHAINED_FEATURE_PATH_RANGE_DEG[0])
        & (feature_path < CHAINED_FEATURE_PATH_RANGE_DEG[1])
        & (feature_aoa > CHAINED_FEATURE_AOA_RANGE_DEG[0])
        & (feature_aoa < CHAINED_FEATURE_AOA_RANGE_DEG[1])
    )
    if int(plausible.sum()) < 3:
        return ChainedDelivery(
            status="rejected_insufficient_physical_features",
            n_features=int(plausible.sum()),
        )
    pre_velocity = pre_velocity[plausible]
    cross_velocity = cross_velocity[plausible]
    central_velocity = central_velocity[plausible]

    pre_path, pre_aoa = _velocity_angles(np.median(pre_velocity, axis=0))
    cross_path, cross_aoa = _velocity_angles(np.median(cross_velocity, axis=0))
    path_deg, attack_angle_deg = _velocity_angles(np.median(central_velocity, axis=0))
    feature_speeds_mph = np.linalg.norm(central_velocity, axis=1) * 2.23694
    speed_mph = float(np.median(feature_speeds_mph))
    velocity_center = np.median(central_velocity, axis=0)
    speed_mad_mph = float(
        np.median(np.linalg.norm(central_velocity - velocity_center, axis=1)) * 2.23694
    )
    speed_ratio = speed_mph / ops_club_speed_mph

    diagnostics = {
        "speed_mph": round(speed_mph, 2),
        "speed_ratio_ops": round(speed_ratio, 3),
        "velocity_mad_mph": round(speed_mad_mph, 2),
        "n_features": int(plausible.sum()),
        "pre_path_deg": round(pre_path, 2),
        "pre_attack_angle_deg": round(pre_aoa, 2),
        "cross_path_deg": round(cross_path, 2),
        "cross_attack_angle_deg": round(cross_aoa, 2),
    }
    result_speed_lo, result_speed_hi = CHAINED_SPEED_RATIO_RANGE
    if not result_speed_lo <= speed_ratio <= result_speed_hi:
        return ChainedDelivery(status="rejected_speed_ratio", **diagnostics)
    if speed_mad_mph > CHAINED_VELOCITY_MAD_MAX_MPH:
        return ChainedDelivery(status="rejected_velocity_dispersion", **diagnostics)
    if not _bounded_angles(path_deg, attack_angle_deg):
        return ChainedDelivery(status="rejected_angle_bounds", **diagnostics)

    intervals_agree = (
        _bounded_angles(pre_path, pre_aoa)
        and _bounded_angles(cross_path, cross_aoa)
        and abs(pre_path - cross_path) <= CHAINED_INTERVAL_AGREEMENT_DEG
        and abs(pre_aoa - cross_aoa) <= CHAINED_INTERVAL_AGREEMENT_DEG
    )
    high = timing_plausible and intervals_agree
    return ChainedDelivery(
        status="chained_high" if high else "chained_experimental",
        attack_angle_deg=round(attack_angle_deg, 2),
        club_path_deg=round(path_deg, 2),
        confidence_tier="high" if high else "experimental",
        **diagnostics,
    )


def _delivery_from_feature_pair(
    feature_pixels: np.ndarray,
    timestamps_s: np.ndarray,
    radar_ranges_m: np.ndarray,
    *,
    ball,
    geometry: CameraDeliveryGeometry,
    ops_club_speed_mph: float,
) -> ApproachPairEstimate | None:
    """Reconstruct one short pre-impact interval from matched image features."""
    pixels = np.asarray(feature_pixels, dtype=float)
    times = np.asarray(timestamps_s, dtype=float)
    ranges = np.asarray(radar_ranges_m, dtype=float)
    if pixels.ndim != 3 or pixels.shape[1:] != (2, 2):
        return None
    if len(pixels) < 3 or times.shape != (2,) or ranges.shape != (2,):
        return None
    elapsed = float(times[1] - times[0])
    if elapsed <= 0.0 or not ops_club_speed_mph:
        return None
    positions = np.stack(
        [
            _pixels_to_world(pixels[:, frame], ranges[frame], ball=ball, geometry=geometry)
            for frame in range(2)
        ],
        axis=1,
    )
    velocity = (positions[:, 1] - positions[:, 0]) / elapsed
    speeds_mph = np.linalg.norm(velocity, axis=1) * 2.23694
    angles = np.asarray([_velocity_angles(value) for value in velocity])
    plausible = (
        (speeds_mph > CHAINED_FEATURE_SPEED_RANGE_MPH[0])
        & (speeds_mph < CHAINED_FEATURE_SPEED_RANGE_MPH[1])
        & (angles[:, 0] > CHAINED_FEATURE_PATH_RANGE_DEG[0])
        & (angles[:, 0] < CHAINED_FEATURE_PATH_RANGE_DEG[1])
        & (angles[:, 1] > CHAINED_FEATURE_AOA_RANGE_DEG[0])
        & (angles[:, 1] < CHAINED_FEATURE_AOA_RANGE_DEG[1])
    )
    velocity = velocity[plausible]
    if len(velocity) < 3:
        return None
    center = np.median(velocity, axis=0)
    path_deg, attack_angle_deg = _velocity_angles(center)
    speed_mph = float(np.median(np.linalg.norm(velocity, axis=1)) * 2.23694)
    velocity_mad_mph = float(np.median(np.linalg.norm(velocity - center, axis=1)) * 2.23694)
    return ApproachPairEstimate(
        path_deg=path_deg,
        attack_angle_deg=attack_angle_deg,
        speed_ratio_ops=speed_mph / ops_club_speed_mph,
        velocity_mad_mph=velocity_mad_mph,
        n_features=len(velocity),
    )


def camera_ops_delivery_from_feature_pair(
    feature_pixels: np.ndarray,
    timestamps_s: np.ndarray,
    *,
    ball,
    geometry: CameraDeliveryGeometry,
    ops_club_speed_mph: float,
) -> ApproachPairEstimate | None:
    """Recover impact velocity from camera flow constrained by OPS speed.

    A down-the-line camera measures lateral and vertical image motion but not
    forward velocity directly. At the known contact point, the two perspective
    flow equations plus the OPS velocity magnitude form a closed 3D solution.
    The teed ball also supplies pitch and yaw references, so a laterally offset
    or slightly mis-aimed enclosure does not become false club path.
    """
    pixels = np.asarray(feature_pixels, dtype=float)
    times = np.asarray(timestamps_s, dtype=float)
    if pixels.ndim != 3 or pixels.shape[1:] != (2, 2) or len(pixels) < 3:
        return None
    if times.shape != (2,) or not math.isfinite(float(np.diff(times)[0])):
        return None
    elapsed = float(times[1] - times[0])
    if elapsed <= 0.0 or not math.isfinite(ops_club_speed_mph) or ops_club_speed_mph <= 0.0:
        return None

    camera_ball_range_m = math.sqrt(
        geometry.camera_lateral_offset_m**2
        + geometry.ball_forward_m**2
        + (geometry.ball_height_m - geometry.camera_height_m) ** 2
    )
    focal_px = ball.diameter_px * camera_ball_range_m / geometry.ball_diameter_m
    if not math.isfinite(focal_px) or focal_px <= 0.0:
        return None

    center_x = geometry.image_width_px / 2.0
    center_y = geometry.image_height_px / 2.0

    def normalized(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        image_x = geometry.horizontal_pixel_sign * (points[:, 0] - center_x) / focal_px
        image_z = -(points[:, 1] - center_y) / focal_px
        return deroll_normalized_offsets(
            image_x,
            image_z,
            geometry.roll_correction_deg,
        )

    ball_x, ball_z = normalized(np.asarray([[ball.x, ball.y]], dtype=float))
    ball_x = float(ball_x[0])
    ball_z = float(ball_z[0])
    expected_azimuth = math.atan2(-geometry.camera_lateral_offset_m, geometry.ball_forward_m)
    observed_azimuth = math.atan2(ball_x, 1.0)
    yaw_rad = expected_azimuth - observed_azimuth
    expected_elevation = math.atan2(
        geometry.ball_height_m - geometry.camera_height_m,
        geometry.ball_forward_m,
    )
    pitch_rad = expected_elevation - math.atan2(ball_z, 1.0)
    contact_depth_m = camera_ball_range_m / math.sqrt(1.0 + ball_x**2 + ball_z**2)

    first_x, first_z = normalized(pixels[:, 0])
    last_x, last_z = normalized(pixels[:, 1])
    x_rate = (last_x - first_x) / elapsed
    z_rate = (last_z - first_z) / elapsed
    speed_ms = ops_club_speed_mph / 2.23694
    velocities = []
    for image_x, image_z, dx_dt, dz_dt in zip(
        last_x,
        last_z,
        x_rate,
        z_rate,
        strict=True,
    ):
        # Vx = depth*du/dt + u*Vforward and likewise for Vz. Substituting
        # both into |V|=OPS speed leaves one quadratic in camera-forward V.
        coefficient_a = 1.0 + image_x**2 + image_z**2
        coefficient_b = 2.0 * contact_depth_m * (image_x * dx_dt + image_z * dz_dt)
        coefficient_c = contact_depth_m**2 * (dx_dt**2 + dz_dt**2) - speed_ms**2
        discriminant = coefficient_b**2 - 4.0 * coefficient_a * coefficient_c
        if discriminant < 0.0:
            continue
        camera_forward = (-coefficient_b + math.sqrt(discriminant)) / (2.0 * coefficient_a)
        if camera_forward <= 0.0:
            continue
        camera_lateral = contact_depth_m * dx_dt + image_x * camera_forward
        camera_vertical = contact_depth_m * dz_dt + image_z * camera_forward

        horizontal_forward = (
            math.cos(pitch_rad) * camera_forward - math.sin(pitch_rad) * camera_vertical
        )
        world_vertical = (
            math.sin(pitch_rad) * camera_forward + math.cos(pitch_rad) * camera_vertical
        )
        world_lateral = math.cos(yaw_rad) * camera_lateral + math.sin(yaw_rad) * horizontal_forward
        world_forward = -math.sin(yaw_rad) * camera_lateral + math.cos(yaw_rad) * horizontal_forward
        path_deg, attack_angle_deg = _velocity_angles(
            np.asarray([world_lateral, world_vertical, world_forward])
        )
        if (
            CHAINED_FEATURE_PATH_RANGE_DEG[0] < path_deg < CHAINED_FEATURE_PATH_RANGE_DEG[1]
            and CHAINED_FEATURE_AOA_RANGE_DEG[0]
            < attack_angle_deg
            < CHAINED_FEATURE_AOA_RANGE_DEG[1]
        ):
            velocities.append([world_lateral, world_vertical, world_forward])

    if len(velocities) < 3:
        return None
    velocity = np.asarray(velocities)
    center = np.median(velocity, axis=0)
    path_deg, attack_angle_deg = _velocity_angles(center)
    velocity_mad_mph = float(np.median(np.linalg.norm(velocity - center, axis=1)) * 2.23694)
    return ApproachPairEstimate(
        path_deg=path_deg,
        attack_angle_deg=attack_angle_deg,
        speed_ratio_ops=float(np.linalg.norm(center) * 2.23694 / ops_club_speed_mph),
        velocity_mad_mph=velocity_mad_mph,
        n_features=len(velocity),
    )


def _clubhead_pair_tracks(
    frames: np.ndarray,
    background: np.ndarray,
    ball,
    *,
    first_idx: int,
    second_idx: int,
    bright_now: float,
    dark_bg: float,
) -> tuple[np.ndarray | None, float | None]:
    """Track a consensus of clubhead pixels across one approach interval."""
    import cv2  # pylint: disable=import-outside-toplevel

    if first_idx < 0 or second_idx >= len(frames) or first_idx >= second_idx:
        return None, None
    seed = frames[first_idx]
    shaft_mask = _club_mask(seed, background, bright_now, dark_bg)
    head = _head_end(shaft_mask, ball)
    if head is None:
        return None, None

    difference = cv2.absdiff(seed, background.astype(np.uint8))
    moving = (difference > 15).astype(np.uint8)
    yy, xx = np.mgrid[0 : seed.shape[0], 0 : seed.shape[1]]
    scale = _image_scale(seed.shape)
    local_radius = max(24.0, 75.0 * scale)
    feature_radius = max(14, round(38 * scale))
    local = (xx - head[0]) ** 2 + (yy - head[1]) ** 2 <= local_radius**2
    distance = cv2.distanceTransform(moving, cv2.DIST_L2, 5)
    head_score = np.where(local, distance, 0.0)
    center_y, center_x = np.unravel_index(np.argmax(head_score), head_score.shape)
    head_thickness = float(head_score[center_y, center_x])
    if head_thickness < 2.5:
        return None, head_thickness
    feature_mask = np.zeros_like(moving)
    cv2.circle(feature_mask, (int(center_x), int(center_y)), feature_radius, 255, -1)
    feature_mask[difference < 8] = 0

    points0 = cv2.goodFeaturesToTrack(
        seed,
        maxCorners=200,
        qualityLevel=0.002,
        minDistance=1,
        mask=feature_mask,
        blockSize=3,
    )
    if points0 is None or len(points0) < 3:
        return None, head_thickness
    lk = {
        "winSize": (41, 41),
        "maxLevel": 3,
        "criteria": (
            cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
            50,
            0.003,
        ),
    }
    first = frames[first_idx]
    second = frames[second_idx]
    points1, status01, _error01 = cv2.calcOpticalFlowPyrLK(first, second, points0, None, **lk)
    if points1 is None:
        return None, head_thickness
    back, status10, _error10 = cv2.calcOpticalFlowPyrLK(second, first, points1, None, **lk)
    if back is None:
        return None, head_thickness

    flat0 = points0.reshape(-1, 2)
    flat1 = points1.reshape(-1, 2)
    valid = status01.reshape(-1).astype(bool) & status10.reshape(-1).astype(bool)
    valid &= np.linalg.norm(back.reshape(-1, 2) - flat0, axis=1) < 3.0
    flat0 = flat0[valid]
    flat1 = flat1[valid]
    if len(flat0) < 3:
        return None, head_thickness

    displacement = flat1 - flat0
    center = np.median(displacement, axis=0)
    deviation = np.linalg.norm(displacement - center, axis=1)
    limit = max(2.0, float(np.median(deviation)) * 3.0)
    consensus = deviation <= limit
    if int(consensus.sum()) < 3:
        return None, head_thickness
    return np.stack((flat0[consensus], flat1[consensus]), axis=1), head_thickness


def _clubhead_feature_tracks(
    frames: np.ndarray,
    background: np.ndarray,
    ball,
    *,
    impact_idx: int,
    bright_now: float,
    dark_bg: float,
) -> tuple[np.ndarray | None, float | None]:
    """Track identical clubhead pixels from pre-impact through post-impact."""
    import cv2  # pylint: disable=import-outside-toplevel

    indexes = (impact_idx - 1, impact_idx, impact_idx + 1)
    if indexes[0] < 0 or indexes[-1] >= len(frames):
        return None, None
    seed = frames[indexes[0]]
    shaft_mask = _club_mask(seed, background, bright_now, dark_bg)
    head = _head_end(shaft_mask, ball)
    if head is None:
        return None, None

    difference = cv2.absdiff(seed, background.astype(np.uint8))
    moving = (difference > 15).astype(np.uint8)
    yy, xx = np.mgrid[0 : seed.shape[0], 0 : seed.shape[1]]
    scale = _image_scale(seed.shape)
    local_radius = max(24.0, 75.0 * scale)
    feature_radius = max(14, round(38 * scale))
    local = (xx - head[0]) ** 2 + (yy - head[1]) ** 2 <= local_radius**2
    distance = cv2.distanceTransform(moving, cv2.DIST_L2, 5)
    head_score = np.where(local, distance, 0.0)
    center_y, center_x = np.unravel_index(np.argmax(head_score), head_score.shape)
    head_thickness = float(head_score[center_y, center_x])
    if head_thickness < 2.5:
        return None, head_thickness
    feature_mask = np.zeros_like(moving)
    cv2.circle(feature_mask, (int(center_x), int(center_y)), feature_radius, 255, -1)
    feature_mask[difference < 8] = 0

    points0 = cv2.goodFeaturesToTrack(
        seed,
        maxCorners=100,
        qualityLevel=0.005,
        minDistance=2,
        mask=feature_mask,
        blockSize=3,
    )
    if points0 is None or len(points0) < 3:
        return None, head_thickness

    lk = {
        "winSize": (31, 31),
        "maxLevel": 3,
        "criteria": (
            cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
            40,
            0.005,
        ),
    }
    first, middle, last = (frames[index] for index in indexes)
    points1, status01, _error01 = cv2.calcOpticalFlowPyrLK(first, middle, points0, None, **lk)
    if points1 is None:
        return None, head_thickness
    points2, status12, _error12 = cv2.calcOpticalFlowPyrLK(middle, last, points1, None, **lk)
    if points2 is None:
        return None, head_thickness
    back, status20, _error20 = cv2.calcOpticalFlowPyrLK(last, first, points2, None, **lk)
    if back is None:
        return None, head_thickness

    flat0 = points0.reshape(-1, 2)
    flat1 = points1.reshape(-1, 2)
    flat2 = points2.reshape(-1, 2)
    valid = (
        status01.reshape(-1).astype(bool)
        & status12.reshape(-1).astype(bool)
        & status20.reshape(-1).astype(bool)
    )
    valid &= np.linalg.norm(back.reshape(-1, 2) - flat0, axis=1) < 2.0
    if int(valid.sum()) < 3:
        return None, head_thickness
    return np.stack((flat0[valid], flat1[valid], flat2[valid]), axis=1), head_thickness


def estimate_chained_delivery(
    frames: np.ndarray,
    host_timestamp_ns: np.ndarray,
    *,
    trigger_index: int | None,
    range_evidence,
    geometry: CameraDeliveryGeometry,
    ops_club_speed_mph: float | None,
    ball_tracker: ReferenceBallTracker | None = None,
    reference_ball: ReferenceBall | None = None,
) -> ChainedDelivery:
    """Estimate final-approach club delivery from camera, IWR, and OPS."""
    if ops_club_speed_mph is None:
        return ChainedDelivery(status="rejected_no_ops_speed")
    if frames.ndim != 3 or len(frames) < 20:
        return ChainedDelivery(status="rejected_invalid_frames")
    timestamps_ns = np.asarray(host_timestamp_ns, dtype=np.int64)
    if timestamps_ns.shape != (len(frames),):
        return ChainedDelivery(status="rejected_invalid_timing")

    background = np.median(frames[:15], axis=0).astype(np.uint8)
    scene_p995, _ball_threshold, bright_now, dark_bg = _adaptive_thresholds(background)
    if scene_p995 < SCENE_P995_MIN:
        return ChainedDelivery(status="rejected_low_light", scene_p995=scene_p995)
    ball = reference_ball
    if ball is None:
        try:
            ball = detect_reference_ball(frames)
        except ValueError:
            ball = ball_tracker.fallback() if ball_tracker is not None else None
            if ball is None:
                return ChainedDelivery(status="rejected_no_ball", scene_p995=scene_p995)
        else:
            if ball_tracker is not None:
                ball, _ball_source = ball_tracker.resolve(ball)
    yy, xx = np.mgrid[0 : frames.shape[1], 0 : frames.shape[2]]
    image_scale = _image_scale(frames.shape)
    ball_zone_radius = max(50.0, BALL_ZONE_RADIUS_PX * image_scale)
    ball_zone = (xx - ball.x) ** 2 + (yy - ball.y) ** 2 <= ball_zone_radius**2
    saturated_zone = float(np.mean(background[ball_zone] >= 250))
    camera_quality_clean = saturated_zone <= BALL_ZONE_SATURATION_MAX

    impact_idx = _detect_impact_index(frames, ball, trigger_index=trigger_index)
    if impact_idx is None:
        return ChainedDelivery(status="rejected_no_impact", scene_p995=scene_p995)
    impact_vs_trigger_ms = None
    timing_plausible = trigger_index is None and camera_quality_clean
    if trigger_index is not None:
        impact_vs_trigger_ms = (
            int(timestamps_ns[impact_idx]) - int(timestamps_ns[trigger_index])
        ) / 1e6
        timing_plausible = camera_quality_clean and (
            trigger_index - IMPACT_PRE_TRIGGER_MAX
            <= impact_idx
            <= trigger_index + IMPACT_POST_TRIGGER_MAX
        )

    contact_camera_s = (int(timestamps_ns[impact_idx]) + int(timestamps_ns[impact_idx + 1])) / 2e9
    track = range_evidence.track if range_evidence is not None else None
    radar_geo = range_evidence.geometry if range_evidence is not None else None
    impact_t_s = range_evidence.impact_t_s if range_evidence is not None else None
    pair_estimates: dict[tuple[int, int], ApproachPairEstimate] = {}
    head_thicknesses = []
    for offsets in APPROACH_PATH_OFFSETS:
        indexes = np.asarray([impact_idx + offset for offset in offsets])
        feature_tracks, head_thickness = _clubhead_pair_tracks(
            frames,
            background,
            ball,
            first_idx=int(indexes[0]),
            second_idx=int(indexes[1]),
            bright_now=bright_now,
            dark_bg=dark_bg,
        )
        if head_thickness is not None:
            head_thicknesses.append(head_thickness)
        if feature_tracks is None:
            continue
        camera_times_s = timestamps_ns[indexes].astype(float) / 1e9
        if range_evidence is None:
            estimate = camera_ops_delivery_from_feature_pair(
                feature_tracks,
                camera_times_s,
                ball=ball,
                geometry=geometry,
                ops_club_speed_mph=ops_club_speed_mph,
            )
        else:
            relative_s = camera_times_s - contact_camera_s
            radar_ranges_m = np.asarray(
                [
                    float(track.range_at(impact_t_s + offset, radar_geo.range_res_m))
                    for offset in relative_s
                ]
            )
            estimate = _delivery_from_feature_pair(
                feature_tracks,
                camera_times_s,
                radar_ranges_m,
                ball=ball,
                geometry=geometry,
                ops_club_speed_mph=ops_club_speed_mph,
            )
        if estimate is not None:
            pair_estimates[offsets] = estimate

    head_thickness = max(head_thicknesses, default=None)
    common = {
        "impact_frame": impact_idx,
        "impact_vs_trigger_ms": (
            round(impact_vs_trigger_ms, 2) if impact_vs_trigger_ms is not None else None
        ),
        "scene_p995": round(scene_p995, 1),
        "head_thickness_px": (round(head_thickness, 2) if head_thickness is not None else None),
    }
    if not pair_estimates:
        return ChainedDelivery(status="rejected_no_stable_clubhead", **common)
    result = combine_approach_estimates(
        list(pair_estimates.values()),
        attack_estimate=pair_estimates.get((-1, 0)),
        preferred_path_estimate=_preferred_path_estimate(pair_estimates),
        timing_plausible=timing_plausible,
    )
    if range_evidence is None and (
        result.attack_angle_deg is not None or result.club_path_deg is not None
    ):
        # Camera+OPS closes the geometry, but TrackMan has not validated this
        # fallback yet. Keep every recovered value visible and explicitly low
        # confidence rather than inheriting the primary estimator's tiers.
        return ChainedDelivery(
            **{
                **vars(result),
                **common,
                "status": "camera_ops_fallback",
                "confidence_tier": "experimental",
                "path_confidence_tier": ("low" if result.club_path_deg is not None else "withheld"),
                "attack_confidence_tier": (
                    "low" if result.attack_angle_deg is not None else "withheld"
                ),
            }
        )
    return ChainedDelivery(**{**vars(result), **common})


# Measured AoA offsets (TrackMan July baseline minus radar candidate median,
# 2026-08-07 session, impact time corrected -2 ms). Keyed by nominal loft.
# The iron points are monotone with loft; irons/wedges interpolate along
# that line. The driver's offset is its own measurement (different head
# geometry entirely); woods and hybrids borrow it as the nearest club class.
_IRON_OFFSET_ANCHORS: tuple[tuple[float, float], ...] = (
    (27.0, 9.1),  # 5-iron
    (34.0, 10.5),  # 7-iron
    (42.0, 16.0),  # 9-iron
)
_DRIVER_OFFSET_DEG = 21.6

_IRON_LIKE = {
    ClubType.IRON_2,
    ClubType.IRON_3,
    ClubType.IRON_4,
    ClubType.IRON_5,
    ClubType.IRON_6,
    ClubType.IRON_7,
    ClubType.IRON_8,
    ClubType.IRON_9,
    ClubType.PW,
    ClubType.GW,
    ClubType.SW,
    ClubType.LW,
}


@dataclass(frozen=True)
class TraceResult:
    """Camera delivery-plane trace for one capture."""

    status: str  # ok | low_light | overexposed | no_ball | no_impact |
    # impact_implausible | insufficient_pairs | error
    trace_deg: float | None = None
    n_pairs: int = 0
    match_scores: tuple[float, ...] = ()
    scene_p995: float | None = None
    impact_frame: int | None = None
    detail: str | None = None


@dataclass(frozen=True)
class FusedDelivery:
    """Offset-corrected AoA and trace-derived club path."""

    status: str  # fused | no_radar_aoa | trace_<status> | trace_out_of_range
    attack_angle_deg: float | None = None
    club_path_deg: float | None = None
    aoa_offset_deg: float | None = None
    offset_source: str | None = None  # measured | loft_interpolated | class_extrapolated
    trace_deg: float | None = None


_MEASURED_OFFSETS: dict[ClubType, float] = {
    ClubType.IRON_5: 9.1,
    ClubType.IRON_7: 10.5,
    ClubType.IRON_9: 16.0,
    ClubType.DRIVER: _DRIVER_OFFSET_DEG,
}


def aoa_offset_for_club(club: ClubType) -> tuple[float, str]:
    """Per-club AoA calibration offset and how it was obtained.

    Measured clubs return their measured offsets exactly. Other irons and
    wedges interpolate/extrapolate the measured iron anchors along nominal
    loft; woods and hybrids borrow the driver's offset as the nearest
    head-geometry class. UNKNOWN gets the iron line at a mid-iron loft.
    """
    measured = _MEASURED_OFFSETS.get(club)
    if measured is not None:
        return measured, "measured"
    if club in _IRON_LIKE or club is ClubType.UNKNOWN:
        loft = get_club_physics(club).nominal_loft_deg
        lofts = np.array([anchor[0] for anchor in _IRON_OFFSET_ANCHORS])
        offsets = np.array([anchor[1] for anchor in _IRON_OFFSET_ANCHORS])
        offset = float(np.interp(loft, lofts, offsets))
        return round(offset, 2), "loft_interpolated"
    # woods / hybrids: nearest measured class is the driver's hollow head
    return _DRIVER_OFFSET_DEG, "class_extrapolated"


def _adaptive_thresholds(background: np.ndarray) -> tuple[float, float, float, float]:
    """(scene_p995, ball_threshold, bright_now, dark_bg) for one capture."""
    scene_p995 = float(np.percentile(background, 99.5))
    top = float(np.percentile(background, 99.95))
    ball_threshold = max(BALL_THRESHOLD_FLOOR, BALL_THRESHOLD_SCALE * top)
    bright_now = max(BRIGHT_NOW_FLOOR, BRIGHT_NOW_SCALE * top)
    dark_bg = bright_now - DARK_BG_MARGIN
    return scene_p995, ball_threshold, bright_now, dark_bg


def _detect_impact_index(
    frames: np.ndarray,
    ball,
    *,
    trigger_index: int | None = None,
) -> int | None:
    """Compatibility wrapper for shared impact-frame detection."""
    return detect_impact_frame(
        frames,
        ball,
        trigger_frame_index=trigger_index,
        pre_trigger_max=IMPACT_PRE_TRIGGER_MAX,
        post_trigger_max=IMPACT_POST_TRIGGER_MAX,
        patch_radius_fraction=BALL_PATCH_RADIUS_FRAC,
        present_delta=BALL_PRESENT_DELTA,
    )


def _club_mask(
    frame: np.ndarray, background: np.ndarray, bright_now: float, dark_bg: float
) -> np.ndarray:
    return ((frame > bright_now) & (background < dark_bg)).astype(np.uint8)


def _head_end(mask: np.ndarray, ball) -> tuple[float, float] | None:
    """Ball-side extremal point of the club component nearest the ball."""
    import cv2  # pylint: disable=import-outside-toplevel

    n_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    best = None
    for label in range(1, n_labels):
        if stats[label, cv2.CC_STAT_AREA] < MIN_CLUB_COMPONENT_PX:
            continue
        distance = math.hypot(centroids[label][0] - ball.x, centroids[label][1] - ball.y)
        if best is None or distance < best[0]:
            best = (distance, label)
    if best is None or best[0] > MAX_COMPONENT_BALL_DIST_PX:
        return None
    ys, xs = np.nonzero(labels == best[1])
    points = np.column_stack([xs, ys]).astype(np.float64)
    centered = points - points.mean(axis=0)
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    projections = centered @ vt[0]
    low = points[projections <= projections.min() + TIP_BAND_PX].mean(axis=0)
    high = points[projections >= projections.max() - TIP_BAND_PX].mean(axis=0)
    head = min((low, high), key=lambda end: math.hypot(end[0] - ball.x, end[1] - ball.y))
    return float(head[0]), float(head[1])


def _crop(image: np.ndarray, cx: float, cy: float, half: int) -> np.ndarray | None:
    x0, x1 = int(round(cx)) - half, int(round(cx)) + half
    y0, y1 = int(round(cy)) - half, int(round(cy)) + half
    if x0 < 0 or y0 < 0 or x1 > image.shape[1] or y1 > image.shape[0]:
        return None
    return image[y0:y1, x0:x1]


def estimate_delivery_trace(
    frames: np.ndarray,
    host_timestamp_ns: np.ndarray,
    trigger_index: int | None = None,
) -> TraceResult:
    """Delivery-plane trace from one DTL capture (frames, per-frame host ns).

    trace = atan2(v_vertical, v_lateral) of the clubhead's transverse motion,
    measured by binary-mask correlation of the club component across the
    frame pairs bracketing impact.
    """
    import cv2  # pylint: disable=import-outside-toplevel

    if frames.ndim != 3 or len(frames) < 20:
        return TraceResult(status="error", detail="frames must be (n>=20, h, w)")
    background = np.median(frames[:15], axis=0)
    scene_p995, ball_threshold, bright_now, dark_bg = _adaptive_thresholds(background)
    if scene_p995 < SCENE_P995_MIN:
        return TraceResult(status="low_light", scene_p995=scene_p995)
    saturated_global = float(np.mean(background >= 250))
    try:
        ball = detect_reference_ball(frames, brightness_threshold=int(ball_threshold))
    except ValueError:
        status = "overexposed" if saturated_global > GLOBAL_SATURATION_HINT else "no_ball"
        return TraceResult(
            status=status, scene_p995=scene_p995, detail=f"saturated_frac={saturated_global:.3f}"
        )
    yy, xx = np.mgrid[0 : frames.shape[1], 0 : frames.shape[2]]
    image_scale = _image_scale(frames.shape)
    ball_zone_radius = max(50.0, BALL_ZONE_RADIUS_PX * image_scale)
    ball_zone = (xx - ball.x) ** 2 + (yy - ball.y) ** 2 <= ball_zone_radius**2
    saturated_zone = float(np.mean(background[ball_zone] >= 250))
    if saturated_zone > BALL_ZONE_SATURATION_MAX:
        return TraceResult(
            status="overexposed",
            scene_p995=scene_p995,
            detail=f"ball_zone_saturated_frac={saturated_zone:.3f}",
        )
    impact_idx = _detect_impact_index(frames, ball, trigger_index=trigger_index)
    if impact_idx is None:
        return TraceResult(status="no_impact", scene_p995=scene_p995)
    if trigger_index is not None and not (
        trigger_index - IMPACT_PRE_TRIGGER_MAX
        <= impact_idx
        <= trigger_index + IMPACT_POST_TRIGGER_MAX
    ):
        return TraceResult(
            status="impact_implausible",
            scene_p995=scene_p995,
            impact_frame=impact_idx,
            detail=f"trigger_index={trigger_index}",
        )

    masks: dict[int, np.ndarray] = {}
    heads: dict[int, tuple[float, float] | None] = {}
    for offset in range(min(PAIR_OFFSETS), max(PAIR_OFFSETS) + 2):
        idx = impact_idx + offset
        if 0 <= idx < len(frames):
            masks[idx] = _club_mask(frames[idx], background, bright_now, dark_bg)
            heads[idx] = _head_end(masks[idx], ball)

    velocities: list[tuple[float, float]] = []
    scores: list[float] = []
    patch_half = max(16, round(PATCH_HALF_PX * image_scale))
    search_half = max(patch_half + 12, round(SEARCH_HALF_PX * image_scale))
    for offset in PAIR_OFFSETS:
        first, second = impact_idx + offset, impact_idx + offset + 1
        if first not in masks or second not in masks or heads.get(first) is None:
            continue
        head = heads[first]
        template = _crop(masks[first].astype(np.float32), head[0], head[1], patch_half)
        window = _crop(masks[second].astype(np.float32), head[0], head[1], search_half)
        if template is None or window is None or template.sum() < 30:
            continue
        response = cv2.matchTemplate(window, template, cv2.TM_CCORR_NORMED)
        _, score, _, location = cv2.minMaxLoc(response)
        if score < MIN_MATCH_SCORE:
            continue
        dx = location[0] - (search_half - patch_half)
        dy = location[1] - (search_half - patch_half)
        if math.hypot(dx, dy) < MIN_PAIR_STEP_PX:
            continue  # self-match on a static blob, not club motion
        dt_s = (int(host_timestamp_ns[second]) - int(host_timestamp_ns[first])) / 1e9
        if dt_s <= 0:
            continue
        velocities.append((dx / dt_s, dy / dt_s))
        scores.append(float(score))

    if len(velocities) < MIN_PAIRS:
        return TraceResult(
            status="insufficient_pairs",
            n_pairs=len(velocities),
            scene_p995=scene_p995,
            impact_frame=impact_idx,
        )
    v_x = float(np.median([v[0] for v in velocities]))
    v_y = float(np.median([v[1] for v in velocities]))
    trace_deg = math.degrees(math.atan2(-v_y, abs(v_x)))
    return TraceResult(
        status="ok",
        trace_deg=round(trace_deg, 2),
        n_pairs=len(velocities),
        match_scores=tuple(round(s, 3) for s in scores),
        scene_p995=round(scene_p995, 1),
        impact_frame=impact_idx,
    )


def fuse_club_delivery(
    radar_attack_angle_deg: float | None,
    trace: TraceResult,
    club: ClubType,
) -> FusedDelivery:
    """Offset-correct the radar AoA candidate and derive club path.

    tan(path) = tan(AoA_corrected) / tan(trace). Both outputs are
    experimental until the per-club offsets are TrackMan-validated.
    """
    offset_deg, offset_source = aoa_offset_for_club(club)
    if radar_attack_angle_deg is None:
        return FusedDelivery(
            status="no_radar_aoa",
            aoa_offset_deg=offset_deg,
            offset_source=offset_source,
            trace_deg=trace.trace_deg,
        )
    attack_angle_deg = radar_attack_angle_deg + offset_deg
    if trace.status != "ok" or trace.trace_deg is None:
        return FusedDelivery(
            status=f"trace_{trace.status}",
            attack_angle_deg=round(attack_angle_deg, 1),
            aoa_offset_deg=offset_deg,
            offset_source=offset_source,
        )
    lo, hi = TRACE_ABS_RANGE_DEG
    if not lo <= abs(trace.trace_deg) <= hi:
        return FusedDelivery(
            status="trace_out_of_range",
            attack_angle_deg=round(attack_angle_deg, 1),
            aoa_offset_deg=offset_deg,
            offset_source=offset_source,
            trace_deg=trace.trace_deg,
        )
    # trace = atan2(tan AoA, tan path), so its sign IS the AoA's sign. A
    # mismatch means the camera locked onto the wrong mover (e.g. the flying
    # ball), so the path would be fabricated.
    if math.copysign(1.0, trace.trace_deg) != math.copysign(1.0, attack_angle_deg):
        return FusedDelivery(
            status="trace_aoa_sign_mismatch",
            attack_angle_deg=round(attack_angle_deg, 1),
            aoa_offset_deg=offset_deg,
            offset_source=offset_source,
            trace_deg=trace.trace_deg,
        )
    path_deg = math.degrees(
        math.atan(
            math.tan(math.radians(attack_angle_deg)) / math.tan(math.radians(trace.trace_deg))
        )
    )
    return FusedDelivery(
        status="fused",
        attack_angle_deg=round(attack_angle_deg, 1),
        club_path_deg=round(path_deg, 1),
        aoa_offset_deg=offset_deg,
        offset_source=offset_source,
        trace_deg=trace.trace_deg,
    )
