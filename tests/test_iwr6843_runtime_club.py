"""Runtime forwards per-shot geometry and club-path inputs."""

import math
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.club import ClubPathResult
from openflight.iwr6843.lcmf import LCMFResult
from openflight.iwr6843.recovery import RecoveryCandidate
from openflight.iwr6843.runtime import IWR6843Runtime
from openflight.iwr6843.tracking import BallTrack


@pytest.fixture(autouse=True)
def _prepared_capture():
    """Runtime unit tests mock LCMF, so they also mock its decode boundary."""
    with patch(
        "openflight.iwr6843.runtime.prepare_lcmf_capture",
        return_value=SimpleNamespace(vertical=object()),
    ):
        yield


class FakeCapture:
    valid = True
    raw = b"x" * 32
    path = None
    error = None
    trigger_timestamp = 1.0
    dump_duration_s = 5.3
    sequence = 1


class FakeMonitor:
    def capture_for_shot(self, _ts, timeout_s):
        return FakeCapture()

    def stop(self):
        pass


def _runtime(**kwargs):
    return IWR6843Runtime(
        capture_monitor=FakeMonitor(),
        calibration=object(),
        net_range_m=4.064,
        **kwargs,
    )


def _candidate(speed_mph=100.0):
    speed_ms = speed_mph / 2.23694
    return RecoveryCandidate(
        track=BallTrack(
            speed_ms=speed_ms,
            slope_bins=100.0,
            intercept_bins=20.0,
            rms_bins=0.30,
            n_inliers=24,
            t_first=0.010,
            t_last=0.030,
            low_confidence=False,
        ),
        scope="window",
        impact_s=0.012,
        speed_ratio=speed_mph / 100.0,
    )


def test_ops_guided_estimator_leaves_speed_consistent_measurement_unchanged():
    baseline = LCMFResult(status="accepted", angle_deg=20.0, track_speed_mph=105.0)

    with (
        patch("openflight.iwr6843.runtime.estimate_lcmf_v1", return_value=baseline),
        patch("openflight.iwr6843.runtime.find_recovery_candidates") as find_candidates,
    ):
        result = _runtime().process_shot(
            impact_timestamp=1.0,
            ball_speed_mph=100.0,
            club="9i",
        )

    assert result.measurement is baseline
    find_candidates.assert_not_called()


def test_ops_guided_estimator_replaces_a_speed_mismatch_and_marks_single_channel():
    baseline = LCMFResult(status="accepted", angle_deg=10.7, track_speed_mph=130.0)
    recovered = LCMFResult(
        status="accepted_low_confidence_recovery",
        angle_deg=16.8,
        track_speed_mph=100.0,
        n_frames=6,
        single_channel=True,
    )
    candidate = _candidate()

    with (
        patch(
            "openflight.iwr6843.runtime.estimate_lcmf_v1",
            side_effect=[baseline, recovered],
        ) as estimate,
        patch(
            "openflight.iwr6843.runtime.find_recovery_candidates",
            return_value=[candidate],
        ) as find_candidates,
        patch("openflight.iwr6843.runtime.estimate_club_path", return_value=None),
    ):
        result = _runtime().process_shot(
            impact_timestamp=1.0,
            ball_speed_mph=100.0,
            club="9i",
        )

    assert result.measurement.angle_deg == pytest.approx(16.8)
    assert result.measurement.status == "accepted_ops_guided_single_channel"
    assert estimate.call_count == 2
    prepared = estimate.call_args_list[0].kwargs["prepared"]
    assert estimate.call_args_list[1].kwargs["prepared"] is prepared
    assert find_candidates.call_args.kwargs["prepared"] is prepared.vertical
    assert estimate.call_args_list[1].kwargs["track_override"] is candidate.track
    assert estimate.call_args_list[1].kwargs["track_override_scope"] == "window"


def test_ops_guided_estimator_warns_when_a_speed_mismatch_has_no_replacement():
    baseline = LCMFResult(status="accepted", angle_deg=10.7, track_speed_mph=130.0)

    with (
        patch("openflight.iwr6843.runtime.estimate_lcmf_v1", return_value=baseline),
        patch(
            "openflight.iwr6843.runtime.find_recovery_candidates",
            return_value=[],
        ),
    ):
        result = _runtime().process_shot(
            impact_timestamp=1.0,
            ball_speed_mph=100.0,
            club="9i",
        )

    assert result.measurement.angle_deg == pytest.approx(10.7)
    assert result.measurement.status == "accepted_track_speed_warning"


