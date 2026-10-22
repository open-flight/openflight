from types import SimpleNamespace
from openflight.rolling_buffer import club_speed_diagnostics as mod

class FakeProcessor:
    def __init__(self, speed=73, legacy=109): self.speed, self.legacy, self.calls = speed,legacy,0
    def find_club_speed(self,*a,**k): self.calls+=1; return self.speed,60
    def _find_club_speed_by_magnitude(self,*a): return self.legacy,60

def capture(): return SimpleNamespace(trigger_offset_ms=65)
def timeline(): return SimpleNamespace(readings=[])
def impact(speed,source='ops_transition'):
    return SimpleNamespace(source=source,last_club_speed_mph=speed)

def test_three_way_strong(monkeypatch):
    monkeypatch.setattr(mod,'_continuous_candidate',lambda *a:(109.5,7,8.0))
    p=FakeProcessor(73,109)
    r=mod.diagnose_club_speed(p,timeline(),154,80,capture(),current_mph=73,impact=impact(109.7))
    assert r.three_way_agreement=='strong' and r.review_candidate_mph==109
    assert r.impact_transition_mph==109.7 and p.calls==0

def test_three_way_large_disagreement_is_not_nominated(monkeypatch):
    monkeypatch.setattr(mod,'_continuous_candidate',lambda *a:(111.9,7,8.0))
    r=mod.diagnose_club_speed(FakeProcessor(74,104.5),timeline(),154,80,capture(),current_mph=74,impact=impact(115.3))
    assert r.three_way_agreement=='disagreement' and r.review_candidate_mph is None

def test_transition_missing_not_nominated(monkeypatch):
    monkeypatch.setattr(mod,'_continuous_candidate',lambda *a:(109.5,7,8.0))
    r=mod.diagnose_club_speed(FakeProcessor(),timeline(),154,80,capture(),current_mph=73,impact=impact(None,'sound_trigger'))
    assert r.three_way_agreement=='unavailable' and r.review_candidate_mph is None

def test_no_mutation_of_impact(monkeypatch):
    monkeypatch.setattr(mod,'_continuous_candidate',lambda *a:(109.5,7,8.0))
    i=impact(109.7)
    before=vars(i).copy()
    mod.diagnose_club_speed(FakeProcessor(),timeline(),154,80,capture(),current_mph=73,impact=i)
    assert vars(i)==before
