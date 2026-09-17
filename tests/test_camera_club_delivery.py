"""Tests for the live camera club-delivery estimator and fusion."""

import math
from dataclasses import replace

import numpy as np
import pytest

import openflight.camera.club_delivery as club_delivery_module
from openflight.camera.club_delivery import (
    SCENE_P995_MIN,
    ApproachPairEstimate,
    CameraDeliveryGeometry,
    ChainedDelivery,
    FusedDelivery,
    ReferenceBallTracker,
    TraceResult,
    _detect_impact_index,
    _preferred_path_estimate,
    aoa_offset_for_club,
    camera_ops_delivery_from_feature_pair,
    combine_approach_estimates,
    delivery_from_feature_tracks,
    estimate_chained_delivery,
    estimate_delivery_trace,
    fuse_club_delivery,
)
from openflight.camera.club_motion import ReferenceBall, detect_reference_ball
from openflight.clubs import ClubType


class _Ball:
    x = 320.0
    y = 200.0
    diameter_px = 28.0


def test_impact_detection_ignores_late_ball_like_brightness_after_trigger():
    frames = np.zeros((60, 9, 9), dtype=np.uint8)
    yy, xx = np.mgrid[:9, :9]
    ball = _Ball()
    ball.x = 4.0
    ball.y = 4.0
    ball.diameter_px = 8.0
    core = (xx - ball.x) ** 2 + (yy - ball.y) ** 2 <= 3**2
    frames[:42, core] = 160
    # Empty background or late motion can accidentally resemble the teed ball.
    frames[59, core] = 160

    assert _detect_impact_index(frames, ball, trigger_index=44) == 41


def test_impact_detection_uses_first_departure_near_trigger():
    frames = np.zeros((60, 9, 9), dtype=np.uint8)
    yy, xx = np.mgrid[:9, :9]
    ball = _Ball()
    ball.x = 4.0
    ball.y = 4.0
    ball.diameter_px = 8.0
    core = (xx - ball.x) ** 2 + (yy - ball.y) ** 2 <= 3**2
    frames[:42, core] = 160
    frames[45, core] = 160  # club/halo briefly resembles the address frame

    assert _detect_impact_index(frames, ball, trigger_index=44) == 41


def test_impact_detection_accepts_camera_departure_after_early_gpio_trigger():
    frames = np.zeros((70, 9, 9), dtype=np.uint8)
    yy, xx = np.mgrid[:9, :9]
    ball = _Ball()
    ball.x = 4.0
    ball.y = 4.0
    ball.diameter_px = 8.0
    core = (xx - ball.x) ** 2 + (yy - ball.y) ** 2 <= 3**2
    frames[:53, core] = 160

    assert _detect_impact_index(frames, ball, trigger_index=44) == 52


def test_reference_ball_tracker_falls_back_to_established_tee_anchor():
    tracker = ReferenceBallTracker()
    for x in (323.0, 324.0, 325.0):
        ball, source = tracker.resolve(ReferenceBall(x, 189.0, 13.5, 143))
        assert source == "detected"
        assert ball.x == x

    ball, source = tracker.resolve(ReferenceBall(472.0, 274.0, 7.7, 47))

    assert source == "session_anchor"
    assert ball.x == pytest.approx(324.0)
    assert ball.y == pytest.approx(189.0)
    assert ball.diameter_px == pytest.approx(13.5)


def test_reference_ball_tracker_returns_rolling_anchor_for_stable_geometry():
    tracker = ReferenceBallTracker()

    first, first_source = tracker.resolve_stable(ReferenceBall(320.0, 190.0, 14.0, 140))
    second, second_source = tracker.resolve_stable(ReferenceBall(322.0, 192.0, 16.0, 160))
    third, third_source = tracker.resolve_stable(ReferenceBall(321.0, 191.0, 15.0, 150))

    assert first_source == "warming"
    assert second_source == "warming"
    assert first.x == 320.0
    assert second.x == 322.0
    assert third_source == "session_anchor"
    assert third.x == pytest.approx(321.0)
    assert third.y == pytest.approx(191.0)
    assert third.diameter_px == pytest.approx(15.0)


def test_reference_ball_tracker_fallback_requires_established_anchor():
    tracker = ReferenceBallTracker()

    assert tracker.fallback() is None
    for x in (320.0, 321.0):
        tracker.resolve_stable(ReferenceBall(x, 190.0, 14.0, 140))
    assert tracker.fallback() is None

    tracker.resolve_stable(ReferenceBall(322.0, 190.0, 14.0, 140))

    anchor = tracker.fallback()
    assert anchor is not None
    assert anchor.x == pytest.approx(321.0)
    assert anchor.y == pytest.approx(190.0)