def test_ops_guided_estimator_preserves_baseline_when_candidate_search_fails():
    baseline = LCMFResult(status="accepted", angle_deg=10.7, track_speed_mph=130.0)

    with (
        patch("openflight.iwr6843.runtime.estimate_lcmf_v1", return_value=baseline),
        patch(
            "openflight.iwr6843.runtime.find_recovery_candidates",
            side_effect=ValueError("bad recovery input"),
        ),
    ):
        result = _runtime().process_shot(
            impact_timestamp=1.0,
            ball_speed_mph=100.0,
            club="9i",
        )

    assert result.measurement.angle_deg == pytest.approx(10.7)
    assert result.measurement.status == "accepted_track_speed_warning"


def test_ops_guided_estimator_rejects_a_below_horizon_candidate():
    baseline = LCMFResult(status="accepted", angle_deg=15.6, track_speed_mph=125.0)
    collapsed = LCMFResult(
        status="accepted_low_confidence_recovery",
        angle_deg=-0.1,
        track_speed_mph=100.0,
        n_frames=6,
    )
    plausible = LCMFResult(
        status="accepted_low_confidence_recovery",
        angle_deg=9.4,
        track_speed_mph=99.0,
        n_frames=5,
        component_std_deg=2.5,
    )

    with (
        patch(
            "openflight.iwr6843.runtime.estimate_lcmf_v1",
            side_effect=[baseline, collapsed, plausible],
        ),
        patch(
            "openflight.iwr6843.runtime.find_recovery_candidates",
            return_value=[_candidate(100.0), _candidate(99.0)],
        ),
    ):
        result = _runtime().process_shot(
            impact_timestamp=1.0,
            ball_speed_mph=100.0,
            club="9i",
        )

    assert result.measurement.angle_deg == pytest.approx(9.4)
    assert result.measurement.status == "accepted_ops_guided"


def test_ops_guided_estimator_prefers_dual_channel_corroboration():
    baseline = LCMFResult(status="accepted", angle_deg=6.8, track_speed_mph=130.0)
    single = LCMFResult(
        status="accepted_low_confidence_recovery",
        angle_deg=4.6,
        track_speed_mph=100.0,
        n_frames=5,
        single_channel=True,
    )
    dual = LCMFResult(
        status="accepted_low_confidence_recovery",
        angle_deg=16.5,
        track_speed_mph=99.0,
        n_frames=6,
        component_std_deg=0.1,
    )

    with (
        patch(
            "openflight.iwr6843.runtime.estimate_lcmf_v1",
            side_effect=[baseline, single, dual],
        ),
        patch(
            "openflight.iwr6843.runtime.find_recovery_candidates",
            return_value=[_candidate(100.0), _candidate(99.0)],
        ),
    ):
        result = _runtime().process_shot(
            impact_timestamp=1.0,
            ball_speed_mph=100.0,
            club="9i",
        )

    assert result.measurement.angle_deg == pytest.approx(16.5)
    assert result.measurement.status == "accepted_ops_guided"


def test_club_path_receives_ball_tdm_sign():
    seen = {}

    def fake_club(raw, cal, **kw):
        seen.update(kw)
        return ClubPathResult(status="accepted", path_deg=2.0, confidence=0.8)

    class Measurement:
        tdm_sign_used = -1

    with (
        patch("openflight.iwr6843.runtime.estimate_lcmf_v1", return_value=Measurement()),
        patch("openflight.iwr6843.runtime.estimate_club_path", side_effect=fake_club),
    ):
        result = _runtime().process_shot(
            impact_timestamp=1.0, ball_speed_mph=100.0, club="7i", club_speed_mph=74.0
        )

    assert result.club_path.path_deg == 2.0
    assert seen["tdm_sign"] == -1
    assert seen["ops_club_speed_mph"] == 74.0


