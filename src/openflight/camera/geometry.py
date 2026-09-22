"""Shared camera-coordinate geometry helpers."""

from __future__ import annotations

import math

import numpy as np


def deroll_normalized_offsets(
    horizontal: float | np.ndarray,
    vertical: float | np.ndarray,
    correction_deg: float,
) -> tuple[float | np.ndarray, float | np.ndarray]:
    """Remove clockwise image roll from normalized image-plane offsets.

    ``vertical`` is positive upward, unlike image-row coordinates. A positive
    correction therefore rotates the coordinate basis clockwise on screen.
    """
    angle = math.radians(correction_deg)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    return (
        cosine * horizontal + sine * vertical,
        -sine * horizontal + cosine * vertical,
    )
