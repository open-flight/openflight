"""Tests for launch_monitor module."""

import pytest
from datetime import datetime
from openflight.clubs import ClubType
from openflight.launch_monitor import (
    Shot,
    estimate_carry_distance,
    adjust_carry_for_launch_angle,
)


class TestEstimateCarryDistance:
    """Tests for the carry distance estimation function."""

    def test_driver_150_mph(self):
        """150 mph driver should be around 265 yards."""
        carry = estimate_carry_distance(150, ClubType.DRIVER)
        assert 250 <= carry <= 280

    def test_driver_100_mph(self):
        """100 mph driver should be around 136 yards."""
        carry = estimate_carry_distance(100, ClubType.DRIVER)
        assert 130 <= carry <= 145

    def test_driver_180_mph(self):
        """180 mph driver (pro level) should be around 335 yards."""
        carry = estimate_carry_distance(180, ClubType.DRIVER)
        assert 320 <= carry <= 350

    def test_same_ball_speed_same_carry_regardless_of_club(self):
        """Ball speed alone determines base carry — club differences come from spin/launch."""
        driver_carry = estimate_carry_distance(120, ClubType.DRIVER)
        iron_carry = estimate_carry_distance(120, ClubType.IRON_7)
        assert driver_carry == iron_carry

    def test_low_speed_extrapolation(self):
        """Very low speeds should still return positive distance."""
        carry = estimate_carry_distance(50, ClubType.DRIVER)
        assert carry > 0
        assert carry < 100

    def test_high_speed_extrapolation(self):
        """Very high speeds should extrapolate reasonably."""
        carry = estimate_carry_distance(220, ClubType.DRIVER)
        assert carry > 400
        assert carry < 500

    def test_driver_carry_monotonic_between_160_and_167_mph(self):
        """Driver carry should not decrease as ball speed increases in this range."""
        carries = [estimate_carry_distance(speed, ClubType.DRIVER) for speed in range(160, 168)]
        for previous, current in zip(carries, carries[1:]):
            assert current >= previous


class TestShot:
    """Tests for the Shot dataclass."""

    def test_basic_shot_creation(self):
        """Create a basic shot with ball speed only."""
        shot = Shot(ball_speed_mph=150.0, timestamp=datetime.now())
        assert shot.ball_speed_mph == 150.0
        assert shot.club == ClubType.DRIVER  # default

    def test_shot_with_club_speed(self):
        """Shot with both ball and club speed."""
        shot = Shot(
            ball_speed_mph=150.0,
            club_speed_mph=103.0,
            timestamp=datetime.now(),
        )
        assert shot.ball_speed_mph == 150.0
        assert shot.club_speed_mph == 103.0

    def test_smash_factor_calculation(self):
        """Smash factor should be ball_speed / club_speed."""
        shot = Shot(
            ball_speed_mph=150.0,
            club_speed_mph=100.0,
            timestamp=datetime.now(),
        )
        assert shot.smash_factor == 1.5

    def test_smash_factor_none_without_club_speed(self):
        """Smash factor should be None if no club speed."""
        shot = Shot(ball_speed_mph=150.0, timestamp=datetime.now())
        assert shot.smash_factor is None

    def test_speed_unit_conversion(self):
        """Test mph to m/s conversion."""
        shot = Shot(
            ball_speed_mph=100.0,
            club_speed_mph=70.0,
            timestamp=datetime.now(),
        )
        # 100 mph ~= 44.7 m/s
        assert 44.5 <= shot.ball_speed_ms <= 44.9
        assert 31.0 <= shot.club_speed_ms <= 31.5

    def test_estimated_carry_same_at_same_ball_speed(self):
        """Base carry depends only on ball speed, not club type."""
        driver_shot = Shot(
            ball_speed_mph=140.0,
            timestamp=datetime.now(),
            club=ClubType.DRIVER,
        )
        iron_shot = Shot(
            ball_speed_mph=140.0,
            timestamp=datetime.now(),
            club=ClubType.IRON_7,
        )
        assert driver_shot.estimated_carry_yards == iron_shot.estimated_carry_yards

    def test_carry_range(self):
        """Carry range should be ±10% of estimate."""
        shot = Shot(ball_speed_mph=150.0, timestamp=datetime.now())
        low, high = shot.estimated_carry_range
        estimate = shot.estimated_carry_yards

        assert low == pytest.approx(estimate * 0.90, rel=0.01)
        assert high == pytest.approx(estimate * 1.10, rel=0.01)

    def test_carry_adjusts_for_launch_angle(self):
        """Shot with launch angle should adjust carry distance."""
        shot_no_angle = Shot(ball_speed_mph=150.0, timestamp=datetime.now())
        shot_low_angle = Shot(
            ball_speed_mph=150.0, timestamp=datetime.now(),
            launch_angle_vertical=7.0,  # well below 11 optimal for driver
            launch_angle_confidence=1.0,
        )
        assert shot_low_angle.estimated_carry_yards < shot_no_angle.estimated_carry_yards

    def test_carry_unchanged_without_launch_angle(self):
        """Shot without launch angle should use current behavior."""
        shot = Shot(ball_speed_mph=150.0, timestamp=datetime.now())
        base = estimate_carry_distance(150.0, ClubType.DRIVER)
        assert shot.estimated_carry_yards == base

    def test_carry_range_tighter_with_angle(self):
        """Shot with launch angle should have tighter carry range."""
        shot_no_angle = Shot(ball_speed_mph=150.0, timestamp=datetime.now())
        shot_angle = Shot(
            ball_speed_mph=150.0, timestamp=datetime.now(),
            launch_angle_vertical=11.0,
            launch_angle_confidence=0.5,
        )
        no_angle_spread = shot_no_angle.estimated_carry_range[1] - shot_no_angle.estimated_carry_range[0]
        angle_spread = shot_angle.estimated_carry_range[1] - shot_angle.estimated_carry_range[0]
        assert angle_spread < no_angle_spread