def test_club_delivery_uses_established_anchor_when_detection_is_missing(monkeypatch):
    tracker = ReferenceBallTracker()
    for x in (159.0, 160.0, 161.0):
        tracker.resolve_stable(ReferenceBall(x, 100.0, 14.0, 140))
    monkeypatch.setattr(
        club_delivery_module,
        "detect_reference_ball",
        lambda _frames: (_ for _ in ()).throw(ValueError("not found")),
    )

    result = estimate_chained_delivery(
        np.full((60, 200, 320), 200, dtype=np.uint8),
        np.arange(60, dtype=np.int64) * 2_000_000,
        trigger_index=40,
        range_evidence=None,
        geometry=CameraDeliveryGeometry(
            camera_height_m=0.2032,
            radar_height_m=0.1524,
            tee_range_m=1.524,
            ball_height_m=0.04,
            image_width_px=320,
            image_height_px=200,
        ),
        ops_club_speed_mph=80.0,
        ball_tracker=tracker,
    )

    assert result.status != "rejected_no_ball"


def _project_impact_tracks(
    *,
    path_deg: float,
    aoa_deg: float,
    speed_ms: float = 35.0,
    n_features: int = 12,
    camera_lateral_offset_m: float = 0.0,
):
    """Synthetic clubhead features viewed by the rear camera at impact."""
    geometry = CameraDeliveryGeometry(
        camera_height_m=0.20955,
        radar_height_m=0.15875,
        tee_range_m=1.524,
        ball_height_m=0.021335,
        camera_lateral_offset_m=camera_lateral_offset_m,
    )
    ball = _Ball()
    times = np.array([-0.0035, 0.0, 0.0035])
    path = math.radians(path_deg)
    aoa = math.radians(aoa_deg)
    forward = speed_ms / math.sqrt(1.0 + math.tan(path) ** 2 + math.tan(aoa) ** 2)
    velocity = np.array([forward * math.tan(path), forward * math.tan(aoa), forward])
    ball_forward = geometry.tee_range_m
    focal_px = (
        ball.diameter_px
        * math.sqrt(
            geometry.camera_lateral_offset_m**2
            + ball_forward**2
            + (geometry.ball_height_m - geometry.camera_height_m) ** 2
        )
        / geometry.ball_diameter_m
    )
    pitch = math.atan2(
        geometry.ball_height_m - geometry.camera_height_m,
        ball_forward,
    )

    tracks = []
    ranges = []
    for feature in range(n_features):
        lateral_offset = (feature - (n_features - 1) / 2) * 0.001
        height_offset = (feature % 3 - 1) * 0.001
        pixels = []
        feature_ranges = []
        for time_s in times:
            lateral = lateral_offset + velocity[0] * time_s
            height = geometry.ball_height_m + height_offset + velocity[1] * time_s
            forward_m = ball_forward + velocity[2] * time_s
            vertical_m = height - geometry.camera_height_m
            camera_forward = math.cos(pitch) * forward_m + math.sin(pitch) * vertical_m
            camera_vertical = -math.sin(pitch) * forward_m + math.cos(pitch) * vertical_m
            camera_lateral = lateral - geometry.camera_lateral_offset_m
            x_px = geometry.image_width_px / 2 + focal_px * camera_lateral / camera_forward
            y_px = geometry.image_height_px / 2 - focal_px * camera_vertical / camera_forward
            pixels.append((x_px, y_px))
            feature_ranges.append(math.hypot(forward_m, height - geometry.radar_height_m))
        tracks.append(pixels)
        ranges.append(feature_ranges)
    return np.asarray(tracks), times, np.median(np.asarray(ranges), axis=0), ball, geometry


