"""Tests for experimental diagnostics and unchanged live speed selection."""
from types import SimpleNamespace
from openflight.rolling_buffer.club_speed_diagnostics import diagnose_club_speed, _continuous_candidate

class FakeProcessor:
    def __init__(self, current, legacy):
        self.current = current
        self.legacy = legacy
        self.calls = 0
    def find_club_speed(self, timeline, ball, timestamp, **kwargs):
        self.calls += 1
        return self.current, 50
    def _find_club_speed_by_magnitude(self, timeline, ball, timestamp, maximum):
        return self.legacy, 50

def timeline(speeds, start=48, dt=1.1):
    return SimpleNamespace(readings=[SimpleNamespace(speed_mph=s, timestamp_ms=start+i*dt,
        is_outbound=True, magnitude=20.0) for i,s in enumerate(speeds)])

def cap(trigger=65):
    return SimpleNamespace(trigger_offset_ms=trigger)

def test_shadow_reuses_selected_speed(monkeypatch):
    from openflight.rolling_buffer import club_speed_diagnostics as mod
    monkeypatch.setattr(mod, '_continuous_candidate', lambda *a: (109.0, 7, 8.0))
    proc = FakeProcessor(73.0, 108.0)
    result = diagnose_club_speed(proc, timeline([]), 154, 80, cap(), current_mph=73.0)
    assert result.current_mph == 73.0
    assert proc.calls == 0
    assert proc.current == 73.0
    assert result.agreement == 'strong'
    assert result.review_candidate_mph is None
    assert result.three_way_agreement == 'unavailable'

def test_medium_agreement(monkeypatch):
    from openflight.rolling_buffer import club_speed_diagnostics as mod
    monkeypatch.setattr(mod, '_continuous_candidate', lambda *a: (111.0, 7, 8.0))
    result = diagnose_club_speed(FakeProcessor(72, 108), timeline([]), 155, 80, cap())
    assert result.agreement == 'moderate'
    assert result.review_candidate_mph is None
    assert result.three_way_agreement == 'unavailable'

def test_large_disagreement_never_nominates(monkeypatch):
    from openflight.rolling_buffer import club_speed_diagnostics as mod
    monkeypatch.setattr(mod, '_continuous_candidate', lambda *a: (113.1, 7, 8.0))
    result = diagnose_club_speed(FakeProcessor(73.8, 106.1), timeline([]), 156.4, 80, cap())
    assert result.agreement == 'disagreement'
    assert result.review_candidate_mph is None

def test_late_but_plausible_never_nominates(monkeypatch):
    from openflight.rolling_buffer import club_speed_diagnostics as mod
    monkeypatch.setattr(mod, '_continuous_candidate', lambda *a: (109.0, 7, 8.0))
    result = diagnose_club_speed(FakeProcessor(108, 108), timeline([]), 153, 80, cap())
    assert result.status == 'review_late_timestamp'
    assert result.review_candidate_mph is None

def test_no_trigger():
    result = diagnose_club_speed(FakeProcessor(106, 108), timeline([]), 151, 70, None)
    assert result.status == 'no_flag'
    assert result.ball_trigger_delay_ms is None

def test_tracker_needs_three_points():
    candidate, points, _ = _continuous_candidate(timeline([109, 110]), 155, 60)
    assert candidate is None and points <= 2
