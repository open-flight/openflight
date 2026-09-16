"""Tests for the openflight-cloud spool-and-retry sidecar mechanics."""

import json

import pytest

from openflight.cloud import spool


def _session(tmp_path, name="session_20260614_120000_range.jsonl"):
    path = tmp_path / name
    path.write_text('{"type":"session_start"}\n')
    return path


class TestDiscovery:
    def test_session_files_finds_only_session_jsonl(self, tmp_path):
        _session(tmp_path, "session_a.jsonl")
        _session(tmp_path, "session_b.jsonl")
        (tmp_path / "radar_raw_x.log").write_text("noise")
        (tmp_path / "other.jsonl").write_text("{}")
        found = {p.name for p in spool.session_files(tmp_path)}
        assert found == {"session_a.jsonl", "session_b.jsonl"}

    def test_session_files_empty_when_dir_missing(self, tmp_path):
        assert spool.session_files(tmp_path / "nope") == []

    def test_pending_excludes_pushed_and_parked(self, tmp_path):
        a = _session(tmp_path, "session_a.jsonl")
        b = _session(tmp_path, "session_b.jsonl")
        c = _session(tmp_path, "session_c.jsonl")
        d = _session(tmp_path, "session_d.jsonl")
        spool.mark_pushed(b, session_id="id-b", shot_count=3)
        spool.mark_parked(c, reason="quota_exceeded", attempts=20, last_error="402")
        spool.mark_skipped(d, reason="no_shots", shot_count=0)
        pending = {p.name for p in spool.pending_sessions(tmp_path)}
        assert pending == {"session_a.jsonl"}
        assert a  # referenced


class TestPushedMarker:
    def test_mark_pushed_creates_sidecar(self, tmp_path):
        path = _session(tmp_path)
        spool.mark_pushed(path, session_id="abc", shot_count=5)
        marker = tmp_path / (path.name + ".pushed")
        assert marker.exists()
        data = json.loads(marker.read_text())
        assert data["session_id"] == "abc"
        assert data["shot_count"] == 5
        assert spool.is_pushed(path)

    def test_mark_pushed_clears_attempt_state(self, tmp_path):
        path = _session(tmp_path)
        spool.record_failure(path, "5xx")
        spool.mark_pushed(path, session_id="abc", shot_count=1)
        assert spool.read_attempts(path) == 0
        assert not (tmp_path / (path.name + ".state")).exists()


class TestParkedMarker:
    def test_mark_parked_creates_sidecar(self, tmp_path):
        path = _session(tmp_path)
        spool.mark_parked(path, reason="invalid_session_id", attempts=1, last_error="422")
        marker = tmp_path / (path.name + ".parked")
        assert marker.exists()
        data = json.loads(marker.read_text())
        assert data["reason"] == "invalid_session_id"
        assert data["attempts"] == 1
        assert data["last_error"] == "422"
        assert spool.is_parked(path)


class TestSkippedMarker:
    def test_mark_skipped_creates_sidecar(self, tmp_path):
        path = _session(tmp_path)
        spool.mark_skipped(path, reason="no_shots", shot_count=0)
        marker = tmp_path / (path.name + ".skipped")
        assert marker.exists()
        data = json.loads(marker.read_text())
        assert data["reason"] == "no_shots"
        assert data["shot_count"] == 0
        assert spool.is_skipped(path)

    def test_mark_skipped_clears_attempt_state(self, tmp_path):
        path = _session(tmp_path)
        spool.record_failure(path, "5xx")
        spool.mark_skipped(path, reason="no_shots", shot_count=0)
        assert spool.read_attempts(path) == 0
        assert not (tmp_path / (path.name + ".state")).exists()