class TestChainedImpactDelivery:
    def test_recovers_known_impact_velocity(self):
        tracks, times, ranges, ball, geometry = _project_impact_tracks(
            path_deg=3.0,
            aoa_deg=-4.0,
        )

        result = delivery_from_feature_tracks(
            tracks,
            times,
            ranges,
            ball=ball,
            geometry=geometry,
            ops_club_speed_mph=35.0 * 2.23694,
            timing_plausible=True,
        )

        assert isinstance(result, ChainedDelivery)
        assert result.status == "chained_high"
        assert result.club_path_deg == pytest.approx(3.0, abs=0.15)
        assert result.attack_angle_deg == pytest.approx(-4.0, abs=0.15)
        assert result.n_features == 12

    def test_recovers_known_velocity_with_laterally_offset_camera(self):
        tracks, times, ranges, ball, geometry = _project_impact_tracks(
            path_deg=3.0,
            aoa_deg=-4.0,
            camera_lateral_offset_m=-0.08,
        )

        result = delivery_from_feature_tracks(
            tracks,
            times,
            ranges,
            ball=ball,
            geometry=geometry,
            ops_club_speed_mph=35.0 * 2.23694,
            timing_plausible=True,
        )

        assert result.club_path_deg == pytest.approx(3.0, abs=0.15)
        assert result.attack_angle_deg == pytest.approx(-4.0, abs=0.15)

    def test_mirrored_capture_preserves_physical_path_sign(self):
        tracks, times, ranges, ball, geometry = _project_impact_tracks(
            path_deg=3.0,
            aoa_deg=-4.0,
        )
        mirrored = tracks.copy()
        mirrored[:, :, 0] = geometry.image_width_px - mirrored[:, :, 0]

        result = delivery_from_feature_tracks(
            mirrored,
            times,
            ranges,
            ball=ball,
            geometry=replace(geometry, horizontal_pixel_sign=-1.0),
            ops_club_speed_mph=35.0 * 2.23694,
            timing_plausible=True,
        )

        assert result.club_path_deg == pytest.approx(3.0, abs=0.15)
        assert result.attack_angle_deg == pytest.approx(-4.0, abs=0.15)

    def test_approach_consensus_keeps_low_confidence_aoa_visible(self):
        path_windows = [
            ApproachPairEstimate(path, -4.0, 1.0, 1.0, 12) for path in (2.5, 3.0, 3.5, 4.0)
        ]
        implausible_aoa = ApproachPairEstimate(3.0, -22.0, 1.0, 1.0, 12)

        result = combine_approach_estimates(
            path_windows,
            attack_estimate=implausible_aoa,
            timing_plausible=True,
        )

        assert result.club_path_deg == pytest.approx(3.25)
        assert result.attack_angle_deg == pytest.approx(-22.0)
        assert result.path_confidence_tier == "high"
        assert result.attack_confidence_tier == "low"

    def test_impact_straddling_path_wins_over_broad_approach_consensus(self):
        path_windows = [
            ApproachPairEstimate(path, -4.0, 1.0, 1.0, 12) for path in (6.0, 7.0, 8.0, 9.0)
        ]
        impact_path = ApproachPairEstimate(3.0, -4.0, 1.0, 1.0, 12)

        result = combine_approach_estimates(
            path_windows,
            attack_estimate=impact_path,
            preferred_path_estimate=impact_path,
            timing_plausible=True,
        )

        assert result.club_path_deg == pytest.approx(3.0)

    def test_path_interval_priority_is_impact_then_final_approach(self):
        impact = ApproachPairEstimate(3.0, -4.0, 1.0, 1.0, 12)
        final_approach = ApproachPairEstimate(7.0, -4.0, 1.0, 1.0, 12)

        assert _preferred_path_estimate({(-4, -1): final_approach}) is final_approach
        assert (
            _preferred_path_estimate(
                {
                    (-4, -1): final_approach,
                    (-2, 1): impact,
                }
            )
            is impact
        )

    def test_approach_consensus_keeps_unstable_path_visible_as_low_confidence(self):
        path_windows = [
            ApproachPairEstimate(path, -4.0, 1.0, 1.0, 12) for path in (-13.0, 2.5, 5.0, 14.0)
        ]
        attack = ApproachPairEstimate(3.0, -4.2, 1.0, 1.0, 12)

        result = combine_approach_estimates(
            path_windows,
            attack_estimate=attack,
            timing_plausible=True,
        )

        assert result.club_path_deg == pytest.approx(3.75)
        assert result.attack_angle_deg == pytest.approx(-4.2)
        assert result.path_confidence_tier == "low"
        assert result.attack_confidence_tier == "high"

    def test_approach_consensus_assigns_medium_to_near_miss_aoa(self):
        paths = [ApproachPairEstimate(path, -4.0, 1.0, 1.0, 12) for path in (2.5, 3.0, 3.5, 4.0)]
        near_miss = ApproachPairEstimate(3.0, -16.0, 1.31, 3.0, 12)

        result = combine_approach_estimates(
            paths,
            attack_estimate=near_miss,
            timing_plausible=True,
        )

        assert result.attack_angle_deg == pytest.approx(-16.0)
        assert result.attack_confidence_tier == "medium"

    def test_ops_speed_mismatch_withholds_both_angles(self):
        tracks, times, ranges, ball, geometry = _project_impact_tracks(
            path_deg=3.0,
            aoa_deg=-4.0,
        )

        result = delivery_from_feature_tracks(
            tracks,
            times,
            ranges,
            ball=ball,
            geometry=geometry,
            ops_club_speed_mph=35.0,
            timing_plausible=True,
        )

        assert result.status == "rejected_speed_ratio"
        assert result.club_path_deg is None
        assert result.attack_angle_deg is None

    def test_static_image_features_do_not_steal_the_club_track(self):
        tracks, times, ranges, ball, geometry = _project_impact_tracks(
            path_deg=3.0,
            aoa_deg=-4.0,
        )
        static = np.repeat(tracks[:8, :1, :], 3, axis=1)
        tracks = np.concatenate((tracks, static), axis=0)

        result = delivery_from_feature_tracks(
            tracks,
            times,
            ranges,
            ball=ball,
            geometry=geometry,
            ops_club_speed_mph=35.0 * 2.23694,
            timing_plausible=True,
        )

        assert result.status == "chained_high"
        assert result.club_path_deg == pytest.approx(3.0, abs=0.15)
        assert result.attack_angle_deg == pytest.approx(-4.0, abs=0.15)
        assert result.n_features == 12

    def test_interval_disagreement_demotes_to_experimental(self):
        tracks, times, ranges, ball, geometry = _project_impact_tracks(
            path_deg=3.0,
            aoa_deg=-4.0,
        )
        # Move only the post-impact point sideways. The central interval stays
        # physically bounded, but pre/cross no longer corroborate each other.
        tracks[:, 2, 0] += 10.0

        result = delivery_from_feature_tracks(
            tracks,
            times,
            ranges,
            ball=ball,
            geometry=geometry,
            ops_club_speed_mph=35.0 * 2.23694,
            timing_plausible=True,
        )

        assert result.status == "chained_experimental"
        assert result.club_path_deg is not None
        assert result.attack_angle_deg is not None


