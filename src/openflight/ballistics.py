"""
Physics-based golf ball flight simulator.

Integrates the drag + Magnus ODE using RK4 to produce a deterministic
trajectory from launch conditions. When measured spin is low-confidence
or missing, `resolve_launch` substitutes a club-typical spin value so the
output remains committable (no probabilistic range).

Coordinate system (world frame):
    x — downrange, target direction
    y — lateral, +right
    z — height, up

Aerodynamic model: Cd and Cl are functions of the spin parameter
Sp = r*omega / v. Fits are consistent with Bearman & Harvey (1976) and
Kensrud & Smith (2018) measurements for dimpled golf balls in the
post-drag-crisis regime (Re ~ 5e4 to 2e5). Spin decay follows
Kiratidis & Leinweber (2018) at ~4%/s.
"""

import math
from dataclasses import dataclass
from typing import Literal, Optional

from .launch_monitor import SPIN_CONFIDENCE_HIGH, ClubType, Shot

MPH_TO_MPS = 0.44704
MPS_TO_MPH = 1.0 / MPH_TO_MPS
M_TO_YD = 1.09361

# USGA maximum-conforming ball (45.93 g, 42.7 mm diameter).
# Using the max — not an average — so carry estimates are upper-bounded by
# the rules rather than by a guess at the specific ball in play.
BALL_MASS_KG = 0.04593
BALL_RADIUS_M = 0.02135
BALL_AREA_M2 = math.pi * BALL_RADIUS_M ** 2
AIR_DENSITY_STD = 1.225  # kg/m³ at sea level, 15 °C ISA

# Cd = CD_POLY[0] + CD_POLY[1]*Sp + CD_POLY[2]*Sp^2
# Cl = CL_POLY[0] + CL_POLY[1]*Sp + CL_POLY[2]*Sp^2, clamped at >= 0
#   Second-order polynomials in the spin parameter Sp = r*omega/v, the form
#   Ferguson, McNally & McPhee (2022, ISEA 14, doi:10.5703/1288284317493)
#   fitted to N=1040 shots (GCQuad launch conditions, FlightScope X3
#   carry/apex/offline, Pro V1, Sp 0.02-0.75). The coefficients below are
#   their published values, not re-fitted here.
#   Lift peaks near Sp ~ 0.52 and turns over, as in Bearman & Harvey (1976)
#   and Lyu, Kensrud & Smith (2018). The previous Hill-type saturating form
#   was monotonic by construction, so no choice of its two constants could
#   represent that peak, and it sat ~4 yd low on apex across every club.
#   Beyond Sp = SP_FIT_MAX both curves are held at their end value rather
#   than extrapolating a parabola into a region the fit never saw.
#
# Validation, TrackMan measured launch conditions in, simulated flight out,
# Hill form (as re-fit in #229) -> this form:
#   committed 24-shot capture:            carry RMSE 3.97 -> 2.90 yd
#   independent 5-session 279-shot set:   carry RMSE 3.48 -> 1.98 yd,
#                                         apex RMSE 4.41 -> 0.97 yd
CD_POLY = (0.1304, 0.9287, -0.8259)
CL_POLY = (0.0504, 1.2031, -1.1490)
SP_FIT_MAX = 0.75

# Exponential spin decay: ω(t) = ω₀·exp(-rate·t).
# ~4%/s per Kiratidis & Leinweber (2018); small but matters over ~6 s flights.
SPIN_DECAY_RATE = 0.04

GRAVITY = 9.81
# 500 Hz integration. RK4 error is O(dt⁵) so this is effectively exact for
# the timescales involved; larger dt starts to visibly shorten long drives.
DT_SECONDS = 0.002
# Safety cap — real shots terminate in 5–9 s; anything longer implies the
# solver went unstable or physical inputs are pathological.
MAX_FLIGHT_SECONDS = 15.0
# Trajectory sample cadence for the returned point list. Integration still
# runs at DT_SECONDS; this only controls how many points the caller sees,
# keeping payload size reasonable for UI/log consumers.
SAMPLE_INTERVAL_S = 0.05

