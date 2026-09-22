"""Tests for deterministic high-speed camera auto exposure."""

import numpy as np
import pytest

from openflight.camera.auto_exposure import (
    EXPOSURE_STEPS,
    AutoExposurePolicy,
    ExposureObservation,
    exposure_steps_for_fps,
    measure_exposure,
    motion_blur_risk,
)


def observation(status, recommendation, *, median=50.0, p90=100.0):
    """Build one deterministic policy observation."""
    return ExposureObservation(
        sample_available=True,
        status=status,
        recommendation=recommendation,
        message=status,
        p10=10.0,
        median=median,
        p90=p90,
        contrast=p90 - 10.0,
        clipped_pct=0.0,
        dark_pct=0.0,
    )


def test_exposure_ladder_is_monotonic_and_respects_frame_period():
    assert [step.signal for step in EXPOSURE_STEPS] == sorted(
        step.signal for step in EXPOSURE_STEPS
    )
    assert exposure_steps_for_fps(488.0) == EXPOSURE_STEPS
    assert max(step.exposure_us for step in exposure_steps_for_fps(1000.0)) < 1000
    with pytest.raises(ValueError, match="positive"):
        exposure_steps_for_fps(0)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(18, "too_dark"), (110, "good"), (252, "too_bright")],
)
def test_measure_exposure_classifies_impact_zone(value, expected):
    image = np.full((200, 320), value, dtype=np.uint8)
    if expected == "good":
        image[100:175, 90:230] = np.tile(
            np.linspace(45, 185, 140, dtype=np.uint8),
            (75, 1),
        )

    result = measure_exposure(image)

    assert result.status == expected
    assert result.sample_available is True


def test_measure_exposure_flags_nearly_black_view():
    result = measure_exposure(np.zeros((200, 320), dtype=np.uint8))

    assert result.status == "too_dark"
    assert "lens cover" in result.message


def test_startup_dark_scene_jumps_more_than_one_step():
    policy = AutoExposurePolicy(fps=488.0)

    decision = policy.evaluate(
        observation("too_dark", "brighter", median=18.0, p90=40.0),
        exposure_us=250,
        gain=4.0,
    )

    assert decision.status == "adjusting"
    assert decision.target is not None
    assert decision.target.signal > EXPOSURE_STEPS[4].signal
    assert decision.analysis_eligible is False


def test_startup_bright_scene_jumps_darker():
    policy = AutoExposurePolicy(fps=488.0)

    decision = policy.evaluate(
        observation("too_bright", "darker", median=240.0, p90=255.0),
        exposure_us=800,
        gain=18.0,
    )

    assert decision.target is not None
    assert decision.target.signal < 800 * 18


def test_acceptable_startup_observation_marks_analysis_ready():
    policy = AutoExposurePolicy(fps=488.0)

    decision = policy.evaluate(
        observation("good", "hold", median=100.0),
        exposure_us=500,
        gain=12.0,
    )

    assert decision.status == "ready"
    assert decision.analysis_eligible is True
    assert not decision.should_apply


def test_ready_setting_stays_locked_until_the_next_startup():
    policy = AutoExposurePolicy(fps=488.0)
    ready = policy.evaluate(
        observation("good", "hold"),
        exposure_us=500,
        gain=12.0,
    )

    changed_scene = policy.evaluate(
        observation("too_dark", "brighter", median=18.0, p90=40.0),
        exposure_us=500,
        gain=12.0,
    )
    policy.reset()
    next_startup = policy.evaluate(
        observation("too_dark", "brighter", median=18.0, p90=40.0),
        exposure_us=500,
        gain=12.0,
    )

    assert changed_scene is ready
    assert changed_scene.status == "ready"
    assert not changed_scene.should_apply
    assert next_startup.status == "adjusting"
    assert next_startup.should_apply
    assert policy.startup is True


def test_ladder_limit_stays_locked_until_the_next_startup():
    policy = AutoExposurePolicy(fps=488.0)
    darkest = EXPOSURE_STEPS[-1]

    failed = policy.evaluate(
        observation("too_dark", "brighter", median=10.0, p90=20.0),
        exposure_us=darkest.exposure_us,
        gain=darkest.gain,
    )
    locked = policy.evaluate(
        observation("marginal", "brighter", median=40.0, p90=95.0),
        exposure_us=darkest.exposure_us,
        gain=darkest.gain,
    )
    policy.reset()
    recovered = policy.evaluate(
        observation("marginal", "brighter", median=40.0, p90=95.0),
        exposure_us=darkest.exposure_us,
        gain=darkest.gain,
    )

    assert failed.status == "lighting_required"
    assert failed.analysis_eligible is False
    assert "add or redirect light" in failed.message
    assert locked is failed
    assert recovered.status == "ready"
    assert recovered.analysis_eligible is True


def test_startup_attempt_limit_requires_lighting_change():
    policy = AutoExposurePolicy(fps=488.0, startup_max_adjustments=1)
    dark = observation("too_dark", "brighter", median=18.0, p90=40.0)
    first = policy.evaluate(dark, exposure_us=250, gain=4.0)

    failed = policy.evaluate(
        dark,
        exposure_us=first.target.exposure_us,
        gain=first.target.gain,
    )

    assert failed.status == "lighting_required"
    assert not failed.should_apply


@pytest.mark.parametrize(
    ("exposure_us", "expected"),
    [(500, "low"), (650, "elevated"), (1000, "high")],
)
def test_motion_blur_risk(exposure_us, expected):
    assert motion_blur_risk(exposure_us) == expected
