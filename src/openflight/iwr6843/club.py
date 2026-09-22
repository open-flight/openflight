"""Club path from the IWR6843's pre-impact frames.

Club path is the horizontal direction the club head travels through impact.
It is a DIRECTION, so it needs at least two spatial samples over time; a
single azimuth reading cannot produce it.

This estimator converts each snapshot to a Cartesian position (x along
boresight, y cross-range) and fits BOTH components linearly against time,
rather than fitting the azimuth angle itself. For a target moving in a
straight line at constant velocity -- the club, over a ~24 ms pre-impact
window -- x(t) and y(t) are each exactly linear in time, while azimuth(t)
and range(t) individually are not. An angle-rate fit's slope is therefore a
window-averaged rate (through the fit's own weighted-mean time, not the
impact instant), and no choice of range-evaluation point can turn that
average back into the instantaneous rate at impact -- confirmed on this
module's own synthetic fixture (2026-07-25): a fitted azimuth rate of
106.4 deg/s against a true instantaneous rate of 64.1 deg/s at impact and
102.5 deg/s at the fit's own mean time. Fitting x and y directly sidesteps
the problem rather than correcting for it: path_deg = atan2(v_y, v_x) is
then exact for straight-line motion, independent of where in the window the
samples happen to sit.

The LEVM's board-specific electrical phase zero is removed first via
``phase_reference_rad``. Installation yaw remains a separate additive
``aim_offset_deg``. Keeping those corrections separate is important: a
constant phase bias rotates every recovered Cartesian position, so it also
rotates the fitted velocity direction and does not cancel out of club path.
"""

from __future__ import annotations

import logging
import math
from bisect import bisect_right
from dataclasses import dataclass, field

import numpy as np

from openflight.iwr6843 import doa, tracking, trajectory
from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.dump import parse_dump
from openflight.iwr6843.shot import (
    TX2_LOOP_PERIOD_S,
    geometry_from_header,
    is_range_snapshot,
    project_tx_pair,
)

logger = logging.getLogger(__name__)

MPH_PER_MS = 2.23694

# The search gate is ASYMMETRIC about the tee, because the clubhead cannot be
# beyond the ball before it strikes the ball. Admitting the post-tee region
# lets most-inliers RANSAC prefer a slow body/hands mover (~10 m/s, projection
# 0.28-0.32) over the real clubhead (~30 m/s): with a symmetric +/-0.6 m gate
# that happened on 5 of the 14 2026-07-25 captures, and the club was found on
# only 12. Capping at the tee finds it on 14 of 14.
CLUB_APPROACH_DEPTH_M = 0.6  # observed approach spans 0.90-1.33 m at a 1.372 m tee
CLUB_GATE_TEE_MARGIN_M = 0.05  # ~1 range bin, so samples at the tee are not clipped

# Radial speed bounds. Measured pre-impact: 24.7-37.9 m/s for 66-88 mph clubs.
# The 18.0 floor is a second, independent guard against the slow body mover
# above; it is well below the slowest observed club (24.7 m/s) but above that
# mover. NOTE: this floor assumes a full swing. A club under ~40 mph would be
# excluded, so putting and short chipping are out of scope for club path.
CLUB_SPEED_BOUNDS_MS = (18.0, 45.0)

CLUB_MIN_FRAMES = 4
CLUB_MIN_SNAPSHOTS = 24

# How many frames of approach history to fit, counting BACK from impact -- not
# a ring slot. The clubhead's useful range walk spans 0.90-1.33 m (measured,
# 2026-07-25), which it covers in 9-16 ms. Six frames provide 12 ms of history
# with the hybrid firmware's dense 2 ms pre-impact cadence, or 24 ms with the
# older 4 ms cadence. Reaching further back adds samples from below the search
# gate's 0.772 m floor, where the club is still swinging down and around.
PRE_IMPACT_FRAMES = 6
ATTACK_PRE_IMPACT_FRAMES = 4

# Provisional. Cannot be derived analytically; set from the observed residual
# distribution across local captures during bring-up.
CLUB_MAX_AZIMUTH_FIT_RESIDUAL_DEG = 0.5

# The track measures RADIAL speed = true club speed x an unknown projection
# factor, so the identity gate is a projection window, not a tolerance. A
# symmetric tolerance would either reject every valid shot or admit anything.
#
# Measured pre-impact across the 14 captures of 2026-07-25: 0.81-1.25, with 12
# of 14 inside (0.70, 1.00). The clubhead travels almost straight down the
# boresight at impact, hence the cluster near 1. Values above 1.0 are not
# physical -- radial speed cannot exceed total speed -- so the headroom to 1.20
# is an error budget for the OPS club-speed reading, not a real projection.
#
# The ceiling cannot go much higher: the BALL's radial speed is 40-49 m/s
# against a 30-39 m/s club, i.e. a ratio of ~1.2-1.3, so a looser ceiling would
# start admitting the ball. PROVISIONAL on one session; revalidate at the next.
CLUB_SPEED_PROJECTION_RANGE = (0.70, 1.20)