# Club-typical spin (RPM) from TrackMan PGA Tour averages.
# Used as fallback when measured spin is missing or low-confidence.
CLUB_TYPICAL_SPIN_RPM: dict[ClubType, float] = {
    ClubType.DRIVER: 2700,
    ClubType.WOOD_3: 3500,
    ClubType.WOOD_5: 4200,
    ClubType.WOOD_7: 4800,
    ClubType.HYBRID_3: 4400,
    ClubType.HYBRID_5: 4900,
    ClubType.HYBRID_7: 5300,
    ClubType.HYBRID_9: 5800,
    ClubType.IRON_2: 4000,
    ClubType.IRON_3: 4500,
    ClubType.IRON_4: 5000,
    ClubType.IRON_5: 5400,
    ClubType.IRON_6: 6000,
    ClubType.IRON_7: 6500,
    ClubType.IRON_8: 7500,
    ClubType.IRON_9: 8500,
    ClubType.PW: 9000,
    ClubType.GW: 9500,
    ClubType.SW: 10000,
    ClubType.LW: 10500,
    ClubType.UNKNOWN: 5000,
}


@dataclass
class LaunchConditions:
    """Deterministic launch parameters for the physics model or simulator export."""

    ball_speed_mph: float
    launch_angle_v: float
    launch_angle_h: float
    spin_rpm: float
    spin_axis_deg: float
    spin_source: Literal["measured", "club_typical"]


@dataclass
class TrajectoryPoint:
    t: float
    x: float
    y: float
    z: float
    speed_mph: float
    spin_rpm: float


@dataclass
class Trajectory:
    points: list[TrajectoryPoint]
    carry_yards: float
    apex_yards: float
    lateral_yards: float
    flight_time_s: float
    landing_speed_mph: float
    landing_angle_deg: float

    @property
    def total_yards(self) -> float:
        """Carry plus a simple rollout estimate (flatter landings roll farther)."""
        # Rough heuristic, not a physical model: a flat landing (~20°) adds
        # ~28 yd, a steep wedge landing (~60°) adds ~15 yd. Surface firmness,
        # wetness, and slope are ignored — fine for a display hint, not for
        # course-play distance.
        rollout = max(0.0, 30.0 * math.cos(math.radians(self.landing_angle_deg)))
        return self.carry_yards + rollout


def resolve_launch(shot: Shot) -> Optional[LaunchConditions]:
    """
    Produce committed launch conditions from a shot.

    Returns None if the vertical launch angle is unavailable (no physics
    simulation possible without it). Spin is taken from the measurement only
    when confidence >= SPIN_CONFIDENCE_HIGH; otherwise a club-typical value
    is substituted and `spin_source` is set to "club_typical".
    """
    if shot.launch_angle_vertical is None:
        return None

    use_measured = (
        shot.spin_rpm is not None
        and shot.spin_confidence is not None
        and shot.spin_confidence >= SPIN_CONFIDENCE_HIGH
    )
    if use_measured:
        spin_rpm = float(shot.spin_rpm)
        source: Literal["measured", "club_typical"] = "measured"
    else:
        spin_rpm = CLUB_TYPICAL_SPIN_RPM.get(
            shot.club, CLUB_TYPICAL_SPIN_RPM[ClubType.UNKNOWN]
        )
        source = "club_typical"

    return LaunchConditions(
        ball_speed_mph=shot.ball_speed_mph,
        launch_angle_v=shot.launch_angle_vertical,
        launch_angle_h=shot.launch_angle_horizontal or 0.0,
        spin_rpm=spin_rpm,
        spin_axis_deg=shot.spin_axis_deg or 0.0,
        spin_source=source,
    )


def _poly(coeffs: tuple, sp: float) -> float:
    return coeffs[0] + coeffs[1] * sp + coeffs[2] * sp * sp