def _project_camera_ops_pair(
    *,
    path_deg: float,
    aoa_deg: float,
    speed_ms: float = 35.0,
    camera_lateral_offset_m: float = -0.060325,
    camera_yaw_deg: float = 3.0,
):
    """Project impact-adjacent club features without supplying radar range."""
    camera_height_m = 0.2032
    radar_height_m = 0.1524
    ball_height_m = 0.04
    ball_forward_m = 1.52
    geometry = CameraDeliveryGeometry(
        camera_height_m=camera_height_m,
        radar_height_m=radar_height_m,
        tee_range_m=math.hypot(ball_forward_m, ball_height_m - radar_height_m),
        ball_height_m=ball_height_m,
        camera_lateral_offset_m=camera_lateral_offset_m,
        image_width_px=640,
        image_height_px=400,
    )
    focal_px = 980.0
    yaw = math.radians(camera_yaw_deg)
    path = math.radians(path_deg)
    aoa = math.radians(aoa_deg)
    forward_speed = speed_ms / math.sqrt(1.0 + math.tan(path) ** 2 + math.tan(aoa) ** 2)
    velocity = np.array(
        [
            forward_speed * math.tan(path),
            forward_speed,
            forward_speed * math.tan(aoa),
        ]
    )
    times = np.array([-0.002, 0.0])

    def project(world_xyz):
        delta = world_xyz - np.array([camera_lateral_offset_m, 0.0, camera_height_m])
        # Inverse of camera-heading rotation: world -> camera coordinates.
        camera_x = math.cos(yaw) * delta[0] - math.sin(yaw) * delta[1]
        camera_y = math.sin(yaw) * delta[0] + math.cos(yaw) * delta[1]
        camera_z = delta[2]
        return np.array(
            [
                geometry.image_width_px / 2 + focal_px * camera_x / camera_y,
                geometry.image_height_px / 2 - focal_px * camera_z / camera_y,
            ]
        )

    contact = np.array([0.0, ball_forward_m, ball_height_m])
    ball_px = project(contact)
    camera_ball_range = np.linalg.norm(contact - geometry.camera_origin)
    ball = ReferenceBall(
        x=float(ball_px[0]),
        y=float(ball_px[1]),
        diameter_px=focal_px * geometry.ball_diameter_m / camera_ball_range,
        area_px=400,
    )
    tracks = []
    for index in range(12):
        feature_offset = np.array([0.001 * (index - 5.5), 0.0, 0.001 * ((index % 3) - 1)])
        tracks.append([project(contact + feature_offset + velocity * time_s) for time_s in times])
    return np.asarray(tracks), times, ball, geometry