# The OPS transition selector measures the club-head branch near impact. Use
# that result while selecting the IWR range walk, not only as a rejection after
# the tracker has already chosen hands or shaft. The preferred ratio is a
# fastest-credible threshold rather than an equality target because IWR sees
# radial speed while OPS estimates total club speed.
CLUB_PREFERRED_SPEED_RATIO = 0.82
CLUB_TRACK_SPEED_LIMIT_MS = (12.0, 60.0)

# A real club track must extrapolate to the known ball position at impact.
# Four 4.7 cm range bins leave room for range quantisation and a slightly
# imperfect impact timestamp without admitting a mover that misses the tee.
CLUB_MAX_IMPACT_ERROR_M = 0.20

# Ceiling on the azimuth swing across the window, measured on PER-FRAME medians
# (see phase_span_rad). Tangential speed is at most ~20 m/s at a ~1.1 m range,
# so the azimuth rate stays under ~18 rad/s; over six 4 ms frames that is
# ~0.44 rad of azimuth, i.e. ~1.3 rad of lambda/2 baseline phase. pi/2 sits
# just above that.
CLUB_MAX_PHASE_SPAN_RAD = math.pi / 2

# How far one snapshot may sit from its own frame's circular median before it
# is discarded. The clubhead's azimuth barely moves within a single 1.08 ms
# burst, so real scatter here is noise, not signal.
CLUB_MAX_PHASE_DEVIATION_RAD = 0.6


@dataclass(frozen=True)
class ClubRangeEvidence:
    """Transient club range trajectory shared with camera fusion."""

    track: tracking.BallTrack
    geometry: tracking.Geometry
    impact_t_s: float


@dataclass
class ClubPathResult:
    """One club-path estimate with the evidence behind it."""

    status: str
    path_deg: float | None = None
    candidate_path_deg: float | None = None
    candidate_path_status: str | None = None
    candidate_path_fit_residual_deg: float | None = None
    candidate_attack_angle_deg: float | None = None
    attack_angle_status: str | None = None
    attack_fit_rms_m: float | None = None
    attack_n_points: int = 0
    confidence: float | None = None
    azimuth_rate_dps: float | None = None
    range_rate_ms: float | None = None
    club_range_m: float | None = None
    n_frames: int = 0
    n_snapshots: int = 0
    n_rejected_snapshots: int = 0
    phase_span_rad: float | None = None
    fit_residual_deg: float | None = None
    track_rms_bins: float | None = None
    track_inliers: int | None = None
    track_span_s: float | None = None
    ops_club_speed_mph: float | None = None
    track_speed_ratio: float | None = None
    track_impact_error_m: float | None = None
    track_selection_mode: str | None = None
    attack_pre_frames: int = 0
    attack_post_frames: int = 0
    attack_post_speed_scale: float = 1.0
    path_pre_frames: int = 0
    path_post_frames: int = 0
    path_post_speed_scale: float = 1.0
    # Kept in memory only. Session JSON already records the scalar track
    # diagnostics above; serializing the fitted object would couple replay
    # files to Python implementation details.
    range_evidence: ClubRangeEvidence | None = field(default=None, repr=False)

    @property
    def accepted(self) -> bool:
        """Whether a club path was produced."""
        return self.path_deg is not None and self.status.startswith("accepted")

    def to_dict(self) -> dict:
        """JSON-safe diagnostics for the session log."""
        payload = vars(self).copy()
        payload.pop("range_evidence", None)
        return payload


@dataclass
class ClubTrackSelection:
    """Club range track plus the identity evidence used to select it."""

    track: tracking.BallTrack
    mode: str
    speed_ratio: float
    impact_error_m: float


@dataclass(frozen=True)
class ClubWindowPolicy:
    """Independent temporal windows for experimental club measurements.

    Post-impact samples need a separate kinematic segment because impact can
    reduce clubhead speed. ``path_post_speed_scale`` is deliberately explicit:
    it is an experimental replay parameter, not a hidden calibration.
    """

    attack_pre_frames: int = ATTACK_PRE_IMPACT_FRAMES
    attack_post_frames: int = 0
    attack_post_speed_scale: float = 1.0
    path_pre_frames: int = ATTACK_PRE_IMPACT_FRAMES
    path_post_frames: int = 0
    path_post_speed_scale: float = 1.0

    def __post_init__(self) -> None:
        frame_counts = (
            self.attack_pre_frames,
            self.attack_post_frames,
            self.path_pre_frames,
            self.path_post_frames,
        )
        if any(not isinstance(value, int) or value < 0 for value in frame_counts):
            raise ValueError("club window frame counts must be non-negative integers")
        speed_scales = (self.attack_post_speed_scale, self.path_post_speed_scale)
        if any(not 0.0 < value <= 1.25 for value in speed_scales):
            raise ValueError("club post-impact speed scales must be in (0, 1.25]")


