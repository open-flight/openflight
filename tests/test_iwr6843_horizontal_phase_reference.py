"""Horizontal LCMF target-line phase-reference tests."""

import math

import pytest

from openflight.iwr6843 import doa
from openflight.iwr6843.lcmf import _referenced_horizontal_mean


def test_levm_tx2_half_wavelength_baseline_recovers_axis_angle():
    """LEVM TX2 is displaced lambda/2 from the TX1/TX3 phase center."""
    angle_rad = math.radians(12.0)
    phase_rad = math.pi * math.sin(angle_rad)

    recovered_rad = doa.tx2_phase_to_axis_angle_rad(phase_rad)

    assert math.degrees(recovered_rad) == pytest.approx(12.0)


def test_phase_reference_maps_calibrated_target_line_to_zero():
    target_line_phase_rad = -0.493587

    angle_deg, coherence = _referenced_horizontal_mean(
        [target_line_phase_rad] * 3,
        [1.0, 2.0, 1.0],
        phase_reference_rad=target_line_phase_rad,
    )

    assert angle_deg == pytest.approx(0.0, abs=1e-12)
    assert coherence == pytest.approx(1.0)


def test_phase_reference_is_subtracted_before_angle_conversion():
    target_line_phase_rad = -0.493587
    relative_phase_rad = 0.2

    angle_deg, _coherence = _referenced_horizontal_mean(
        [target_line_phase_rad + relative_phase_rad],
        [1.0],
        phase_reference_rad=target_line_phase_rad,
    )

    expected_deg = -math.degrees(math.asin(relative_phase_rad / math.pi))
    assert angle_deg == pytest.approx(expected_deg)


def test_omitting_phase_reference_preserves_raw_rf_zero():
    raw_phase_rad = -0.493587

    angle_deg, _coherence = _referenced_horizontal_mean(
        [raw_phase_rad],
        [1.0],
        phase_reference_rad=None,
    )

    expected_deg = -math.degrees(math.asin(raw_phase_rad / math.pi))
    assert angle_deg == pytest.approx(expected_deg)