class TestCameraOpsFallback:
    def test_recovers_known_delivery_without_iwr_range(self):
        tracks, times, ball, geometry = _project_camera_ops_pair(
            path_deg=3.0,
            aoa_deg=-5.0,
        )

        result = camera_ops_delivery_from_feature_pair(
            tracks,
            times,
            ball=ball,
            geometry=geometry,
            ops_club_speed_mph=35.0 * 2.23694,
        )

        assert result is not None
        assert result.path_deg == pytest.approx(3.0, abs=0.25)
        assert result.attack_angle_deg == pytest.approx(-5.0, abs=0.25)

    def test_rejects_missing_or_nonphysical_ops_speed(self):
        tracks, times, ball, geometry = _project_camera_ops_pair(
            path_deg=3.0,
            aoa_deg=-5.0,
        )

        assert (
            camera_ops_delivery_from_feature_pair(
                tracks,
                times,
                ball=ball,
                geometry=geometry,
                ops_club_speed_mph=0.0,
            )
            is None
        )

    def test_mirrored_capture_preserves_fallback_path_sign(self):
        tracks, times, ball, geometry = _project_camera_ops_pair(
            path_deg=3.0,
            aoa_deg=-5.0,
        )
        mirrored_tracks = tracks.copy()
        mirrored_tracks[:, :, 0] = geometry.image_width_px - mirrored_tracks[:, :, 0]
        mirrored_ball = replace(ball, x=geometry.image_width_px - ball.x)

        result = camera_ops_delivery_from_feature_pair(
            mirrored_tracks,
            times,
            ball=mirrored_ball,
            geometry=replace(geometry, horizontal_pixel_sign=-1.0),
            ops_club_speed_mph=35.0 * 2.23694,
        )

        assert result is not None
        assert result.path_deg == pytest.approx(3.0, abs=0.25)
        assert result.attack_angle_deg == pytest.approx(-5.0, abs=0.25)

    def test_live_estimator_uses_low_confidence_fallback_without_iwr_range(self, monkeypatch):
        frames = np.full((60, 20, 20), 150, dtype=np.uint8)
        timestamps = np.arange(60, dtype=np.int64) * 2_000_000
        ball = ReferenceBall(10.0, 10.0, 12.0, 120)
        estimate = ApproachPairEstimate(3.0, -5.0, 1.0, 1.0, 12)
        monkeypatch.setattr(club_delivery_module, "detect_reference_ball", lambda _frames: ball)
        monkeypatch.setattr(
            club_delivery_module,
            "_detect_impact_index",
            lambda _frames, _ball, trigger_index: 40,
        )
        monkeypatch.setattr(
            club_delivery_module,
            "_clubhead_pair_tracks",
            lambda *_args, **_kwargs: (np.zeros((12, 2, 2)), 5.0),
        )
        monkeypatch.setattr(
            club_delivery_module,
            "camera_ops_delivery_from_feature_pair",
            lambda *_args, **_kwargs: estimate,
        )
        geometry = CameraDeliveryGeometry(
            camera_height_m=0.2032,
            radar_height_m=0.1524,
            tee_range_m=1.524,
            ball_height_m=0.04,
            image_width_px=20,
            image_height_px=20,
        )

        result = estimate_chained_delivery(
            frames,
            timestamps,
            trigger_index=40,
            range_evidence=None,
            geometry=geometry,
            ops_club_speed_mph=80.0,
        )

        assert result.status == "camera_ops_fallback"
        assert result.attack_angle_deg == pytest.approx(-5.0)
        assert result.club_path_deg == pytest.approx(3.0)
        assert result.attack_confidence_tier == "low"
        assert result.path_confidence_tier == "low"


# ---------------------------------------------------------------------------
# Per-club AoA offsets
# ---------------------------------------------------------------------------


class TestAoaOffsets:
    def test_measured_clubs_return_measured_values(self):
        assert aoa_offset_for_club(ClubType.IRON_9) == (16.0, "measured")
        assert aoa_offset_for_club(ClubType.IRON_7) == (10.5, "measured")
        assert aoa_offset_for_club(ClubType.IRON_5) == (9.1, "measured")
        assert aoa_offset_for_club(ClubType.DRIVER) == (21.6, "measured")

    def test_mid_iron_interpolates_between_anchors(self):
        offset, source = aoa_offset_for_club(ClubType.IRON_8)  # loft 38, between 34 and 42
        assert source == "loft_interpolated"
        assert 10.5 < offset < 16.0

    def test_interpolation_is_monotone_with_loft(self):
        six, _ = aoa_offset_for_club(ClubType.IRON_6)
        eight, _ = aoa_offset_for_club(ClubType.IRON_8)
        assert 9.1 <= six <= 10.5
        assert 10.5 <= eight <= 16.0

    def test_wedges_clamp_to_last_anchor_not_extrapolate(self):
        # No measurements above the 9-iron: clamping is the conservative call.
        for club in (ClubType.PW, ClubType.SW, ClubType.LW):
            offset, source = aoa_offset_for_club(club)
            assert offset == 16.0
            assert source == "loft_interpolated"

    def test_long_irons_clamp_to_first_anchor(self):
        offset, source = aoa_offset_for_club(ClubType.IRON_3)
        assert offset == 9.1
        assert source == "loft_interpolated"

    def test_woods_and_hybrids_borrow_driver_offset(self):
        for club in (ClubType.WOOD_3, ClubType.HYBRID_5):
            offset, source = aoa_offset_for_club(club)
            assert offset == 21.6
            assert source == "class_extrapolated"

    def test_unknown_club_uses_iron_line(self):
        offset, source = aoa_offset_for_club(ClubType.UNKNOWN)
        assert source == "loft_interpolated"
        assert offset == 10.5  # mid-iron default loft = the 7-iron anchor


