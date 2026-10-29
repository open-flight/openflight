"""Experimental read-only clubhead-speed diagnostics; never selects a live speed.

Radar-trigger timing is provisional: wind can make microphone triggers unreliable.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional
import numpy as np

DELAY_FLAG_MS = 10.0
SMASH_REVIEW_THRESHOLD = 1.65
STRONG_AGREEMENT_MPH = 2.0
MODERATE_AGREEMENT_MPH = 4.0


@dataclass(frozen=True)
class ClubSpeedDiagnostic:
    current_mph: Optional[float]
    legacy_mph: Optional[float]
    tracker_mph: Optional[float]
    tracker_points: int
    tracker_span_ms: Optional[float]
    ball_trigger_delay_ms: Optional[float]
    current_smash: Optional[float]
    delayed_ball_timestamp: bool
    suspect_current_speed: bool
    agreement: str
    impact_transition_mph: Optional[float]
    three_way_agreement: str
    three_way_spread_mph: Optional[float]
    review_candidate_mph: Optional[float]
    status: str

    def to_dict(self) -> dict:
        return asdict(self)


def _continuous_candidate(timeline, ball_speed_mph: float, trigger_ms: float):
    per_timestamp = {}
    for reading in timeline.readings:
        if not (reading.is_outbound
                and 0.67 * ball_speed_mph <= reading.speed_mph <= 0.85 * ball_speed_mph
                and trigger_ms - 22 <= reading.timestamp_ms <= trigger_ms + 2):
            continue
        previous = per_timestamp.get(reading.timestamp_ms)
        if previous is None or reading.speed_mph > previous.speed_mph:
            per_timestamp[reading.timestamp_ms] = reading

    readings = sorted(per_timestamp.values(), key=lambda r: r.timestamp_ms)
    chains = []
    for reading in readings:
        predecessors = [
            (score, index)
            for index, (previous, score, chain) in enumerate(chains)
            if 0 < reading.timestamp_ms - previous.timestamp_ms <= 3.25
            and abs(reading.speed_mph - previous.speed_mph) <= 5.0
        ]
        parent = max(predecessors, default=None)
        chain = chains[parent[1]][2] + [reading] if parent else [reading]
        score = len(chain) + 0.015 * sum(r.magnitude for r in chain)
        chains.append((reading, score, chain))

    if not chains:
        return None, 0, None
    trace = max(chains, key=lambda v: (len(v[2]), v[0].timestamp_ms))[2]
    if len(trace) < 3:
        return None, len(trace), None
    candidate = float(np.quantile([r.speed_mph for r in trace[-6:]], 0.70))
    span = trace[-1].timestamp_ms - trace[0].timestamp_ms
    return candidate, len(trace), span


def diagnose_club_speed(processor, timeline, ball_speed_mph: float,
                        ball_timestamp_ms: float, capture, club_type=None,
                        current_mph=None, impact=None):
    """Compare candidates without mutating processor, timeline, or capture.

    current_mph lets the production path reuse its already-selected club speed;
    this avoids calling the live selection method a second time.
    """
    if current_mph is None:
        kw = {"club_type": club_type} if club_type is not None else {}
        current_mph, _ = processor.find_club_speed(
            timeline, ball_speed_mph, ball_timestamp_ms, **kw
        )
    legacy, _ = processor._find_club_speed_by_magnitude(
        timeline, ball_speed_mph, ball_timestamp_ms, 100
    )
    trigger_ms = capture.trigger_offset_ms if capture is not None else None
    delay = ball_timestamp_ms - trigger_ms if trigger_ms is not None else None
    if trigger_ms is not None:
        tracker, points, span = _continuous_candidate(timeline, ball_speed_mph, trigger_ms)
    else:
        tracker, points, span = None, 0, None

    smash = ball_speed_mph / current_mph if current_mph and current_mph > 0 else None
    late = delay is not None and delay > DELAY_FLAG_MS
    suspect = smash is not None and smash > SMASH_REVIEW_THRESHOLD
    if legacy is None or tracker is None:
        agreement = 'unavailable'
    else:
        diff = abs(legacy - tracker)
        agreement = ('strong' if diff <= STRONG_AGREEMENT_MPH else
                     'moderate' if diff <= MODERATE_AGREEMENT_MPH else 'disagreement')

    # Uses the EXISTING impact estimate, never recalculates or mutates it.
    impact_speed = (
        getattr(impact, 'last_club_speed_mph', None)
        if getattr(impact, 'source', None) == 'ops_transition' else None
    )
    values = (legacy, tracker, impact_speed)
    if all(v is not None and np.isfinite(v) and v > 0 for v in values):
        spread = float(max(values) - min(values))
        three_way = ('strong' if spread <= STRONG_AGREEMENT_MPH else
                     'moderate' if spread <= MODERATE_AGREEMENT_MPH else 'disagreement')
    else:
        spread = None
        three_way = 'unavailable'

    if late and suspect and three_way in ('strong', 'moderate'):
        status = 'review_timing_and_smash'
        review = legacy
    elif late and suspect:
        status = 'review_timing_and_smash_no_agreement'
        review = None
    elif late:
        status = 'review_late_timestamp'
        review = None
    elif suspect:
        status = 'review_implausible_smash'
        review = None
    else:
        status = 'no_flag'
        review = None

    return ClubSpeedDiagnostic(
        current_mph=current_mph, legacy_mph=legacy, tracker_mph=tracker,
        tracker_points=points, tracker_span_ms=span,
        ball_trigger_delay_ms=delay, current_smash=smash,
        delayed_ball_timestamp=late, suspect_current_speed=suspect,
        agreement=agreement, impact_transition_mph=impact_speed,
        three_way_agreement=three_way, three_way_spread_mph=spread,
        review_candidate_mph=review, status=status,
    )
