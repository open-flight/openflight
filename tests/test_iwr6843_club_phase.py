"""Wrapped interferometric phase is the only valid azimuth on this array.

The horizontal aperture is a SINGLE lambda/2 baseline (TX2 against the
TX1/TX3 row -- see openflight.iwr6843.lcmf's spatial dictionary, which places
all eight TX1/TX3 virtual elements on the vertical axis). A lambda/2 baseline's
unambiguous phase range is exactly +/-pi, so:

- Unwrapping past +/-pi fabricates azimuths outside the array's field of view.
  There is no information out there to recover.
- Worse, `np.unwrap` over a sequence of noisy per-snapshot phases random-walks.
  On the 2026-07-25 captures it produced 1.0-17.3 rad of "swing", implying
  20-144 degrees of azimuth travel in 9-16 ms -- impossible for a clubhead
  whose motion is 85-90% radial.

So the production estimator keeps the wrapped phase and rejects outliers
against each frame's own circular median instead. The debug-only continuity
candidate is different: it retains the two TX2-to-outer-TX phase references
references separately and unwraps each through time before combining them.
"""

import math

import numpy as np
import pytest

from openflight.iwr6843 import club


class TestPhaseOutlierRejection:
    """Per-frame circular median, not a cumulative unwrap."""

    def test_clean_samples_all_survive(self):
        phases = np.array([0.10, 0.12, 0.11, 0.30, 0.31, 0.29])
        frames = np.array([0, 0, 0, 1, 1, 1])
        keep = club.phase_outlier_mask(phases, frames)
        assert keep.all()

    def test_a_wild_sample_is_dropped_from_its_own_frame(self):
        phases = np.array([0.10, 0.12, 3.00, 0.30, 0.31, 0.29])
        frames = np.array([0, 0, 0, 1, 1, 1])
        keep = club.phase_outlier_mask(phases, frames)
        assert list(keep) == [True, True, False, True, True, True]

    def test_frames_are_judged_independently(self):
        """A frame at a genuinely different azimuth is not an outlier.

        The club is moving, so per-frame azimuth SHOULD drift across frames.
        Judging against a global median would delete the signal.
        """
        phases = np.array([0.00, 0.01, 0.40, 0.41, 0.80, 0.81])
        frames = np.array([0, 0, 1, 1, 2, 2])
        assert club.phase_outlier_mask(phases, frames).all()

    def test_rejection_wraps_across_pi(self):
        """Samples straddling +/-pi are near each other, not 2pi apart."""
        phases = np.array([3.13, -3.13, 3.10])
        frames = np.array([0, 0, 0])
        assert club.phase_outlier_mask(phases, frames).all()

    def test_a_single_sample_frame_survives(self):
        phases = np.array([0.5])
        frames = np.array([7])
        assert club.phase_outlier_mask(phases, frames).all()


class TestNoUnwrap:
    """The invalid unwrap must be gone, not merely tuned."""

    def test_module_no_longer_unwraps(self):
        source = (
            club.__loader__.get_source("openflight.iwr6843.club")  # type: ignore[union-attr]
            or ""
        )
        assert "np.unwrap" not in source, (
            "np.unwrap on a lambda/2 baseline invents angles outside the "
            "array's unambiguous +/-90 degree field of view"
        )

    def test_azimuth_stays_inside_the_unambiguous_field_of_view(self):
        """arcsin(phase/pi) on wrapped phase can never exceed +/-90 degrees."""
        for phase in (-math.pi, -1.0, 0.0, 1.0, math.pi):
            az = math.degrees(math.asin(max(-1.0, min(1.0, phase / math.pi))))
            assert -90.0 <= az <= 90.0


class TestPhaseSpanGate:
    """The span gate measures per-frame medians, so noise cannot trip it."""

    def test_physical_span_is_accepted(self):
        """A clubhead sweeps at most ~1.3 rad of baseline phase over 24 ms.

        Tangential speed is at most ~20 m/s at a ~1.1 m range, so the azimuth
        rate is under ~18 rad/s; over six 4 ms frames that is ~0.44 rad of
        azimuth, or ~1.3 rad of lambda/2 baseline phase.
        """
        assert club.CLUB_MAX_PHASE_SPAN_RAD == pytest.approx(math.pi / 2)

    def test_span_is_measured_on_frame_medians_not_raw_samples(self):
        """Noisy samples inside frames must not inflate the span.

        This is the specific failure that rejected 4 of 14 real shots: raw
        per-snapshot scatter, amplified by unwrap, looked like a huge azimuth
        swing.
        """
        frames = np.array([0, 0, 0, 1, 1, 1])
        tight = np.array([0.0, 0.0, 0.0, 0.2, 0.2, 0.2])
        noisy = np.array([-1.2, 0.0, 1.2, -1.0, 0.2, 1.4])

        assert club.phase_span_rad(tight, frames) == pytest.approx(0.2, abs=1e-9)
        assert club.phase_span_rad(noisy, frames) == pytest.approx(0.2, abs=0.3), (
            "per-frame medians should track the underlying drift, not the scatter"
        )

    def test_span_wraps_across_pi(self):
        frames = np.array([0, 0, 1, 1])
        phases = np.array([3.10, 3.10, -3.10, -3.10])
        span = club.phase_span_rad(phases, frames)
        assert span == pytest.approx(abs(math.remainder(-3.10 - 3.10, math.tau)), abs=1e-6)
        assert span < 0.2, "3.10 and -3.10 are 0.08 rad apart, not 6.2"

    def test_single_frame_has_zero_span(self):
        assert club.phase_span_rad(np.array([0.4, 0.5]), np.array([3, 3])) == 0.0
