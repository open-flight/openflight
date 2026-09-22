"""Frozen regression baselines against real captures, when present locally.

session_logs/ is gitignored, so this skips on CI and runs on the machine that
recorded the 2026-07-25 sessions. It is the only test that exercises the
channel selection on live data rather than synthetic data.

**These baselines are the estimator's own output, not independent
measurements.** No independent reference -- no TrackMan pairing, no optical
capture -- exists in this repo for session 20260725_140533; the numbers below
were produced by running this estimator on these captures and rounding. For
one shot the figure is the mean of that shot's own two channels.

So this file detects CHANGE, not ERROR. It will catch a regression that
reintroduces the collapsed-channel behavior, a coverage drop, or an
unintended shift in the estimator's output. It cannot tell you the estimator
is accurate, and passing it is not evidence that it is: if the estimator is
systematically wrong today, these baselines are wrong by exactly the same
amount and the test still passes. Validating accuracy needs a paired session
against a reference instrument, which is separate outstanding work.
"""

import json
from pathlib import Path

import pytest

SESSION = Path("session_logs/session_20260725_140533_range.jsonl")
DUMPS = Path("session_logs/iwr")

pytestmark = pytest.mark.skipif(
    not SESSION.is_file() or not DUMPS.is_dir(),
    reason="local 2026-07-25 captures not present (session_logs is gitignored)",
)


def _captures():
    rows = [json.loads(line) for line in SESSION.read_text().splitlines() if line.strip()]
    return sorted(
        [r for r in rows if r.get("type") == "iwr6843_capture" and r.get("capture_path")],
        key=lambda r: r["shot_number"],
    )


def _estimate(capture):
    from openflight.iwr6843.lcmf import estimate_lcmf_v1
    from openflight.iwr6843.replay import build_replay_calibration

    raw = (DUMPS / Path(capture["capture_path"]).name).read_bytes()
    cal = build_replay_calibration(
        "config/iwr6843_calibration_reference.json",
        tee_range_m=1.372,
        tilt_deg=5.5,
        radar_height_m=0.229,
        ball_height_m=0.040,
    )
    return estimate_lcmf_v1(
        raw,
        cal,
        ball_speed_mph=capture["ball_speed_mph"],
        club="7i",
        net_range_m=4.064,
        tx_order="normal",
    )


def test_all_seven_captures_accepted():
    """15 ms span floor takes this session to 7/7.

    Before the span-floor fix, shot 1 (17.3 ms span) was rejected outright by
    the old 18 ms gate, leaving 6/7. Coverage is a regression target in its
    own right: a future tightening of the span floor would silently drop
    shot 1 again without ever touching the angle assertions below.
    """
    results = [_estimate(capture) for capture in _captures()]
    accepted = [r for r in results if r.accepted]
    assert len(accepted) == 7, [r.status for r in results]


def test_collapsed_channel_no_longer_drags_the_answer():
    """Seven 7-irons stay near the frozen 18 deg baseline, not the old 10.9 deg.

    The baseline for this session is this estimator's own output, not a
    measurement of the real launch angles (see the module docstring):
    per-shot 19.25, 17.71, 24.32, 19.23, 16.02, 15.28, 16.48 deg, mean
    18.3 deg -- and the 24.32 figure is itself the mean of that shot's two
    channels. What IS independent is the operator's report that the balls
    flew like normal 7-irons, which is why the pre-fix output is known to be
    wrong: one channel collapsed to ~0 deg and the plain mean dragged the
    session average to 10.9 deg (and shot 1 was rejected outright, see the
    coverage test above). That gives the bounds a direction to defend, but
    not a number to check against.

    Bounds sit clearly between the two known outcomes rather than tight
    around the current output, so the test tracks stability without pinning
    the estimator in place:
    - Mean [14.0, 22.0]: the midpoint between 10.9 and 18.3 is ~14.6, so the
      floor at 14.0 is just below that midpoint -- a regression to the old
      collapsed-channel behavior (mean ~10.9) fails by a wide margin, while
      the ceiling at 22.0 gives ~3.7 deg of headroom above the current 18.3
      for legitimate estimator refinement without becoming a no-op gate.
    - Per-shot [12.0, 26.0]: wide enough to allow shot-to-shot variance (the
      current range is 15.28-24.32 deg) and future estimator tweaks, but
      still well above the ~0 deg a collapsed channel produces on any single
      shot, so a single reintroduced bad channel is still caught.
    """
    angles = [r.angle_deg for r in (_estimate(c) for c in _captures()) if r.accepted]
    mean_angle = sum(angles) / len(angles)
    assert 14.0 <= mean_angle <= 22.0, f"7-iron mean {mean_angle:.1f} deg is implausible"
    assert all(12.0 <= angle <= 26.0 for angle in angles), angles


