"""LIS3DH mounting-orientation check tests (+Y must point at the ball)."""

import math

import pytest

from openflight.inclinometer import AccelerationSample
from openflight.inclinometer.orientation import (
    MountingVerdict,
    check_mounting,
    mean_sample,
    roll_degrees,
)
from openflight.inclinometer.service import pitch_degrees


def _reading(*, heading_deg=0.0, lift_deg=0.0, side_slope_deg=0.0, face_up=True, t=0.0):
    """Gravity reaction seen by a flat board whose +Y is turned heading_deg from downrange.

    heading_deg is the downrange direction in the board frame, measured from +Y
    toward +X. lift_deg raises the front of the rig; side_slope_deg raises the
    board's +X side.
    """
    heading = math.radians(heading_deg)
    lift = math.radians(lift_deg)
    x = math.sin(lift) * math.sin(heading) + math.sin(math.radians(side_slope_deg))
    y = math.sin(lift) * math.cos(heading)
    z = math.sqrt(max(0.0, 1.0 - x * x - y * y))
    return AccelerationSample(t, x, y, z if face_up else -z)


def _pose(count=10, **kwargs):
    return [_reading(t=float(i), **kwargs) for i in range(count)]


def test_y_toward_ball_passes_and_runtime_pitch_rises():
    level = _pose()
    lifted = _pose(lift_deg=8.0)

    result = check_mounting(level, lifted)

    assert result.verdict is MountingVerdict.OK
    assert result.ok
    assert result.pitch_change_deg == pytest.approx(8.0, abs=0.01)
    assert result.heading_deg == pytest.approx(0.0, abs=0.01)
    # The runtime's own pitch convention must agree: no sign flip needed.
    assert pitch_degrees(mean_sample(lifted)) > pitch_degrees(mean_sample(level))
    assert "No sign flip" in result.message


def test_y_toward_golfer_is_reported_as_reversed():
    result = check_mounting(_pose(heading_deg=180.0), _pose(heading_deg=180.0, lift_deg=8.0))

    assert result.verdict is MountingVerdict.Y_REVERSED
    assert not result.ok
    assert result.pitch_change_deg == pytest.approx(-8.0, abs=0.01)
    assert "180" in result.message


@pytest.mark.parametrize(
    ("heading_deg", "direction"),
    [(90.0, "clockwise"), (-90.0, "counter-clockwise"), (40.0, "clockwise")],
)
def test_board_turned_sideways_says_which_way_to_turn(heading_deg, direction):
    result = check_mounting(
        _pose(heading_deg=heading_deg),
        _pose(heading_deg=heading_deg, lift_deg=10.0),
    )

    assert result.verdict is MountingVerdict.ROTATED
    assert result.heading_deg == pytest.approx(heading_deg, abs=0.5)
    assert f"{abs(heading_deg):.0f} deg {direction}" in result.message


@pytest.mark.parametrize("heading_deg", [10.0, -14.0])
def test_small_misalignment_is_accepted(heading_deg):
    result = check_mounting(
        _pose(heading_deg=heading_deg),
        _pose(heading_deg=heading_deg, lift_deg=10.0),
    )

    assert result.verdict is MountingVerdict.OK


@pytest.mark.parametrize("heading_deg", [170.0, -168.0])
def test_nearly_reversed_board_is_reported_as_reversed(heading_deg):
    result = check_mounting(
        _pose(heading_deg=heading_deg),
        _pose(heading_deg=heading_deg, lift_deg=10.0),
    )

    assert result.verdict is MountingVerdict.Y_REVERSED


def test_sloped_floor_does_not_change_the_answer():
    level = _pose(side_slope_deg=3.0)
    lifted = _pose(side_slope_deg=3.0, lift_deg=6.0)

    result = check_mounting(level, lifted)

    assert result.verdict is MountingVerdict.OK


def test_upside_down_board_is_rejected_before_direction():
    result = check_mounting(_pose(face_up=False), _pose(face_up=False, lift_deg=8.0))

    assert result.verdict is MountingVerdict.UPSIDE_DOWN
    assert "upside down" in result.message


def test_board_on_its_edge_is_not_flat():
    on_edge = [AccelerationSample(0.0, 0.0, 1.0, 0.0)]

    result = check_mounting(on_edge, on_edge)

    assert result.verdict is MountingVerdict.NOT_FLAT


def test_too_small_a_lift_asks_for_more():
    result = check_mounting(_pose(), _pose(lift_deg=1.0))

    assert result.verdict is MountingVerdict.NO_TILT
    assert "Lift the front further" in result.message


def test_moving_rig_is_rejected():
    lifted = [_reading(lift_deg=8.0 + (3.0 if i % 2 else -3.0), t=float(i)) for i in range(10)]

    result = check_mounting(_pose(), lifted)

    assert result.verdict is MountingVerdict.MOVING


def test_single_sample_poses_are_allowed():
    result = check_mounting([_reading()], [_reading(lift_deg=5.0)])

    assert result.verdict is MountingVerdict.OK


def test_mean_sample_rejects_empty_pose():
    with pytest.raises(ValueError):
        mean_sample([])


def test_roll_is_positive_when_x_side_is_raised():
    assert roll_degrees(AccelerationSample(0.0, 0.0, 0.0, 1.0)) == pytest.approx(0.0)
    assert roll_degrees(_reading(side_slope_deg=5.0)) == pytest.approx(5.0, abs=0.01)