class TestAttemptCounter:
    def test_attempts_start_at_zero(self, tmp_path):
        path = _session(tmp_path)
        assert spool.read_attempts(path) == 0

    def test_record_failure_increments_and_returns_count(self, tmp_path):
        path = _session(tmp_path)
        assert spool.record_failure(path, "5xx") == 1
        assert spool.record_failure(path, "5xx") == 2
        assert spool.read_attempts(path) == 2

    def test_record_failure_stores_last_error(self, tmp_path):
        path = _session(tmp_path)
        spool.record_failure(path, "boom")
        state = json.loads((tmp_path / (path.name + ".state")).read_text())
        assert state["last_error"] == "boom"

    def test_record_failure_parks_after_max_attempts(self, tmp_path):
        path = _session(tmp_path)
        for _ in range(spool.MAX_ATTEMPTS - 1):
            spool.record_failure(path, "5xx")
        assert not spool.is_parked(path)
        spool.record_failure(path, "5xx")
        assert spool.is_parked(path)
        assert spool.read_attempts(path) == spool.MAX_ATTEMPTS


class TestCooldown:
    def test_no_cooldown_by_default(self, tmp_path):
        path = _session(tmp_path)
        assert spool.in_cooldown(path, now=1000.0) is False

    def test_record_cooldown_blocks_until_elapsed(self, tmp_path):
        path = _session(tmp_path)
        spool.record_cooldown(path, "quota_exceeded", seconds=100, now=1000.0)
        assert spool.in_cooldown(path, now=1050.0) is True
        assert spool.in_cooldown(path, now=1101.0) is False

    def test_cooldown_does_not_park(self, tmp_path):
        path = _session(tmp_path)
        spool.record_cooldown(path, "quota_exceeded", seconds=100, now=1000.0)
        assert not spool.is_parked(path)


class TestClearMarkers:
    def test_clears_parked_and_state_keeps_pushed_by_default(self, tmp_path):
        path = _session(tmp_path)
        spool.mark_pushed(path, "id", 1)
        spool.mark_parked(path, reason="r", attempts=2, last_error="e")
        spool.mark_skipped(path, reason="no_shots", shot_count=0)
        spool.record_failure(path, "e")  # writes .state
        cleared = spool.clear_markers(path)
        assert spool.PARKED_SUFFIX in cleared
        assert spool.SKIPPED_SUFFIX in cleared
        assert spool.STATE_SUFFIX in cleared
        assert spool.PUSHED_SUFFIX not in cleared
        assert spool.is_pushed(path)
        assert not spool.is_parked(path)
        assert not spool.is_skipped(path)

    def test_include_pushed_clears_everything(self, tmp_path):
        path = _session(tmp_path)
        spool.mark_pushed(path, "id", 1)
        cleared = spool.clear_markers(path, include_pushed=True)
        assert spool.PUSHED_SUFFIX in cleared
        assert not spool.is_pushed(path)

    def test_returns_empty_when_no_markers(self, tmp_path):
        path = _session(tmp_path)
        assert spool.clear_markers(path) == []

    def test_after_clear_session_is_pending_again(self, tmp_path):
        a = _session(tmp_path, "session_a.jsonl")
        spool.mark_parked(a, reason="r", attempts=20, last_error="e")
        assert a not in spool.pending_sessions(tmp_path)
        spool.clear_markers(a)
        assert a in spool.pending_sessions(tmp_path)


class TestStatusSummary:
    def test_summarizes_counts(self, tmp_path):
        a = _session(tmp_path, "session_a.jsonl")
        b = _session(tmp_path, "session_b.jsonl")
        c = _session(tmp_path, "session_c.jsonl")
        spool.mark_pushed(a, session_id="a", shot_count=1)
        spool.mark_parked(b, reason="r", attempts=20, last_error="402")
        spool.mark_skipped(c, reason="no_shots", shot_count=0)
        assert c
        summary = spool.summarize(tmp_path)
        assert summary["pushed"] == 1
        assert summary["parked"] == 1
        assert summary["skipped"] == 1
        assert summary["pending"] == 0
        assert summary["total"] == 3
