"""Deterministic exposure selection for the high-speed OV9281 camera."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Literal

import numpy as np

ExposureQualityStatus = Literal[
    "good",
    "marginal",
    "too_dark",
    "too_bright",
    "unavailable",
]
ExposureRecommendation = Literal["brighter", "darker", "hold"]
AutoExposureStatus = Literal[
    "ready",
    "adjusting",
    "lighting_required",
    "unavailable",
]


@dataclass(frozen=True)
class ExposureStep:
    """One validated manual exposure/gain pair in increasing brightness order."""

    exposure_us: int
    gain: float
    label: str | None = None

    @property
    def signal(self) -> float:
        """Approximate relative image signal used for startup targeting."""
        return self.exposure_us * self.gain

    def to_dict(self) -> dict:
        """Serialize the step for API consumers."""
        return asdict(self)


EXPOSURE_STEPS = (
    ExposureStep(100, 2.0),
    ExposureStep(150, 3.0),
    ExposureStep(200, 3.0),
    ExposureStep(250, 4.0, "Outdoor sun"),
    ExposureStep(300, 5.0),
    ExposureStep(350, 6.0, "Outdoor shade"),
    ExposureStep(400, 8.0),
    ExposureStep(450, 10.0, "Evening"),
    ExposureStep(500, 12.0, "Indoor bright"),
    ExposureStep(500, 15.0),
    ExposureStep(650, 15.0, "Indoor dark"),
    ExposureStep(800, 18.0),
    ExposureStep(1000, 20.0, "Night"),
    ExposureStep(1250, 16.0, "Facility dark"),
)


@dataclass(frozen=True)
class ExposureObservation:
    """Brightness and contrast measurements from the impact-zone ROI."""

    sample_available: bool
    status: ExposureQualityStatus
    recommendation: ExposureRecommendation
    message: str
    p10: float | None = None
    median: float | None = None
    p90: float | None = None
    contrast: float | None = None
    clipped_pct: float | None = None
    dark_pct: float | None = None

    @property
    def acceptable(self) -> bool:
        """Whether camera-derived metrics may use frames with this exposure."""
        return self.status in {"good", "marginal"}

    def to_dict(self) -> dict:
        """Serialize the observation for logs and the operator UI."""
        return asdict(self)


@dataclass(frozen=True)
class AutoExposureDecision:
    """One side-effect-free controller decision."""

    status: AutoExposureStatus
    analysis_eligible: bool
    message: str
    observation: ExposureObservation
    target: ExposureStep | None = None
    motion_blur_risk: Literal["low", "elevated", "high"] = "low"

    @property
    def should_apply(self) -> bool:
        """Whether the runtime should apply a new camera setting."""
        return self.target is not None

    def to_dict(self) -> dict:
        """Serialize the decision for status and logging."""
        payload = {
            "status": self.status,
            "analysis_eligible": self.analysis_eligible,
            "message": self.message,
            "motion_blur_risk": self.motion_blur_risk,
            "observation": self.observation.to_dict(),
        }
        payload["target"] = self.target.to_dict() if self.target else None
        return payload


def exposure_steps_for_fps(fps: float) -> tuple[ExposureStep, ...]:
    """Return ladder entries that fit inside the requested frame period."""
    if fps <= 0:
        raise ValueError("camera FPS must be positive")
    frame_period_us = round(1_000_000 / fps)
    steps = tuple(step for step in EXPOSURE_STEPS if step.exposure_us < frame_period_us)
    if not steps:
        raise ValueError(f"no exposure steps fit inside the {frame_period_us}us frame period")
    return steps


def measure_exposure(image: np.ndarray) -> ExposureObservation:
    """Rate exposure in the center-lower hitting zone of one grayscale frame."""
    pixels = np.asarray(image)
    if pixels.ndim != 2 or not pixels.size:
        return ExposureObservation(
            sample_available=False,
            status="unavailable",
            recommendation="hold",
            message="Waiting for a camera frame",
        )

    height, width = pixels.shape
    region = pixels[
        round(height * 0.45) : round(height * 0.9),
        round(width * 0.2) : round(width * 0.8),
    ]
    if not region.size:
        return ExposureObservation(
            sample_available=False,
            status="unavailable",
            recommendation="hold",
            message="Impact-area exposure region is unavailable",
        )

    p10, median, p90 = (float(np.percentile(region, value)) for value in (10, 50, 90))
    clipped_pct = float(np.mean(region >= 250) * 100.0)
    dark_pct = float(np.mean(region <= 12) * 100.0)
    contrast = p90 - p10

    if clipped_pct >= 8.0 or median >= 205.0:
        status: ExposureQualityStatus = "too_bright"
        recommendation: ExposureRecommendation = "darker"
        message = "Impact area is clipping; reducing exposure"
    elif p90 < 75.0 or median < 28.0 or contrast < 30.0:
        status = "too_dark"
        recommendation = "brighter"
        if p90 <= 12.0 and dark_pct >= 90.0:
            message = "Camera view is nearly black; check lighting and the lens cover"
        else:
            message = "Club contrast is low; increasing exposure"
    elif clipped_pct <= 2.0 and 45.0 <= median <= 180.0 and p90 >= 100.0:
        status = "good"
        recommendation = "hold"
        message = "Impact-area exposure and contrast look good"
    else:
        status = "marginal"
        recommendation = "darker" if clipped_pct > 2.0 or median > 180.0 else "brighter"
        message = f"Exposure is usable; a {recommendation} setting may improve contrast"

    return ExposureObservation(
        sample_available=True,
        status=status,
        recommendation=recommendation,
        message=message,
        p10=round(p10, 1),
        median=round(median, 1),
        p90=round(p90, 1),
        contrast=round(contrast, 1),
        clipped_pct=round(clipped_pct, 2),
        dark_pct=round(dark_pct, 2),
    )


def motion_blur_risk(exposure_us: int) -> Literal["low", "elevated", "high"]:
    """Describe the clubhead blur risk introduced by shutter duration."""
    if exposure_us > 800:
        return "high"
    if exposure_us > 500:
        return "elevated"
    return "low"


class AutoExposurePolicy:
    """Choose startup exposure steps and lock the first terminal decision."""

    def __init__(
        self,
        *,
        fps: float,
        startup_max_adjustments: int = 3,
    ):
        if startup_max_adjustments < 1:
            raise ValueError("startup_max_adjustments must be positive")
        self.steps = exposure_steps_for_fps(fps)
        self.startup_max_adjustments = startup_max_adjustments
        self._startup = True
        self._startup_adjustments = 0
        self._locked_decision: AutoExposureDecision | None = None

    def _reset_startup_state(self) -> None:
        """Enter startup convergence and clear any prior locked result."""
        self._startup = True
        self._startup_adjustments = 0
        self._locked_decision: AutoExposureDecision | None = None

    def reset(self) -> None:
        """Unlock the policy for a new camera startup."""
        self._reset_startup_state()

    @property
    def startup(self) -> bool:
        """Whether the policy is still using fast startup convergence."""
        return self._startup

    def evaluate(
        self,
        observation: ExposureObservation,
        *,
        exposure_us: int,
        gain: float,
    ) -> AutoExposureDecision:
        """Return the next camera-control decision without applying it."""
        if self._locked_decision is not None:
            return self._locked_decision

        current_index = self._nearest_step_index(exposure_us, gain)
        current_step = self.steps[current_index]
        risk = motion_blur_risk(current_step.exposure_us)

        if not observation.sample_available:
            return AutoExposureDecision(
                status="unavailable",
                analysis_eligible=False,
                message=observation.message,
                observation=observation,
                motion_blur_risk=risk,
            )

        if observation.acceptable:
            self._startup = False
            self._startup_adjustments = 0
            self._locked_decision = AutoExposureDecision(
                status="ready",
                analysis_eligible=True,
                message=observation.message,
                observation=observation,
                motion_blur_risk=risk,
            )
            return self._locked_decision

        return self._startup_decision(observation, current_index)

    def _startup_decision(
        self,
        observation: ExposureObservation,
        current_index: int,
    ) -> AutoExposureDecision:
        current = self.steps[current_index]
        risk = motion_blur_risk(current.exposure_us)
        if self._startup_adjustments >= self.startup_max_adjustments:
            self._startup = False
            self._locked_decision = AutoExposureDecision(
                status="lighting_required",
                analysis_eligible=False,
                message=self._lighting_message(observation),
                observation=observation,
                motion_blur_risk=risk,
            )
            return self._locked_decision

        target_index = self._startup_target_index(observation, current_index)
        if target_index == current_index:
            self._startup = False
            self._locked_decision = AutoExposureDecision(
                status="lighting_required",
                analysis_eligible=False,
                message=self._lighting_message(observation),
                observation=observation,
                motion_blur_risk=risk,
            )
            return self._locked_decision

        self._startup_adjustments += 1
        target = self.steps[target_index]
        return AutoExposureDecision(
            status="adjusting",
            analysis_eligible=False,
            message=(
                "Calibrating camera exposure "
                f"({self._startup_adjustments}/{self.startup_max_adjustments})"
            ),
            observation=observation,
            target=target,
            motion_blur_risk=motion_blur_risk(target.exposure_us),
        )

    def _startup_target_index(
        self,
        observation: ExposureObservation,
        current_index: int,
    ) -> int:
        current = self.steps[current_index]
        median = max(float(observation.median or 1.0), 1.0)
        target_signal = current.signal * max(0.125, min(8.0, 110.0 / median))
        target_index = min(
            range(len(self.steps)),
            key=lambda index: abs(math.log(self.steps[index].signal / target_signal)),
        )
        if observation.recommendation == "brighter":
            return (
                max(current_index + 1, target_index)
                if current_index < len(self.steps) - 1
                else current_index
            )
        if observation.recommendation == "darker":
            return min(current_index - 1, target_index) if current_index > 0 else current_index
        return current_index

    def _nearest_step_index(self, exposure_us: int, gain: float) -> int:
        exact_index = next(
            (
                index
                for index, step in enumerate(self.steps)
                if step.exposure_us == exposure_us and math.isclose(step.gain, gain)
            ),
            None,
        )
        if exact_index is not None:
            return exact_index
        signal = max(float(exposure_us) * float(gain), 1.0)
        return min(
            range(len(self.steps)),
            key=lambda index: abs(math.log(self.steps[index].signal / signal)),
        )

    @staticmethod
    def _lighting_message(observation: ExposureObservation) -> str:
        if observation.status == "too_dark":
            return "Camera lighting is insufficient; add or redirect light toward the ball"
        return "Camera view is overexposed; reduce or redirect light near the ball"