@dataclass
class _ImpactSegmentedTrack:
    """BallTrack-compatible club trajectory with continuous impact position."""

    base: tracking.BallTrack
    impact_t_s: float
    range_res_m: float
    post_speed_scale: float
    t_first: float
    t_last: float

    def _post_speed_ms(self) -> float:
        return self.base.speed_ms_at(self.impact_t_s, self.range_res_m) * self.post_speed_scale

    def bin_at(self, t_s):
        """Return continuous range-bin position across the impact boundary."""
        times = np.asarray(t_s)
        before = self.base.bin_at(times)
        impact_bin = self.base.bin_at(self.impact_t_s)
        after = impact_bin + self._post_speed_ms() * (times - self.impact_t_s) / self.range_res_m
        result = np.where(times <= self.impact_t_s, before, after)
        return float(result) if result.ndim == 0 else result

    def range_at(self, t_s, range_res_m: float):
        """Return apparent range using the caller's range resolution."""
        return self.bin_at(t_s) * range_res_m

    def speed_ms_at(self, t_s, range_res_m: float):
        """Return measured pre-impact speed or the explicit post-impact segment."""
        del range_res_m
        times = np.asarray(t_s)
        before = self.base.speed_ms_at(times, self.range_res_m)
        after = np.full_like(times, self._post_speed_ms(), dtype=float)
        result = np.where(times <= self.impact_t_s, before, after)
        return float(result) if result.ndim == 0 else result


def pre_impact_window_s(
    geo: tracking.Geometry,
    impact_t_s: float | None,
    *,
    n_frames: int = PRE_IMPACT_FRAMES,
) -> tuple[float, float] | None:
    """The ``n_frames`` of approach history ending at impact.

    Anchored to the measured impact instant, NOT to a ring slot. The freeze is
    requested by a UART command, so the trigger frame lands late by a variable
    2-4 frames; the previous slot-anchored window returned
    ``(0, n_frames * period)`` and therefore straddled impact on every shot
    measured, handing the estimator the follow-through instead of the approach.

    Returns None when there is no impact instant, or when impact sits at or
    before the start of the ring so no approach history was captured. A window
    shorter than ``n_frames`` is returned rather than rejected: judging whether
    it holds enough evidence is the job of the ``CLUB_MIN_FRAMES`` and
    ``CLUB_MIN_SNAPSHOTS`` gates, which record what they rejected.
    """
    if impact_t_s is None or impact_t_s <= 0.0:
        return None
    if impact_t_s > geo.capture_duration_s:
        return None
    return (max(0.0, impact_t_s - n_frames * geo.frame_period_s), float(impact_t_s))


def _frame_medians(phases: np.ndarray, frames: np.ndarray) -> list[tuple[int, float]]:
    """Each frame's circular median phase, in frame order."""
    return [
        (int(frame), doa.circular_median(list(phases[frames == frame])))
        for frame in np.unique(frames)
    ]


def phase_outlier_mask(phases: np.ndarray, frames: np.ndarray) -> np.ndarray:
    """Keep snapshots within CLUB_MAX_PHASE_DEVIATION_RAD of their frame median.

    Judged per frame, never globally: the clubhead's azimuth genuinely drifts
    from frame to frame -- that drift IS the signal -- while within one 1.08 ms
    burst it barely moves, so spread inside a frame is noise. Circular
    differences, so samples straddling +/-pi read as neighbours.
    """
    keep = np.zeros(phases.shape, dtype=bool)
    for frame, median in _frame_medians(phases, frames):
        selector = frames == frame
        deviation = np.abs(np.angle(np.exp(1j * (phases[selector] - median))))
        keep[selector] = deviation <= CLUB_MAX_PHASE_DEVIATION_RAD
    return keep


def phase_span_rad(phases: np.ndarray, frames: np.ndarray) -> float:
    """Total azimuth swing across the window, from per-frame medians.

    Deliberately NOT the range of the raw samples, and deliberately not an
    unwrapped range. A lambda/2 baseline carries no information beyond +/-pi,
    so unwrapping fabricates angles and turns per-snapshot noise into
    cumulative drift -- which is what rejected 4 of the 14 2026-07-25 shots
    with apparent swings of 1.0-17.3 rad. Successive frame medians are close
    together, so accumulating their circular differences is well posed.
    """
    medians = [median for _frame, median in _frame_medians(phases, frames)]
    if len(medians) < 2:
        return 0.0
    steps = [
        math.remainder(later - earlier, math.tau) for earlier, later in zip(medians, medians[1:])
    ]
    walk = np.cumsum([0.0] + steps)
    return float(np.ptp(walk))


