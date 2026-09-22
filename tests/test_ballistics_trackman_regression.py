"""Regression guard: ballistic model accuracy against committed TrackMan data.

The repo ships paired TrackMan reference captures in ``session_logs/`` and a
manual validator (``scripts/analysis/validate_ballistics.py``), but nothing in
the test suite reads them. That gap lets an accuracy regression ship green:
``test_ballistics.py`` asserts driver carry only within 250-300 yd, a 50-yard
window, so a 20-40 yard model bias passes unnoticed.

This module closes it by replaying the committed capture through the production
``resolve_launch``/``simulate`` path and asserting per-club error against a
budget. The budget is seeded at the CURRENT measured error, not at zero, so it
blocks *new* error immediately while leaving the existing bias to be paid down
deliberately. Ratchet the numbers down when the model improves; never up.

Reuses the validator's own loading/statistics helpers so the test and the
manual tool can never disagree about what the reference data means.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ANALYSIS = _REPO_ROOT / "scripts" / "analysis"
if str(_ANALYSIS) not in sys.path:
    sys.path.insert(0, str(_ANALYSIS))

from validate_ballistics import (  # noqa: E402  (path set up above)
    _stats,
    load_trackman,
    validate_tm_inputs,
)

TRACKMAN_CSV = _REPO_ROOT / "session_logs" / "OpenFlight-Test.Normalized.csv"

# Per-club RMSE ceilings in yards. Ratcheted down after the aero coefficients
# were re-fit against this same capture (see ballistics.py CD_/CL_ constants).
#
#                    before re-fit        after re-fit
#   club            rmse      bias       rmse     bias
#   7-iron         11.64   +11.00        2.96   +1.52
#   driver         38.60   -37.18        1.75   -0.45
#   pitching wedge 13.56   +11.02        6.26   -2.96
#   OVERALL        24.52    -5.05        3.97   -0.45
#
# Ceilings sit slightly above the measured values to absorb float/platform
# drift. Lower them again if the model improves; never raise them.
RMSE_BUDGET_YARDS = {
    "driver": 3.0,
    "7-iron": 4.0,
    "pitching wedge": 7.5,
}
OVERALL_RMSE_BUDGET_YARDS = 5.0

# The reference capture is a fixed, committed file: if the shot count changes,
# the fixture changed and every budget above needs re-deriving.
EXPECTED_SHOT_COUNT = 24


@pytest.fixture(scope="module")
def validation_rows():
    if not TRACKMAN_CSV.exists():
        pytest.skip(f"TrackMan reference capture not present: {TRACKMAN_CSV}")
    rows = validate_tm_inputs(load_trackman(TRACKMAN_CSV))
    if not rows:
        pytest.skip("TrackMan reference capture produced no usable shots")
    return rows


def test_reference_capture_shot_count(validation_rows):
    """Pin the fixture size so budget drift cannot hide behind a changed file."""
    assert len(validation_rows) == EXPECTED_SHOT_COUNT, (
        f"Reference capture has {len(validation_rows)} usable shots, expected "
        f"{EXPECTED_SHOT_COUNT}. If the fixture changed intentionally, re-derive "
        f"the RMSE budgets in this module from a fresh validator run."
    )


def test_overall_carry_rmse_within_budget(validation_rows):
    """Model carry vs TrackMan carry, fed TrackMan's own launch conditions."""
    stats = _stats([r.delta_yards for r in validation_rows])
    assert stats["rmse"] <= OVERALL_RMSE_BUDGET_YARDS, (
        f"Overall carry RMSE {stats['rmse']:.2f} yd exceeds budget "
        f"{OVERALL_RMSE_BUDGET_YARDS} yd (bias {stats['mean']:+.2f}, "
        f"max |delta| {stats['max_abs']:.2f}, n={stats['n']}). "
        f"The ballistic model got less accurate against the reference capture."
    )


@pytest.mark.parametrize("club", sorted(RMSE_BUDGET_YARDS))
def test_per_club_carry_rmse_within_budget(validation_rows, club):
    """Per-club budgets: a club-specific regression cannot hide in the average."""
    deltas = [r.delta_yards for r in validation_rows if r.club == club]
    assert deltas, f"No reference shots for club {club!r}"
    stats = _stats(deltas)
    budget = RMSE_BUDGET_YARDS[club]
    assert stats["rmse"] <= budget, (
        f"{club}: carry RMSE {stats['rmse']:.2f} yd exceeds budget {budget} yd "
        f"(bias {stats['mean']:+.2f}, max |delta| {stats['max_abs']:.2f}, "
        f"n={stats['n']})."
    )
