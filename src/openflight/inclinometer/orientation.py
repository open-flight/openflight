"""Verify that the LIS3DH is mounted with its +Y arrow pointing at the ball.

The runtime treats positive LIS3DH Y as positive enclosure pitch (tilt back,
radar aiming up). That only holds when the board's +Y arrow points downrange.
This check compares a level reading with one taken while the front of the rig
is lifted, and reports which way the board actually faces.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from .models import AccelerationSample
from .service import pitch_degrees

MIN_TILT_DEG = 2.0
"""Smallest front lift that gives a trustworthy direction."""

MAX_HEADING_ERROR_DEG = 15.0
"""How far the +Y arrow may be turned away from downrange and still pass."""

FLAT_MIN_Z_G = 0.8
"""Minimum Z reading (in g) for a board lying flat, component side up."""

MAX_POSE_STD_DEG = 0.5
"""Pitch spread inside one pose above which the rig is treated as moving."""


class MountingVerdict(str, Enum):
    """Outcome of a mounting check."""

    OK = "ok"
    Y_REVERSED = "y_reversed"
    ROTATED = "rotated"
    UPSIDE_DOWN = "upside_down"
    NOT_FLAT = "not_flat"
    MOVING = "moving"
    NO_TILT = "no_tilt"


@dataclass(frozen=True)
class MountingCheck:
    """Result of comparing a level pose with a front-lifted pose."""

    verdict: MountingVerdict
    pitch_change_deg: float = 0.0
    roll_change_deg: float = 0.0

    @property
    def ok(self) -> bool:
        """Return whether the board is mounted with +Y toward the ball."""
        return self.verdict is MountingVerdict.OK

    @property
    def heading_deg(self) -> float:
        """Downrange direction in the board frame, from +Y toward +X.

        0 means +Y points at the ball, 180 means it points at the golfer, and
        +90 means +X points at the ball. Positive values are clockwise seen
        from above, so turning the board clockwise by this angle fixes it.
        """
        return math.degrees(math.atan2(self.roll_change_deg, self.pitch_change_deg))

    @property
    def message(self) -> str:
        """Explain the result and what to change."""
        if self.verdict is MountingVerdict.OK:
            return (
                f"+Y points at the ball (pitch rose {self.pitch_change_deg:+.1f} deg "
                "when the front was lifted). No sign flip is needed."
            )
        if self.verdict is MountingVerdict.Y_REVERSED:
            return (
                f"+Y points at the golfer: pitch fell {self.pitch_change_deg:+.1f} deg when "
                "the front was lifted. Turn the board 180 deg so the Y arrow (the "
                "I2/A1-A3/SDO/CS pin row) faces the ball."
            )
        if self.verdict is MountingVerdict.ROTATED:
            heading = self.heading_deg
            direction = "clockwise" if heading > 0 else "counter-clockwise"
            return (
                f"The Y arrow is {abs(heading):.0f} deg away from downrange. Turn the board "
                f"about {abs(heading):.0f} deg {direction} (seen from above) so the Y "
                "arrow points at the ball."
            )
        if self.verdict is MountingVerdict.UPSIDE_DOWN:
            return "The board is upside down. Mount it with the components facing up."
        if self.verdict is MountingVerdict.NOT_FLAT:
            return "The board is not lying flat. Screw it flat onto the enclosure floor."
        if self.verdict is MountingVerdict.MOVING:
            return "The rig moved during a reading. Hold it still and run the check again."
        return (
            f"Lift the front further: the tilt changed by less than {MIN_TILT_DEG:.0f} deg. "
            "Put something about 2-3 cm thick under the front feet."
        )


def roll_degrees(sample: AccelerationSample) -> float:
    """Return rotation about the board Y axis; positive X raised is positive."""
    return math.degrees(math.atan2(sample.x_g, math.hypot(sample.y_g, sample.z_g)))


def mean_sample(samples: Sequence[AccelerationSample]) -> AccelerationSample:
    """Average a pose's readings into one sample."""
    if not samples:
        raise ValueError("at least one sample is required")
    return AccelerationSample(
        timestamp=samples[-1].timestamp,
        x_g=statistics.fmean(sample.x_g for sample in samples),
        y_g=statistics.fmean(sample.y_g for sample in samples),
        z_g=statistics.fmean(sample.z_g for sample in samples),
    )


def _is_moving(samples: Sequence[AccelerationSample]) -> bool:
    if len(samples) < 2:
        return False
    return statistics.pstdev(pitch_degrees(sample) for sample in samples) > MAX_POSE_STD_DEG


def check_mounting(
    level: Sequence[AccelerationSample],
    front_lifted: Sequence[AccelerationSample],
) -> MountingCheck:
    """Compare a level pose with a front-lifted pose and classify the mounting."""
    level_mean = mean_sample(level)
    lifted_mean = mean_sample(front_lifted)

    if level_mean.z_g <= -FLAT_MIN_Z_G:
        return MountingCheck(MountingVerdict.UPSIDE_DOWN)
    if level_mean.z_g < FLAT_MIN_Z_G:
        return MountingCheck(MountingVerdict.NOT_FLAT)
    if _is_moving(level) or _is_moving(front_lifted):
        return MountingCheck(MountingVerdict.MOVING)

    pitch_change = pitch_degrees(lifted_mean) - pitch_degrees(level_mean)
    roll_change = roll_degrees(lifted_mean) - roll_degrees(level_mean)
    if math.hypot(pitch_change, roll_change) < MIN_TILT_DEG:
        return MountingCheck(MountingVerdict.NO_TILT, pitch_change, roll_change)

    result = MountingCheck(MountingVerdict.OK, pitch_change, roll_change)
    heading = abs(result.heading_deg)
    if heading <= MAX_HEADING_ERROR_DEG:
        return result
    if heading >= 180.0 - MAX_HEADING_ERROR_DEG:
        return MountingCheck(MountingVerdict.Y_REVERSED, pitch_change, roll_change)
    return MountingCheck(MountingVerdict.ROTATED, pitch_change, roll_change)
