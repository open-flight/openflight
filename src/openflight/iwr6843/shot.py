"""Per-shot pipeline: dump bytes in, measured shot out.

This is the productized form of the weekend field harness — the same chain
the TrackMan validation will run. It deliberately emits EVERYTHING it knows
(all three launch-angle estimators, quality evidence, geometry) and leaves
display/selection policy to the caller; a ``to_shot()`` adapter to the
server's Shot dataclass comes after TrackMan blesses the numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from openflight.iwr6843 import doa, tracking, trajectory
from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.doa import TX2_VERTICAL_TDM_TAU_S  # noqa: F401 - re-exported
from openflight.iwr6843.dump import is_range_snapshot, parse_dump, project_tx_pair
from openflight.iwr6843.tracking import BallTrack, Geometry
from openflight.iwr6843.trajectory import TrajectoryFit

DEFAULT_FRAME_PERIOD_S = 0.012  # header field is 0 on pre-v3 firmware dumps
TX2_LOOP_PERIOD_S = 3 * doa.TDM_TAU_S

# Two-ray configuration per club class, split-half TrackMan-scored on the
# 2026-07-14 session (holdout MAE in comments). PROVISIONAL: one session of
# evidence; revalidate at the next truth session before trusting further.
TH_GATE_RAD = -0.0436  # -2.5 deg: late-track corrupted zone starts

# Estimator policy keys off PER-SHOT OBSERVABLES, not the golfer's club
# distribution (club labels only prime the tracker's speed floor below):
# - "far": near an MTI notch (or very fast) the track start is suspect and
#   bursts are degraded -> no tee anchor, dominance-weighted, near points
#   only. TrackMan-scored: 5i 2.43 / driver 0.92 deg holdout.
# - "anchored": everything else -> exact floor-referenced tee anchor +
#   corrupted-zone angle gate (a no-op for flights that stay low), with an
#   ungated retry when the gate starves the fit.
#   TrackMan-scored: SW 1.31 / 9i 2.50 / 7i 3.17 deg holdout.
POLICY_FAR = {"dominance": True, "x_max_m": 3.8}
POLICY_ANCHORED = {"anchor_tee": True, "th_gate_rad": TH_GATE_RAD}
NOTCH_SPACING_MS = 26.93  # MTI notch comb: loop phase ~ 0 mod 2pi
# Dirichlet attenuation width: suppression is >50% only within ~2 m/s of a
# notch center. n=1 (26.93) is EXCLUDED: wedge balls there are close-range/
# strong enough to survive, and treating them as notched costs the anchor
# (SW 2.1 -> 7 deg regression when tried).
NOTCH_HALF_WIDTH_MS = 2.5
FAST_BALL_MS = 55.0

# slowest RADIAL speed a real ball can read, per class: must sit ABOVE the
# class's post-impact club (~0.7x ball) and the flying tee (~25 m/s), or
# they steal the RANSAC track (the 2026-07-14 driver/SW live bug).
# NOTE these floors assume adult full-swing speeds; a junior's driver ball
# (~35 m/s radial) would need the class floors scaled or a max-range-reached
# candidate rule in the tracker (next-session item).
CLUB_MIN_BALL_MS: dict[str, float] = {
    "wedge": 26.5,  # slowest real SW ball ~27.5 radial
    "mid_iron": 34.0,  # 9i ball >=39; 9i club ~31
    "long_iron": 40.0,  # 5i ball >=47; 5i club ~37
    "driver": 50.0,  # ball >=58; club ~45
    "default": 30.0,
}


def near_mti_notch(speed_ms: float, half_width_ms: float = NOTCH_HALF_WIDTH_MS) -> bool:
    """True when a radial speed sits inside the burst-MTI notch comb."""
    n = round(speed_ms / NOTCH_SPACING_MS)
    return n >= 2 and abs(speed_ms - n * NOTCH_SPACING_MS) < half_width_ms


# no-measurement gate: junk captures (ring overwritten, notch-broken tracks)
# produce thin ragged tracks whose angles AND speed are wrong; a product
# says "no read" instead (2026-07-14: 3 driver + 2 5i captures)
REJECT_RMS_BINS = 0.50
REJECT_MIN_INLIERS = 28
REJECT_MIN_SPAN_S = 0.015


def track_broken(trk: BallTrack | None) -> bool:
    """Module-scope copy of the reject predicate, for direct testing."""
    return (
        trk is None
        or trk.rms_bins >= REJECT_RMS_BINS
        or trk.n_inliers < REJECT_MIN_INLIERS
        or (trk.t_last - trk.t_first) < REJECT_MIN_SPAN_S
    )


def impact_time_s(
    trk: BallTrack | None,
    geo: Geometry,
    tee_range_m: float | None,
    *,
    range_bias_m: float = 0.0,
) -> float | None:
    """When the ball left the tee, from its own fitted range walk.

    Impact's position in the ring CANNOT be assumed. The firmware's freeze is
    requested by a UART CLI command, and ``l3_dump.c`` samples the ring
    position when that command is parsed, so the trigger frame lands late by a
    variable 2-4 frames. Measured on the 2026-07-25 captures, impact fell at
    slot order 1.7-4.1 of an 18-frame ring against an assumed slot 6 -- which
    is why the club-path estimator was fitting the follow-through.

    Back-extrapolating the ball's range fit to the tee range locates impact
    from the data instead. It resolved on 14 of 14 of those captures.

    Returns None when there is no track, no measured tee range, a non-receding
    fit, or an impact instant outside the captured window.
    """
    if trk is None or tee_range_m is None:
        return None
    # The radar sits behind the tee, so a real ball recedes. A flat or
    # approaching fit belongs to something else, and dividing by that slope
    # would invent an impact time rather than measure one.
    if trk.slope_bins <= 0.0:
        return None
    apparent_tee_range_m = tee_range_m + range_bias_m
    t_s = (apparent_tee_range_m / geo.range_res_m - trk.intercept_bins) / trk.slope_bins
    if not 0.0 <= t_s <= geo.capture_duration_s:
        return None
    return float(t_s)


def club_class(club: str | None) -> str:
    """Free-text club name -> tracker speed-floor class (default on miss).

    Only primes the tracker's minimum credible ball speed; the ESTIMATOR
    policy is chosen from per-shot observables, never the club label.
    """
    if not club:
        return "default"
    text = club.strip().lower()
    if "wedge" in text or text[:2] in ("sw", "lw", "gw", "pw"):
        return "wedge"
    if "driver" in text or "wood" in text:
        return "driver"
    if "hybrid" in text:
        return "long_iron"
    digits = [ch for ch in text if ch.isdigit()]
    if digits:
        num = int(digits[0])
        if 1 <= num <= 5:
            return "long_iron"
        if 6 <= num <= 9:
            return "mid_iron"
    return "default"


@dataclass
class ShotMeasurement:
    """Everything the radar knows about one swing."""

    geometry: Geometry
    ball_found: bool
    track: BallTrack | None = None
    fits: dict[str, TrajectoryFit] = field(default_factory=dict)
    n_angle_points: int = 0
    quality: str = "ok"  # ok | low | reject
    ball_speed_corrected_mph: float | None = None
    club: str | None = None
    tx_order: str = "normal"  # chirp order: normal | reversed
    tdm_sign_policy: str = "auto"  # auto | positive | negative
    tdm_sign_used: int | None = None
    policy: str = ""  # far | anchored | anchored_ungated
    notch_recovered: bool = False  # window-scope MTI retry used

    @property
    def ball_speed_mph(self) -> float | None:
        """Ball speed from the range walk, mph (raw radial)."""
        return self.track.speed_mph if self.track else None

    @property
    def launch_angle_deg(self) -> float | None:
        """Headline launch angle: policy two-ray, then tee, then free.

        Order set by the 2026-07-14 TrackMan session (two-ray beat the
        line fits on every club class once weighted/anchored/gated).
        """
        for method in ("two_ray", "tee", "free"):
            fit = self.fits.get(method)
            if fit is not None:
                return fit.launch_angle_deg
        return None

    def to_dict(self) -> dict:
        """JSON-serializable record for session logging / later pairing."""
        from dataclasses import asdict

        return {
            "geometry": asdict(self.geometry),
            "ball_found": self.ball_found,
            "track": asdict(self.track) if self.track else None,
            "ball_speed_mph": self.ball_speed_mph,
            "ball_speed_corrected_mph": self.ball_speed_corrected_mph,
            "launch_angle_deg": self.launch_angle_deg,
            "fits": {k: asdict(v) for k, v in self.fits.items()},
            "n_angle_points": self.n_angle_points,
            "quality": self.quality,
            "club": self.club,
            "tx_order": self.tx_order,
            "tdm_sign_policy": self.tdm_sign_policy,
            "tdm_sign_used": self.tdm_sign_used,
            "policy": self.policy,
            "notch_recovered": self.notch_recovered,
        }

    def summary(self) -> str:
        """One human line for live display at the tee."""
        if not self.ball_found or self.track is None:
            return "no ball detected"
        if self.quality == "reject":
            return (
                f"capture rejected (thin/ragged track: "
                f"{self.track.n_inliers} inliers, "
                f"rms {self.track.rms_bins:.2f})"
            )
        parts = [f"BALL {self.track.speed_ms:5.1f} m/s = {self.track.speed_mph:5.1f} mph"]
        if self.ball_speed_corrected_mph is not None:
            parts.append(f"(cos-corr {self.ball_speed_corrected_mph:5.1f})")
        angle = self.launch_angle_deg
        if angle is not None:
            parts.append(f"LA {angle:4.1f} deg")
            others = [f"{m}:{f.launch_angle_deg:.1f}" for m, f in self.fits.items()]
            parts.append("(" + " ".join(others) + ")")
        else:
            parts.append("LA n/a")
        if self.track.low_confidence:
            parts.append("[LOW CONF]")
        return "  ".join(parts)


@dataclass
class PreparedShotDump:
    """Decoded two-TX capture with lazily cached MTI products."""

    metadata: dict
    cube: np.ndarray
    geometry: Geometry
    range_domain: bool
    _mti_by_scope: dict[str, np.ndarray] = field(default_factory=dict)
    _noise_by_scope: dict[str, float] = field(default_factory=dict)

    def mti(self, scope: str = "burst") -> np.ndarray:
        """Return one static-removal view, computing each scope once."""
        if scope not in ("burst", "window"):
            raise ValueError("MTI scope must be burst or window")
        if scope not in self._mti_by_scope:
            self._mti_by_scope[scope] = tracking.mti_filter(
                self.cube,
                scope=scope,
                range_domain=self.range_domain,
                geometry=self.geometry,
            )
        return self._mti_by_scope[scope]

    def noise_power(self, scope: str = "burst") -> float:
        """Return the invariant median noise power for one MTI scope."""
        if scope not in self._noise_by_scope:
            mti = self.mti(scope)
            if self.geometry.range_bin_counts is None:
                noise = np.median(np.abs(mti) ** 2)
            else:
                valid_power = np.concatenate(
                    [
                        np.abs(mti[frame, ..., : self.geometry.frame_bin_count(frame)]).reshape(-1)
                        ** 2
                        for frame in range(self.geometry.n_frames)
                    ]
                )
                noise = np.median(valid_power)
            self._noise_by_scope[scope] = float(noise)
        return self._noise_by_scope[scope]


def geometry_from_header(meta: dict, *, loop_period_s: float = tracking.LOOP_PRI_S) -> Geometry:
    """Dump-header dict -> Geometry (period falls back for pre-v3 dumps)."""
    period_us = meta.get("frame_period_us", 0)
    range_domain = is_range_snapshot(meta)
    return Geometry(
        n_frames=meta["n_frames"],
        chirps_per_frame=meta["chirps_per_frame"],
        n_tx=meta.get("n_tx", 2),
        n_rx=meta["n_rx"],
        n_samples=meta["n_samples"],
        frame_period_s=(period_us / 1e6 if period_us else DEFAULT_FRAME_PERIOD_S),
        trigger_frame=meta["trigger_frame"],
        loop_period_s=loop_period_s,
        range_bin_start=meta.get("range_bin_start", 0),
        range_fft_size=128 if range_domain else None,
        range_bin_starts=meta.get("range_bin_starts"),
        range_bin_counts=meta.get("range_bin_counts"),
        frame_time_offsets_s=(
            tuple(offset / 1e6 for offset in meta["frame_time_offsets_us"])
            if meta.get("frame_time_offsets_us") is not None
            else None
        ),
    )


def prepare_shot_dump(
    raw: bytes,
    *,
    loop_period_s: float = tracking.LOOP_PRI_S,
) -> PreparedShotDump:
    """Decode one already-projected two-TX dump for repeated track fits."""
    metadata, cube = parse_dump(raw)
    geometry = geometry_from_header(metadata, loop_period_s=loop_period_s)
    frame_values = geometry.chirps_per_frame * geometry.n_rx * geometry.n_samples
    got_frames = cube.reshape(-1).size // frame_values
    if got_frames < geometry.n_frames:
        geometry.n_frames = got_frames
        cube = cube[:got_frames]
    return PreparedShotDump(
        metadata=metadata,
        cube=cube,
        geometry=geometry,
        range_domain=is_range_snapshot(metadata),
    )


def process_dump(
    raw: bytes,
    cal: Calibration,
    *,
    coherent_loops: int = 4,  # pylint: disable=unused-argument
    two_ray: bool = True,
    net_range_m: float | None = None,
    club: str | None = None,
    tx_order: str = "normal",
    tdm_sign_policy: str = "auto",
    loop_period_s: float = tracking.LOOP_PRI_S,
    tdm_tau_s: float = doa.TDM_TAU_S,
    prepared: PreparedShotDump | None = None,
) -> ShotMeasurement:
    """Full pipeline on one dump's bytes.

    ``coherent_loops`` is accepted for backward-compatibility with callers;
    two-ray snapshots and angle points freeze ``coherent_loops=1`` per the
    winning 2026-07-13 calibration.
    ``two_ray`` adds the height-hypothesis estimator (the headline since
    2026-07-15); ``club`` selects the TrackMan-scored per-class two-ray
    configuration (free text, e.g. "7i", "SandWedge"; None = default).
    """
    tx_order = doa.validate_tx_order(tx_order)
    tdm_sign_policy = doa.validate_tdm_sign_policy(tdm_sign_policy)
    if prepared is None:
        meta0, _cube0 = parse_dump(raw)
        if meta0["n_tx"] == 3:
            raw = project_tx_pair(raw, (0, 2))
            if loop_period_s == tracking.LOOP_PRI_S:
                loop_period_s = TX2_LOOP_PERIOD_S
            if tdm_tau_s == doa.TDM_TAU_S:
                tdm_tau_s = TX2_VERTICAL_TDM_TAU_S
        prepared = prepare_shot_dump(raw, loop_period_s=loop_period_s)
    geo = prepared.geometry
    mti = prepared.mti()
    # keep everything 25 cm short of the net: a ball riding up the net is
    # an upward mover that tilts every angle fit high (user setup: net
    # ~3 m past the tee)
    max_r = (net_range_m - 0.25) if net_range_m else None
    klass = club_class(club)
    min_ms = CLUB_MIN_BALL_MS[klass]
    track = tracking.find_ball(mti, geo, max_range_m=max_r, min_ball_ms=min_ms)

    notch_used = False
    if track_broken(track) or (track is not None and near_mti_notch(track.speed_ms)):
        # burst-MTI notches balls near n x 26.93 m/s and shatters their
        # range walk; the window-scope filter keeps them (statics still
        # cancel over the full window)
        mti_w = prepared.mti("window")
        track_w = tracking.find_ball(mti_w, geo, max_range_m=max_r, min_ball_ms=min_ms)
        if not track_broken(track_w) and (
            track_broken(track)
            or track_w.rms_bins < track.rms_bins
            or track_w.n_inliers >= track.n_inliers
        ):
            mti, track, notch_used = mti_w, track_w, True
    result = ShotMeasurement(
        geometry=geo,
        ball_found=track is not None,
        track=track,
        club=club,
        tx_order=tx_order,
        tdm_sign_policy=tdm_sign_policy,
        notch_recovered=notch_used,
    )
    if track is None:
        return result
    if track_broken(track):
        result.quality = "reject"
        return result
    if track.low_confidence:
        result.quality = "low"
    result.tdm_sign_used = doa.resolve_tdm_sign(tdm_sign_policy, mti, track, geo)
    # Line fits use K=1: the plentiful per-loop points are what produced
    # the winning consistency in the 2026-07-13 variant shootout. The strict
    # coherent series (K>1) starves typical shots below min_points — it is
    # reserved for two-ray, where few ultra-clean snapshots win.
    points = doa.angle_points(
        mti,
        track,
        geo,
        cal,
        coherent_loops=1,
        tx_order=tx_order,
        tdm_sign=result.tdm_sign_used,
        tdm_tau_s=tdm_tau_s,
    )
    result.n_angle_points = len(points)
    for fit in (trajectory.fit_free(points, cal), trajectory.fit_tee(points, cal)):
        if fit is not None:
            result.fits[fit.method] = fit
    if two_ray:
        series = doa.snapshot_series(
            mti,
            track,
            geo,
            cal,
            coherent_loops=1,
            tx_order=tx_order,
            tdm_sign=result.tdm_sign_used,
            tdm_tau_s=tdm_tau_s,
        )
        snaps = [(t, r, s) for t, r, s, _snr in series]
        # policy from PER-SHOT observables (see POLICY_* above)
        if near_mti_notch(track.speed_ms) or track.speed_ms >= FAST_BALL_MS:
            result.policy = "far"
            fit = trajectory.fit_two_ray(
                snaps, cal, min_explained=0.70, weighted=True, **POLICY_FAR
            )
        else:
            result.policy = "anchored"
            fit = trajectory.fit_two_ray(
                snaps, cal, min_explained=0.70, weighted=True, **POLICY_ANCHORED
            )
            if fit is None:  # gate starved the fit: retry open
                result.policy = "anchored_ungated"
                fit = trajectory.fit_two_ray(
                    snaps, cal, min_explained=0.70, weighted=True, anchor_tee=True
                )
        if fit is not None:
            result.fits[fit.method] = fit
    angle = result.launch_angle_deg
    if angle is not None:
        res_m = geo.range_res_m
        r_first = track.range_at(track.t_first, res_m) - cal.range_bias_m
        r_last = track.range_at(track.t_last, res_m) - cal.range_bias_m
        factor = trajectory.cosine_speed_factor(angle, r_first, r_last, cal)
        result.ball_speed_corrected_mph = float(track.speed_mph / factor)
    return result


def movers_by_slot(raw: bytes) -> list[tuple[int, float, float]]:
    """Diagnostic: per ring slot, (slot, strongest mover range m, power)."""
    meta, cube = parse_dump(raw)
    geo = geometry_from_header(meta)
    mti = tracking.mti_filter(cube, range_domain=is_range_snapshot(meta), geometry=geo)
    power = (np.abs(mti) ** 2).sum(axis=(1, 2, 3))
    res = geo.range_res_m
    lo_bin = max(3, int(0.35 / res))
    out = []
    for slot in range(power.shape[0]):
        start = geo.frame_bin_start(slot)
        lo_local = max(0, lo_bin - start)
        best_local = lo_local + int(np.argmax(power[slot, lo_local:]))
        best = start + best_local
        out.append((slot, best * res, float(power[slot, best_local])))
    return out
