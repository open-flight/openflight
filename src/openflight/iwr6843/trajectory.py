"""Launch-angle estimation from the angle/snapshot series of one shot.

Estimators, all TrackMan-scored on 2026-07-14 truth (60 shots, production
mount, split-half holdout):

- ``fit_free``     — unconstrained line through the (x, h) points.
- ``fit_tee``      — line forced through the tape-measured launch point.
                     Anchor is FLOOR-referenced (ball-on-mat height minus
                     radar height); the previous radar-plane anchor sat
                     ~0.15 m high and biased every tee fit low.
- ``fit_two_ray``  — the physics fix: per snapshot, hypothesize the ball
                     height and model direct + floor-image arrivals JOINTLY,
                     picking the height that best explains the 8-element
                     data. Options added from the truth session: explained
                     weighting, image-dominance weighting, an angle-domain
                     gate (snapshots above ~-2.5 deg boresight are corrupted
                     by an unresolved late-track systematic), a tee anchor
                     (heights are floor-referenced so the anchor is exact),
                     and a range limit.

All line fits work in ground coordinates: x = horizontal meters from the
radar, h = meters above the RADAR PLANE (tilt already applied). Two-ray
heights are meters above the FLOOR.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.doa import AnglePoint
from openflight.iwr6843.music import est_bartlett


@dataclass
class TrajectoryFit:
    """One launch-angle estimate with its quality evidence."""

    method: str
    launch_angle_deg: float
    n_points: int
    h_rms_m: float                 # scatter about the fit
    launch_cross_m: float          # where the fit meets the radar plane


def _ground_xy(points: list[AnglePoint], cal: Calibration
               ) -> tuple[np.ndarray, np.ndarray]:
    """AnglePoints -> (x horizontal, h above radar plane)."""
    theta = np.array([p.theta_rad for p in points]) + cal.tilt_rad
    rng = np.array([p.range_m for p in points])
    return rng * np.cos(theta), rng * np.sin(theta)


def fit_free(points: list[AnglePoint], cal: Calibration,
             min_points: int = 8) -> TrajectoryFit | None:
    """Unconstrained least-squares line fit."""
    if len(points) < min_points:
        return None
    x_m, h_m = _ground_xy(points, cal)
    slope, icpt = np.polyfit(x_m, h_m, 1)
    resid = h_m - (slope * x_m + icpt)
    cross = float(-icpt / slope) if slope else float("nan")
    return TrajectoryFit(method="free",
                         launch_angle_deg=float(np.degrees(np.arctan(slope))),
                         n_points=len(points),
                         h_rms_m=float(np.sqrt((resid ** 2).mean())),
                         launch_cross_m=cross)


def fit_tee(points: list[AnglePoint], cal: Calibration,
            min_points: int = 8,
            th_gate_rad: float | None = None) -> TrajectoryFit | None:
    """Line forced through the measured launch point (floor-referenced).

    ``th_gate_rad`` keeps only points measured BELOW that boresight angle
    (the late-track corrupted zone starts ~-2.5 deg); falls back to all
    points when too few survive.
    """
    if len(points) < min_points or cal.tee_range_m is None:
        return None
    if th_gate_rad is not None:
        gated = [p for p in points if p.theta_rad <= th_gate_rad]
        if len(gated) >= min_points:
            points = gated
    x_0, h_0 = cal.tee_range_m, cal.tee_anchor_h_m
    x_m, h_m = _ground_xy(points, cal)
    d_x = x_m - x_0
    slope = float(np.sum(d_x * (h_m - h_0)) / np.sum(d_x * d_x))
    resid = h_m - (h_0 + slope * d_x)
    return TrajectoryFit(method="tee",
                         launch_angle_deg=float(np.degrees(np.arctan(slope))),
                         n_points=len(points),
                         h_rms_m=float(np.sqrt((resid ** 2).mean())),
                         launch_cross_m=float(x_0 - h_0 / slope) if slope
                         else float("nan"))


def _two_ray_solve(snapshot: np.ndarray, x_m: float, radar_height_m: float,
                   tilt_rad: float, grid_m: np.ndarray
                   ) -> tuple[float, float, float]:
    """Best-fit ball height ABOVE THE FLOOR for one snapshot (vectorized).

    For each hypothesized height the direct and floor-image arrival angles
    are fixed by geometry; the two complex amplitudes solve in closed form.
    Returns (height_m, explained_fraction, image_power_fraction).
    """
    n_el = len(snapshot)
    m = np.arange(n_el)
    th_d = np.arctan2(grid_m - radar_height_m, x_m) - tilt_rad
    th_i = np.arctan2(-(grid_m + radar_height_m), x_m) - tilt_rad
    s_d = np.exp(1j * np.pi * np.sin(th_d)[:, None] * m[None, :])
    s_i = np.exp(1j * np.pi * np.sin(th_i)[:, None] * m[None, :])
    a12 = np.einsum("gm,gm->g", s_d.conj(), s_i)
    b_1 = s_d.conj() @ snapshot
    b_2 = s_i.conj() @ snapshot
    det = float(n_el) ** 2 - np.abs(a12) ** 2
    det = np.where(np.abs(det) < 1e-9, 1e-9, det)
    c_a = (n_el * b_1 - a12 * b_2) / det
    c_b = (n_el * b_2 - np.conj(a12) * b_1) / det
    pred_power = (np.abs(c_a) ** 2 * n_el + np.abs(c_b) ** 2 * n_el
                  + 2 * np.real(np.conj(c_a) * c_b * a12))
    power = float(np.vdot(snapshot, snapshot).real) + 1e-12
    expl = np.real(pred_power) / power
    k = int(np.argmax(expl))
    p_a, p_b = abs(c_a[k]) ** 2, abs(c_b[k]) ** 2
    return (float(grid_m[k]), float(expl[k]),
            float(p_b / (p_a + p_b + 1e-12)))


def fit_two_ray(snap_points: list[tuple[float, float, np.ndarray]],
                cal: Calibration, *, radar_height_m: float | None = None,
                grid_step_m: float = 0.01, min_points: int = 6,
                min_explained: float = 0.70, weighted: bool = True,
                dominance: bool = False, th_gate_rad: float | None = None,
                x_max_m: float | None = None,
                anchor_tee: bool = False) -> TrajectoryFit | None:
    """Two-ray trajectory: per-snapshot height solve, then a line fit.

    ``snap_points`` is [(t_s, range_m, calibrated 8-el snapshot), ...] from
    :func:`openflight.iwr6843.doa.snapshot_series`. Heights are estimated
    above the FLOOR; the returned fit is converted back to radar-plane
    coordinates so it is comparable with the line fits.

    TrackMan-scored options (2026-07-15): ``weighted`` weights each height
    by its explained margin; ``dominance`` additionally downweights
    mid-mix snapshots (direct and image powers comparable — the regime
    where the two-source model is least trustworthy); ``th_gate_rad``
    drops snapshots whose Bartlett angle is above the gate (late-track
    corruption); ``x_max_m`` drops far snapshots; ``anchor_tee`` forces
    the line through the tee (exact in floor coordinates).
    """
    if radar_height_m is None:
        radar_height_m = float(cal.meta.get("radar_height_m", 0.152))
    grid = np.arange(-0.02, 1.30, grid_step_m)
    xs: list[float] = []
    hs: list[float] = []
    ws: list[float] = []
    for _t, rng_m, snap in snap_points:
        x_m = float(rng_m)                 # slant ~ horizontal at these angles
        if x_max_m is not None and x_m > x_max_m:
            continue
        # Bartlett-peak gate for the corrupted zone. Tested alternative
        # (gating on the solved height's implied angle) scored WORSE on
        # truth (9i 2.5 -> 4.6 deg): corrupted snapshots solve to plausible
        # heights, while their Bartlett peak drifts high and betrays them.
        if th_gate_rad is not None and est_bartlett(snap) > th_gate_rad:
            continue
        height, explained, imfrac = _two_ray_solve(
            snap, x_m, radar_height_m, cal.tilt_rad, grid)
        if explained >= min_explained and np.isfinite(height):
            w = (explained - min_explained + 1e-3) if weighted else 1.0
            if dominance:
                w *= 0.25 + 0.75 * 2.0 * abs(imfrac - 0.5)
            xs.append(x_m)
            hs.append(height)
            ws.append(w)
    if len(xs) < min_points:
        return None
    x_a, h_a, w_a = np.asarray(xs), np.asarray(hs), np.asarray(ws)
    if anchor_tee and cal.tee_range_m is not None:
        x_0, h_0 = cal.tee_range_m, cal.tee_ball_height_m
        d_x = x_a - x_0
        den = float(np.sum(w_a * d_x * d_x))
        if den <= 0:
            return None
        slope = float(np.sum(w_a * d_x * (h_a - h_0)) / den)
        icpt = h_0 - slope * x_0
    else:
        x_c = float(np.average(x_a, weights=w_a))
        h_c = float(np.average(h_a, weights=w_a))
        den = float(np.sum(w_a * (x_a - x_c) ** 2))
        if den <= 0:
            return None
        slope = float(np.sum(w_a * (x_a - x_c) * (h_a - h_c)) / den)
        icpt = h_c - slope * x_c
    resid = h_a - (slope * x_a + icpt)
    # launch_cross in radar-plane coords: floor height == radar_height below
    cross = float((radar_height_m - icpt) / slope) if slope else float("nan")
    return TrajectoryFit(method="two_ray",
                         launch_angle_deg=float(np.degrees(np.arctan(slope))),
                         n_points=len(xs),
                         h_rms_m=float(np.sqrt((resid ** 2).mean())),
                         launch_cross_m=cross)


def cosine_speed_factor(la_deg: float, r_first_m: float, r_last_m: float,
                        cal: Calibration,
                        radar_height_m: float | None = None) -> float:
    """Radial-projection factor: track slope = factor * true ball speed.

    The radar measures range rate along the line of sight; a climbing ball's
    radial speed is cos(angle between velocity and LOS) times its true
    speed, averaged over the observed span. TrackMan-validated 2026-07-15:
    dividing the track speed by this factor halved the ball-speed error
    (4.96 -> 2.77 mph across 60 truth shots) and removed the club-to-club
    structure. Uses the measured launch angle; clamped to [0.90, 1.0].
    """
    if cal.tee_range_m is None:
        return 1.0
    if radar_height_m is None:
        radar_height_m = cal.radar_height_m
    la = np.radians(np.clip(la_deg, 0.0, 40.0))
    z_0 = cal.tee_ball_height_m
    d_z = z_0 - radar_height_m
    x_tee = float(np.sqrt(max(cal.tee_range_m ** 2 - d_z * d_z, 0.25)))

    def r_of(x: float) -> float:
        z = z_0 + np.tan(la) * (x - x_tee)
        return float(np.hypot(x, z - radar_height_m))

    def x_of(r: float) -> float:
        lo, hi = 0.5, 8.0
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            if r_of(mid) < r:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)

    x_0, x_1 = x_of(r_first_m), x_of(r_last_m)
    if x_1 - x_0 < 0.2:
        return 1.0
    path = (x_1 - x_0) / np.cos(la)
    factor = (r_of(x_1) - r_of(x_0)) / path
    return float(np.clip(factor, 0.90, 1.0))