def find_club(
    mti: np.ndarray,
    geo: tracking.Geometry,
    *,
    tee_range_m: float,
    window_s: tuple[float, float],
    ops_club_speed_mph: float,
    impact_t_s: float,
) -> ClubTrackSelection | None:
    """Track the clubhead's approach inside the pre-impact window.

    Two constraints do the separating, and both are load-bearing:

    - The time window, which must END at impact. The club's radial speed
      (24.7-37.9 m/s measured) overlaps the ball's (40-49 m/s), and the ball
      crosses this range gate on its way out, so without the window this fits
      the ball.
    - The gate's upper edge at the tee. Before impact the clubhead is always
      short of the ball, so anything beyond the tee is body, hands, or clutter
      -- and being slower, it wins most-inliers RANSAC when admitted.
    """
    lo = max(0.35, tee_range_m - CLUB_APPROACH_DEPTH_M)
    hi = tee_range_m + CLUB_GATE_TEE_MARGIN_M
    ops_speed_ms = ops_club_speed_mph / MPH_PER_MS
    projection_lo, projection_hi = CLUB_SPEED_PROJECTION_RANGE
    speed_bounds_ms = (
        max(CLUB_TRACK_SPEED_LIMIT_MS[0], ops_speed_ms * projection_lo),
        min(CLUB_TRACK_SPEED_LIMIT_MS[1], ops_speed_ms * projection_hi),
    )

    track = tracking.find_ball(
        mti,
        geo,
        gates_m=((lo, hi),),
        speed_bounds_ms=speed_bounds_ms,
        min_ball_ms=max(speed_bounds_ms[0], ops_speed_ms * CLUB_PREFERRED_SPEED_RATIO),
        time_window_s=window_s,
    )
    mode = "ops_speed_prior"
    if track is None:
        # Preserve coverage for sparse/aliased captures, but expose that the
        # broad legacy search supplied the candidate. The downstream speed and
        # impact-contact gates still prevent it from being promoted.
        track = tracking.find_ball(
            mti,
            geo,
            gates_m=((lo, hi),),
            speed_bounds_ms=CLUB_SPEED_BOUNDS_MS,
            min_ball_ms=CLUB_SPEED_BOUNDS_MS[0],
            time_window_s=window_s,
        )
        mode = "broad_fallback"
    if track is None:
        return None

    speed_ratio = abs(track.speed_ms) / max(ops_speed_ms, 1e-6)
    impact_range_m = track.range_at(impact_t_s, geo.range_res_m)
    return ClubTrackSelection(
        track=track,
        mode=mode,
        speed_ratio=speed_ratio,
        impact_error_m=abs(impact_range_m - tee_range_m),
    )


def impact_centered_window_s(
    geo: tracking.Geometry,
    impact_t_s: float | None,
    *,
    pre_frames: int,
    post_frames: int,
) -> tuple[float, float] | None:
    """Select complete acquisition frames around the frame containing impact.

    ``post_frames=0`` ends at the impact frame's final loop. A positive value
    adds that many complete, strictly post-impact frames. Acquisition frames
    can be closer together than their RF duration but never overlap; using the
    final loop rather than the next frame start prevents admitting idle time as
    measurement evidence.
    """
    if pre_frames < 0 or post_frames < 0:
        raise ValueError("impact window frame counts must be non-negative")
    if impact_t_s is None or impact_t_s <= 0.0 or impact_t_s > geo.capture_duration_s:
        return None
    frame_starts = geo.frame_time_offsets_s or tuple(
        frame * geo.frame_period_s for frame in range(geo.n_frames)
    )
    impact_frame = bisect_right(frame_starts, impact_t_s) - 1
    if impact_frame < 0:
        return None
    first_frame = max(0, impact_frame - pre_frames)
    last_frame = min(len(frame_starts) - 1, impact_frame + post_frames)
    lo_s = frame_starts[first_frame]
    hi_s = min(
        geo.capture_duration_s,
        frame_starts[last_frame] + geo.n_loops * geo.loop_period_s,
    )
    if hi_s <= lo_s:
        return None
    return float(lo_s), float(hi_s)


def impact_centered_attack_window_s(
    geo: tracking.Geometry,
    impact_t_s: float | None,
    *,
    pre_frames: int = ATTACK_PRE_IMPACT_FRAMES,
    post_frames: int = 0,
) -> tuple[float, float] | None:
    """Compatibility wrapper for the independently configurable AoA window."""
    return impact_centered_window_s(
        geo,
        impact_t_s,
        pre_frames=pre_frames,
        post_frames=post_frames,
    )


