"""Tests for the ballistics flight simulator and launch resolution."""

import math
from datetime import datetime

import pytest

from openflight.ballistics import (
    BALL_RADIUS_M,
    CD_POLY,
    CL_POLY,
    CLUB_TYPICAL_SPIN_RPM,
    MPH_TO_MPS,
    SP_FIT_MAX,
    SPIN_DECAY_RATE,
    LaunchConditions,
    _cd,
    _cl,
    _poly,
    resolve_launch,
    simulate,
)
from openflight.launch_monitor import ClubType, Shot


def _shot(**kwargs) -> Shot:
    defaults = dict(
        ball_speed_mph=160.0,
        timestamp=datetime.now(),
        club=ClubType.DRIVER,
        launch_angle_vertical=12.0,
    )
    defaults.update(kwargs)
    return Shot(**defaults)


class TestResolveLaunch:
    def test_returns_none_without_vertical_launch_angle(self):
        shot = _shot(launch_angle_vertical=None)
        assert resolve_launch(shot) is None

    def test_uses_measured_spin_when_high_confidence(self):
        shot = _shot(spin_rpm=2500, spin_confidence=0.85)
        cond = resolve_launch(shot)
        assert cond is not None
        assert cond.spin_rpm == 2500
        assert cond.spin_source == "measured"

    def test_uses_club_typical_when_low_confidence(self):
        shot = _shot(spin_rpm=1500, spin_confidence=0.3, club=ClubType.DRIVER)
        cond = resolve_launch(shot)
        assert cond is not None
        assert cond.spin_rpm == CLUB_TYPICAL_SPIN_RPM[ClubType.DRIVER]
        assert cond.spin_source == "club_typical"

    def test_uses_club_typical_when_spin_missing(self):
        shot = _shot(spin_rpm=None, club=ClubType.IRON_7)
        cond = resolve_launch(shot)
        assert cond is not None
        assert cond.spin_rpm == CLUB_TYPICAL_SPIN_RPM[ClubType.IRON_7]
        assert cond.spin_source == "club_typical"

    def test_medium_confidence_still_falls_back(self):
        # Medium confidence (~0.5) is below the high threshold — use typical.
        shot = _shot(spin_rpm=3000, spin_confidence=0.5)
        cond = resolve_launch(shot)
        assert cond is not None
        assert cond.spin_source == "club_typical"

    def test_defaults_horizontal_angle_to_zero(self):
        shot = _shot(launch_angle_horizontal=None)
        cond = resolve_launch(shot)
        assert cond.launch_angle_h == 0.0

    def test_defaults_spin_axis_to_zero(self):
        shot = _shot(spin_axis_deg=None)
        cond = resolve_launch(shot)
        assert cond.spin_axis_deg == 0.0


def _driver(spin_rpm=2700, launch=11.0, ball_speed=165.0, axis=0.0, la_h=0.0):
    return LaunchConditions(
        ball_speed_mph=ball_speed,
        launch_angle_v=launch,
        launch_angle_h=la_h,
        spin_rpm=spin_rpm,
        spin_axis_deg=axis,
        spin_source="measured",
    )


class TestSimulate:
    def test_driver_carry_in_expected_range(self):
        # 165 mph ball speed / 11° / 2700 RPM is close to PGA Tour averages.
        # TrackMan data: ~270–285 yards carry.
        traj = simulate(_driver())
        assert 250 <= traj.carry_yards <= 300, (
            f"Driver carry {traj.carry_yards:.1f} yd outside plausible range"
        )

    def test_iron_carry_in_expected_range(self):
        # 7-iron: 120 mph ball speed, 17° launch, 6500 RPM → ~160-180 yd
        cond = LaunchConditions(
            ball_speed_mph=120.0,
            launch_angle_v=17.0,
            launch_angle_h=0.0,
            spin_rpm=6500,
            spin_axis_deg=0.0,
            spin_source="measured",
        )
        traj = simulate(cond)
        assert 140 <= traj.carry_yards <= 200, (
            f"7-iron carry {traj.carry_yards:.1f} yd outside plausible range"
        )

    def test_higher_launch_produces_higher_apex(self):
        low = simulate(_driver(launch=8.0))
        high = simulate(_driver(launch=15.0))
        assert high.apex_yards > low.apex_yards

    def test_more_spin_produces_higher_apex(self):
        low_spin = simulate(_driver(spin_rpm=1800))
        high_spin = simulate(_driver(spin_rpm=3500))
        assert high_spin.apex_yards > low_spin.apex_yards

    def test_fade_lands_right_of_target(self):
        traj = simulate(_driver(axis=10.0))  # +axis = fade
        assert traj.lateral_yards > 3.0

    def test_draw_lands_left_of_target(self):
        traj = simulate(_driver(axis=-10.0))  # -axis = draw
        assert traj.lateral_yards < -3.0

    def test_straight_shot_stays_near_center(self):
        traj = simulate(_driver(axis=0.0, la_h=0.0))
        assert abs(traj.lateral_yards) < 1.0

    def test_horizontal_launch_offsets_landing(self):
        # +la_h should push ball right
        traj = simulate(_driver(la_h=2.0))
        assert traj.lateral_yards > 1.0

    def test_trajectory_ends_at_ground(self):
        traj = simulate(_driver())
        assert traj.points[-1].z <= 0.01
        assert traj.points[-1].t == pytest.approx(traj.flight_time_s, rel=0.01)

    def test_spin_decays_over_flight(self):
        initial_spin = 3000
        traj = simulate(_driver(spin_rpm=initial_spin))
        final_spin = traj.points[-1].spin_rpm

        # Assert the decay law itself rather than a hardcoded window, so the
        # test stays valid when aero coefficients change the flight time.
        expected = initial_spin * math.exp(-SPIN_DECAY_RATE * traj.flight_time_s)
        assert final_spin == pytest.approx(expected, rel=1e-3)

        # Sanity: spin must fall, but not collapse over a single flight.
        assert 0.6 * initial_spin < final_spin < initial_spin

    def test_flight_time_reasonable(self):
        traj = simulate(_driver())
        # Drivers typically spend 5-8 seconds in the air.
        assert 4.0 < traj.flight_time_s < 9.0

    def test_landing_angle_is_positive_descent(self):
        traj = simulate(_driver())
        # Ball descends on landing — angle below horizontal is positive.
        assert 20.0 < traj.landing_angle_deg < 60.0

    def test_zero_launch_angle_does_not_crash(self):
        # Extreme input should still produce a terminated trajectory.
        cond = LaunchConditions(
            ball_speed_mph=100.0,
            launch_angle_v=0.5,
            launch_angle_h=0.0,
            spin_rpm=3000,
            spin_axis_deg=0.0,
            spin_source="measured",
        )
        traj = simulate(cond)
        assert traj.carry_yards > 0
        assert traj.flight_time_s < 5.0

    def test_total_distance_includes_rollout(self):
        traj = simulate(_driver())
        assert traj.total_yards > traj.carry_yards


