"""Regression guard: radar speed accuracy against committed TrackMan data.

``test_ballistics_trackman_regression.py`` feeds the ballistic model
TrackMan's own launch conditions, so nothing checks the radar's speeds against
real data. This replays every I/Q capture in the committed session through
``process_capture`` the way the monitor calls it, pairs the results with the
same session's TrackMan export via ``compare_trackman``, and asserts per-club
speed RMSE. Speeds are raw radial speeds, before the server's cosine
correction.

Measured on the 22 shots that pair within the tool's 5 mph tolerance (mph)::

                     ball speed        club speed
    club            rmse    bias      rmse    bias
    driver          2.99   -2.98      2.43   -1.97
    7-iron          2.71   -2.64      3.90   -3.64
    pw              2.62   -2.47      8.41   -1.27
    overall         2.77   -2.69      5.47   -2.36

Unpaired: shot 13 (driver) reads 65.0 against 162.2 because ball-speed
selection picks the club approach over a 158.7 mph ball track; shot 9 (7-iron)
reads 122.8 against 117.3. PW club speed is dominated by shot 19 (96.3 against
81.9; its club/ball ratio of 0.94 sits just under the 0.95 contamination guard)
and shot 21 (65.2 against 80.5, a TrackMan mishit); without them it is 3.2 mph
RMSE.

Budgets sit just above the measured RMSE: lower them when the processor
improves. If a fix brings an unpaired shot back, re-derive them with it
included; they can rise, because that shot's error now counts.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ANALYSIS = _REPO_ROOT / "scripts" / "analysis"
if str(_ANALYSIS) not in sys.path:
    sys.path.insert(0, str(_ANALYSIS))

from compare_trackman import (  # noqa: E402  (path set up above)
    _rmse,
    load_openflight,
    load_trackman,
    pair_shots,
)
from replay_captures import load_captures  # noqa: E402

from openflight.clubs import ClubType  # noqa: E402
from openflight.rolling_buffer.monitor import (  # noqa: E402
    get_optimal_spin_for_ball_speed,
)
from openflight.rolling_buffer.processor import RollingBufferProcessor  # noqa: E402
from openflight.rolling_buffer.types import IQCapture  # noqa: E402

SESSION_LOG = _REPO_ROOT / "session_logs" / "session_20260506_152406_range.jsonl"
TRACKMAN_CSV = _REPO_ROOT / "session_logs" / "OpenFlight-Test.Normalized.csv"

EXPECTED_CAPTURE_COUNT = 24
EXPECTED_UNPAIRED_SHOTS = {9, 13}

RMSE_BUDGET_MPH = {
    "ball_speed_mph": {"driver": 3.25, "7-iron": 3.0, "pw": 3.0, "overall": 3.0},
    "club_speed_mph": {"driver": 2.75, "7-iron": 4.25, "pw": 8.75, "overall": 5.75},
}


def _replay(capture: dict, club: ClubType):
    """Run one logged capture through the processor the way the monitor does."""
    return RollingBufferProcessor().process_capture(
        IQCapture(
            sample_time=capture["sample_time"],
            trigger_time=capture["trigger_time"],
            i_samples=capture["i_samples"],
            q_samples=capture["q_samples"],
        ),
        expected_spin_for_ball_speed=lambda ball_speed_mph: get_optimal_spin_for_ball_speed(
            ball_speed_mph, club
        ),
        club_type=club,
    )


@pytest.fixture(scope="module")
def captures():
    if not SESSION_LOG.exists():
        pytest.skip(f"Radar reference session not present: {SESSION_LOG}")
    return {c["shot_number"]: c for c in load_captures(str(SESSION_LOG))}


@pytest.fixture(scope="module")
def pairs(captures):
    """Logged shots (for club and timestamp) with replayed speeds, paired with TrackMan."""
    if not TRACKMAN_CSV.exists():
        pytest.skip(f"TrackMan reference capture not present: {TRACKMAN_CSV}")
    shots = load_openflight(SESSION_LOG)
    for shot in shots:
        processed = _replay(captures[shot.shot_number], ClubType(shot.club))
        shot.ball_speed_mph = processed.ball_speed_mph if processed else None
        shot.club_speed_mph = processed.club_speed_mph if processed else None
    return pair_shots(shots, load_trackman(TRACKMAN_CSV))


@pytest.fixture(scope="module")
def good_pairs(pairs):
    """``pair_shots`` also calls a pair good when the replay produced no ball
    speed, so drop those here."""
    return [p for p in pairs if p.match_quality == "good" and p.of.ball_speed_mph is not None]


def test_reference_session_capture_count(captures):
    """Pin the fixture size so budget drift cannot hide behind a changed file."""
    assert len(captures) == EXPECTED_CAPTURE_COUNT, (
        f"Reference session has {len(captures)} I/Q captures, expected "
        f"{EXPECTED_CAPTURE_COUNT}. Re-derive the budgets if it changed on purpose."
    )


def test_unpaired_shots(captures, pairs, good_pairs):
    """A shot the processor stops reading correctly drops out of the pairing
    instead of failing a budget, so pin exactly which shots are out."""
    unpaired = set(captures) - {p.of.shot_number for p in good_pairs}
    readings = {
        p.of.shot_number: f"{p.of.ball_speed_mph and round(p.of.ball_speed_mph, 1)} vs "
        f"TrackMan {p.tm.ball_speed_mph:.1f}"
        for p in pairs
        if p.of and p.tm
    }
    changed = sorted(unpaired ^ EXPECTED_UNPAIRED_SHOTS)
    assert not changed, (
        f"Unpaired shots are {sorted(unpaired)}, expected {sorted(EXPECTED_UNPAIRED_SHOTS)}. "
        f"Ball speed (mph) for the shots that changed: "
        f"{', '.join(f'{n}: {readings.get(n)}' for n in changed)}."
    )


def test_every_good_pair_has_club_speed(good_pairs):
    """Pin coverage so the club-speed budgets cannot pass on a shrinking set."""
    missing = [p.of.shot_number for p in good_pairs if p.of.club_speed_mph is None]
    assert not missing, f"No club speed detected for shots {missing}."


@pytest.mark.parametrize(
    "field, club",
    [(field, club) for field, budgets in RMSE_BUDGET_MPH.items() for club in budgets],
)
def test_speed_rmse_within_budget(good_pairs, field, club):
    deltas = [
        getattr(p.of, field) - getattr(p.tm, field)
        for p in good_pairs
        if getattr(p.of, field) is not None and club in ("overall", p.of.club)
    ]
    assert deltas, f"No good pairs for club {club!r}"
    rmse = _rmse(deltas)
    budget = RMSE_BUDGET_MPH[field][club]
    assert rmse <= budget, (
        f"{club} {field}: RMSE {rmse:.2f} mph exceeds budget {budget} mph "
        f"(bias {sum(deltas) / len(deltas):+.2f}, n={len(deltas)})."
    )