# ---------------------------------------------------------------------------
# Fusion math
# ---------------------------------------------------------------------------


class TestFusion:
    def _trace(self, deg: float) -> TraceResult:
        return TraceResult(status="ok", trace_deg=deg, n_pairs=3)

    def test_fused_reproduces_offline_9i_chain(self):
        # Radar candidate -20.4 + 16.0 = -4.4; trace -48.3 -> path ~ +3.9
        fused = fuse_club_delivery(-20.4, self._trace(-48.3), ClubType.IRON_9)
        assert fused.status == "fused"
        assert fused.attack_angle_deg == pytest.approx(-4.4, abs=0.05)
        expected_path = math.degrees(
            math.atan(math.tan(math.radians(-4.4)) / math.tan(math.radians(-48.3)))
        )
        assert fused.club_path_deg == pytest.approx(expected_path, abs=0.05)
        assert fused.club_path_deg > 0  # descending blow on a -48 trace = in-to-out

    def test_positive_aoa_negative_trace_is_inconsistent(self):
        # +3.6 corrected AoA (up hit) with a negative trace cannot both be
        # true: the trace's sign IS the AoA's sign. Blocked, AoA kept.
        fused = fuse_club_delivery(-18.0, self._trace(-48.0), ClubType.DRIVER)
        assert fused.status == "trace_aoa_sign_mismatch"
        assert fused.attack_angle_deg == pytest.approx(3.6, abs=0.05)
        assert fused.club_path_deg is None

    def test_no_radar_aoa(self):
        fused = fuse_club_delivery(None, self._trace(-48.0), ClubType.IRON_9)
        assert fused.status == "no_radar_aoa"
        assert fused.attack_angle_deg is None
        assert fused.club_path_deg is None

    def test_trace_failure_still_reports_corrected_aoa(self):
        fused = fuse_club_delivery(-20.4, TraceResult(status="low_light"), ClubType.IRON_9)
        assert fused.status == "trace_low_light"
        assert fused.attack_angle_deg == pytest.approx(-4.4, abs=0.05)
        assert fused.club_path_deg is None

    @pytest.mark.parametrize("trace_deg", [5.0, -10.0, 88.0, -89.0])
    def test_trace_out_of_range_blocks_path(self, trace_deg):
        fused = fuse_club_delivery(-20.4, self._trace(trace_deg), ClubType.IRON_9)
        assert fused.status == "trace_out_of_range"
        assert fused.club_path_deg is None
        assert fused.attack_angle_deg is not None

    def test_sign_mismatch_blocks_path(self):
        # Negative corrected AoA with a positive trace = camera locked onto
        # the wrong mover (observed live: flying-ball lock gave trace +19).
        fused = fuse_club_delivery(-20.4, self._trace(19.5), ClubType.IRON_9)
        assert fused.status == "trace_aoa_sign_mismatch"
        assert fused.club_path_deg is None
        assert fused.attack_angle_deg == pytest.approx(-4.4, abs=0.05)

    def test_driver_positive_aoa_positive_trace_is_legal(self):
        fused = fuse_club_delivery(-18.0, self._trace(78.0), ClubType.DRIVER)
        assert fused.status == "fused"  # +3.6 AoA with +78 trace is consistent
        assert fused.club_path_deg is not None and fused.club_path_deg > 0

    def test_offset_source_propagates(self):
        fused = fuse_club_delivery(-15.0, self._trace(-50.0), ClubType.IRON_8)
        assert fused.offset_source == "loft_interpolated"

    def test_returns_dataclass(self):
        assert isinstance(
            fuse_club_delivery(None, TraceResult(status="no_capture"), ClubType.IRON_9),
            FusedDelivery,
        )


# ---------------------------------------------------------------------------
# Trace estimation on synthetic captures
# ---------------------------------------------------------------------------

HEIGHT, WIDTH = 400, 640  # production capture geometry
BALL_XY = (400, 200)
FPS = 288.0