class TestAeroCoefficientSafeguards:
    """Guards on the Cd/Cl quadratics outside their fitted range.

    The Ferguson coefficients are fitted over Sp <= SP_FIT_MAX. Extrapolated
    past it both parabolas turn over and cross zero (Cl above Sp ~1.09, Cd
    above ~1.25), which would mean lift pulling the ball down and drag
    *accelerating* it. Sp is recomputed every integration step from
    r*omega/v, and v decays faster than spin, so a normal lob wedge reaches
    Sp ~1.5 near apex - inside the negative-drag region. Holding both curves
    at their end value is the guard; these tests are what make its removal
    fail.
    """

    def test_zero_spin_produces_no_lift(self):
        assert _cl(0.0) == 0.0

    def test_lift_never_negative_above_fit_range(self):
        # Raw Cl(1.5) is about -0.73; clamped it must not pull the ball down.
        for sp in (SP_FIT_MAX, 1.0, 1.5, 3.0):
            assert _cl(sp) >= 0.0, f"Cl({sp}) = {_cl(sp):.4f} is negative"

    def test_drag_stays_positive_above_fit_range(self):
        # Raw Cd crosses zero near Sp 1.25; negative drag is unphysical.
        for sp in (SP_FIT_MAX, 1.3, 1.5, 3.0):
            assert _cd(sp) > 0.0, f"Cd({sp}) = {_cd(sp):.4f} is not positive"

    def test_both_curves_held_flat_above_fit_range(self):
        # Beyond the fitted range the value is pinned to the endpoint, so the
        # curves are constant there rather than continuing to fall.
        for sp in (1.0, 1.5, 3.0):
            assert _cd(sp) == pytest.approx(_cd(SP_FIT_MAX))
            assert _cl(sp) == pytest.approx(_cl(SP_FIT_MAX))

    def test_curves_unclamped_inside_fit_range(self):
        # The guard must not disturb the fitted region it sits above.
        for sp in (0.1, 0.25, 0.5):
            assert _cd(sp) == pytest.approx(_poly(CD_POLY, sp))
            assert _cl(sp) == pytest.approx(_poly(CL_POLY, sp))

    def test_lob_wedge_reaches_clamped_region_in_flight(self):
        """A real lob wedge drives Sp past the fitted range in flight.

        This is why the clamp is not hypothetical: 55 mph / 40 deg /
        10000 rpm peaks near Sp 1.5, where the raw parabola gives
        Cd = -0.35 and Cl = -0.73. The clamp is what holds that at the
        Sp 0.75 endpoint instead.

        Note this asserts reachability, not the clamp's presence: removing
        the clamp *lowers* peak Sp (less drag keeps the ball faster), so it
        cannot serve as a red control. The coefficient tests above do that.
        """
        cond = LaunchConditions(
            ball_speed_mph=55.0,
            launch_angle_v=40.0,
            launch_angle_h=0.0,
            spin_rpm=10000,
            spin_axis_deg=0.0,
            spin_source="measured",
        )
        traj = simulate(cond)

        # Recorded points are downsampled, so this is a lower bound on the
        # true peak - which only makes the assertion stricter.
        peak_sp = max(
            BALL_RADIUS_M * (p.spin_rpm * 2 * math.pi / 60.0) / (p.speed_mph * MPH_TO_MPS)
            for p in traj.points
            if p.speed_mph > 1e-6
        )
        assert peak_sp > SP_FIT_MAX, (
            f"lob wedge peak Sp {peak_sp:.2f} no longer exceeds {SP_FIT_MAX}; "
            f"the clamp would be untested by any realistic shot"
        )
        assert 30.0 < traj.carry_yards < 90.0, (
            f"lob wedge carry {traj.carry_yards:.1f} yd is not plausible"
        )