def estimate_attack_angle_candidate(
    mti: np.ndarray,
    track: tracking.BallTrack,
    geo: tracking.Geometry,
    cal: Calibration,
    *,
    impact_t_s: float,
    tdm_sign: int,
    window_policy: ClubWindowPolicy | None = None,
) -> tuple[float | None, str, int, float | None]:
    """Fit AoA from four approach frames and the complete impact frame.

    The TX1/TX3 pair is the calibrated eight-element vertical aperture.
    ``track`` is identified only from the six-frame pre-impact range walk,
    then extrapolated through the end of the impact-containing frame. A
    floor-referenced tee anchor turns those selected bearings into the local
    direction through impact: negative is descending, positive is ascending.

    This remains an experimental candidate. The ball pipeline's strict SNR
    and MUSIC/Bartlett agreement checks are retained, but four points are
    sufficient because this impact-centered club window cannot offer the
    ball trajectory's usual eight-point minimum.
    """
    if window_policy is None:
        window_policy = ClubWindowPolicy()
    window_s = impact_centered_attack_window_s(
        geo,
        impact_t_s,
        pre_frames=window_policy.attack_pre_frames,
        post_frames=window_policy.attack_post_frames,
    )
    if window_s is None:
        return None, "rejected_no_impact_frame", 0, None
    lo_s, hi_s = window_s
    extended_track = _ImpactSegmentedTrack(
        base=track,
        impact_t_s=impact_t_s,
        range_res_m=geo.range_res_m,
        post_speed_scale=window_policy.attack_post_speed_scale,
        t_first=min(track.t_first, lo_s),
        t_last=max(track.t_last, hi_s),
    )
    points = doa.angle_points(
        mti,
        extended_track,
        geo,
        cal,
        coherent_loops=1,
        tx_order="normal",
        tdm_sign=tdm_sign,
        tdm_tau_s=doa.TX2_VERTICAL_TDM_TAU_S,
    )
    points = [point for point in points if lo_s <= point.t_s < hi_s]
    fit = trajectory.fit_tee(points, cal, min_points=4)
    if fit is None:
        return None, "rejected_insufficient_vertical_points", len(points), None
    status = "candidate_available"
    if abs(fit.launch_angle_deg) > 25.0:
        status = "candidate_out_of_bounds"
    elif fit.h_rms_m > 0.08:
        status = "candidate_noisy_fit"
    return (
        fit.launch_angle_deg,
        status,
        fit.n_points,
        fit.h_rms_m,
    )


def experimental_path_candidate(
    times: np.ndarray,
    ranges_m: np.ndarray,
    phase_tx1: np.ndarray,
    phase_tx3: np.ndarray,
    frames: np.ndarray,
    *,
    aim_offset_deg: float = 0.0,
    phase_reference_rad: float | None = None,
) -> tuple[float | None, str, float | None]:
    """Fuse TX2 horizontal motion through time without midpoint branch flips.

    TX2 is half a wavelength from the TX1/TX3 vertical midpoint. Each frame's
    two wrapped reference phases are combined on the unit circle. Keeping the
    result inside the physical +/-pi interval avoids inventing angles outside
    the array's unambiguous field of view.
    """
    rows = []
    for frame in np.unique(frames):
        selector = frames == frame
        rows.append(
            (
                float(np.median(times[selector])),
                float(np.median(ranges_m[selector])),
                doa.circular_median(list(phase_tx1[selector])),
                doa.circular_median(list(phase_tx3[selector])),
            )
        )
    rows.sort(key=lambda row: row[0])
    if len(rows) < CLUB_MIN_FRAMES:
        return None, "rejected_insufficient_phase_frames", None

    t_array = np.asarray([row[0] for row in rows])
    r_array = np.asarray([row[1] for row in rows])
    tx1 = np.asarray([row[2] for row in rows], dtype=float)
    tx3 = np.asarray([row[3] for row in rows], dtype=float)

    # Follow each reference's shortest wrapped step through time, combine the
    # continuous references, then project their midpoint back into the
    # physical lambda/2 interval. This avoids the branch flip without asking
    # NumPy to invent an unconstrained phase trajectory.
    def continuous_reference(values: np.ndarray) -> np.ndarray:
        if values.size < 2:
            return values.copy()
        steps = np.angle(np.exp(1j * np.diff(values)))
        return np.concatenate(([values[0]], values[0] + np.cumsum(steps)))

    midpoint = 0.5 * (continuous_reference(tx1) + continuous_reference(tx3))
    midpoint = np.angle(np.exp(1j * midpoint))

    if phase_reference_rad is not None:
        if not math.isfinite(phase_reference_rad):
            raise ValueError("horizontal phase reference must be finite")
        midpoint = np.angle(np.exp(1j * (midpoint - phase_reference_rad)))

    # Board convention: positive TrackMan path is the negative TX2 phase
    # direction. LEVM TX2 is displaced lambda/2 from the TX1/TX3 phase center.
    azimuth = -doa.tx2_phase_to_axis_angle_rad(midpoint)
    x_m = r_array * np.cos(azimuth)
    y_m = r_array * np.sin(azimuth)

    dt = t_array[:, None] - t_array[None, :]
    upper = np.triu(np.ones(dt.shape, dtype=bool), k=1)
    valid = upper & (np.abs(dt) > 1e-9)
    if not np.any(valid):
        return None, "rejected_insufficient_phase_frames", None
    x_slopes = np.zeros_like(dt)
    y_slopes = np.zeros_like(dt)
    np.divide(x_m[:, None] - x_m[None, :], dt, out=x_slopes, where=valid)
    np.divide(y_m[:, None] - y_m[None, :], dt, out=y_slopes, where=valid)
    v_x = float(np.median(x_slopes[valid]))
    v_y = float(np.median(y_slopes[valid]))
    y_0 = float(np.median(y_m - v_y * t_array))
    residual_deg = float(
        np.degrees(
            np.sqrt(np.mean((y_m - (v_y * t_array + y_0)) ** 2))
            / max(float(np.mean(r_array)), 1e-9)
        )
    )
    path_deg = math.degrees(math.atan2(v_y, v_x)) + aim_offset_deg
    status = "candidate_available"
    if abs(path_deg) > 30.0:
        status = "candidate_out_of_bounds"
    elif residual_deg > 2.0:
        status = "candidate_noisy_fit"
    return path_deg, status, residual_deg