def test_club_path_receives_the_configured_azimuth_offset():
    """The aim offset must cross the runtime -> estimator boundary.

    Club path is an absolute angle and the offset enters additively, so a
    boresight that is not the target line shifts every reported path by that
    amount. Coverage stopped one step short on each side: test_server.py
    checks the CLI value reaches ``IWR6843Runtime.azimuth_offset_deg``, and the
    tests around this one check that ``tdm_sign`` and ``ops_club_speed_mph``
    reach ``estimate_club_path`` -- but nothing checked that *this* value
    crosses *that* boundary. Replacing ``aim_offset_deg=self.azimuth_offset_deg``
    with a literal ``0.0`` therefore left the entire suite green while silently
    reporting club path relative to boresight instead of the target line, which
    is precisely the error the flag exists to correct.

    A non-zero offset is required here: with 0.0 the assertion would pass
    against the hardcoded literal too.
    """
    seen = {}

    def fake_club(raw, cal, **kw):
        seen.update(kw)
        return ClubPathResult(status="accepted", path_deg=2.0, confidence=0.8)

    class Measurement:
        tdm_sign_used = 1

    with (
        patch("openflight.iwr6843.runtime.estimate_lcmf_v1", return_value=Measurement()),
        patch("openflight.iwr6843.runtime.estimate_club_path", side_effect=fake_club),
    ):
        _runtime(azimuth_offset_deg=1.5).process_shot(
            impact_timestamp=1.0, ball_speed_mph=100.0, club="7i", club_speed_mph=74.0
        )

    assert seen["aim_offset_deg"] == 1.5


def test_azimuth_offset_corrects_horizontal_launch():
    measurement = LCMFResult(
        status="accepted",
        angle_deg=18.0,
        horizontal_deg=3.3,
        horizontal_status="hlcmf_v0_accepted",
    )
    with patch("openflight.iwr6843.runtime.estimate_lcmf_v1", return_value=measurement):
        result = _runtime(azimuth_offset_deg=-3.0).process_shot(
            impact_timestamp=1.0,
            ball_speed_mph=100.0,
            club="9i",
            club_speed_mph=None,
        )

    assert result.measurement.horizontal_deg == pytest.approx(0.3)
    assert result.measurement.horizontal_raw_deg == pytest.approx(3.3)


def test_lcmf_receives_the_configured_horizontal_phase_reference():
    seen = {}

    def fake_lcmf(raw, cal, **kwargs):
        seen.update(kwargs)
        return None

    with patch("openflight.iwr6843.runtime.estimate_lcmf_v1", side_effect=fake_lcmf):
        _runtime(horizontal_phase_reference_rad=-0.493587).process_shot(
            impact_timestamp=1.0,
            ball_speed_mph=100.0,
            club="7i",
        )

    assert seen["horizontal_phase_reference_rad"] == pytest.approx(-0.493587)


def test_club_path_receives_the_configured_horizontal_phase_reference():
    seen = {}

    def fake_club(raw, cal, **kwargs):
        seen.update(kwargs)
        return ClubPathResult(status="accepted", path_deg=2.0, confidence=0.8)

    class Measurement:
        tdm_sign_used = 1
        impact_t_s = 0.04

    with (
        patch("openflight.iwr6843.runtime.estimate_lcmf_v1", return_value=Measurement()),
        patch("openflight.iwr6843.runtime.estimate_club_path", side_effect=fake_club),
    ):
        _runtime(horizontal_phase_reference_rad=-0.493587).process_shot(
            impact_timestamp=1.0,
            ball_speed_mph=100.0,
            club="7i",
            club_speed_mph=74.0,
        )

    assert seen["phase_reference_rad"] == pytest.approx(-0.493587)


def test_falls_back_to_policy_sign_when_ball_has_none():
    seen = {}

    def fake_club(raw, cal, **kw):
        seen.update(kw)
        return ClubPathResult(status="accepted", path_deg=1.0)

    class Measurement:
        tdm_sign_used = None

    with (
        patch("openflight.iwr6843.runtime.estimate_lcmf_v1", return_value=Measurement()),
        patch("openflight.iwr6843.runtime.estimate_club_path", side_effect=fake_club),
    ):
        result = _runtime().process_shot(
            impact_timestamp=1.0, ball_speed_mph=100.0, club="7i", club_speed_mph=74.0
        )

    assert seen["tdm_sign"] == 1, "policy default is positive"
    assert result.club_path.status.endswith("tdm_sign_fallback")


def test_no_club_speed_skips_the_estimate():
    """Without OPS club speed there is no identity gate, so do not guess."""
    with patch("openflight.iwr6843.runtime.estimate_lcmf_v1", return_value=None):
        result = _runtime().process_shot(
            impact_timestamp=1.0, ball_speed_mph=100.0, club="7i", club_speed_mph=None
        )
    assert result.club_path is None