def _cd(sp: float) -> float:
    return _poly(CD_POLY, min(sp, SP_FIT_MAX))


def _cl(sp: float) -> float:
    if sp <= 0:
        return 0.0
    return max(0.0, _poly(CL_POLY, min(sp, SP_FIT_MAX)))


def _derivatives(
    state: tuple,
    omega: float,
    axis: tuple,
    air_density: float,
) -> tuple:
    """d/dt of (x, y, z, vx, vy, vz) under gravity + drag + Magnus."""
    _, _, _, vx, vy, vz = state
    v = math.sqrt(vx * vx + vy * vy + vz * vz)
    # At v ≈ 0 both drag and Magnus vanish (F ∝ v²) and division below
    # would be unsafe. Should only happen at the instantaneous apex of a
    # pathological straight-up launch.
    if v < 1e-6:
        return (vx, vy, vz, 0.0, 0.0, -GRAVITY)

    # Spin parameter Sp drives both Cd and Cl. Note ω is tracked separately
    # (not in the state tuple) because its decay is exponential, not an ODE
    # we want to couple into RK4.
    sp = BALL_RADIUS_M * omega / v
    cd = _cd(sp)
    cl = _cl(sp)

    # q: dynamic-pressure × reference-area / mass = acceleration per unit C.
    # Pre-dividing by mass here lets us return accelerations directly.
    q = 0.5 * air_density * v * v * BALL_AREA_M2 / BALL_MASS_KG
    drag_a = cd * q
    lift_a = cl * q

    # Drag opposes velocity: -v̂ · |F_drag|.
    ax = -drag_a * vx / v
    ay = -drag_a * vy / v
    az = -drag_a * vz / v

    # Magnus direction is (ω̂ × v̂). We compute axis × velocity and normalize
    # by the cross-product magnitude rather than |ω|·|v|·sin(θ), which avoids
    # an extra sin() and gracefully degrades when axis ∥ v (spin does no work).
    ox, oy, oz = axis
    cx = oy * vz - oz * vy
    cy = oz * vx - ox * vz
    cz = ox * vy - oy * vx
    c_mag = math.sqrt(cx * cx + cy * cy + cz * cz)
    if c_mag > 1e-6:
        ax += lift_a * cx / c_mag
        ay += lift_a * cy / c_mag
        az += lift_a * cz / c_mag

    az -= GRAVITY

    return (vx, vy, vz, ax, ay, az)


def _rk4_step(
    state: tuple,
    omega: float,
    axis: tuple,
    air_density: float,
    dt: float,
) -> tuple:
    k1 = _derivatives(state, omega, axis, air_density)
    s2 = tuple(state[i] + 0.5 * dt * k1[i] for i in range(6))
    k2 = _derivatives(s2, omega, axis, air_density)
    s3 = tuple(state[i] + 0.5 * dt * k2[i] for i in range(6))
    k3 = _derivatives(s3, omega, axis, air_density)
    s4 = tuple(state[i] + dt * k3[i] for i in range(6))
    k4 = _derivatives(s4, omega, axis, air_density)
    return tuple(
        state[i] + (dt / 6.0) * (k1[i] + 2 * k2[i] + 2 * k3[i] + k4[i])
        for i in range(6)
    )


