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
    detect_impact_frame,
    detect_impact_reference_ball,
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
    median_residual_px: float | None = None
    median_horizontal_residual_px: float | None = None
    median_vertical_residual_px: float | None = None
    calibration_span_deg: float | None = None
    jackknife_mad_deg: float | None = None
    profile_span_deg: float | None = None
    impact_delay_ms: float | None = None


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


@dataclass(frozen=True)
class _HorizontalFit:
    horizontal_deg: float
    median_residual_px: float
    median_horizontal_residual_px: float
    median_vertical_residual_px: float
    profile_span_deg: float
    impact_delay_ms: float


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


def _clean_launch_path(
    path: list[tuple[int, BallCandidate]],
    frame_indices: list[int],
) -> dict[int, BallCandidate]:
    points = [(frame_indices[relative], candidate) for relative, candidate in path]
    for _ in range(2):
        if len(points) < 4:
            break
        slopes_x = []
        slopes_y = []
        for first_index, (first_frame, first) in enumerate(points):
            for second_frame, second in points[first_index + 1 :]:
                delta = second_frame - first_frame
                if delta > 0:
                    slopes_x.append((second.x - first.x) / delta)
                    slopes_y.append((second.y - first.y) / delta)
        velocity_x = statistics.median(slopes_x)
        velocity_y = statistics.median(slopes_y)
        intercept_x = statistics.median(
            candidate.x - velocity_x * frame for frame, candidate in points
        )
        intercept_y = statistics.median(
            candidate.y - velocity_y * frame for frame, candidate in points
        )
        residuals = [
            math.hypot(
                candidate.x - (intercept_x + velocity_x * frame),
                candidate.y - (intercept_y + velocity_y * frame),
            )
            for frame, candidate in points
        ]
        kept = [point for point, residual in zip(points, residuals) if residual <= 6.0]
        if len(kept) == len(points) or len(kept) < 4:
            break
        points = kept

    result = dict(points)
    for (first_frame, first), (second_frame, second) in zip(points, points[1:]):
        if second_frame - first_frame != 2:
            continue
        result[first_frame + 1] = BallCandidate(
            x=(first.x + second.x) / 2.0,
            y=(first.y + second.y) / 2.0,
            area=round((first.area + second.area) / 2.0),
            width=round((first.width + second.width) / 2.0),
            height=round((first.height + second.height) / 2.0),
            fill=(first.fill + second.fill) / 2.0,
            circularity=(first.circularity + second.circularity) / 2.0,
            mean_intensity=(first.mean_intensity + second.mean_intensity) / 2.0,
        )
    return result


def _track_launch_ball(
    frames: np.ndarray,
    *,
    trigger_frame: int,
    reference_ball: ReferenceBall,
) -> dict[int, BallCandidate]:
    background = np.median(frames[: min(20, len(frames))], axis=0).astype(np.uint8)
    frame_indices = list(range(trigger_frame, min(len(frames), trigger_frame + 15)))
    nodes = [
        _candidates(
            frames[frame],
            background,
            reference_ball,
            bright_threshold=130,
            difference_threshold=18,
            min_area=10,
        )
        for frame in frame_indices
    ]
    options = []
    for path in _pixel_paths(nodes, reference_ball):
        cleaned = _clean_launch_path(path, frame_indices)
        if len(cleaned) < 4:
            continue
        ordered = sorted(cleaned.items())
        if ordered[0][1].y - ordered[-1][1].y < 20.0:
            continue
        options.append((_rough_path_score(path), cleaned))
    return max(options, key=lambda item: item[0])[1] if options else {}


def _project_world_points(
    points: np.ndarray,
    *,
    model: tuple[float, float, np.ndarray],
    geometry: CameraBallGeometry,
) -> np.ndarray:
    focal_px, pitch, _radar_from_camera = model
    vectors = points - geometry.camera_origin
    camera_forward = math.cos(pitch) * vectors[..., 1] + math.sin(pitch) * vectors[..., 2]
    camera_vertical = -math.sin(pitch) * vectors[..., 1] + math.cos(pitch) * vectors[..., 2]
    horizontal = vectors[..., 0] / camera_forward
    vertical = camera_vertical / camera_forward
    roll = math.radians(geometry.roll_correction_deg)
    raw_horizontal = math.cos(roll) * horizontal - math.sin(roll) * vertical
    raw_vertical = math.sin(roll) * horizontal + math.cos(roll) * vertical
    return np.stack(
        (
            geometry.image_width_px / 2.0
            + focal_px * raw_horizontal / geometry.horizontal_pixel_sign,
            geometry.image_height_px / 2.0 - focal_px * raw_vertical,
        ),
        axis=-1,
    )