def test_impact_is_located_early_in_the_ring_on_every_capture():
    """Impact lands at slot 2-4 of 18, never at the slot 6 club.py assumed.

    This is the defect that made club path fit the follow-through on every
    shot. The freeze is requested by a UART CLI command and `l3_dump.c` samples
    the ring position when that command is parsed, so the trigger frame lands
    late by a variable 2-4 frames.

    These captures come from the 18-frame ring. The v3 firmware allocates 13
    pre-trigger frames instead of 6, which moves impact to roughly slot 9-11
    and is what gives club path enough approach history -- so the slot bound
    below is deliberately expressed against `n_frames`, not hard-coded to 18.
    """
    frame_period_s = 4e-3
    slots = []
    for capture in _captures():
        result = _estimate(capture)
        assert result.impact_t_s is not None, (
            f"shot {capture['shot_number']} produced no impact time: {result.status}"
        )
        slots.append(result.impact_t_s / frame_period_s)

    assert len(slots) == 7
    assert all(1.0 <= slot <= 5.0 for slot in slots), (
        f"impact slots {[round(s, 1) for s in slots]} do not match the measured "
        "2026-07-25 range of 1.7-4.1; a change here means the trigger latency "
        "or the ring layout moved"
    )


def test_club_is_found_pre_impact_with_a_plausible_speed_projection():
    """The clubhead's approach is trackable on every capture.

    What this pins is the part that does NOT depend on the firmware: with the
    window anchored to the measured impact and the search gate capped at the
    tee, the approaching clubhead is found and its radial speed is a plausible
    fraction of the OPS club speed. Before those two fixes the tracker found
    the follow-through instead, at a projection factor of 0.27-0.58.

    Club path itself is still expected to be REJECTED on these captures for
    want of frames: the 18-frame ring leaves only 2-4 pre-impact frames against
    CLUB_MIN_FRAMES=4. That is the limit the v3 ring lifts, so this test
    deliberately asserts the tracking property rather than an accepted path.
    """
    from openflight.iwr6843.club import CLUB_SPEED_PROJECTION_RANGE, estimate_club_path
    from openflight.iwr6843.replay import build_replay_calibration

    rows = [json.loads(line) for line in SESSION.read_text().splitlines() if line.strip()]
    club_speeds = {
        r["shot_number"]: r.get("club_speed_mph") for r in rows if r.get("type") == "shot_detected"
    }
    cal = build_replay_calibration(
        "config/iwr6843_calibration_reference.json",
        tee_range_m=1.372,
        tilt_deg=5.5,
        radar_height_m=0.229,
        ball_height_m=0.040,
    )

    low, high = CLUB_SPEED_PROJECTION_RANGE
    projections = []
    for capture in _captures():
        shot_number = capture["shot_number"]
        club_speed_mph = club_speeds.get(shot_number)
        assert club_speed_mph, f"shot {shot_number} has no OPS club speed"
        raw = (DUMPS / Path(capture["capture_path"]).name).read_bytes()
        measurement = _estimate(capture)
        result = estimate_club_path(
            raw,
            cal,
            ops_club_speed_mph=club_speed_mph,
            impact_t_s=measurement.impact_t_s,
            tdm_sign=measurement.tdm_sign_used or 1,
        )
        assert result.range_rate_ms is not None, (
            f"shot {shot_number}: no club track in the pre-impact window ({result.status})"
        )
        projections.append(abs(result.range_rate_ms) / (club_speed_mph / 2.23694))

    assert len(projections) == 7
    assert all(0.70 <= p <= 1.30 for p in projections), (
        f"projections {[round(p, 2) for p in projections]} left the plausible "
        "band; 0.27-0.58 would mean the follow-through is being tracked again"
    )
    in_gate = [p for p in projections if low <= p <= high]
    assert len(in_gate) >= 6, (
        f"only {len(in_gate)}/7 projections pass the identity gate "
        f"({low}, {high}): {[round(p, 2) for p in projections]}"
    )
