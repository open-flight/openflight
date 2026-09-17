import math

import numpy as np

from openflight.clubs import ClubType
from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.calibration_session import (
    CalibrationShotRecord,
    CalibrationSummary,
    clone_calibration,
    parse_club,
)


def test_parse_club_accepts_common_short_labels():
    assert parse_club("7i") == ClubType.IRON_7
    assert parse_club("sand_wedge") == ClubType.SW
    assert parse_club("driver") == ClubType.DRIVER
    assert parse_club("mystery") == ClubType.UNKNOWN


def test_clone_calibration_overrides_geometry_without_mutating_original():
    original = Calibration(
        elem_correction=np.ones(8, dtype=complex),
        tilt_rad=math.radians(10.0),
        range_bias_m=0.066,
        tee_range_m=1.5,
        tee_ball_height_m=0.04,
        meta={"radar_height_m": 0.152},
    )

    cloned = clone_calibration(
        original,
        tee_range_m=1.7,
        tilt_deg=11.8,
        radar_height_m=0.20,
        ball_height_m=0.065,
    )

    assert original.tee_range_m == 1.5
    assert math.degrees(original.tilt_rad) == 10.0
    assert original.radar_height_m == 0.152
    assert cloned.tee_range_m == 1.7
    assert math.degrees(cloned.tilt_rad) == 11.8
    assert cloned.radar_height_m == 0.20
    assert cloned.tee_ball_height_m == 0.065


def test_calibration_summary_reports_coverage_and_medians():
    summary = CalibrationSummary(
        shots=[
            CalibrationShotRecord(
                shot_number=1,
                timestamp="2026-07-18T00:00:00Z",
                club="7-iron",
                ball_speed_mph=100.0,
                club_speed_mph=80.0,
                iwr_capture_path="a.l3dump",
                iwr_capture_error=None,
                lcmf_status="accepted",
                launch_angle_deg=20.0,
                component_std_deg=1.0,
                track_speed_mph=98.0,
                track_rms_bins=0.2,
                track_inliers=40,
                track_start_range_m=1.55,
                tilt_candidate_deg=10.5,
                tilt_candidate_score=0.9,
            ),
            CalibrationShotRecord(
                shot_number=2,
                timestamp="2026-07-18T00:00:01Z",
                club="7-iron",
                ball_speed_mph=101.0,
                club_speed_mph=81.0,
                iwr_capture_path="b.l3dump",
                iwr_capture_error=None,
                lcmf_status="rejected_by_ball_tracker",
                launch_angle_deg=None,
                component_std_deg=None,
                track_speed_mph=None,
                track_rms_bins=None,
                track_inliers=None,
                track_start_range_m=None,
            ),
            CalibrationShotRecord(
                shot_number=3,
                timestamp="2026-07-18T00:00:02Z",
                club="7-iron",
                ball_speed_mph=102.0,
                club_speed_mph=82.0,
                iwr_capture_path="c.l3dump",
                iwr_capture_error=None,
                lcmf_status="accepted",
                launch_angle_deg=22.0,
                component_std_deg=1.4,
                track_speed_mph=99.0,
                track_rms_bins=0.3,
                track_inliers=42,
                track_start_range_m=1.65,
                tilt_candidate_deg=11.5,
                tilt_candidate_score=0.8,
            ),
        ]
    )

    data = summary.to_dict()

    assert data["shots"] == 3
    assert data["accepted_shots"] == 2
    assert data["coverage"] == 2 / 3
    assert data["median_launch_angle_deg"] == 21.0
    assert data["median_track_start_range_m"] == 1.6
    assert data["median_tilt_candidate_deg"] == 11.0