def _fit_horizontal_launch(  # pylint: disable=too-many-arguments
    track: dict[int, BallCandidate],
    timestamps_ns: np.ndarray,
    *,
    impact_frame: int,
    reference_ball: ReferenceBall,
    geometry: CameraBallGeometry,
    speed_mph: float,
    vertical_deg: float,
) -> _HorizontalFit | None:
    ordered = sorted(track.items())
    if len(ordered) < 4:
        return None
    frames = np.asarray([frame for frame, _candidate in ordered], dtype=int)
    observed = np.asarray([[candidate.x, candidate.y] for _frame, candidate in ordered])
    elapsed = (timestamps_ns[frames].astype(np.int64) - int(timestamps_ns[impact_frame])) / 1e9
    if np.any(elapsed <= 0.0):
        return None

    model = _camera_model(reference_ball, geometry)
    tee = np.array([0.0, geometry.ball_forward_m, geometry.ball_height_m])
    projected_tee = _project_world_points(tee[None, :], model=model, geometry=geometry)[0]
    image_alignment = np.array([reference_ball.x, reference_ball.y]) - projected_tee
    speed_ms = speed_mph / MPH_PER_MS
    vertical = math.radians(vertical_deg)
    target_angles = np.linspace(-20.0, 20.0, 801)
    camera_angles = np.radians(target_angles - geometry.horizontal_offset_deg)
    velocities = speed_ms * np.stack(
        (
            math.cos(vertical) * np.sin(camera_angles),
            math.cos(vertical) * np.cos(camera_angles),
            np.full_like(camera_angles, math.sin(vertical)),
        ),
        axis=-1,
    )
    median_interval = float(np.median(np.diff(timestamps_ns.astype(np.int64))) / 1e9)
    max_impact_delay = min(
        0.015,
        max(0.0, float(elapsed[0]) - max(median_interval * 0.5, 0.0005)),
    )
    best: tuple[float, int, float, np.ndarray] | None = None
    profile = np.full(len(target_angles), np.inf)
    for impact_delay in np.linspace(0.0, max_impact_delay, 31):
        times = elapsed - impact_delay
        if np.any(times <= 0.0):
            continue
        positions = tee + velocities[:, None, :] * times[None, :, None]
        positions[..., 2] -= 4.903325 * times[None, :] ** 2
        projected = _project_world_points(positions, model=model, geometry=geometry)
        projected += image_alignment
        residual = projected - observed
        distance = np.sqrt(residual[..., 0] ** 2 + 0.25 * residual[..., 1] ** 2)
        losses = np.median(distance, axis=1)
        profile = np.minimum(profile, losses)
        angle_index = int(np.argmin(losses))
        candidate = (float(losses[angle_index]), angle_index, impact_delay, residual[angle_index])
        if best is None:
            best = candidate
        elif candidate[0] < best[0]:
            best = candidate
    if best is None:
        return None
    loss, angle_index, impact_delay, residual = best
    supported_angles = target_angles[profile <= loss + 0.5]
    profile_span = (
        float(supported_angles[-1] - supported_angles[0]) if len(supported_angles) else 40.0
    )
    return _HorizontalFit(
        horizontal_deg=float(target_angles[angle_index]),
        median_residual_px=float(np.median(np.linalg.norm(residual, axis=1))),
        median_horizontal_residual_px=float(np.median(np.abs(residual[:, 0]))),
        median_vertical_residual_px=float(np.median(np.abs(residual[:, 1]))),
        profile_span_deg=profile_span,
        impact_delay_ms=impact_delay * 1000.0,
    )


def _scaled_reference_ball(ball: ReferenceBall, scale: float) -> ReferenceBall:
    return ReferenceBall(
        x=ball.x,
        y=ball.y,
        diameter_px=ball.diameter_px * scale,
        area_px=max(1, round(ball.area_px * scale * scale)),
    )


