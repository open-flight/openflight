"""Impact is located from the data, never from a fixed ring slot.

The firmware's freeze is requested by a UART CLI command (`l3_dump.c:873`
samples `gRingFrame` when the command is parsed), so the trigger frame lands
2-4 frames late and the lateness varies shot to shot. Measured on the
2026-07-25 captures, impact fell at slot order 1.7-4.1 of an 18-frame ring
while `club.py` assumed slot 6 -- so the "pre-impact" window straddled impact
and the club-path estimator was fitting the follow-through on 14 of 14 shots.

These tests pin the replacement: back-extrapolate the ball's own fitted range
walk to the tee range, and anchor the pre-impact window to END at that instant.
"""

import pytest

from openflight.iwr6843 import club
from openflight.iwr6843.shot import impact_time_s
from openflight.iwr6843.tracking import BallTrack, Geometry

RES_M = 6.0 / 128.0  # 4.6875 cm, the production range bin
TEE_M = 1.372


def _geo(n_frames=25, frame_period_s=4e-3):
    return Geometry(
        n_frames=n_frames,
        chirps_per_frame=36,
        n_tx=3,
        n_rx=4,
        n_samples=53,
        frame_period_s=frame_period_s,
        trigger_frame=0,
        range_fft_size=128,
    )


def _track(
    *,
    impact_at_s,
    speed_ms=45.0,
    t_first=0.030,
    t_last=0.070,
    apparent_tee_m=TEE_M,
):
    """A receding ball whose fit crosses the tee range at ``impact_at_s``."""
    slope_bins = speed_ms / RES_M
    return BallTrack(
        speed_ms=speed_ms,
        slope_bins=slope_bins,
        intercept_bins=apparent_tee_m / RES_M - slope_bins * impact_at_s,
        rms_bins=0.21,
        n_inliers=40,
        t_first=t_first,
        t_last=t_last,
        low_confidence=False,
    )


class TestImpactTime:
    """Recovering the instant the ball left the tee."""

    @pytest.mark.parametrize("impact_at_s", [0.0067, 0.0134, 0.0163, 0.040])
    def test_recovers_a_known_impact_time(self, impact_at_s):
        """The measured 2026-07-25 spread was 6.7-16.3 ms."""
        got = impact_time_s(_track(impact_at_s=impact_at_s), _geo(), TEE_M)
        assert got == pytest.approx(impact_at_s, abs=1e-9)

    def test_applies_range_bias_before_intersecting_the_track(self):
        range_bias_m = 0.066
        impact_at_s = 0.0134
        track = _track(
            impact_at_s=impact_at_s,
            apparent_tee_m=TEE_M + range_bias_m,
        )

        got = impact_time_s(
            track,
            _geo(),
            TEE_M,
            range_bias_m=range_bias_m,
        )

        assert got == pytest.approx(impact_at_s, abs=1e-9)

    def test_no_track_is_none(self):
        assert impact_time_s(None, _geo(), TEE_M) is None

    def test_no_tee_range_is_none(self):
        """LCMF requires a measured tee range; without it there is no anchor."""
        assert impact_time_s(_track(impact_at_s=0.013), _geo(), None) is None

    def test_non_receding_track_is_none(self):
        """A non-positive slope is not a ball leaving the tee.

        The radar sits behind the tee, so a real ball's range increases. A flat
        or approaching fit means the tracker locked onto something else, and
        dividing by that slope would invent an impact time.
        """
        flat = _track(impact_at_s=0.013)
        flat.slope_bins = 0.0
        assert impact_time_s(flat, _geo(), TEE_M) is None

        approaching = _track(impact_at_s=0.013)
        approaching.slope_bins = -100.0
        assert impact_time_s(approaching, _geo(), TEE_M) is None

    def test_impact_outside_the_captured_window_is_none(self):
        """Impact before slot 0 or after the ring cannot be used."""
        window_s = 25 * 4e-3
        assert impact_time_s(_track(impact_at_s=-0.005), _geo(), TEE_M) is None
        assert impact_time_s(_track(impact_at_s=window_s + 0.005), _geo(), TEE_M) is None

    def test_boundaries_of_the_window_are_inclusive(self):
        window_s = 25 * 4e-3
        assert impact_time_s(_track(impact_at_s=0.0), _geo(), TEE_M) == pytest.approx(0.0)
        assert impact_time_s(_track(impact_at_s=window_s), _geo(), TEE_M) == pytest.approx(window_s)


class TestPreImpactWindow:
    """The window must END at impact, not start at ring slot 0."""

    def test_window_ends_at_impact(self):
        got = club.pre_impact_window_s(_geo(), 0.040)
        assert got is not None
        lo, hi = got
        assert hi == pytest.approx(0.040), "window must end at impact"
        assert lo == pytest.approx(0.040 - club.PRE_IMPACT_FRAMES * 4e-3)

    def test_window_clamps_at_the_start_of_the_ring(self):
        """Impact early in the ring yields a short window, not a negative one."""
        got = club.pre_impact_window_s(_geo(), 0.010)
        assert got == (pytest.approx(0.0), pytest.approx(0.010))

    def test_no_impact_time_is_none(self):
        assert club.pre_impact_window_s(_geo(), None) is None

    def test_impact_at_or_before_ring_start_is_none(self):
        """No pre-impact time exists at all, so there is no window."""
        assert club.pre_impact_window_s(_geo(), 0.0) is None
        assert club.pre_impact_window_s(_geo(), -0.001) is None

    def test_frame_count_is_overridable(self):
        got = club.pre_impact_window_s(_geo(), 0.050, n_frames=2)
        assert got == (pytest.approx(0.050 - 2 * 4e-3), pytest.approx(0.050))

    def test_lcmf_result_carries_impact_time(self):
        """The estimator must publish where impact landed in the ring.

        This is the diagnostic whose absence hid the slot-assumption defect:
        nothing in the session log said where impact actually was, so a window
        that straddled it looked indistinguishable from one that did not.
        """
        from openflight.iwr6843.lcmf import LCMFResult

        payload = LCMFResult(status="accepted", impact_t_s=0.0134).to_dict()
        assert payload["impact_t_s"] == 0.0134

        absent = LCMFResult(status="rejected_by_ball_tracker").to_dict()
        assert absent["impact_t_s"] is None

    def test_the_old_fixed_slot_behaviour_is_gone(self):
        """Regression guard for the defect this replaced.

        The old implementation returned (0, PRE_IMPACT_FRAMES * period)
        regardless of impact, which straddled impact whenever impact landed
        earlier than slot 6 -- as it did on every shot measured.
        """
        impact_s = 0.0134  # median of the 2026-07-25 sessions
        lo, hi = club.pre_impact_window_s(_geo(), impact_s)
        assert hi <= impact_s, "window must not extend past impact"
        assert (lo, hi) != (0.0, club.PRE_IMPACT_FRAMES * 4e-3)
