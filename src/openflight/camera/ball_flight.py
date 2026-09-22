"""Experimental rear-camera horizontal ball-flight reconstruction.

Camera centroids provide ball bearing. IWR6843 range is the preferred metric
depth source; apparent regulation-ball size provides a lower-confidence camera-
only fallback. OPS ball speed gates target identity. IWR horizontal remains an
independent comparison and fallback.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

import numpy as np

from openflight.camera.club_motion import (
    BALL_DIAMETER_MM,
    ReferenceBall,
    detect_reference_ball,
)
from openflight.camera.geometry import deroll_normalized_offsets

MPH_PER_MS = 2.23694
PARAMETER_SWEEP_SIZE = 27
MAX_IWR_FALLBACK_ABS_DEG = 20.0


@dataclass(frozen=True)
class BallCandidate:
    """One ball-like connected component in a camera frame."""

    x: float
    y: float
    area: int
    width: int
    height: int
    fill: float
    circularity: float
    mean_intensity: float


@dataclass(frozen=True)
class CameraBallGeometry:
    """Measured geometry shared by the rear camera and IWR6843."""

    camera_height_m: float
    radar_height_m: float
    tee_range_m: float
    ball_height_m: float
    # Camera optical-center position relative to radar center. Positive is
    # target-right when viewed from behind the sensors looking downrange.
    camera_lateral_offset_m: float = 0.0
    horizontal_offset_deg: float = 0.0
    # Convert saved-image horizontal pixels back to physical target direction.
    # Mirrored operator previews use -1; unmirrored captures use +1.
    horizontal_pixel_sign: float = 1.0
    roll_correction_deg: float = 0.0
    ball_diameter_m: float = BALL_DIAMETER_MM / 1000.0
    image_width_px: int = 640
    image_height_px: int = 400

    @property
    def ball_forward_m(self) -> float:
        """Forward radar-to-ball distance derived from tee slant range."""
        vertical = self.ball_height_m - self.radar_height_m
        return math.sqrt(max(self.tee_range_m**2 - vertical**2, 1e-9))

    @property
    def camera_origin(self) -> np.ndarray:
        """Camera origin in the radar-centered world coordinate system."""
        return np.array([self.camera_lateral_offset_m, 0.0, self.camera_height_m])


@dataclass(frozen=True)
class CameraBallEstimate:
    """Consensus result from the camera/IWR/OPS ball-flight estimator."""

    status: str
    confidence_tier: str = "withheld"
    horizontal_deg: float | None = None
    vertical_deg: float | None = None
    support: int = 0
    support_pct: float = 0.0
    parameter_mad_deg: float | None = None
    window_mad_deg: float | None = None
    speed_mph: float | None = None
    speed_error_mph: float | None = None
    n_points: int = 0
    first_frame: int | None = None
    last_frame: int | None = None
    depth_source: str | None = None


@dataclass(frozen=True)
class HorizontalFusionDecision:
    """Selected horizontal result plus independent sensor provenance."""

    selected_deg: float | None
    source: str | None
    confidence: float | None
    status: str
    iwr_horizontal_deg: float | None
    camera_horizontal_deg: float | None
    camera_iwr_delta_deg: float | None


@dataclass(frozen=True)
class _PathEstimate:
    horizontal_deg: float
    vertical_deg: float
    speed_mph: float
    speed_error_mph: float
    fit_median_m: float
    step_speed_mad_mph: float
    window_mad_deg: float
    n_points: int
    first_frame: int
    last_frame: int


def _camera_model(
    anchor: ReferenceBall,
    geometry: CameraBallGeometry,
) -> tuple[float, float, np.ndarray]:
    """Infer focal scale and pose from the stationary regulation-size ball."""
    center_x = geometry.image_width_px / 2.0
    center_y = geometry.image_height_px / 2.0
    camera_ball_range = math.sqrt(
        geometry.camera_lateral_offset_m**2
        + geometry.ball_forward_m**2
        + (geometry.ball_height_m - geometry.camera_height_m) ** 2
    )
    focal_px = anchor.diameter_px * camera_ball_range / geometry.ball_diameter_m
    ball_x = geometry.horizontal_pixel_sign * (anchor.x - center_x) / focal_px
    ball_z = -(anchor.y - center_y) / focal_px
    _ball_x, ball_z = deroll_normalized_offsets(
        ball_x,
        ball_z,
        geometry.roll_correction_deg,
    )
    pitch = math.atan2(
        geometry.ball_height_m - geometry.camera_height_m,
        geometry.ball_forward_m,
    ) - math.atan2(ball_z, 1.0)
    radar_from_camera = geometry.camera_origin - np.array([0.0, 0.0, geometry.radar_height_m])
    return focal_px, pitch, radar_from_camera


def _project(
    candidate: BallCandidate,
    radar_range_m: float,
    *,
    model: tuple[float, float, np.ndarray],
    geometry: CameraBallGeometry,
) -> np.ndarray | None:
    _focal_px, _pitch, radar_from_camera = model
    ray = _camera_ray(candidate, model=model, geometry=geometry)
    ray_offset = float(ray @ radar_from_camera)
    discriminant = ray_offset**2 - (float(radar_from_camera @ radar_from_camera) - radar_range_m**2)
    if discriminant < 0.0:
        return None
    distance = -ray_offset + math.sqrt(discriminant)
    if distance <= 0.0:
        return None
    return geometry.camera_origin + distance * ray


def _camera_ray(
    candidate: BallCandidate,
    *,
    model: tuple[float, float, np.ndarray],
    geometry: CameraBallGeometry,
) -> np.ndarray:
    """Return the unit camera ray through a detected ball centroid."""
    focal_px, pitch, _radar_from_camera = model
    image_x = (
        geometry.horizontal_pixel_sign * (candidate.x - geometry.image_width_px / 2.0) / focal_px
    )
    image_z = -(candidate.y - geometry.image_height_px / 2.0) / focal_px
    image_x, image_z = deroll_normalized_offsets(
        image_x,
        image_z,
        geometry.roll_correction_deg,
    )
    ray = np.array(
        [
            image_x,
            math.cos(pitch) - image_z * math.sin(pitch),
            math.sin(pitch) + image_z * math.cos(pitch),
        ]
    )
    ray /= np.linalg.norm(ray)
    return ray


def _project_from_ball_size(
    candidate: BallCandidate,
    *,
    model: tuple[float, float, np.ndarray],
    geometry: CameraBallGeometry,
) -> np.ndarray | None:
    """Project a regulation ball using apparent diameter as camera depth."""
    measured_diameter_px = math.sqrt(4.0 * candidate.area / math.pi)
    if measured_diameter_px <= 0.0:
        return None
    camera_range_m = model[0] * geometry.ball_diameter_m / measured_diameter_px
    if not 0.25 <= camera_range_m <= 15.0:
        return None
    ray = _camera_ray(candidate, model=model, geometry=geometry)
    return geometry.camera_origin + camera_range_m * ray


def _candidates(
    frame: np.ndarray,
    background: np.ndarray,
    anchor: ReferenceBall,
    *,
    bright_threshold: int,
    difference_threshold: int,
    min_area: int,
) -> list[BallCandidate]:
    try:
        import cv2  # noqa: PLC0415  pylint: disable=import-outside-toplevel
    except ImportError as exc:  # pragma: no cover - optional hardware dependency
        raise RuntimeError("camera ball flight requires OpenCV") from exc

    difference = cv2.subtract(frame, background)
    mask = ((frame > bright_threshold) & (difference > difference_threshold)).astype(np.uint8)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    found: list[BallCandidate] = []
    for label in range(1, count):
        _, _, width, height, area = stats[label]
        x, y = centroids[label]
        aspect = width / max(height, 1)
        fill = area / max(width * height, 1)
        if not (
            min_area <= area <= 400
            and 0.35 <= aspect <= 2.8
            and fill >= 0.18
            and abs(x - anchor.x) < 160
            and 10 < y < anchor.y + 15
        ):
            continue
        left = int(stats[label][cv2.CC_STAT_LEFT])
        top = int(stats[label][cv2.CC_STAT_TOP])
        roi = (labels[top : top + height, left : left + width] == label).astype(np.uint8)
        contours, _ = cv2.findContours(roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        perimeter = sum(cv2.arcLength(contour, True) for contour in contours)
        circularity = 4.0 * math.pi * area / perimeter**2 if perimeter > 0.0 else 0.0
        pixels = frame[top : top + height, left : left + width][roi > 0]
        found.append(
            BallCandidate(
                x=float(x),
                y=float(y),
                area=int(area),
                width=int(width),
                height=int(height),
                fill=float(fill),
                circularity=float(circularity),
                mean_intensity=float(np.mean(pixels)),
            )
        )
    return found


def _rough_path_score(path: list[tuple[int, BallCandidate]]) -> float:
    if len(path) < 3:
        return 20.0 * len(path)
    steps = [
        ((second.x - first.x) / (j - i), (second.y - first.y) / (j - i))
        for (i, first), (j, second) in zip(path, path[1:])
    ]
    median_x = statistics.median(step[0] for step in steps)
    median_y = statistics.median(step[1] for step in steps)
    dispersion = statistics.median(
        math.hypot(step_x - median_x, step_y - median_y) for step_x, step_y in steps
    )
    return 20.0 * len(path) - 2.0 * dispersion


def _pixel_paths(
    nodes: list[list[BallCandidate]],
    anchor: ReferenceBall,
) -> list[list[tuple[int, BallCandidate]]]:
    all_paths: list[list[tuple[int, BallCandidate]]] = []
    frontier: list[list[tuple[int, BallCandidate]]] = []
    for frame in range(min(5, len(nodes))):
        for candidate in nodes[frame]:
            if math.hypot(candidate.x - anchor.x, candidate.y - anchor.y) <= 70.0:
                frontier.append([(frame, candidate)])
    all_paths.extend(frontier)
    for _ in range(len(nodes)):
        extended: list[list[tuple[int, BallCandidate]]] = []
        for path in frontier:
            previous_frame, previous = path[-1]
            for frame in range(previous_frame + 1, min(len(nodes), previous_frame + 3)):
                gap = frame - previous_frame
                for candidate in nodes[frame]:
                    delta_x = candidate.x - previous.x
                    delta_y = candidate.y - previous.y
                    if abs(delta_x) <= 30.0 * gap and -38.0 * gap <= delta_y <= -0.5 * gap:
                        extended.append([*path, (frame, candidate)])
        if not extended:
            break
        extended.sort(key=_rough_path_score, reverse=True)
        frontier = extended[:150]
        all_paths.extend(frontier)
    viable = [path for path in all_paths if len(path) >= 4]
    viable.sort(key=_rough_path_score, reverse=True)
    return viable[:120]


def _robust_velocity(times: np.ndarray, positions: np.ndarray) -> tuple[np.ndarray, float]:
    slopes = []
    for first in range(len(times)):
        for second in range(first + 1, len(times)):
            delta = times[second] - times[first]
            if delta > 0.0:
                slopes.append((positions[second] - positions[first]) / delta)
    velocity = np.median(slopes, axis=0)
    intercept = np.median(positions - times[:, None] * velocity, axis=0)
    residual = np.linalg.norm(positions - (intercept + times[:, None] * velocity), axis=1)
    return velocity, float(np.median(residual))


def _horizontal(velocity: np.ndarray) -> float:
    """Return motion direction relative to the camera optical target line."""
    angle = math.atan2(float(velocity[0]), float(velocity[1]))
    return (math.degrees(angle) + 180.0) % 360.0 - 180.0


def _apply_horizontal_offset(angle_deg: float, offset_deg: float) -> float:
    """Apply a measured setup yaw correction while preserving angle wrapping."""
    return (angle_deg + offset_deg + 180.0) % 360.0 - 180.0


def _path_estimate(
    *,
    path: list[tuple[int, BallCandidate]],
    frame_indices: list[int],
    timestamps_ns: np.ndarray,
    trigger_ns: int,
    range_evidence,
    ops_ball_speed_mph: float,
    iwr_vertical_deg: float | None,
    model: tuple[float, float, np.ndarray],
    geometry: CameraBallGeometry,
    thresholds: tuple[int, int, int],  # retained for replay/debug provenance
) -> tuple[float, _PathEstimate] | None:
    del thresholds
    _focal_px, _pitch, _radar_from_camera = model
    times: list[float] = []
    positions: list[np.ndarray] = []
    actual_frames: list[int] = []
    used_candidates: list[BallCandidate] = []
    for relative_frame, candidate in path:
        frame = frame_indices[relative_frame]
        relative_time = (int(timestamps_ns[frame]) - trigger_ns) / 1e9
        if range_evidence is None:
            position = _project_from_ball_size(candidate, model=model, geometry=geometry)
        else:
            radar_range = float(
                range_evidence.track.range_at(
                    range_evidence.impact_t_s + relative_time,
                    range_evidence.geometry.range_res_m,
                )
            )
            position = _project(candidate, radar_range, model=model, geometry=geometry)
        if position is not None:
            times.append(relative_time)
            positions.append(position)
            actual_frames.append(frame)
            used_candidates.append(candidate)
    if len(positions) < 4:
        return None

    times_array = np.asarray(times)
    positions_array = np.stack(positions)
    velocity, fit_median = _robust_velocity(times_array, positions_array)
    horizontal = _apply_horizontal_offset(
        _horizontal(velocity),
        geometry.horizontal_offset_deg,
    )
    vertical = math.degrees(
        math.atan2(float(velocity[2]), math.hypot(float(velocity[0]), float(velocity[1])))
    )
    speed = float(np.linalg.norm(velocity) * MPH_PER_MS)
    step_velocity = np.diff(positions_array, axis=0) / np.diff(times_array)[:, None]
    step_speeds = np.linalg.norm(step_velocity, axis=1) * MPH_PER_MS
    step_speed_mad = float(np.median(np.abs(step_speeds - np.median(step_speeds))))
    step_angles = np.asarray(
        [
            _apply_horizontal_offset(_horizontal(step), geometry.horizontal_offset_deg)
            for step in step_velocity
        ]
    )
    window_mad = float(np.median(np.abs(step_angles - horizontal)))
    shape = np.asarray(
        [abs(math.log(candidate.width / max(candidate.height, 1))) for candidate in used_candidates]
    )
    shape_median = float(np.median(shape))
    fill_median = float(np.median([candidate.fill for candidate in used_candidates]))
    circularity_median = float(np.median([candidate.circularity for candidate in used_candidates]))
    intensity_median = float(np.median([candidate.mean_intensity for candidate in used_candidates]))
    camera_origin = np.array([0.0, 0.0, geometry.camera_height_m])
    camera_ranges = np.linalg.norm(positions_array - camera_origin, axis=1)
    expected_diameter = model[0] * geometry.ball_diameter_m / camera_ranges
    measured_diameter = np.asarray(
        [math.sqrt(4.0 * candidate.area / math.pi) for candidate in used_candidates]
    )
    size_ratio = measured_diameter / expected_diameter
    size_ratio_median = float(np.median(size_ratio))
    size_ratio_mad = float(np.median(np.abs(size_ratio - size_ratio_median)))
    if not (
        -30.0 <= horizontal <= 30.0
        and -5.0 <= vertical <= 55.0
        and 0.5 * ops_ball_speed_mph <= speed <= 1.5 * ops_ball_speed_mph
        and shape_median <= 0.55
        and fill_median >= 0.5
        and circularity_median >= 0.5
        and intensity_median >= 195.0
        and 0.45 <= size_ratio_median <= 2.5
        and size_ratio_mad <= 0.75
    ):
        return None

    vertical_prior = abs(vertical - iwr_vertical_deg) if iwr_vertical_deg is not None else 0.0
    score = (
        10.0 * len(positions_array)
        - 400.0 * fit_median
        - 0.45 * abs(speed - ops_ball_speed_mph)
        - 0.35 * step_speed_mad
        - 0.5 * vertical_prior
        - 6.0 * window_mad
        - 8.0 * shape_median
        + 6.0 * fill_median
        + 4.0 * circularity_median
        - 4.0 * size_ratio_mad
        - 2.0 * (actual_frames[0] - frame_indices[0])
    )
    return score, _PathEstimate(
        horizontal_deg=horizontal,
        vertical_deg=vertical,
        speed_mph=speed,
        speed_error_mph=speed - ops_ball_speed_mph,
        fit_median_m=fit_median,
        step_speed_mad_mph=step_speed_mad,
        window_mad_deg=window_mad,
        n_points=len(positions_array),
        first_frame=actual_frames[0],
        last_frame=actual_frames[-1],
    )


def _confidence_tier(support: int, parameter_mad: float, window_mad: float) -> str:
    """Map detector consensus and local trajectory coherence to a confidence tier."""
    stable_consensus = parameter_mad <= 1.0
    if support >= 9 and stable_consensus and window_mad <= 0.5:
        return "high"
    if support >= 2 and stable_consensus and window_mad <= 1.5:
        return "experimental"
    return "withheld"


def estimate_camera_ball_flight(
    frames: np.ndarray,
    timestamps_ns: np.ndarray,
    *,
    trigger_ns: int,
    range_evidence,
    geometry: CameraBallGeometry,
    ops_ball_speed_mph: float,
    iwr_vertical_deg: float | None = None,
    ball_tracker=None,
) -> CameraBallEstimate:
    """Estimate horizontal flight with a frozen detector-consensus sweep."""
    if frames.ndim != 3 or len(frames) < 4 or len(timestamps_ns) != len(frames):
        return CameraBallEstimate("rejected_invalid_camera_frames")
    try:
        anchor = detect_reference_ball(frames)
    except ValueError:
        fallback = getattr(ball_tracker, "fallback", None)
        anchor = fallback() if fallback is not None else None
        if anchor is None:
            return CameraBallEstimate("rejected_reference_ball_not_found")
    else:
        if ball_tracker is not None:
            resolver = getattr(ball_tracker, "resolve_stable", ball_tracker.resolve)
            anchor, _anchor_source = resolver(anchor)
    if not 9.0 <= anchor.diameter_px <= 30.0:
        return CameraBallEstimate("rejected_implausible_reference_ball")

    model = _camera_model(anchor, geometry)
    trigger_frame = int(np.argmin(np.abs(timestamps_ns.astype(np.int64) - trigger_ns)))
    frame_indices = list(range(trigger_frame, min(len(frames), trigger_frame + 15)))
    if len(frame_indices) < 4:
        return CameraBallEstimate("rejected_insufficient_post_trigger_frames")
    background = np.median(frames[: min(20, len(frames))], axis=0).astype(np.uint8)

    def collect(depth_evidence) -> list[_PathEstimate]:
        found: list[_PathEstimate] = []
        for bright in (100, 115, 130):
            for difference in (12, 18, 24):
                for min_area in (5, 10, 20):
                    nodes = [
                        _candidates(
                            frames[frame],
                            background,
                            anchor,
                            bright_threshold=bright,
                            difference_threshold=difference,
                            min_area=min_area,
                        )
                        for frame in frame_indices
                    ]
                    options = [
                        result
                        for path in _pixel_paths(nodes, anchor)
                        if (
                            result := _path_estimate(
                                path=path,
                                frame_indices=frame_indices,
                                timestamps_ns=timestamps_ns,
                                trigger_ns=trigger_ns,
                                range_evidence=depth_evidence,
                                ops_ball_speed_mph=ops_ball_speed_mph,
                                iwr_vertical_deg=iwr_vertical_deg,
                                model=model,
                                geometry=geometry,
                                thresholds=(bright, difference, min_area),
                            )
                        )
                        is not None
                    ]
                    if options:
                        found.append(max(options, key=lambda item: item[0])[1])
        return found

    depth_source = "iwr_range" if range_evidence is not None else "camera_size"
    estimates = collect(range_evidence)
    if range_evidence is not None:
        primary_tier = "withheld"
        if estimates:
            primary_horizontal = np.asarray([estimate.horizontal_deg for estimate in estimates])
            primary_median = float(np.median(primary_horizontal))
            primary_tier = _confidence_tier(
                len(estimates),
                float(np.median(np.abs(primary_horizontal - primary_median))),
                float(np.median([estimate.window_mad_deg for estimate in estimates])),
            )
        if primary_tier == "withheld":
            camera_only = collect(None)
            if camera_only:
                camera_horizontal = np.asarray(
                    [estimate.horizontal_deg for estimate in camera_only]
                )
                camera_median = float(np.median(camera_horizontal))
                camera_tier = _confidence_tier(
                    len(camera_only),
                    float(np.median(np.abs(camera_horizontal - camera_median))),
                    float(np.median([estimate.window_mad_deg for estimate in camera_only])),
                )
                if camera_tier != "withheld":
                    depth_source = "camera_size"
                    estimates = camera_only

    if not estimates:
        return CameraBallEstimate("rejected_no_stable_path")
    horizontal = np.asarray([estimate.horizontal_deg for estimate in estimates])
    median_horizontal = float(np.median(horizontal))
    parameter_mad = float(np.median(np.abs(horizontal - median_horizontal)))
    window_mad = float(np.median([estimate.window_mad_deg for estimate in estimates]))
    tier = _confidence_tier(len(estimates), parameter_mad, window_mad)
    if depth_source == "camera_size" and tier == "high":
        tier = "experimental"
    representative = min(estimates, key=lambda item: abs(item.horizontal_deg - median_horizontal))
    return CameraBallEstimate(
        status=(
            "accepted_camera_only"
            if tier != "withheld" and depth_source == "camera_size"
            else "accepted"
            if tier != "withheld"
            else "rejected_unstable_consensus"
        ),
        confidence_tier=tier,
        horizontal_deg=median_horizontal if tier != "withheld" else None,
        vertical_deg=float(np.median([estimate.vertical_deg for estimate in estimates])),
        support=len(estimates),
        support_pct=100.0 * len(estimates) / PARAMETER_SWEEP_SIZE,
        parameter_mad_deg=parameter_mad,
        window_mad_deg=window_mad,
        speed_mph=float(np.median([estimate.speed_mph for estimate in estimates])),
        speed_error_mph=float(np.median([estimate.speed_error_mph for estimate in estimates])),
        n_points=representative.n_points,
        first_frame=representative.first_frame,
        last_frame=representative.last_frame,
        depth_source=depth_source,
    )


def _angle_delta(first_deg: float, second_deg: float) -> float:
    return (first_deg - second_deg + 180.0) % 360.0 - 180.0


def select_camera_assisted_horizontal(
    estimate: CameraBallEstimate,
    *,
    iwr_horizontal_deg: float | None,
    iwr_confidence: float | None,
) -> HorizontalFusionDecision:
    """Select accepted camera output while keeping IWR as an honest fallback."""
    camera_deg = estimate.horizontal_deg
    delta = (
        _angle_delta(camera_deg, iwr_horizontal_deg)
        if camera_deg is not None and iwr_horizontal_deg is not None
        else None
    )
    if estimate.depth_source == "camera_size" and camera_deg is not None:
        return HorizontalFusionDecision(
            camera_deg,
            "camera_only_experimental",
            0.30,
            "camera_only_experimental",
            iwr_horizontal_deg,
            camera_deg,
            delta,
        )
    if estimate.confidence_tier == "high" and camera_deg is not None:
        return HorizontalFusionDecision(
            camera_deg,
            "camera_assisted_experimental",
            0.75,
            "camera_assisted_high",
            iwr_horizontal_deg,
            camera_deg,
            delta,
        )
    if estimate.confidence_tier == "experimental" and camera_deg is not None:
        agreement = delta is not None and abs(delta) <= 3.0
        return HorizontalFusionDecision(
            camera_deg,
            "camera_assisted_experimental",
            0.45 if agreement else 0.30,
            (
                "camera_assisted_experimental_agreement"
                if agreement
                else "camera_experimental_disagreement"
                if iwr_horizontal_deg is not None
                else "camera_experimental_no_iwr"
            ),
            iwr_horizontal_deg,
            camera_deg,
            delta,
        )
    if iwr_horizontal_deg is not None and abs(iwr_horizontal_deg) > MAX_IWR_FALLBACK_ABS_DEG:
        return HorizontalFusionDecision(
            None,
            None,
            None,
            "camera_withheld_iwr_implausible",
            iwr_horizontal_deg,
            camera_deg,
            delta,
        )
    return HorizontalFusionDecision(
        iwr_horizontal_deg,
        "radar" if iwr_horizontal_deg is not None else None,
        iwr_confidence if iwr_horizontal_deg is not None else None,
        "camera_withheld_fallback_iwr",
        iwr_horizontal_deg,
        camera_deg,
        delta,
    )


__all__ = [
    "BallCandidate",
    "CameraBallEstimate",
    "CameraBallGeometry",
    "HorizontalFusionDecision",
    "estimate_camera_ball_flight",
    "select_camera_assisted_horizontal",
]