def estimate_club_path(
    raw: bytes,
    cal: Calibration,
    *,
    ops_club_speed_mph: float,
    impact_t_s: float | None,
    aim_offset_deg: float = 0.0,
    phase_reference_rad: float | None = None,
    tdm_sign: int = 1,
    window_policy: ClubWindowPolicy | None = None,
) -> ClubPathResult:
    """Estimate experimental club path and AoA with independent windows.

    ``impact_t_s`` is seconds from the oldest retained frame, as located by
    ``shot.impact_time_s`` from the ball's own range walk. It is a required
    argument with no default on purpose: impact's ring slot varies shot to
    shot, so there is no safe value to assume. Club detection remains strictly
    pre-impact; the already-identified track is then propagated through each
    metric's selected impact window. Any post-impact segment uses that metric's
    explicit speed scale rather than silently extending pre-impact velocity.
    """
    if phase_reference_rad is not None and not math.isfinite(phase_reference_rad):
        raise ValueError("horizontal phase reference must be finite")
    if window_policy is None:
        window_policy = ClubWindowPolicy()
    meta0, _ = parse_dump(raw)
    if meta0.get("n_tx") != 3:
        return ClubPathResult(status="rejected_requires_three_tx")
    if impact_t_s is None:
        return ClubPathResult(status="rejected_no_impact_time")

    projected = project_tx_pair(raw, (0, 2))
    meta, cube = parse_dump(projected)
    geo = geometry_from_header(meta, loop_period_s=TX2_LOOP_PERIOD_S)
    res = geo.range_res_m

    window_s = pre_impact_window_s(geo, impact_t_s)
    if window_s is None:
        return ClubPathResult(status="rejected_no_pre_impact_frames")

    mti = tracking.mti_filter(cube, range_domain=is_range_snapshot(meta), geometry=geo)
    selection = find_club(
        mti,
        geo,
        tee_range_m=cal.tee_range_m,
        window_s=window_s,
        ops_club_speed_mph=ops_club_speed_mph,
        impact_t_s=impact_t_s,
    )
    if selection is None:
        return ClubPathResult(status="rejected_no_club_track")
    track = selection.track

    result = ClubPathResult(
        status="pending",
        range_rate_ms=track.slope_bins * res,
        track_rms_bins=track.rms_bins,
        track_inliers=track.n_inliers,
        track_span_s=track.t_last - track.t_first,
        ops_club_speed_mph=ops_club_speed_mph,
        track_speed_ratio=selection.speed_ratio,
        track_impact_error_m=selection.impact_error_m,
        track_selection_mode=selection.mode,
        attack_pre_frames=window_policy.attack_pre_frames,
        attack_post_frames=window_policy.attack_post_frames,
        attack_post_speed_scale=window_policy.attack_post_speed_scale,
        path_pre_frames=window_policy.path_pre_frames,
        path_post_frames=window_policy.path_post_frames,
        path_post_speed_scale=window_policy.path_post_speed_scale,
        range_evidence=ClubRangeEvidence(
            track=track,
            geometry=geo,
            impact_t_s=impact_t_s,
        ),
    )

    path_window_s = impact_centered_window_s(
        geo,
        impact_t_s,
        pre_frames=window_policy.path_pre_frames,
        post_frames=window_policy.path_post_frames,
    )
    if path_window_s is None:
        result.status = "rejected_no_impact_frame"
        return result
    path_lo_s, path_hi_s = path_window_s
    phase_track = _ImpactSegmentedTrack(
        base=track,
        impact_t_s=impact_t_s,
        range_res_m=res,
        post_speed_scale=window_policy.path_post_speed_scale,
        t_first=min(track.t_first, path_lo_s),
        t_last=max(track.t_last, path_hi_s),
    )

    logger.info(
        "[CLUB] track=%s OPS=%.1f mph radial=%.1f m/s ratio=%.2f "
        "impact_error=%.3f m inliers=%d rms=%.2f bins",
        selection.mode,
        ops_club_speed_mph,
        abs(track.speed_ms),
        selection.speed_ratio,
        selection.impact_error_m,
        track.n_inliers,
        track.rms_bins,
    )

    low, high = CLUB_SPEED_PROJECTION_RANGE
    speed_mismatch = not low <= selection.speed_ratio <= high
    impact_mismatch = selection.impact_error_m > CLUB_MAX_IMPACT_ERROR_M

    (
        result.candidate_attack_angle_deg,
        result.attack_angle_status,
        result.attack_n_points,
        result.attack_fit_rms_m,
    ) = estimate_attack_angle_candidate(
        mti,
        track,
        geo,
        cal,
        impact_t_s=impact_t_s,
        tdm_sign=tdm_sign,
        window_policy=window_policy,
    )
    if speed_mismatch:
        result.attack_angle_status = "candidate_club_speed_mismatch"
    elif impact_mismatch:
        result.attack_angle_status = "candidate_impact_contact_mismatch"

    # TX2 phase needs all three transmitters, so re-split the ORIGINAL dump.
    _meta3, cube3 = parse_dump(raw)
    n_frames, chirps, n_rx, n_samples = cube3.shape
    n_tx = 3
    loops = chirps // n_tx
    tdm = cube3.reshape(n_frames, loops, n_tx, n_rx, n_samples)
    # Range-snapshot dumps (production) are already range-domain; raw-ADC
    # dumps (replay of older captures) still need the range FFT. Mirror
    # lcmf._tx2_horizontal_proxy: FFTing an already-FFT'd range snapshot a
    # second time would flatten a real peak into noise.
    rfft = tdm if is_range_snapshot(_meta3) else np.fft.fft(tdm, axis=-1)
    tdm = rfft - rfft.mean(axis=1, keepdims=True)

    times: list[float] = []
    phases: list[float] = []
    weights: list[float] = []
    frame_ids: list[int] = []
    candidate_times: list[float] = []
    candidate_ranges: list[float] = []
    candidate_phase_tx1: list[float] = []
    candidate_phase_tx3: list[float] = []
    candidate_frames: list[int] = []
    frames_seen: set[int] = set()
    for frame in range(geo.n_frames):
        for loop in range(geo.n_loops):
            t_s = geo.loop_time(frame, loop)
            if not path_lo_s <= t_s < path_hi_s:
                continue
            absolute_bin = int(round(phase_track.bin_at(t_s)))
            if not geo.contains_bin(absolute_bin, margin=1, frame=frame):
                continue
            local_bin = geo.local_bin(absolute_bin, frame)
            if not 0 <= local_bin < n_samples:
                continue
            phase_pair = doa.tx2_reference_phases_at(
                tdm,
                frame,
                loop,
                local_bin,
                velocity_ms=phase_track.speed_ms_at(t_s, res),
                tdm_sign=tdm_sign,
                n_rx=n_rx,
            )
            if phase_pair is not None:
                phase_tx1, phase_tx3, _candidate_weight = phase_pair
                candidate_times.append(t_s)
                candidate_ranges.append(float(phase_track.range_at(t_s, res)))
                candidate_phase_tx1.append(phase_tx1)
                candidate_phase_tx3.append(phase_tx3)
                candidate_frames.append(frame)
            sample = doa.tx2_phase_at(
                tdm,
                frame,
                loop,
                local_bin,
                velocity_ms=phase_track.speed_ms_at(t_s, res),
                tdm_sign=tdm_sign,
                n_rx=n_rx,
            )
            if sample is None:
                continue
            phase, weight = sample
            times.append(t_s)
            phases.append(phase)
            weights.append(weight)
            frame_ids.append(frame)
            frames_seen.add(frame)

    (
        result.candidate_path_deg,
        result.candidate_path_status,
        result.candidate_path_fit_residual_deg,
    ) = experimental_path_candidate(
        np.asarray(candidate_times),
        np.asarray(candidate_ranges),
        np.asarray(candidate_phase_tx1),
        np.asarray(candidate_phase_tx3),
        np.asarray(candidate_frames),
        aim_offset_deg=aim_offset_deg,
        phase_reference_rad=phase_reference_rad,
    )

    # Discard snapshots that disagree with their own frame before counting, so
    # the reported counts are the evidence the fit actually used.
    phase_array = np.asarray(phases)
    frame_array = np.asarray(frame_ids)
    # Remove the static LEVM electrical phase zero before spatial conversion.
    # Circular wrapping preserves the lambda/2 baseline's physical +/-pi
    # field of view.
    if phase_reference_rad is not None:
        phase_array = np.angle(np.exp(1j * (phase_array - phase_reference_rad)))
    if phase_array.size:
        keep = phase_outlier_mask(phase_array, frame_array)
        result.n_rejected_snapshots = int((~keep).sum())
        phase_array = phase_array[keep]
        frame_array = frame_array[keep]
        times = list(np.asarray(times)[keep])
        weights = list(np.asarray(weights)[keep])
        frames_seen = set(frame_array.tolist())

    result.n_snapshots = len(times)
    result.n_frames = len(frames_seen)
    if result.n_frames < CLUB_MIN_FRAMES or result.n_snapshots < CLUB_MIN_SNAPSHOTS:
        result.status = "rejected_insufficient_snapshots"
        return result

    result.phase_span_rad = phase_span_rad(phase_array, frame_array)
    phase_span_rejected = result.phase_span_rad > CLUB_MAX_PHASE_SPAN_RAD

    # Phase -> azimuth. The wrapped phase is used as-is: a lambda/2 baseline is
    # unambiguous over exactly +/-pi, which arcsin maps onto the full +/-90
    # degree field of view. Unwrapping would invent angles beyond it.
    # With the validated TX-above-RX board rotation, TX2 is physically left
    # of the TX1/TX3 phase center. A target to the right therefore produces a
    # negative residual phase; negate it so positive cross-range and positive
    # club path follow TrackMan's in-to-out convention.
    azimuth_rad = -doa.tx2_phase_to_axis_angle_rad(phase_array)
    t_array = np.asarray(times)
    weight_array = np.asarray(weights)

    # Per-sample position in Cartesian coordinates: x along boresight, y
    # cross-range. r comes from the track's own range-vs-time fit (smooth;
    # de-noises the per-loop range), az from this sample's own phase.
    r_i = phase_track.range_at(t_array, res)
    x_i = r_i * np.cos(azimuth_rad)
    y_i = r_i * np.sin(azimuth_rad)

    # x(t) and y(t) are each exactly linear in time for a target moving in
    # a straight line at constant velocity, so a weighted linear fit's
    # slope is v_x/v_y with no bias from where the samples sit in the
    # window -- unlike fitting the azimuth ANGLE, whose rate of change is
    # only constant for motion on a circular arc (see module docstring).
    design = np.vstack([t_array, np.ones(t_array.size)]).T
    weighted_design = design * np.sqrt(weight_array)[:, None]
    targets = np.stack([x_i, y_i], axis=1) * np.sqrt(weight_array)[:, None]
    (v_x, v_y), (_x0, y0) = np.linalg.lstsq(weighted_design, targets, rcond=None)[0]

    # Fit quality: cross-range residual is the direct, first-order-sensitive
    # signal for azimuth/phase noise (the x-residual is dominated by the
    # track's own range-fit smoothness and carries little independent
    # information). Expressed in degrees at the mean range so the existing,
    # separately-calibrated CLUB_MAX_AZIMUTH_FIT_RESIDUAL_DEG threshold still
    # applies to a quantity of the same scale as before.
    mean_r = float(np.mean(r_i))
    cross_range_resid_m = y_i - (v_y * t_array + y0)
    result.fit_residual_deg = float(
        np.degrees(np.sqrt((cross_range_resid_m**2).mean()) / max(mean_r, 1e-9))
    )
    result.club_range_m = mean_r
    # Diagnostic only (not a path input): the azimuth rate implied by the
    # cross-range velocity at the mean range.
    result.azimuth_rate_dps = float(np.degrees(v_y / max(mean_r, 1e-9)))

    path_deg = math.degrees(math.atan2(v_y, v_x))
    if speed_mismatch:
        result.status = "rejected_club_speed_mismatch"
        return result
    if impact_mismatch:
        result.status = "rejected_impact_contact_mismatch"
        return result
    if phase_span_rejected:
        result.status = "rejected_phase_span"
        return result
    if result.fit_residual_deg > CLUB_MAX_AZIMUTH_FIT_RESIDUAL_DEG:
        result.status = "rejected_azimuth_fit"
        return result

    result.path_deg = path_deg + aim_offset_deg
    result.confidence = _confidence(result)
    result.status = "accepted"
    logger.info(
        "[CLUB] path=%.2f deg (rate %.1f deg/s, residual %.2f deg, %d frames)",
        result.path_deg,
        result.azimuth_rate_dps,
        result.fit_residual_deg,
        result.n_frames,
    )
    return result


def _confidence(result: ClubPathResult) -> float:
    """Confidence from fit residual and evidence count, never a constant."""
    residual = result.fit_residual_deg or CLUB_MAX_AZIMUTH_FIT_RESIDUAL_DEG
    residual_score = max(0.0, 1.0 - residual / CLUB_MAX_AZIMUTH_FIT_RESIDUAL_DEG)
    evidence_score = min(1.0, result.n_snapshots / (2.0 * CLUB_MIN_SNAPSHOTS))
    return round(0.2 + 0.7 * residual_score * evidence_score, 3)


__all__ = [
    "ClubPathResult",
    "ClubRangeEvidence",
    "ClubWindowPolicy",
    "estimate_club_path",
    "find_club",
    "impact_centered_attack_window_s",
    "impact_centered_window_s",
    "pre_impact_window_s",
]