def _synthetic_capture(
    *,
    scene_level: int = 60,
    ball_bright: int = 245,
    club_bright: int = 240,
    club_velocity_px=(30.0, -18.0),
    n_frames: int = 40,
    impact_frame: int = 30,
) -> tuple[np.ndarray, np.ndarray]:
    """DTL-like capture: static bright ball on tee, bright club sweeping in.

    The club is a diagonal line segment with a blob (head) at its lower end,
    moving at ``club_velocity_px`` per frame toward the ball; the ball
    disappears after ``impact_frame``.
    """
    rng = np.random.default_rng(7)
    frames = np.full((n_frames, HEIGHT, WIDTH), scene_level, dtype=np.uint8)
    frames += rng.integers(0, 8, size=frames.shape, dtype=np.uint8)
    # a static bright distractor (ball pile) far from the tee
    frames[:, 360:380, 40:80] = 230
    # bright static "sky/background" band so scene brightness matches a real
    # daylight capture (the light gate keys on the background's p99.5)
    frames[:, 0:28, :] = min(255, int(scene_level * 2.4))

    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    ball_mask = (xx - BALL_XY[0]) ** 2 + (yy - BALL_XY[1]) ** 2 <= 5 * 5

    vx, vy = club_velocity_px
    for idx in range(n_frames):
        if idx <= impact_frame:
            frames[idx][ball_mask] = ball_bright
        steps_to_impact = impact_frame + 0.5 - idx
        head_x = BALL_XY[0] - vx * steps_to_impact
        head_y = BALL_XY[1] - vy * steps_to_impact
        if not (0 <= head_x < WIDTH and 0 <= head_y < HEIGHT):
            continue
        if abs(head_x - BALL_XY[0]) < 8 and abs(head_y - BALL_XY[1]) < 8 and idx <= impact_frame:
            continue  # don't overdraw the ball pre-impact
        # shaft: connected bright line from the head up-left; head: small blob
        import cv2

        cv2.line(
            frames[idx],
            (int(round(head_x)), int(round(head_y))),
            (int(round(head_x - 90)), int(round(head_y - 140))),
            int(club_bright),
            3,
        )
        hx, hy = int(round(head_x)), int(round(head_y))
        head_blob = (xx - hx) ** 2 + (yy - hy) ** 2 <= 6 * 6
        frames[idx][head_blob] = club_bright - 60  # head darker than shaft

    ts = (np.arange(n_frames) * (1e9 / FPS)).astype(np.int64)
    return frames, ts