# pylint: disable-next=too-many-locals,too-many-arguments,too-many-return-statements,too-many-branches
def estimate_horizontal_launch(
    frames: np.ndarray,
    timestamps_ns: np.ndarray,
    *,
    trigger_ns: int,
    geometry: CameraBallGeometry,
    ops_ball_speed_mph: float | None,
    vertical_deg: float | None,
    vertical_source: str | None,
    reference_ball: ReferenceBall | None = None,
) -> CameraBallEstimate:
    """Estimate horizontal launch using tracked centroids and measured vertical launch."""
    if frames.ndim != 3 or len(frames) < 4 or len(timestamps_ns) != len(frames):
        return CameraBallEstimate("rejected_invalid_camera_frames")
    if vertical_source != "radar":
        return CameraBallEstimate("withheld_vertical_not_radar")
    if vertical_deg is None or not 18.0 <= vertical_deg <= 55.0:
        return CameraBallEstimate("withheld_vertical_out_of_bounds")
    if ops_ball_speed_mph is None or not 35.0 <= ops_ball_speed_mph <= 130.0:
        return CameraBallEstimate("withheld_ball_speed_out_of_bounds")

    timestamps = np.asarray(timestamps_ns, dtype=np.int64)
    if timestamps.shape != (len(frames),):
        return CameraBallEstimate("rejected_invalid_camera_timing")
    trigger_frame = int(np.argmin(np.abs(timestamps - trigger_ns)))
    ball = reference_ball
    if ball is None:
        try:
            ball = detect_impact_reference_ball(frames, trigger_frame_index=trigger_frame)
        except ValueError:
            return CameraBallEstimate("withheld_reference_ball_not_found")
    if not 9.0 <= ball.diameter_px <= 30.0:
        return CameraBallEstimate("withheld_reference_ball_out_of_bounds")
    impact_frame = detect_impact_frame(
        frames,
        ball,
        trigger_frame_index=trigger_frame,
    )
    if impact_frame is None:
        return CameraBallEstimate("withheld_impact_not_found")
    track = _track_launch_ball(
        frames,
        trigger_frame=trigger_frame,
        reference_ball=ball,
    )
    support = len(track)
    if support < 6:
        return CameraBallEstimate("withheld_insufficient_track", support=support, n_points=support)

    def fit(
        *,
        selected_track: dict[int, BallCandidate] | None = None,
        selected_ball: ReferenceBall | None = None,
        speed: float | None = None,
        vertical: float | None = None,
    ) -> _HorizontalFit | None:
        return _fit_horizontal_launch(
            track if selected_track is None else selected_track,
            timestamps,
            impact_frame=impact_frame,
            reference_ball=ball if selected_ball is None else selected_ball,
            geometry=geometry,
            speed_mph=ops_ball_speed_mph if speed is None else speed,
            vertical_deg=vertical_deg if vertical is None else vertical,
        )

    base = fit()
    if base is None:
        return CameraBallEstimate("withheld_fit_failed", support=support, n_points=support)
    sensitivity_fits = [
        fit(selected_ball=_scaled_reference_ball(ball, scale)) for scale in (0.925, 1.075)
    ]
    sensitivity_fits.extend(
        fit(speed=speed, vertical=vertical)
        for speed, vertical in (
            (ops_ball_speed_mph, vertical_deg - 1.5),
            (ops_ball_speed_mph, vertical_deg + 1.5),
            (ops_ball_speed_mph - 2.0, vertical_deg),
            (ops_ball_speed_mph + 2.0, vertical_deg),
        )
    )
    sensitivity_angles = [
        candidate.horizontal_deg for candidate in sensitivity_fits if candidate is not None
    ]
    calibration_span = (
        max([base.horizontal_deg, *sensitivity_angles])
        - min([base.horizontal_deg, *sensitivity_angles])
        if len(sensitivity_angles) == len(sensitivity_fits)
        else math.inf
    )
    jackknife_angles = []
    for omitted in track:
        candidate = fit(
            selected_track={frame: point for frame, point in track.items() if frame != omitted}
        )
        if candidate is not None:
            jackknife_angles.append(candidate.horizontal_deg)
    jackknife_mad = (
        float(np.median(np.abs(np.asarray(jackknife_angles) - np.median(jackknife_angles))))
        if len(jackknife_angles) == support
        else math.inf
    )
    gates = (
        (abs(base.horizontal_deg) <= 15.0, "angle_out_of_bounds"),
        (base.median_horizontal_residual_px <= 1.5, "horizontal_residual"),
        (base.median_vertical_residual_px <= 6.0, "vertical_residual"),
        (calibration_span <= 2.0, "calibration_sensitivity"),
        (jackknife_mad <= 0.5, "jackknife_instability"),
        (base.profile_span_deg <= 3.0, "weak_identifiability"),
    )
    failure = next((reason for passed, reason in gates if not passed), None)
    ordered_frames = sorted(track)
    return CameraBallEstimate(
        status=(
            "accepted_horizontal_only_experimental" if failure is None else f"withheld_{failure}"
        ),
        confidence_tier="experimental" if failure is None else "withheld",
        horizontal_deg=base.horizontal_deg if failure is None else None,
        vertical_deg=vertical_deg,
        support=support,
        support_pct=min(100.0, 100.0 * support / 15.0),
        n_points=support,
        first_frame=ordered_frames[0],
        last_frame=ordered_frames[-1],
        depth_source="ops_speed_iwr_vertical",
        median_residual_px=base.median_residual_px,
        median_horizontal_residual_px=base.median_horizontal_residual_px,
        median_vertical_residual_px=base.median_vertical_residual_px,
        calibration_span_deg=calibration_span,
        jackknife_mad_deg=jackknife_mad,
        profile_span_deg=base.profile_span_deg,
        impact_delay_ms=base.impact_delay_ms,
    )


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
    "estimate_horizontal_launch",
    "select_camera_assisted_horizontal",
]