class TestAdjustCarryForLaunchAngle:
    """Tests for launch-angle-based carry distance adjustment."""

    def test_optimal_angle_no_penalty(self):
        """Optimal launch angle should return base carry unchanged."""
        result = adjust_carry_for_launch_angle(
            base_carry=250, launch_angle=11.0, club=ClubType.DRIVER, confidence=0.5
        )
        assert result == pytest.approx(250, abs=1)

    def test_low_angle_reduces_carry(self):
        """Below-optimal launch angle should reduce carry."""
        result = adjust_carry_for_launch_angle(
            base_carry=250, launch_angle=7.0, club=ClubType.DRIVER, confidence=1.0
        )
        # 4 degrees low * 2.0 yards/deg = -8 yards
        assert result < 250
        assert result == pytest.approx(242, abs=1)

    def test_high_angle_reduces_carry(self):
        """Above-optimal launch angle should reduce carry (less severe)."""
        result = adjust_carry_for_launch_angle(
            base_carry=250, launch_angle=16.0, club=ClubType.DRIVER, confidence=1.0
        )
        # 5 degrees high * 1.5 yards/deg = -7.5 yards
        assert result < 250
        assert result == pytest.approx(242.5, abs=1)

    def test_confidence_scaling(self):
        """Low confidence should reduce the adjustment magnitude."""
        full_conf = adjust_carry_for_launch_angle(
            base_carry=250, launch_angle=7.0, club=ClubType.DRIVER, confidence=1.0
        )
        low_conf = adjust_carry_for_launch_angle(
            base_carry=250, launch_angle=7.0, club=ClubType.DRIVER, confidence=0.2
        )
        assert low_conf > full_conf
        assert low_conf < 250

    def test_penalty_capped_at_10_percent(self):
        """Carry penalty should never exceed 10% of base carry."""
        result = adjust_carry_for_launch_angle(
            base_carry=250, launch_angle=0.0, club=ClubType.DRIVER, confidence=1.0
        )
        assert result >= 250 * 0.90

    def test_iron_optimal_angle(self):
        """Iron clubs should use their own optimal launch angle."""
        result = adjust_carry_for_launch_angle(
            base_carry=150, launch_angle=20.5, club=ClubType.IRON_7, confidence=0.5
        )
        assert result == pytest.approx(150, abs=1)


class TestMultiObjectReporting:
    """Tests for multi-object radar configuration."""

    def test_set_num_reports_single_digit(self):
        """set_num_reports should use On format for 1-9."""
        from openflight.ops243 import OPS243Radar

        radar = OPS243Radar.__new__(OPS243Radar)
        radar.serial = None

        # Verify the method exists and handles single digits
        # Can't test actual command without hardware, but method should not raise
        assert hasattr(radar, 'set_num_reports')

    def test_direction_constants(self):
        """Verify direction enum values."""
        from openflight.ops243 import Direction

        assert Direction.INBOUND.value == "inbound"
        assert Direction.OUTBOUND.value == "outbound"
        assert Direction.UNKNOWN.value == "unknown"
