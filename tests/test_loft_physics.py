"""A custom club's own loft moves the physics defaults along its club family."""

from dataclasses import fields
from datetime import datetime

import pytest

from openflight.ballistics import resolve_launch
from openflight.clubs import ClubType
from openflight.clubs.club import CustomClub
from openflight.clubs.physics import CLUB_PHYSICS, LOFT_FAMILIES, get_club_physics
from openflight.launch_monitor import Shot
from openflight.rolling_buffer.monitor import (
    estimate_carry_with_spin,
    get_optimal_spin_for_ball_speed,
)
from openflight.server import estimate_launch_angle, radar_launch_is_plausible
from openflight.sim.resolver import resolve_shot
from openflight.sim.types import PlayerState

# Every field the loft moves; nominal_loft_deg becomes the given loft instead.
INTERPOLATED_FIELDS = [field.name for field in fields(CLUB_PHYSICS[ClubType.SW])][1:]


def _custom(club="sw", loft_deg=58):
    return CustomClub.from_dict({"club": club, "loft_deg": loft_deg}, club_id="c1", bag="b1")


def _shot(club=ClubType.SW, custom_club=None, **fields_):
    return Shot(
        ball_speed_mph=75.0,
        timestamp=datetime.now(),
        club=club,
        custom_club=custom_club,
        **fields_,
    )


class TestFamilies:
    """Which clubs share a loft curve."""

    def test_every_selectable_club_is_in_exactly_one_family(self):
        members = [club for family in LOFT_FAMILIES for club in family]

        assert sorted(members, key=lambda c: c.value) == sorted(
            (club for club in ClubType if club is not ClubType.UNKNOWN), key=lambda c: c.value
        )

    @pytest.mark.parametrize("family", LOFT_FAMILIES)
    def test_each_family_rises_in_loft(self, family):
        lofts = [CLUB_PHYSICS[club].nominal_loft_deg for club in family]

        assert lofts == sorted(set(lofts))


class TestInterpolation:
    """get_club_physics(club_type, loft_deg)."""

    @pytest.mark.parametrize("loft", [None, 54.0])
    def test_no_loft_or_the_nominal_loft_is_the_table_row(self, loft):
        assert get_club_physics(ClubType.SW, loft) is CLUB_PHYSICS[ClubType.SW]

    def test_halfway_between_two_clubs_takes_the_mean_of_each_field(self):
        sand, lob = CLUB_PHYSICS[ClubType.SW], CLUB_PHYSICS[ClubType.LW]

        physics = get_club_physics(ClubType.SW, 56.0)

        assert physics.nominal_loft_deg == 56.0
        for name in INTERPOLATED_FIELDS:
            expected = (getattr(sand, name) + getattr(lob, name)) / 2
            assert getattr(physics, name) == pytest.approx(expected), name

    def test_the_documented_56_degree_sand_wedge(self):
        physics = get_club_physics(ClubType.SW, 56.0)

        assert physics.optimal_launch_deg == pytest.approx(33.5)
        assert physics.typical_spin_rpm == pytest.approx(10250)
        assert physics.optimal_spin_multiplier == pytest.approx(4.45)
        assert physics.optimal_smash == pytest.approx(1.21)

    def test_a_neighbours_loft_takes_that_neighbours_row(self):
        assert get_club_physics(ClubType.SW, 58.0) == CLUB_PHYSICS[ClubType.LW]
        assert get_club_physics(ClubType.SW, 50.0) == CLUB_PHYSICS[ClubType.GW]

    def test_a_quarter_of_the_way_weights_the_nearer_club(self):
        physics = get_club_physics(ClubType.HYBRID_3, 19.75)
        three, five = CLUB_PHYSICS[ClubType.HYBRID_3], CLUB_PHYSICS[ClubType.HYBRID_5]

        expected = three.typical_spin_rpm + 0.25 * (five.typical_spin_rpm - three.typical_spin_rpm)
        assert physics.typical_spin_rpm == pytest.approx(expected)

    def test_interpolation_stays_inside_the_family(self):
        # A 19° 5-wood interpolates between woods, not with the 19° 3-hybrid.
        physics = get_club_physics(ClubType.WOOD_5, 19.0)
        five, seven = CLUB_PHYSICS[ClubType.WOOD_5], CLUB_PHYSICS[ClubType.WOOD_7]

        assert physics.optimal_launch_deg == pytest.approx(
            five.optimal_launch_deg + (seven.optimal_launch_deg - five.optimal_launch_deg) / 3
        )

    @pytest.mark.parametrize(
        "club, loft, end",
        [
            (ClubType.DRIVER, 8.0, ClubType.DRIVER),
            (ClubType.WOOD_7, 24.0, ClubType.WOOD_7),
            (ClubType.IRON_2, 16.0, ClubType.IRON_2),
            (ClubType.LW, 64.0, ClubType.LW),
        ],
    )
    def test_lofts_past_the_family_take_the_end_values(self, club, loft, end):
        physics = get_club_physics(club, loft)

        assert physics.nominal_loft_deg == loft
        for name in INTERPOLATED_FIELDS:
            assert getattr(physics, name) == getattr(CLUB_PHYSICS[end], name), name

    def test_unknown_ignores_the_loft(self):
        assert get_club_physics(ClubType.UNKNOWN, 40.0) is CLUB_PHYSICS[ClubType.UNKNOWN]


