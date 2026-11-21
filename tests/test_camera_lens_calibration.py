"""Measured lens calibration in the camera club-delivery rays."""

import json

import cv2
import numpy as np
import pytest

from openflight.camera.club_delivery import (
    CameraDeliveryGeometry,
    LensCalibration,
    _normalized_image_points,
    load_lens_calibration,
)

GEOMETRY = dict(camera_height_m=0.21, radar_height_m=0.20, tee_range_m=1.6, ball_height_m=0.04)


def test_distortion_free_lens_matches_legacy_model():
    focal = 560.0
    lens = LensCalibration(fx=focal, fy=focal, cx=320.0, cy=200.0, dist=(0.0, 0.0, 0.0, 0.0, 0.0))
    pts = np.array([[100.0, 50.0], [320.0, 200.0], [600.0, 390.0]])
    legacy = _normalized_image_points(
        pts, focal_px=focal, geometry=CameraDeliveryGeometry(**GEOMETRY)
    )
    calibrated = _normalized_image_points(
        pts, focal_px=focal, geometry=CameraDeliveryGeometry(**GEOMETRY, lens=lens)
    )
    np.testing.assert_allclose(calibrated, legacy, atol=1e-9)


def test_barrel_distortion_is_removed():
    """A point imaged through the measured barrel lens maps back to its true ray."""
    lens = LensCalibration(
        fx=557.7, fy=557.6, cx=327.8, cy=214.9, dist=(-0.463, 0.308, 0.0, 0.0, -0.145)
    )
    true_rays = np.array([[0.45, -0.25, 1.0], [-0.5, 0.3, 1.0], [0.05, 0.02, 1.0]])
    camera_matrix = np.array([[lens.fx, 0, lens.cx], [0, lens.fy, lens.cy], [0, 0, 1.0]])
    distorted, _ = cv2.projectPoints(
        true_rays, np.zeros(3), np.zeros(3), camera_matrix, np.array(lens.dist)
    )
    recovered = lens.normalized(distorted.reshape(-1, 2))
    np.testing.assert_allclose(recovered, true_rays[:, :2], atol=1e-4)
    # The legacy distortion-free model would put the outer point well inside its true angle.
    legacy_x = (distorted.reshape(-1, 2)[0, 0] - 320.0) / lens.fx
    assert legacy_x < 0.45 * 0.95  # ~7% short at this angle without the lens model


def _write(tmp_path):
    data = {
        "image_size": [1280, 800],
        "raw_sensor": {
            "K": [[1115.4, 0, 656.1], [0, 1115.2, 430.2], [0, 0, 1]],
            "dist": [-0.46, 0.31, 0.0007, -0.0002, -0.14],
        },
        "rotated_180": {
            "K": [[1115.4, 0, 622.9], [0, 1115.2, 368.8], [0, 0, 1]],
            "dist": [-0.46, 0.31, -0.0007, 0.0002, -0.14],
        },
    }
    path = tmp_path / "intrinsics.json"
    path.write_text(json.dumps(data))
    return path


def test_loader_scales_to_binned_capture_and_picks_orientation(tmp_path):
    path = _write(tmp_path)
    raw = load_lens_calibration(path, width=640, height=400, rotate_180=False)
    rot = load_lens_calibration(path, width=640, height=400, rotate_180=True)
    assert raw.fx == pytest.approx(557.7) and raw.fy == pytest.approx(557.6)
    assert raw.cx == pytest.approx((656.1 + 0.5) / 2 - 0.5)
    assert rot.cx == pytest.approx((622.9 + 0.5) / 2 - 0.5)
    assert rot.dist[2] == pytest.approx(-0.0007)


def test_loader_refuses_crops_and_mirrors(tmp_path):
    path = _write(tmp_path)
    assert (
        load_lens_calibration(path, width=640, height=400, rotate_180=True, mirror_horizontal=True)
        is None
    )
    assert (
        load_lens_calibration(
            path, width=640, height=400, rotate_180=True, scaler_crop=(0, 200, 1280, 400)
        )
        is None
    )
    assert load_lens_calibration(path, width=320, height=100, rotate_180=True) is None


def test_loader_maps_high_speed_window(tmp_path):
    path = _write(tmp_path)
    raw = load_lens_calibration(
        path, width=320, height=200, rotate_180=False, sensor_window=(344.0, 258.0, 2.0)
    )
    assert raw.fx == pytest.approx(1115.4 / 2)
    assert raw.cx == pytest.approx((656.1 - 344.0) / 2)
    assert raw.cy == pytest.approx((430.2 - 258.0) / 2)
    rot = load_lens_calibration(
        path, width=320, height=200, rotate_180=True, sensor_window=(344.0, 258.0, 2.0)
    )
    assert rot.cx == pytest.approx(319 - (656.1 - 344.0) / 2)
    assert rot.cy == pytest.approx(199 - (430.2 - 258.0) / 2)
    assert rot.dist[2] == pytest.approx(-0.0007)


def test_loader_maps_binned_640x200_window(tmp_path):
    """The 640x200 high-speed mode bins the sensor rows 300-699; binned pixel centres sit at 2u + 0.5."""
    path = _write(tmp_path)
    window = (0.5, 300.5, 2.0)
    raw = load_lens_calibration(path, width=640, height=200, rotate_180=False, sensor_window=window)
    assert raw.fx == pytest.approx(1115.4 / 2) and raw.fy == pytest.approx(1115.2 / 2)
    assert raw.cx == pytest.approx((656.1 - 0.5) / 2)
    assert raw.cy == pytest.approx((430.2 - 300.5) / 2)
    # Same full-width x mapping as the 640x400 binned mode.
    binned = load_lens_calibration(path, width=640, height=400, rotate_180=False)
    assert raw.cx == pytest.approx(binned.cx)
    rot = load_lens_calibration(path, width=640, height=200, rotate_180=True, sensor_window=window)
    assert rot.cx == pytest.approx(639 - (656.1 - 0.5) / 2)
    assert rot.cy == pytest.approx(199 - (430.2 - 300.5) / 2)
