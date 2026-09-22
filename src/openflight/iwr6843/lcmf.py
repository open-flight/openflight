"""Late-Flight Complex Multipath Fusion (LCMF-v1) launch estimator.

This module is the production form of the frozen 2026-07-16 TrackMan
candidate.  The model consumes an IWR6843 L3 dump plus an independently
measured OPS243 ball speed.  Its constants are intentionally fixed: changing
them requires a new estimator version and independent validation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from openflight.iwr6843 import doa, tracking
from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.dump import is_range_snapshot, parse_dump, project_tx_pair
from openflight.iwr6843.multipath import (
    ballistic_trajectory_from_range,
    leave_one_channel_out_error,
)
from openflight.iwr6843.music import LAM
from openflight.iwr6843.shot import (
    TX2_LOOP_PERIOD_S,
    TX2_VERTICAL_TDM_TAU_S,
    PreparedShotDump,
    ShotMeasurement,
    geometry_from_header,
    impact_time_s,
    prepare_shot_dump,
    process_dump,
)
from openflight.iwr6843.tracking import BallTrack

NAME = "lcmf_v1"
DISPLAY_NAME = "Late-Flight Complex Multipath Fusion v1"
# The July 14 development session used +2.8387689102 degrees. Independent
# validation showed that offset did not transfer across physical bay alignment,
# so production exposes the fused radar estimate without a truth-fitted shift.
ANGLE_CORRECTION_DEG = 0.0
CHANNEL_MODELS = ("two8", "four4_path_tdm")
FAST_MODELS = ("direct1", "two2", "four4")
MIN_SNR = 8.0
MAX_RANGE_M = 4.7
MAX_PER_FRAME = 4
LATERAL_TEE_OFFSET_M = 0.064
MPH_PER_MS = 2.23694
HORIZONTAL_TAIL_FRAMES = 8
HORIZONTAL_COHERENCE_MIN = 0.90

# One collapsed channel must not drag the answer down. 8.0 sits in an observed
# gap: agreeing channels spread 4.59 deg, collapsed ones 15.9-20.2 deg
# (2026-07-25 session 140533).
CHANNEL_SPREAD_MAX_DEG = 8.0

# A single-channel estimate has no cross-check. 0.7 keeps it above the
# fallback-estimate confidence of 0.5 while marking it weaker than a
# corroborated one. Policy, not measurement.
SINGLE_CHANNEL_CONFIDENCE_FACTOR = 0.7


@dataclass(frozen=True)
class BallRangeEvidence:
    """Transient fitted ball trajectory shared with camera fusion."""

    track: tracking.BallTrack
    geometry: tracking.Geometry
    impact_t_s: float


@dataclass
class LCMFResult:
    """One LCMF-v1 estimate with enough evidence for session replay."""

    status: str
    angle_deg: float | None = None
    raw_angle_deg: float | None = None
    components_deg: dict[str, float] = field(default_factory=dict)
    n_snapshots: int = 0
    n_frames: int = 0
    component_std_deg: float | None = None
    channels_used: list[str] = field(default_factory=list)
    single_channel: bool = False
    tracker_quality: str | None = None
    track_speed_mph: float | None = None
    track_rms_bins: float | None = None
    track_inliers: int | None = None
    track_span_s: float | None = None
    impact_t_s: float | None = None
    tdm_sign_used: int | None = None
    horizontal_deg: float | None = None
    horizontal_raw_deg: float | None = None
    horizontal_confidence: float | None = None
    horizontal_status: str | None = None
    effective_tdm_tau_s: float = doa.TDM_TAU_S
    effective_loop_period_s: float = tracking.LOOP_PRI_S
    range_evidence: BallRangeEvidence | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    @property
    def accepted(self) -> bool:
        """Whether the estimator produced a launch angle."""
        return self.angle_deg is not None and self.status.startswith("accepted")

    def to_dict(self) -> dict:
        """Return JSON-safe estimator diagnostics."""
        return {
            "estimator": NAME,
            "status": self.status,
            "launch_angle_deg": self.angle_deg,
            "raw_angle_deg": self.raw_angle_deg,
            "angle_correction_deg": ANGLE_CORRECTION_DEG,
            "components_deg": dict(self.components_deg),
            "n_snapshots": self.n_snapshots,
            "n_frames": self.n_frames,
            "component_std_deg": self.component_std_deg,
            "channels_used": list(self.channels_used),
            "single_channel": self.single_channel,
            "tracker_quality": self.tracker_quality,
            "track_speed_mph": self.track_speed_mph,
            "track_rms_bins": self.track_rms_bins,
            "track_inliers": self.track_inliers,
            "track_span_s": self.track_span_s,
            "impact_t_s": self.impact_t_s,
            "tdm_sign_used": self.tdm_sign_used,
            "horizontal_deg": self.horizontal_deg,
            "horizontal_raw_deg": self.horizontal_raw_deg,
            "horizontal_confidence": self.horizontal_confidence,
            "horizontal_status": self.horizontal_status,
            "effective_tdm_tau_s": self.effective_tdm_tau_s,
            "effective_loop_period_s": self.effective_loop_period_s,
        }


@dataclass
class PreparedLCMFCapture:
    """Invariant signal products shared by every candidate range track."""

    full_metadata: dict
    full_cube: np.ndarray
    vertical: PreparedShotDump
    tdm_tau_s: float
    loop_period_s: float
    _horizontal_mti: np.ndarray | None = None

    def horizontal_mti(self) -> np.ndarray:
        """Return the three-TX range movie after per-frame static removal."""
        if self._horizontal_mti is None:
            metadata = self.full_metadata
            n_frames, chirps_per_frame, n_rx, n_samples = self.full_cube.shape
            loops = chirps_per_frame // metadata["n_tx"]
            tdm = self.full_cube.reshape(n_frames, loops, metadata["n_tx"], n_rx, n_samples)
            rfft = tdm if is_range_snapshot(metadata) else np.fft.fft(tdm, axis=-1)
            self._horizontal_mti = rfft - rfft.mean(axis=1, keepdims=True)
        return self._horizontal_mti


def prepare_lcmf_capture(raw: bytes) -> PreparedLCMFCapture:
    """Decode and project one capture before evaluating candidate tracks."""
    full_metadata, full_cube = parse_dump(raw)
    vertical_raw = project_tx_pair(raw, (0, 2)) if full_metadata["n_tx"] == 3 else raw
    tdm_tau_s = TX2_VERTICAL_TDM_TAU_S if full_metadata["n_tx"] == 3 else doa.TDM_TAU_S
    loop_period_s = TX2_LOOP_PERIOD_S if full_metadata["n_tx"] == 3 else tracking.LOOP_PRI_S
    return PreparedLCMFCapture(
        full_metadata=full_metadata,
        full_cube=full_cube,
        vertical=prepare_shot_dump(vertical_raw, loop_period_s=loop_period_s),
        tdm_tau_s=tdm_tau_s,
        loop_period_s=loop_period_s,
    )


def _result_from_track(
    status: str,
    shot: ShotMeasurement,
    *,
    cal: Calibration,
    effective_tdm_tau_s: float = doa.TDM_TAU_S,
    effective_loop_period_s: float = tracking.LOOP_PRI_S,
) -> LCMFResult:
    track = shot.track
    impact_t_s = impact_time_s(
        track,
        shot.geometry,
        cal.tee_range_m,
        range_bias_m=cal.range_bias_m,
    )
    return LCMFResult(
        status=status,
        tracker_quality=shot.quality,
        track_speed_mph=track.speed_mph if track is not None else None,
        track_rms_bins=track.rms_bins if track is not None else None,
        track_inliers=track.n_inliers if track is not None else None,
        track_span_s=(track.t_last - track.t_first) if track is not None else None,
        impact_t_s=impact_t_s,
        range_evidence=(
            BallRangeEvidence(track, shot.geometry, impact_t_s)
            if track is not None and impact_t_s is not None
            else None
        ),
        tdm_sign_used=shot.tdm_sign_used,
        effective_tdm_tau_s=effective_tdm_tau_s,
        effective_loop_period_s=effective_loop_period_s,
    )


def _snapshot_cache(
    raw: bytes,
    shot: ShotMeasurement,
    cal: Calibration,
    tx_order: str,
    tdm_sign: int,
    tdm_tau_s: float,
    loop_period_s: float,
    *,
    phase_velocity_ms: float | None = None,
    prepared: PreparedShotDump | None = None,
) -> tuple[dict[str, np.ndarray], object, np.ndarray]:
    """Build calibrated per-loop snapshots along the TI range track."""
    if prepared is None:
        prepared = prepare_shot_dump(raw, loop_period_s=loop_period_s)
    meta = prepared.metadata
    cube = prepared.cube
    geometry = prepared.geometry
    if meta["n_tx"] != 2:
        raise ValueError(f"LCMF-v1 requires two TX channels, got {meta['n_tx']}")

    scope = "window" if shot.notch_recovered else "burst"
    mti = prepared.mti(scope)
    noise = prepared.noise_power(scope)
    track = shot.track
    if track is None:
        raise ValueError("ball track unavailable")

    values: dict[str, list] = {
        "t": [],
        "frame": [],
        "loop": [],
        "r": [],
        "snr": [],
        "vr": [],
        "vec": [],
    }
    for frame in range(geometry.n_frames):
        for loop in range(geometry.n_loops):
            time_s = geometry.loop_time(frame, loop)
            if not track.t_first - 2e-3 <= time_s <= track.t_last + 2e-3:
                continue
            range_bin = int(round(track.bin_at(time_s)))
            local_bin = geometry.local_bin(range_bin, frame)
            if not geometry.contains_bin(range_bin, margin=1, frame=frame):
                continue
            velocity = (
                phase_velocity_ms
                if phase_velocity_ms is not None
                else track.speed_ms_at(time_s, geometry.range_res_m)
            )
            tdm_phase = tdm_sign * 4.0 * np.pi * velocity * tdm_tau_s / LAM
            uncalibrated = doa.canonicalize_tx_blocks(
                mti[frame, 0, loop, :, local_bin],
                mti[frame, 1, loop, :, local_bin],
                tdm_phase=tdm_phase,
                tx_order=tx_order,
            )
            values["t"].append(time_s)
            values["frame"].append(frame)
            values["loop"].append(loop)
            values["r"].append(cal.true_range(track.range_at(time_s, geometry.range_res_m)))
            values["snr"].append(float(np.mean(np.abs(uncalibrated) ** 2) / noise))
            values["vr"].append(velocity)
            values["vec"].append(cal.apply(uncalibrated))
    cache = {name: np.asarray(data) for name, data in values.items()}
    return cache, geometry, cube


def _balanced_indices(cache: dict[str, np.ndarray]) -> np.ndarray:
    """Select at most four strong snapshots per frame, as frozen for v1."""
    indices = np.nonzero((cache["snr"] >= MIN_SNR) & (cache["r"] <= MAX_RANGE_M))[0]
    selected: list[int] = []
    for frame in np.unique(cache["frame"][indices]):
        frame_indices = indices[cache["frame"][indices] == frame]
        order = np.argsort(cache["snr"][frame_indices])[::-1]
        selected.extend(frame_indices[order[:MAX_PER_FRAME]])
    return np.asarray(selected, dtype=int)


def _candidate_trajectory(
    launch_rad: float,
    range_m: np.ndarray,
    geometry: dict,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    return ballistic_trajectory_from_range(
        launch_rad,
        range_m,
        speed_ms=geometry["speed_ms"],
        tee_x_m=geometry["tee_x_m"],
        launch_height_m=geometry["ball_height_m"],
        radar_height_m=geometry["radar_height_m"],
        lateral_offset_m=LATERAL_TEE_OFFSET_M,
    )


def _spatial_dictionary(
    model: str,
    launch_rad: float,
    range_m: np.ndarray,
    geometry: dict,
    tdm_tau_s: float,
) -> np.ndarray:
    """Build the exact frozen DD/DG/GD/GG moving-ball dictionary."""
    tx_order = doa.validate_tx_order(geometry["tx_order"])
    x_m, height_m, direct_vr, image_vr = _candidate_trajectory(launch_rad, range_m, geometry)
    tilt_rad = geometry["tilt_rad"]
    radar_height_m = geometry["radar_height_m"]
    direct = np.arctan2(height_m - radar_height_m, x_m) - tilt_rad
    image = np.arctan2(-(height_m + radar_height_m), x_m) - tilt_rad

    tx = np.repeat(np.array([0.0, 4.0]), 4)[None, :]
    rx = np.tile(np.arange(4, dtype=float), 2)[None, :]
    sin_direct = np.sin(direct)[:, None]
    sin_image = np.sin(image)[:, None]
    dd = np.exp(1j * np.pi * (tx * sin_direct + rx * sin_direct))
    dg = np.exp(1j * np.pi * (tx * sin_direct + rx * sin_image))
    gd = np.exp(1j * np.pi * (tx * sin_image + rx * sin_direct))
    gg = np.exp(1j * np.pi * (tx * sin_image + rx * sin_image))

    if model == "four4_path_tdm":
        cross_phase = 2.0 * np.pi * (image_vr - direct_vr) * tdm_tau_s / LAM
        later = doa.later_physical_tx_index(tx_order)
        block = slice(4 * later, 4 * (later + 1))
        dg[:, block] *= np.exp(1j * cross_phase)[:, None]
        gd[:, block] *= np.exp(1j * cross_phase)[:, None]
        gg[:, block] *= np.exp(2j * cross_phase)[:, None]

    if model == "two8":
        return np.stack([dd, gg], axis=-1)
    if model in ("four4", "four4_path_tdm"):
        return np.stack([dd, dg, gd, gg], axis=-1)
    raise ValueError(f"unknown spatial model: {model}")


def _frame_objective(errors: np.ndarray, frames: np.ndarray, ceiling: float) -> float:
    frame_errors = [
        np.median(np.clip(errors[frames == frame], 1e-8, ceiling)) for frame in np.unique(frames)
    ]
    return float(np.mean(np.log(frame_errors)))


def _refine_grid(grid_deg: np.ndarray, objective: np.ndarray) -> float:
    index = int(np.argmin(objective))
    if not 0 < index < len(grid_deg) - 1:
        return float(grid_deg[index])
    y0, y1, y2 = objective[index - 1 : index + 2]
    denominator = y0 - 2.0 * y1 + y2
    offset = 0.5 * (y0 - y2) / denominator if denominator > 0 else 0.0
    return float(grid_deg[index] + np.clip(offset, -1.0, 1.0) * np.diff(grid_deg)[0])


def grid_curvature(objective: np.ndarray) -> float | None:
    """Sharpness of the objective's minimum: y0 - 2*y1 + y2 at the argmin.

    This is the channel's conditioning: a well-posed channel has a sharp
    minimum, a degenerate one a shallow one.

    Returns ``None`` when the argmin sits on a grid edge. That is a different
    failure from a shallow minimum and must not be conflated with it: an edge
    minimum means the true minimum lies outside the searched range -- a real
    launch above the grid's 45 degree ceiling pins a perfectly healthy
    channel to the last sample -- so the channel produced no measurement at
    all rather than a poor one. Scoring both cases 0.0, as this used to, let
    an edge-pinned channel tie with a collapsed one and lose on dict order.

    For an interior argmin the result is always strictly positive, so the
    ``max(0.0, ...)`` floor never fires: ``argmin`` reports the FIRST
    occurrence of the minimum, hence ``y_0 > y_1`` strictly and
    ``y_2 >= y_1``. ``combine_channels`` still treats 0.0 as "degenerate,
    unusable" because a caller may supply its own scores.
    """
    index = int(np.argmin(objective))
    if not 0 < index < len(objective) - 1:
        return None
    y_0, y_1, y_2 = objective[index - 1 : index + 2]
    return float(max(0.0, y_0 - 2.0 * y_1 + y_2))


def measured_channels(
    components: dict[str, float],
    evidence: dict[str, float | None],
) -> dict[str, float]:
    """The subset of ``components`` whose curvature is an actual measurement.

    A channel with ``None`` evidence -- or none recorded at all -- searched a
    grid that did not contain its own minimum, so its angle is not a
    measurement of anything. Such a channel takes part in nothing: not in
    selection candidacy, not in the spread comparison, and not in the
    agreement metric reported to the caller.
    """
    return {
        name: float(value) for name, value in components.items() if evidence.get(name) is not None
    }


def combine_channels(
    components: dict[str, float],
    evidence: dict[str, float | None],
) -> tuple[float | None, list[str], bool]:
    """Combine channel estimates, dropping an outlier rather than averaging it.

    ``evidence`` is each channel's objective curvature from
    ``grid_curvature`` (higher is better conditioned) and breaks the tie when
    the channels disagree. Only channels that actually measured something
    participate (see ``measured_channels``).

    Returns the angle, the channels that contributed, and whether the result
    rests on a single channel. The angle is ``None`` when no channel is
    defensible: with the channels disagreeing beyond
    ``CHANNEL_SPREAD_MAX_DEG``, selection needs strictly positive curvature
    to name a winner, and when nothing has any -- every channel flat, off the
    grid, or unscored -- picking one would be arbitrary. Rejecting is the
    honest answer; ``channels_used`` is then empty.
    """
    candidates = measured_channels(components, evidence)
    names = list(candidates)
    if not names:
        return None, [], False
    values = list(candidates.values())
    if len(names) == 1:
        return values[0], names, True
    if max(values) - min(values) <= CHANNEL_SPREAD_MAX_DEG:
        return float(sum(values) / len(values)), names, False
    conditioned = [name for name in names if float(evidence[name]) > 0.0]
    if not conditioned:
        return None, [], False
    best = max(conditioned, key=lambda name: float(evidence[name]))
    return candidates[best], [best], True


def _channel_estimates(
    cache: dict[str, np.ndarray],
    indices: np.ndarray,
    geometry: dict,
    grid_deg: np.ndarray,
) -> tuple[dict[str, float], dict[str, float | None]]:
    frames = cache["frame"][indices]
    if len(indices) < 12 or len(np.unique(frames)) < 3:
        raise ValueError("insufficient channel snapshots")
    vectors = cache["vec"][indices]
    range_m = cache["r"][indices]
    estimates: dict[str, float] = {}
    evidence: dict[str, float | None] = {}
    for model in CHANNEL_MODELS:
        objective = []
        for angle_deg in grid_deg:
            dictionary = _spatial_dictionary(
                model, np.radians(angle_deg), range_m, geometry, geometry["tdm_tau_s"]
            )
            errors = leave_one_channel_out_error(vectors, dictionary)
            objective.append(_frame_objective(errors, frames, 1e3))
        objective = np.asarray(objective)
        estimates[f"channel_{model}_deg"] = _refine_grid(grid_deg, objective)
        evidence[f"channel_{model}_deg"] = grid_curvature(objective)
    return estimates, evidence


def _prepared_fft(
    cube: np.ndarray,
    radar_geometry,
    indices: np.ndarray,
    cache: dict[str, np.ndarray],
    element_correction: np.ndarray,
    tx_order: str,
    tdm_sign: int,
    tdm_tau_s: float,
    *,
    n_fft: int = 512,
) -> tuple[np.ndarray, np.ndarray]:
    tdm = cube.reshape(
        radar_geometry.n_frames,
        radar_geometry.n_loops,
        2,
        radar_geometry.n_rx,
        radar_geometry.n_samples,
    ).transpose(0, 2, 1, 3, 4)
    tdm = tdm - tdm.mean(axis=2, keepdims=True)
    window = np.hanning(radar_geometry.n_samples)
    snapshots = []
    phase_per_ms = 4.0 * np.pi * tdm_tau_s / LAM
    for index in indices:
        frame = int(cache["frame"][index])
        loop = int(cache["loop"][index])
        snapshot = doa.canonicalize_tx_blocks(
            tdm[frame, 0, loop],
            tdm[frame, 1, loop],
            tdm_phase=tdm_sign * phase_per_ms * cache["vr"][index],
            tx_order=tx_order,
        )
        snapshot *= element_correction[:, None]
        snapshots.append(np.fft.fft(snapshot * window[None, :], n=n_fft, axis=-1))
    return np.asarray(snapshots), window


def _quadratic_peak(profile: np.ndarray, center: float, radius: int) -> float:
    center_bin = int(round(center))
    lo = max(center_bin - radius, 1)
    hi = min(center_bin + radius + 1, len(profile) - 1)
    peak = lo + int(np.argmax(profile[lo:hi]))
    y0, y1, y2 = profile[peak - 1 : peak + 2]
    denominator = y0 - 2.0 * y1 + y2
    offset = 0.5 * (y0 - y2) / denominator if denominator < 0 else 0.0
    return float(peak + np.clip(offset, -1.0, 1.0))


def _fast_design(
    model: str,
    launch_rad: float,
    range_m: np.ndarray,
    center_native_bins: np.ndarray,
    selected_bins: np.ndarray,
    geometry: dict,
    *,
    n_samples: int,
    n_fft: int,
    range_res_m: float,
    window: np.ndarray,
) -> np.ndarray:
    spatial = _spatial_dictionary("four4", launch_rad, range_m, geometry, geometry["tdm_tau_s"])
    x_m, height_m, _direct_vr, _image_vr = _candidate_trajectory(launch_rad, range_m, geometry)
    radar_height_m = geometry["radar_height_m"]
    direct_distance = np.hypot(x_m, height_m - radar_height_m)
    image_distance = np.hypot(x_m, height_m + radar_height_m)
    delta_bins = (image_distance - direct_distance) / range_res_m
    path_bins = center_native_bins[:, None] + np.stack(
        [
            np.zeros_like(delta_bins),
            0.5 * delta_bins,
            0.5 * delta_bins,
            delta_bins,
        ],
        axis=1,
    )
    sample = np.arange(n_samples)
    tones = np.exp(2j * np.pi * path_bins[:, :, None] * sample / n_samples)
    response = np.fft.fft(tones * window[None, None, :], n=n_fft, axis=-1)
    response = np.take_along_axis(response, selected_bins[:, None, :], axis=2)
    design = spatial[:, :, None, :] * response.transpose(0, 2, 1)[:, None, :, :]
    if model == "direct1":
        return design[..., [0]]
    if model == "two2":
        return design[..., [0, 3]]
    if model == "four4":
        return design
    raise ValueError(f"unknown fast-time model: {model}")


def _fit_error(data: np.ndarray, design: np.ndarray) -> np.ndarray:
    flattened_data = data.reshape(len(data), -1)
    flattened_design = design.reshape(len(data), -1, design.shape[-1])
    coefficients = np.einsum("...kn,...n->...k", np.linalg.pinv(flattened_design), flattened_data)
    prediction = np.einsum("...nk,...k->...n", flattened_design, coefficients)
    residual = np.sum(np.abs(flattened_data - prediction) ** 2, axis=1)
    power = np.sum(np.abs(flattened_data) ** 2, axis=1) + 1e-12
    return residual / power


def _fast_estimates(
    cube: np.ndarray,
    radar_geometry,
    cache: dict[str, np.ndarray],
    indices: np.ndarray,
    geometry: dict,
    cal: Calibration,
    grid_deg: np.ndarray,
    tx_order: str,
    tdm_sign: int,
) -> dict[str, float]:
    ordered = indices[np.argsort(cache["t"][indices])]
    indices = ordered[len(ordered) // 2 :]
    frames = cache["frame"][indices]
    if len(indices) < 6 or len(np.unique(frames)) < 2:
        raise ValueError("insufficient late-flight snapshots")

    n_fft = 512
    data_fft, window = _prepared_fft(
        cube,
        radar_geometry,
        indices,
        cache,
        cal.elem_correction,
        tx_order,
        tdm_sign,
        geometry["tdm_tau_s"],
        n_fft=n_fft,
    )
    oversample = n_fft / radar_geometry.n_samples
    range_m = cache["r"][indices]
    track_bins = (range_m + cal.range_bias_m) / radar_geometry.range_res_m * oversample
    power = np.sum(np.abs(data_fft) ** 2, axis=1)
    centers = np.asarray(
        [
            _quadratic_peak(profile, center, int(np.ceil(oversample)))
            for profile, center in zip(power, track_bins, strict=True)
        ]
    )
    offsets = np.arange(-int(np.ceil(2.0 * oversample)), int(np.ceil(6.0 * oversample)) + 1)
    bins = np.rint(centers).astype(int)[:, None] + offsets[None, :]
    if np.any(bins < 0) or np.any(bins >= data_fft.shape[-1]):
        raise ValueError("local range window exceeds FFT bounds")
    local_data = np.take_along_axis(data_fft, bins[:, None, :], axis=2)
    center_native = centers / oversample

    estimates: dict[str, float] = {}
    for model in FAST_MODELS:
        objective = []
        for angle_deg in grid_deg:
            design = _fast_design(
                model,
                np.radians(angle_deg),
                range_m,
                center_native,
                bins,
                geometry,
                n_samples=radar_geometry.n_samples,
                n_fft=n_fft,
                range_res_m=radar_geometry.range_res_m,
                window=window,
            )
            objective.append(_frame_objective(_fit_error(local_data, design), frames, 1.0))
        estimates[f"fast_{model}_deg"] = _refine_grid(grid_deg, np.asarray(objective))
    return estimates


def _phase_to_angle_deg(phase_rad: float) -> float:
    """Convert LEVM TX2 residual phase into a signed one-axis proxy angle."""
    return float(np.degrees(doa.tx2_phase_to_axis_angle_rad(phase_rad)))


def _weighted_circular_mean(phases: list[float], weights: list[float]) -> tuple[float, float]:
    z = np.sum(np.asarray(weights) * np.exp(1j * np.asarray(phases)))
    weight_sum = float(np.sum(weights))
    if weight_sum <= 0:
        raise ValueError("horizontal proxy has no positive weights")
    return float(np.angle(z)), float(abs(z) / weight_sum)


def _referenced_horizontal_mean(
    phases: list[float],
    weights: list[float],
    *,
    phase_reference_rad: float | None,
) -> tuple[float, float]:
    """Apply the calibrated target-line phase before angle conversion."""
    if phase_reference_rad is not None:
        if not math.isfinite(phase_reference_rad):
            raise ValueError("horizontal phase reference must be finite")
        phases = [float(np.angle(np.exp(1j * (phase - phase_reference_rad)))) for phase in phases]
    phase_rad, coherence = _weighted_circular_mean(phases, weights)
    return -_phase_to_angle_deg(phase_rad), coherence


def _frame_balanced_horizontal_mean(
    snapshots: list[tuple[float, float, float]],
    *,
    phase_reference_rad: float | None,
) -> tuple[float, float]:
    """Fuse the outgoing phase movie without letting one bright frame dominate.

    Power remains useful inside each radar frame, where the loops observe
    nearly the same ball position. Across frames, every retained instant gets
    one vote. TrackMan holdout testing on 2026-08-09 found the final eight
    observed frames materially more precise than pooling all loop snapshots.
    """
    observed_frames = sorted({frame for frame, _phase, _weight in snapshots})
    tail_frames = observed_frames[-HORIZONTAL_TAIL_FRAMES:]
    frame_phases: list[float] = []
    for frame in tail_frames:
        frame_snapshots = [snapshot for snapshot in snapshots if snapshot[0] == frame]
        phase_rad, _within_frame_coherence = _weighted_circular_mean(
            [phase for _frame, phase, _weight in frame_snapshots],
            [weight for _frame, _phase, weight in frame_snapshots],
        )
        frame_phases.append(phase_rad)
    return _referenced_horizontal_mean(
        frame_phases,
        [1.0] * len(frame_phases),
        phase_reference_rad=phase_reference_rad,
    )


def _tx2_horizontal_proxy(
    raw: bytes,
    shot: ShotMeasurement,
    *,
    tdm_sign: int,
    phase_reference_rad: float | None = None,
    phase_velocity_ms: float | None = None,
    prepared: PreparedLCMFCapture | None = None,
) -> tuple[float | None, float | None, str | None]:
    """HLCMF-v1: frame-balanced TX2 horizontal launch estimator.

    Follow the fitted ball range track, correct TDM motion, robustly median
    TX2 phase per RX channel, and power-fuse loops within each frame. The final
    eight observed frames are then fused with equal frame influence. The final
    sign is flipped into TrackMan convention: positive starts right of the
    target line, negative starts left.
    """
    if prepared is None:
        meta, cube = parse_dump(raw)
        horizontal_mti = None
    else:
        meta = prepared.full_metadata
        cube = prepared.full_cube
        horizontal_mti = prepared.horizontal_mti()
    if meta["n_tx"] != 3 or shot.track is None:
        return None, None, None
    geometry = geometry_from_header(meta, loop_period_s=TX2_LOOP_PERIOD_S)
    n_frames, chirps_per_frame, n_rx, n_samples = cube.shape
    loops = chirps_per_frame // meta["n_tx"]
    tdm = cube.reshape(n_frames, loops, meta["n_tx"], n_rx, n_samples)
    if horizontal_mti is None:
        rfft = tdm if is_range_snapshot(meta) else np.fft.fft(tdm, axis=-1)
        mti = rfft - rfft.mean(axis=1, keepdims=True)
    else:
        mti = horizontal_mti
    snapshots: list[tuple[float, float, float]] = []
    for frame in range(geometry.n_frames):
        for loop in range(geometry.n_loops):
            time_s = geometry.loop_time(frame, loop)
            if not shot.track.t_first - 2e-3 <= time_s <= shot.track.t_last + 2e-3:
                continue
            range_bin = int(round(shot.track.bin_at(time_s)))
            local_bin = geometry.local_bin(range_bin, frame)
            if not geometry.contains_bin(range_bin, margin=1, frame=frame):
                continue
            velocity = (
                phase_velocity_ms
                if phase_velocity_ms is not None
                else shot.track.speed_ms_at(time_s, geometry.range_res_m)
            )
            sample = doa.tx2_phase_at(
                mti,
                frame,
                loop,
                local_bin,
                velocity_ms=velocity,
                tdm_sign=tdm_sign,
                n_rx=n_rx,
            )
            if sample is not None:
                phase, weight = sample
                snapshots.append((float(frame), phase, weight))
    if len(snapshots) < 6:
        return None, None, "hlcmf_v1_insufficient_tail_snapshots"
    angle_deg, coherence = _frame_balanced_horizontal_mean(
        snapshots,
        phase_reference_rad=phase_reference_rad,
    )
    if coherence < HORIZONTAL_COHERENCE_MIN:
        return None, coherence, "hlcmf_v1_low_coherence"
    return angle_deg, coherence, "hlcmf_v1_accepted"


def estimate_lcmf_v1(
    raw: bytes,
    cal: Calibration,
    *,
    ball_speed_mph: float,
    club: str | None = None,
    net_range_m: float | None = None,
    tx_order: str = "normal",
    tdm_sign_policy: str = "positive",
    grid_step_deg: float = 0.5,
    horizontal_phase_reference_rad: float | None = None,
    track_override: BallTrack | None = None,
    track_override_scope: str = "burst",
    prepared: PreparedLCMFCapture | None = None,
) -> LCMFResult:
    """Estimate vertical launch from one TI dump and OPS ball speed.

    ``track_override`` is an offline-research hook for evaluating an
    independently selected range walk. Normal production calls leave it
    unset and retain the frozen LCMF-v1 behavior.
    """
    if ball_speed_mph <= 0:
        raise ValueError("ball_speed_mph must be positive")
    if cal.tee_range_m is None:
        raise ValueError("LCMF-v1 requires the measured tee slant range")
    full_raw = raw
    if prepared is None:
        prepared = prepare_lcmf_capture(raw)
    meta = prepared.full_metadata
    tdm_tau_s = prepared.tdm_tau_s
    loop_period_s = prepared.loop_period_s
    vertical = prepared.vertical
    recovery_override = track_override is not None
    if recovery_override:
        if track_override_scope not in ("burst", "window"):
            raise ValueError("track_override_scope must be burst or window")
        if tdm_sign_policy not in ("positive", "negative"):
            raise ValueError("track_override requires an explicit TDM sign policy")
        shot = ShotMeasurement(
            geometry=vertical.geometry,
            ball_found=True,
            track=track_override,
            club=club,
            tx_order=tx_order,
            tdm_sign_policy=tdm_sign_policy,
            quality="low",
            notch_recovered=track_override_scope == "window",
            tdm_sign_used=1 if tdm_sign_policy == "positive" else -1,
        )
    else:
        shot = process_dump(
            raw,
            cal,
            club=club,
            net_range_m=net_range_m,
            tx_order=tx_order,
            tdm_sign_policy=tdm_sign_policy,
            loop_period_s=loop_period_s,
            tdm_tau_s=tdm_tau_s,
            prepared=vertical,
        )
    if shot.track is None:
        return _result_from_track(
            "rejected_by_ball_tracker",
            shot,
            cal=cal,
            effective_tdm_tau_s=tdm_tau_s,
            effective_loop_period_s=loop_period_s,
        )
    if shot.quality == "reject":
        return _result_from_track(
            "rejected_track_quality",
            shot,
            cal=cal,
            effective_tdm_tau_s=tdm_tau_s,
            effective_loop_period_s=loop_period_s,
        )
    if shot.tdm_sign_used not in (-1, 1):
        return _result_from_track(
            "rejected_missing_tdm_sign",
            shot,
            cal=cal,
            effective_tdm_tau_s=tdm_tau_s,
            effective_loop_period_s=loop_period_s,
        )

    try:
        # OPS measures radial speed directly. TI range slope still supplies
        # position, but multipath-biased slope must not rotate TDM phase.
        phase_velocity_ms = ball_speed_mph / MPH_PER_MS
        horizontal_deg, horizontal_conf, horizontal_status = _tx2_horizontal_proxy(
            full_raw,
            shot,
            tdm_sign=shot.tdm_sign_used,
            phase_reference_rad=horizontal_phase_reference_rad,
            phase_velocity_ms=phase_velocity_ms,
            prepared=prepared,
        )
        cache, radar_geometry, cube = _snapshot_cache(
            raw,
            shot,
            cal,
            tx_order,
            shot.tdm_sign_used,
            tdm_tau_s,
            loop_period_s,
            phase_velocity_ms=phase_velocity_ms,
            prepared=vertical,
        )
        indices = _balanced_indices(cache)
        vertical_delta_m = cal.tee_ball_height_m - cal.radar_height_m
        tee_x_m = math.sqrt(max(cal.tee_range_m**2 - vertical_delta_m**2, 0.25))
        model_geometry = {
            "speed_ms": ball_speed_mph / MPH_PER_MS,
            "tee_x_m": tee_x_m,
            "ball_height_m": cal.tee_ball_height_m,
            "radar_height_m": cal.radar_height_m,
            "tilt_rad": cal.tilt_rad,
            "tx_order": tx_order,
            "tdm_tau_s": tdm_tau_s,
        }
        grid_deg = np.arange(-5.0, 45.0 + grid_step_deg / 2.0, grid_step_deg)
        channel_components, channel_evidence = _channel_estimates(
            cache, indices, model_geometry, grid_deg
        )
        # ``components`` is the diagnostic record for the session log;
        # ``channel_components`` is what the answer is allowed to rest on. The
        # fast-time models are unvalidated against curvature evidence and
        # cannot be selected, so letting them into the agreement comparison
        # would only ever be able to veto a real corroboration -- an outlier
        # among them widens the spread past the gate, dropping two agreeing
        # channels to one. They stay logged and stay out of the decision.
        components = dict(channel_components)
        if not is_range_snapshot(meta):
            fast = _fast_estimates(
                cube,
                radar_geometry,
                cache,
                indices,
                model_geometry,
                cal,
                grid_deg,
                tx_order,
                shot.tdm_sign_used,
            )
            components.update(fast)
    except (ValueError, IndexError, np.linalg.LinAlgError) as error:
        return _result_from_track(
            str(error).replace(" ", "_"),
            shot,
            cal=cal,
            effective_tdm_tau_s=tdm_tau_s,
            effective_loop_period_s=loop_period_s,
        )

    raw_angle_deg, channels_used, single_channel = combine_channels(
        channel_components, channel_evidence
    )
    if raw_angle_deg is None:
        return _result_from_track(
            "rejected_no_conditioned_channel",
            shot,
            cal=cal,
            effective_tdm_tau_s=tdm_tau_s,
            effective_loop_period_s=loop_period_s,
        )
    measured = measured_channels(channel_components, channel_evidence)
    status = "accepted_low_confidence_recovery" if recovery_override else "accepted"
    result = _result_from_track(status, shot, cal=cal)
    result.angle_deg = raw_angle_deg + ANGLE_CORRECTION_DEG
    result.raw_angle_deg = raw_angle_deg
    result.components_deg = components
    result.n_snapshots = len(indices)
    result.n_frames = len(np.unique(cache["frame"][indices]))
    # Agreement is between the channels that measured something and could have
    # been selected -- the same set combine_channels compared. A diagnostic
    # component must not be able to make a corroborated estimate look noisy.
    result.component_std_deg = float(np.std(list(measured.values())))
    result.channels_used = channels_used
    result.single_channel = single_channel
    result.horizontal_deg = horizontal_deg
    result.horizontal_confidence = horizontal_conf
    result.horizontal_status = horizontal_status
    result.effective_tdm_tau_s = tdm_tau_s
    result.effective_loop_period_s = loop_period_s
    return result


__all__ = [
    "ANGLE_CORRECTION_DEG",
    "BallRangeEvidence",
    "DISPLAY_NAME",
    "LCMFResult",
    "NAME",
    "estimate_lcmf_v1",
    "prepare_lcmf_capture",
]
