"""Tests for offline camera club-motion analysis."""

import math

import numpy as np
import pytest

from openflight.camera.club_motion import (
    BALL_DIAMETER_MM,
    ImagePoint,
    detect_reference_ball,
    image_plane_motion,
)


def test_detect_reference_ball_prefers_round_center_candidate():
    frames = np.full((12, 80, 120), 30, dtype=np.uint8)
    yy, xx = np.indices(frames.shape[1:])
    frames[:, (xx - 62) ** 2 + (yy - 43) ** 2 <= 5**2] = 240
    frames[:, 55:72, 102:106] = 255  # Bright tee marker near the edge.

    ball = detect_reference_ball(frames)

    assert ball.x == pytest.approx(62.0, abs=0.5)
    assert ball.y == pytest.approx(43.0, abs=0.5)
    assert ball.diameter_px == pytest.approx(math.sqrt(4 * 81 / math.pi), rel=0.1)


def test_detect_reference_ball_finds_dark_ball_in_bright_spotlight():
    frames = np.full((12, 100, 160), 185, dtype=np.uint8)
    yy, xx = np.indices(frames.shape[1:])
    spotlight = np.clip(55 - np.hypot(xx - 80, yy - 55), 0, 55)
    frames[:] = np.clip(frames.astype(np.int16) + spotlight.astype(np.int16), 0, 255)
    frames[:, (xx - 82) ** 2 + (yy - 54) ** 2 <= 6**2] = 65
    frames[:, 18:78, 20:24] = 40  # Dark golfer/club-like edge away from center.

    ball = detect_reference_ball(frames)

    assert ball.x == pytest.approx(82.0, abs=1.0)
    assert ball.y == pytest.approx(54.0, abs=1.0)
    assert 9.0 <= ball.diameter_px <= 15.0


def test_compact_capture_ignores_saturated_clutter_above_hitting_zone():
    frames = np.full((12, 200, 320), 175, dtype=np.uint8)
    yy, xx = np.indices(frames.shape[1:])
    # The compact outdoor capture can contain round, saturated background
    # highlights much closer to image center than the teed ball.
    frames[:, (xx - 160) ** 2 + (yy - 42) ** 2 <= 5**2] = 255
    frames[:, (xx - 148) ** 2 + (yy - 130) ** 2 <= 7**2] = 65

    ball = detect_reference_ball(frames)

    assert ball.x == pytest.approx(148.0, abs=1.0)
    assert ball.y == pytest.approx(130.0, abs=1.0)
    assert 10.0 <= ball.diameter_px <= 18.0


def test_image_plane_motion_uses_terminal_interval_and_ball_scale():
    points = [
        ImagePoint(frame_index=4, x=10.0, y=20.0),
        ImagePoint(frame_index=5, x=14.0, y=23.0),
    ]
    timestamps_ns = np.arange(8, dtype=np.int64) * 4_000_000

    motion = image_plane_motion(points, timestamps_ns, ball_diameter_px=10.0)

    assert motion.horizontal_px_s == pytest.approx(1000.0)
    assert motion.vertical_px_s == pytest.approx(-750.0)
    assert motion.mm_per_px == pytest.approx(BALL_DIAMETER_MM / 10.0)
    assert motion.horizontal_m_s == pytest.approx(4.267)
    assert motion.vertical_m_s == pytest.approx(-3.20025)


def test_image_plane_motion_rejects_nonconsecutive_points():
    points = [
        ImagePoint(frame_index=4, x=10.0, y=20.0),
        ImagePoint(frame_index=6, x=14.0, y=23.0),
    ]

    with pytest.raises(ValueError, match="consecutive"):
        image_plane_motion(points, np.arange(8, dtype=np.int64), ball_diameter_px=10.0)