class TestCustomClubPhysics:
    """A custom club's physics follow its own loft."""

    def test_own_loft(self):
        assert _custom(loft_deg=56).physics == get_club_physics(ClubType.SW, 56.0)

    def test_blank_loft_is_the_types_row(self):
        assert _custom(loft_deg=None).physics is CLUB_PHYSICS[ClubType.SW]

    def test_shot_exposes_the_custom_loft(self):
        assert _shot().custom_loft_deg is None
        assert _shot(custom_club=_custom(loft_deg=56)).custom_loft_deg == 56.0


class TestEstimatesFollowTheLoft:
    """A 58° sand wedge is estimated like the 58° lob wedge it matches."""

    def test_launch_angle(self):
        measured = {"club_speed_mph": 62.0, "spin_rpm": 9000}

        as_sand = estimate_launch_angle(ClubType.SW, 75.0, loft_deg=58.0, **measured)

        assert as_sand == estimate_launch_angle(ClubType.LW, 75.0, **measured)
        assert as_sand != estimate_launch_angle(ClubType.SW, 75.0, **measured)

    def test_launch_angle_rises_with_loft(self):
        lofts = [50.0, 54.0, 56.0, 58.0]
        angles = [estimate_launch_angle(ClubType.SW, 75.0, loft_deg=loft)[0] for loft in lofts]

        assert angles == sorted(angles)
        assert len(set(angles)) == len(angles)

    def test_radar_launch_guard(self):
        as_sand = radar_launch_is_plausible(30.0, ClubType.SW, 75.0, loft_deg=58.0)

        assert as_sand == radar_launch_is_plausible(30.0, ClubType.LW, 75.0)

    def test_optimal_spin(self):
        assert get_optimal_spin_for_ball_speed(75.0, ClubType.SW, 58.0) == pytest.approx(
            get_optimal_spin_for_ball_speed(75.0, ClubType.LW)
        )

    def test_carry(self):
        as_sand = estimate_carry_with_spin(
            75.0, 9000, ClubType.SW, club_speed_mph=62.0, loft_deg=58.0
        )

        assert as_sand == pytest.approx(
            estimate_carry_with_spin(75.0, 9000, ClubType.LW, club_speed_mph=62.0)
        )

    def test_shot_carry_with_a_launch_angle(self):
        custom = _shot(custom_club=_custom(loft_deg=58), launch_angle_vertical=20.0)
        lob = _shot(club=ClubType.LW, launch_angle_vertical=20.0)

        assert custom.estimated_carry_yards == pytest.approx(lob.estimated_carry_yards)

    def test_ballistics_spin_fallback(self):
        shot = _shot(custom_club=_custom(loft_deg=56), launch_angle_vertical=30.0)

        conditions = resolve_launch(shot)

        assert conditions.spin_source == "club_typical"
        assert conditions.spin_rpm == pytest.approx(10250)

    def test_simulator_launch_fallback(self):
        shot = _shot(custom_club=_custom(loft_deg=56))

        resolved = resolve_shot(shot, PlayerState())

        assert resolved.vla == pytest.approx(33.5)
