"""Stage 4: pure, read-only OpenFlight clubhead recovery classification.

Classifies existing Stage 3 replay measurements. No production measurements
are modified, and agreement is not a substitute for external calibration.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from math import isfinite
from typing import Optional


@dataclass(frozen=True)
class RecoveryDecision:
    classification: str
    candidate_mph: Optional[float]
    evidence: str
    terminal_agreement: str
    terminal_difference_mph: Optional[float]
    three_way_agreement: str
    flagged: bool
    eligible_for_recovery_review: bool

    def to_dict(self):
        return asdict(self)


def _number(x):
    if x is None or x == "":
        return None
    try:
        value = float(x)
    except (TypeError, ValueError):
        return None
    return value if isfinite(value) and value > 0 else None


def classify(row: dict, *, delay_threshold_ms: float = 10.0,
             smash_threshold: float = 1.65) -> RecoveryDecision:
    """Advisory classification only; never writes a chosen speed.

    Unknown club types are allowed for historical REVIEW, but not asserted to
    be safe for an automatic fix. Low ball speeds get no nominated candidate.
    """
    current = _number(row.get("current_mph"))
    ball = _number(row.get("measured_ball_mph"))
    legacy = _number(row.get("legacy_mph"))
    tracker = _number(row.get("tracker_mph"))
    transition = _number(row.get("transition_mph"))
    try:
        delay = float(row.get("ball_trigger_delay_ms"))
    except (ValueError, TypeError):
        delay = float("nan")
    smash = (ball / current) if (ball and current) else None
    late = isfinite(delay) and delay > delay_threshold_ms
    bad_smash = smash is not None and smash > smash_threshold
    suspect = late and bad_smash
    flagged = late or bad_smash

    terminal_delta = abs(tracker-transition) if tracker and transition else None
    terminal_agree = (
        "unavailable" if terminal_delta is None else
        "strong" if terminal_delta <= 2 else
        "moderate" if terminal_delta <= 4 else "disagreement"
    )
    speeds = (legacy, tracker, transition)
    if all(x is not None for x in speeds):
        spread = max(speeds)-min(speeds)
        three_way = "strong" if spread <= 2 else "moderate" if spread <= 4 else "disagreement"
    else:
        three_way = "unavailable"

    if not flagged:
        return RecoveryDecision("keep_current", None, "no_timing_or_smash_flag",
                                terminal_agree, terminal_delta, three_way, False, False)
    if not suspect:
        return RecoveryDecision("investigate_only", None, "timing_or_smash_flag_only",
                                terminal_agree, terminal_delta, three_way, True, False)
    if not ball or ball < 130:
        return RecoveryDecision("investigate_only", None, "low_ball_speed_or_missing",
                                terminal_agree, terminal_delta, three_way, True, False)
    if three_way in ("strong","moderate") and legacy is not None:
        return RecoveryDecision("strong_review", legacy, "three_way_agreement",
                                terminal_agree, terminal_delta, three_way, True, True)
    if terminal_agree == "strong" and tracker and transition:
        candidate = (tracker + transition) / 2
        return RecoveryDecision("provisional_review", candidate, "terminal_pair_only",
                                terminal_agree, terminal_delta, three_way, True, True)
    return RecoveryDecision("investigate_only", None, "candidates_disagree_or_unavailable",
                            terminal_agree, terminal_delta, three_way, True, False)