class TestTraceEstimation:
    def test_bright_scene_produces_trace(self):
        frames, ts = _synthetic_capture()
        result = estimate_delivery_trace(frames, ts)
        assert result.status == "ok", result
        assert result.n_pairs >= 2
        # velocity (30, -18) px/frame, image y down -> trace = atan2(18, 30)
        expected = math.degrees(math.atan2(18.0, 30.0))
        assert result.trace_deg == pytest.approx(expected, abs=12.0)

    def test_impact_frame_detected_near_truth(self):
        frames, ts = _synthetic_capture(impact_frame=30)
        result = estimate_delivery_trace(frames, ts)
        assert result.impact_frame is not None
        assert abs(result.impact_frame - 30) <= 1

    def test_dim_scene_reports_low_light(self):
        frames, ts = _synthetic_capture(scene_level=20, ball_bright=90, club_bright=85)
        result = estimate_delivery_trace(frames, ts)
        assert result.status == "low_light"
        assert result.trace_deg is None
        assert result.scene_p995 is not None
        assert result.scene_p995 < SCENE_P995_MIN

    def test_saturation_near_ball_reports_overexposed(self):
        frames, ts = _synthetic_capture()
        # hot patch on the mat right next to the ball (inside the ball zone)
        frames[:, BALL_XY[1] + 30 : BALL_XY[1] + 90, BALL_XY[0] - 60 : BALL_XY[0] + 60] = 255
        result = estimate_delivery_trace(frames, ts)
        assert result.status == "overexposed"
        assert result.trace_deg is None

    def test_dappled_background_saturation_is_tolerated(self):
        frames, ts = _synthetic_capture()
        frames[:, 0:80, 0:200] = 255  # sunlit trees far from the hitting zone
        result = estimate_delivery_trace(frames, ts)
        assert result.status == "ok"

    def test_impact_far_from_trigger_is_implausible(self):
        # A never-departing ball patch (wrong-blob lock) pins "impact" to the
        # buffer end; the trigger-window check must catch it.
        frames, ts = _synthetic_capture(impact_frame=39)  # ball never leaves
        frames[39:, :, :] = frames[38]  # freeze the scene: ball stays put
        result = estimate_delivery_trace(frames, ts, trigger_index=20)
        assert result.status in ("impact_implausible", "no_impact")
        assert result.trace_deg is None

    def test_no_ball_status(self):
        frames, ts = _synthetic_capture()
        # erase the ball everywhere (keep the scene bright via the distractor)
        yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
        ball_mask = (xx - BALL_XY[0]) ** 2 + (yy - BALL_XY[1]) ** 2 <= 6 * 6
        frames[:, ball_mask] = 60
        frames[:, 10:60, 10:60] = 235  # keep p99.5 above the light gate
        result = estimate_delivery_trace(frames, ts)
        assert result.status in ("no_ball", "no_impact", "insufficient_pairs")
        assert result.trace_deg is None

    def test_rejects_malformed_input(self):
        result = estimate_delivery_trace(np.zeros((5, 4, 4), dtype=np.uint8), np.arange(5))
        assert result.status == "error"

    def test_320x200_capture_prefers_bright_ball_over_dark_background_blob(self):
        import cv2

        frames = np.full((20, 200, 320), 70, dtype=np.uint8)
        frames[:, :20, :] = 165
        for frame in frames:
            cv2.circle(frame, (155, 105), 8, 35, -1)
            cv2.circle(frame, (160, 140), 6, 245, -1)

        ball = detect_reference_ball(frames, brightness_threshold=220)

        assert ball.x == pytest.approx(160.0, abs=1.0)
        assert ball.y == pytest.approx(140.0, abs=1.0)
        assert ball.diameter_px == pytest.approx(12.0, abs=2.0)

    def test_320x200_capture_produces_impact_trace(self):
        import cv2

        frames = np.full((99, 200, 320), 70, dtype=np.uint8)
        frames += np.random.default_rng(7).integers(0, 8, size=frames.shape, dtype=np.uint8)
        frames[:, :20, :] = 165
        ball_x, ball_y = 160, 140
        impact_frame = 73
        yy, xx = np.mgrid[:200, :320]
        ball_mask = (xx - ball_x) ** 2 + (yy - ball_y) ** 2 <= 6**2
        for frame_index, frame in enumerate(frames):
            cv2.circle(frame, (155, 105), 8, 35, -1)
            if frame_index <= impact_frame:
                frame[ball_mask] = 245
            steps_to_impact = impact_frame + 0.5 - frame_index
            head_x = ball_x - 8.0 * steps_to_impact
            head_y = ball_y + 5.0 * steps_to_impact
            if 0 <= head_x < 320 and 0 <= head_y < 200:
                cv2.line(
                    frame,
                    (round(head_x), round(head_y)),
                    (round(head_x + 45), round(head_y - 70)),
                    240,
                    2,
                )
                cv2.circle(frame, (round(head_x), round(head_y)), 5, 180, -1)
        timestamps_ns = (np.arange(len(frames)) * (1e9 / 468.0)).astype(np.int64)

        result = estimate_delivery_trace(frames, timestamps_ns, trigger_index=impact_frame)

        assert result.status == "ok", result
        assert result.impact_frame == pytest.approx(impact_frame, abs=1)
        assert result.n_pairs >= 2


class TestVelocityAngleProjection:
    """Attack angle is the elevation of a 3D velocity, not a 2D ratio.

    ``_velocity_angles`` returned ``atan2(vertical, forward)``, which is the
    elevation only when the club path is zero. The exact elevation divides by
    the full horizontal speed, so the error scales as ``cos(path)``.

    Measured on session 20260825_181734 this is +0.052 deg mean and 0.347 deg
    max at the observed fused club paths -- small, but a systematic,
    path-correlated bias, and it grows to 0.60 deg at a 30 deg path.
    """

    def test_attack_angle_uses_total_horizontal_speed(self):
        """A velocity with lateral motion must not inflate the attack angle."""
        # 10 m/s lateral, 10 m/s vertical, 10 m/s forward.
        # forward-only ratio  -> atan2(10, 10)            = 45 deg
        # true elevation      -> atan2(10, hypot(10, 10)) = 35.264 deg
        velocity = np.array([10.0, 10.0, 10.0])

        path_deg, attack_deg = club_delivery_module._velocity_angles(velocity)

        assert path_deg == pytest.approx(45.0, abs=1e-9)
        assert attack_deg == pytest.approx(math.degrees(math.atan2(10.0, math.hypot(10.0, 10.0))))
        assert attack_deg == pytest.approx(35.26438968, abs=1e-6)

    def test_zero_path_is_unchanged(self):
        """With no lateral motion the two formulations must agree exactly."""
        velocity = np.array([0.0, -3.0, 40.0])

        _path_deg, attack_deg = club_delivery_module._velocity_angles(velocity)

        assert attack_deg == pytest.approx(math.degrees(math.atan2(-3.0, 40.0)))

    def test_club_path_still_uses_forward_speed(self):
        """Path is an azimuth in the horizontal plane and must not change."""
        velocity = np.array([5.0, 99.0, 20.0])

        path_deg, _attack_deg = club_delivery_module._velocity_angles(velocity)

        assert path_deg == pytest.approx(math.degrees(math.atan2(5.0, 20.0)))
