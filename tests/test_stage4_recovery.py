"""Stage 4 advisory regression tests."""
import csv
from pathlib import Path
from openflight.rolling_buffer.club_speed_stage4 import classify

CASES = list(csv.DictReader((Path(__file__).with_name('stage4_regression_cases.csv')).open()))


def test_historical_anomalies():
    anomalies = [r for r in CASES if r['diagnostic_status'].startswith('review_timing_and_smash')]
    assert len(anomalies) == 9
    kinds = [classify(r).classification for r in anomalies]
    assert kinds.count('strong_review') == 5
    assert kinds.count('provisional_review') == 3
    assert kinds.count('investigate_only') == 1


def test_other_fixtures_not_nominated():
    for r in CASES:
        if not r['diagnostic_status'].startswith('review_timing_and_smash'):
            assert classify(r).candidate_mph is None


def test_known_unresolved():
    case = next(r for r in CASES if '20261016' in r['session'] and r['shot'] == '13')
    assert classify(case).classification == 'investigate_only'


def test_does_not_mutate():
    row = dict(current_mph=72, measured_ball_mph=154, legacy_mph=108,
               tracker_mph=109, transition_mph=109.4, ball_trigger_delay_ms=14)
    before = row.copy()
    assert classify(row).classification == 'strong_review'
    assert row == before


def test_no_speed_replacement_without_both_flags():
    row = dict(current_mph=109, measured_ball_mph=154, legacy_mph=108,
               tracker_mph=109, transition_mph=109.4, ball_trigger_delay_ms=14)
    assert classify(row).candidate_mph is None