def simulate(
    conditions: LaunchConditions,
    air_density: float = AIR_DENSITY_STD,
    dt: float = DT_SECONDS,
) -> Trajectory:
    """
    Integrate flight from launch to first ground contact (z = 0).
    """
    v0 = conditions.ball_speed_mph * MPH_TO_MPS
    la_v = math.radians(conditions.launch_angle_v)
    la_h = math.radians(conditions.launch_angle_h)

    # Initial velocity in world frame (x downrange, y right, z up).
    vx = v0 * math.cos(la_v) * math.cos(la_h)
    vy = v0 * math.cos(la_v) * math.sin(la_h)
    vz = v0 * math.sin(la_v)

    # Spin axis convention:
    #   spin_axis_deg = 0  → pure backspin, axis = -y
    #     (ω × v with v ≈ +x gives Magnus = +z, i.e. lift up — correct)
    #   spin_axis_deg > 0  → top of axis tilts toward +y (right);
    #     Magnus gains a +y component → fade/slice.
    #   spin_axis_deg < 0  → draw/hook.
    # World-frame (not velocity-frame) definition, matching launch-monitor
    # and simulator conventions where spin axis is specified at launch.
    axis_rad = math.radians(conditions.spin_axis_deg)
    axis = (0.0, -math.cos(axis_rad), math.sin(axis_rad))

    # rpm → rad/s. Decays exponentially each step; see loop below.
    omega = conditions.spin_rpm * 2 * math.pi / 60.0

    points: list[TrajectoryPoint] = [
        TrajectoryPoint(0.0, 0.0, 0.0, 0.0, conditions.ball_speed_mph, conditions.spin_rpm)
    ]

    state = (0.0, 0.0, 0.0, vx, vy, vz)
    t = 0.0
    max_z = 0.0
    last_sample_t = 0.0

    while t < MAX_FLIGHT_SECONDS:
        new_state = _rk4_step(state, omega, axis, air_density, dt)
        t += dt
        omega *= math.exp(-SPIN_DECAY_RATE * dt)

        _, _, z, _, _, _ = new_state
        if z > max_z:
            max_z = z

        # Ground contact. Linear interpolation between prev and new state
        # is sufficient because dt (2 ms) is tiny relative to the timescale
        # of the descent — the state is effectively linear across one step
        # even though the full trajectory is not.
        if z <= 0.0 and t > dt:
            prev_z = state[2]
            denom = prev_z - z
            frac = prev_z / denom if abs(denom) > 1e-9 else 0.0
            t_hit = (t - dt) + frac * dt
            final = tuple(state[i] + frac * (new_state[i] - state[i]) for i in range(6))
            fx, fy, fz, fvx, fvy, fvz = final
            v_final = math.sqrt(fvx * fvx + fvy * fvy + fvz * fvz)
            landing_angle = math.degrees(
                math.atan2(-fvz, math.sqrt(fvx * fvx + fvy * fvy))
            )
            points.append(TrajectoryPoint(
                t_hit,
                fx * M_TO_YD, fy * M_TO_YD, max(fz, 0.0) * M_TO_YD,
                v_final * MPS_TO_MPH,
                omega * 60 / (2 * math.pi),
            ))
            return Trajectory(
                points=points,
                carry_yards=fx * M_TO_YD,
                apex_yards=max_z * M_TO_YD,
                lateral_yards=fy * M_TO_YD,
                flight_time_s=t_hit,
                landing_speed_mph=v_final * MPS_TO_MPH,
                landing_angle_deg=landing_angle,
            )

        state = new_state

        if t - last_sample_t >= SAMPLE_INTERVAL_S:
            sx_, sy_, sz_, svx, svy, svz = state
            v = math.sqrt(svx * svx + svy * svy + svz * svz)
            points.append(TrajectoryPoint(
                t,
                sx_ * M_TO_YD, sy_ * M_TO_YD, sz_ * M_TO_YD,
                v * MPS_TO_MPH,
                omega * 60 / (2 * math.pi),
            ))
            last_sample_t = t

    # Flight did not terminate — return current state as best-effort
    fx, fy, fz, fvx, fvy, fvz = state
    v_final = math.sqrt(fvx * fvx + fvy * fvy + fvz * fvz)
    landing_angle = math.degrees(math.atan2(-fvz, math.sqrt(fvx * fvx + fvy * fvy)))
    return Trajectory(
        points=points,
        carry_yards=fx * M_TO_YD,
        apex_yards=max_z * M_TO_YD,
        lateral_yards=fy * M_TO_YD,
        flight_time_s=t,
        landing_speed_mph=v_final * MPS_TO_MPH,
        landing_angle_deg=landing_angle,
    )