def test_per_shot_tilt_uses_a_calibration_copy_without_mutating_runtime():
    calibration = Calibration.identity()
    runtime = IWR6843Runtime(
        capture_monitor=FakeMonitor(),
        calibration=calibration,
        net_range_m=4.064,
    )
    seen = {}

    def fake_lcmf(_raw, shot_calibration, **_kwargs):
        seen["calibration"] = shot_calibration
        return None

    with patch("openflight.iwr6843.runtime.estimate_lcmf_v1", side_effect=fake_lcmf):
        runtime.process_shot(
            impact_timestamp=1.0,
            ball_speed_mph=100.0,
            club="7i",
            tilt_deg=13.5,
        )

    assert seen["calibration"] is not calibration
    assert math.degrees(seen["calibration"].tilt_rad) == pytest.approx(13.5)
    assert calibration.tilt_rad == 0.0


def test_recovered_ball_impact_anchor_is_used_for_club_range_search():
    """A rejected vertical angle may still anchor experimental club delivery."""
    seen = {}
    runtime = _runtime()
    runtime.recovery_observations = [
        (1.0, 0.016, 0.040),
        (1.01, 0.017, 0.041),
        (0.99, 0.015, 0.039),
    ]
    measurement = LCMFResult(status="rejected_track_quality", impact_t_s=None)

    def fake_club(_raw, _cal, **kwargs):
        seen.update(kwargs)
        return ClubPathResult(status="rejected_phase_span")

    candidate = type("Candidate", (), {"impact_s": 0.019})()
    with (
        patch("openflight.iwr6843.runtime.estimate_lcmf_v1", return_value=measurement),
        patch("openflight.iwr6843.runtime.RecoveryPrior.fit"),
        patch("openflight.iwr6843.runtime.find_recovery_candidates", return_value=[candidate]),
        patch("openflight.iwr6843.runtime.select_recovery_candidate", return_value=candidate),
        patch("openflight.iwr6843.runtime.estimate_club_path", side_effect=fake_club),
    ):
        result = runtime.process_shot(
            impact_timestamp=1.0,
            ball_speed_mph=100.0,
            club="9i",
            club_speed_mph=74.0,
        )

    assert result.club_path is not None
    assert seen["impact_t_s"] == pytest.approx(0.017)
    assert "recovered_impact" in result.club_path.status


def test_ball_estimate_receives_the_configured_tdm_sign_policy():
    """``tdm_sign_policy`` must cross the runtime -> LCMF boundary.

    Same trap as the azimuth-offset test above: the dataclass exposes a
    configurable ``tdm_sign_policy``, and the club-path fallback honors it,
    but the ball-estimate call passed a hardcoded ``"positive"`` literal.
    ``replay.replay_capture`` plumbs a caller-supplied policy end to end, so
    any non-default setting silently produced different live-vs-replay
    answers for the same capture. A non-default value is required here: with
    ``"positive"`` the assertion would pass against the literal too.
    """
    seen = {}

    def fake_lcmf(raw, cal, **kw):
        seen.update(kw)

        class Measurement:
            tdm_sign_used = 1

        return Measurement()

    with patch("openflight.iwr6843.runtime.estimate_lcmf_v1", side_effect=fake_lcmf):
        _runtime(tdm_sign_policy="auto").process_shot(
            impact_timestamp=1.0, ball_speed_mph=100.0, club="7i", club_speed_mph=None
        )

    assert seen["tdm_sign_policy"] == "auto"


def test_ball_estimate_receives_horizontal_phase_reference():
    seen = {}

    def fake_lcmf(raw, cal, **kwargs):
        seen.update(kwargs)
        return None

    with patch("openflight.iwr6843.runtime.estimate_lcmf_v1", side_effect=fake_lcmf):
        _runtime(horizontal_phase_reference_rad=-0.5).process_shot(
            impact_timestamp=1.0,
            ball_speed_mph=100.0,
            club="7i",
        )

    assert seen["horizontal_phase_reference_rad"] == pytest.approx(-0.5)


def test_azimuth_offset_corrects_horizontal_ball_launch():
    measurement = LCMFResult(
        status="accepted",
        angle_deg=18.0,
        horizontal_deg=3.3,
        horizontal_status="hlcmf_v0_accepted",
    )
    with patch("openflight.iwr6843.runtime.estimate_lcmf_v1", return_value=measurement):
        result = _runtime(azimuth_offset_deg=-3.0).process_shot(
            impact_timestamp=1.0,
            ball_speed_mph=100.0,
            club="9i",
        )

    assert result.measurement.horizontal_deg == pytest.approx(0.3)
    assert result.measurement.horizontal_raw_deg == pytest.approx(3.3)
