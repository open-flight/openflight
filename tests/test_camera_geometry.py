"""Tests for shared camera-coordinate calibration."""

import math

import numpy as np

from openflight.camera.geometry import deroll_normalized_offsets


def test_deroll_removes_clockwise_preview_correction_from_sloped_line():
    correction_deg = 2.8
    horizontal = np.linspace(-0.5, 0.5, 9)
    vertical = np.tan(math.radians(correction_deg)) * horizontal

    corrected_x, corrected_z = deroll_normalized_offsets(
        horizontal,
        vertical,
        correction_deg,
    )

    assert np.max(np.abs(corrected_z)) < 1e-12
    assert np.all(np.diff(corrected_x) > 0.0)


def test_zero_roll_preserves_scalar_offsets():
    assert deroll_normalized_offsets(0.25, -0.1, 0.0) == (0.25, -0.1)
