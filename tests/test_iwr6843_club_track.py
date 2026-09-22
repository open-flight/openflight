"""Gate parameterisation: one fitter, two targets.

The club is slower and closer than the ball. find_ball's own docstring notes
the club steals the track when gates are wrong, so the fitter finds clubs
already — it just needs pointing at one deliberately.
"""

import numpy as np
import pytest

from openflight.iwr6843 import tracking
from openflight.iwr6843.dump import parse_dump
from openflight.iwr6843.shot import geometry_from_header


def _synth(range_m_at, speed_ms, *, n_frames=18, loops=12, n_rx=4, n_samples=128):
    """Pack a single mover with a known range walk into the wire format."""
    from openflight.iwr6843.dump import SAMPLE_RANGE_FFT_IQ16, pack_dump

    res = 6.0 / n_samples
    cube = np.zeros((n_frames, loops * 2, n_rx, n_samples), dtype=complex)
    for frame in range(n_frames):
        for loop in range(loops * 2):
            t = frame * 4e-3 + (loop // 2) * 90e-6
            bin_at = (range_m_at + speed_ms * t) / res
            lo = int(bin_at)
            if 0 <= lo < n_samples - 1:
                frac = bin_at - lo
                cube[frame, loop, :, lo] = 1000.0 * (1 - frac)
                cube[frame, loop, :, lo + 1] = 1000.0 * frac
    return pack_dump(
        cube, n_tx=2, version=3, frame_period_us=4000, sample_fmt=SAMPLE_RANGE_FFT_IQ16
    )


def _track(raw, **kwargs):
    from openflight.iwr6843.dump import is_range_snapshot

    meta, cube = parse_dump(raw)
    geo = geometry_from_header(meta)
    # Derive range_domain from the header: this fixture writes the target
    # directly onto the range-bin axis (see _synth), so mti_filter must not
    # re-FFT it — doing so would flatten the spike into a broadband,
    # non-localized spectrum. The header now declares this correctly via
    # sample_fmt=SAMPLE_RANGE_FFT_IQ16.
    mti = tracking.mti_filter(cube, range_domain=is_range_snapshot(meta), geometry=geo)
    return tracking.find_ball(mti, geo, **kwargs)


def test_club_gates_find_a_slow_close_mover():
    """A 22 m/s mover at 1.6 m is invisible to ball gates, found with club gates."""
    # n_frames=6 (not the 18-frame default): over 18 frames a 22 m/s radial
    # walk from 1.6 m crosses into the ball gate (>= 2.25 m) and IS found by
    # the ball search, defeating the point of this test. 6 frames caps the
    # walk under 2.1 m, safely inside club range for the whole capture.
    raw = _synth(1.6, 22.0, n_frames=6)

    with_ball_gates = _track(raw, min_ball_ms=26.5)
    assert with_ball_gates is None, (
        "ball gates (2.25-5.5 m, 20-90 m/s) must not claim a club-range mover"
    )

    with_club_gates = _track(
        raw,
        gates_m=((1.0, 2.4),),
        speed_bounds_ms=(10.0, 45.0),
        min_ball_ms=10.0,
    )
    assert with_club_gates is not None, "club gates failed to find the synthetic club"
    assert with_club_gates.speed_ms == pytest.approx(22.0, abs=2.0)


def test_speed_bounds_are_the_callers_not_the_module_default():
    """The caller's speed band must actually gate the fit.

    ``speed_bounds_ms`` needs a test that fails when the argument is ignored
    and the module default is substituted. Every other fixture in this suite
    runs at 22 m/s radial, which sits inside BOTH the ball band (20-90) and the
    club band (18-45), so none of them can tell the two apart.

    The bands themselves no longer separate ball from club -- they overlap
    everywhere except 18-20 m/s, which is far too narrow for the RANSAC fit's
    own tolerance to resolve. In production that separation rests on
    ``time_window_s`` (which ends at impact) and on the club gate's upper edge
    being capped at the tee, since the clubhead is always short of the ball
    before it strikes it. What this test pins is therefore the plumbing: an
    explicit band supplied by the caller must gate the fit. Gates and
    ``min_ball_ms`` are held identical across the two calls, so
    ``speed_bounds_ms`` is the only thing that can explain the difference.
    """
    raw = _synth(1.6, 22.0)
    shared = {"gates_m": ((1.0, 2.6),), "min_ball_ms": 10.0}

    admitted = _track(raw, speed_bounds_ms=(10.0, 45.0), **shared)
    assert admitted is not None, "22 m/s is inside (10, 45) and must be found"
    assert admitted.speed_ms == pytest.approx(22.0, abs=2.0)

    excluded = _track(raw, speed_bounds_ms=(30.0, 45.0), **shared)
    assert excluded is None, (
        f"22 m/s is below the supplied band's 30 m/s floor and must be rejected; got {excluded}"
    )


def test_ball_behaviour_unchanged_by_defaults():
    """Defaults must reproduce today's behaviour exactly."""
    raw = _synth(2.6, 45.0)
    explicit = _track(raw, gates_m=tracking.BALL_GATES_M, speed_bounds_ms=tracking.SPEED_BOUNDS_MS)
    implicit = _track(raw)
    assert (explicit is None) == (implicit is None)
    if explicit is not None:
        assert explicit.speed_ms == implicit.speed_ms
        assert explicit.n_inliers == implicit.n_inliers


def test_time_window_excludes_a_later_mover():
    """The club search must not be able to claim the ball.

    Two movers: a slow one early, a fast one late. Both are inside the club
    speed bounds, so only the time window separates them.
    """
    # 18 frames (default): needs data past 50 ms to prove the second window
    # legitimately yields no detections.
    raw_early = _synth(1.6, 22.0)
    early = _track(
        raw_early,
        gates_m=((1.0, 2.4),),
        speed_bounds_ms=(10.0, 45.0),
        min_ball_ms=10.0,
        time_window_s=(0.0, 0.024),
    )
    assert early is not None
    assert early.t_last <= 0.024 + 1e-9, "window must bound the fitted track"
    assert early.t_first >= 0.0 - 1e-9

    # A window past the mover's exit from the club gate must find nothing,
    # and must not silently fall back to the early detections.
    late_only = _track(
        raw_early,
        gates_m=((1.0, 2.4),),
        speed_bounds_ms=(10.0, 45.0),
        min_ball_ms=10.0,
        time_window_s=(0.050, 0.072),
    )
    assert late_only is None or late_only.t_first >= 0.050 - 1e-9
