"""Selecting and caching the session shots a reconnecting phone missed."""

import threading

import pytest

from openflight.phone_catch_up import (
    CATCH_UP_LIMIT,
    MAX_REMEMBERED_DELETIONS,
    PhoneShotCache,
    normalize_last_event_id,
    select_catch_up,
)


def _entries(count):
    return [(f"id-{index}", f"payload-{index}".encode()) for index in range(count)]


def _ids(entries):
    return [event_id for event_id, _payload in entries]


class TestSelectCatchUp:
    def test_known_id_returns_that_shot_and_later_ones_in_order(self):
        """The named shot is resent too: the phone may only have its provisional."""
        assert _ids(select_catch_up(_entries(5), "id-2")) == ["id-2", "id-3", "id-4"]

    def test_latest_id_returns_only_that_shot(self):
        assert _ids(select_catch_up(_entries(5), "id-4")) == ["id-4"]

    def test_no_id_returns_whole_session(self):
        assert _ids(select_catch_up(_entries(3), None)) == ["id-0", "id-1", "id-2"]

    def test_unknown_id_returns_whole_session(self):
        """A cleared session, deleted shot or Pi restart: the phone's anchor is gone."""
        assert _ids(select_catch_up(_entries(3), "not-in-session")) == ["id-0", "id-1", "id-2"]

    def test_empty_session_returns_nothing(self):
        assert select_catch_up([], None) == []
        assert select_catch_up([], "id-0") == []

    def test_caps_to_most_recent_shots(self):
        selected = select_catch_up(_entries(CATCH_UP_LIMIT + 5), None)

        assert len(selected) == CATCH_UP_LIMIT
        assert selected[0][0] == "id-5"
        assert selected[-1][0] == f"id-{CATCH_UP_LIMIT + 4}"

    def test_caps_missed_shots_after_a_known_id(self):
        selected = select_catch_up(_entries(30), "id-2", limit=4)

        assert _ids(selected) == ["id-26", "id-27", "id-28", "id-29"]

    def test_payloads_travel_with_their_ids(self):
        assert select_catch_up(_entries(2), "id-1") == [("id-1", b"payload-1")]

    def test_limit_covers_a_long_gap(self):
        """A tester missed 55 shots; the old limit of 20 lost the oldest 35."""
        assert CATCH_UP_LIMIT >= 100
        assert len(select_catch_up(_entries(55), "id-0")) == 55

    def test_rejects_non_positive_limit(self):
        with pytest.raises(ValueError):
            select_catch_up(_entries(2), None, limit=0)


class TestNormalizeLastEventId:
    @pytest.mark.parametrize("value", [None, "", "   ", 7, True, ["id"], {"id": 1}, "x" * 65])
    def test_invalid_values_mean_no_anchor(self, value):
        assert normalize_last_event_id(value) is None

    def test_strips_whitespace(self):
        assert normalize_last_event_id("  abc  ") == "abc"

    def test_accepts_uuid(self):
        event_id = "05dd37ec-49ed-596b-b1a4-953d54e4f239"
        assert normalize_last_event_id(event_id) == event_id


class TestPhoneShotCache:
    def test_remembers_latest_payload_per_event_id(self):
        cache = PhoneShotCache()
        cache.remember("a", b"provisional")
        cache.remember("a", b"final")

        assert cache.get("a") == b"final"
        assert cache.get("missing") is None

    def test_evicts_least_recently_written_when_full(self):
        cache = PhoneShotCache(max_entries=2)
        cache.remember("a", b"1")
        cache.remember("b", b"2")
        cache.remember("a", b"1-final")  # refreshes "a"
        cache.remember("c", b"3")

        assert cache.get("b") is None
        assert cache.get("a") == b"1-final"
        assert cache.get("c") == b"3"
        assert len(cache) == 2

    def test_rejects_non_positive_size(self):
        with pytest.raises(ValueError):
            PhoneShotCache(max_entries=0)

    def test_concurrent_writers_stay_bounded(self):
        cache = PhoneShotCache(max_entries=50)

        def write(prefix):
            for index in range(500):
                cache.remember(f"{prefix}-{index}", b"x")

        threads = [threading.Thread(target=write, args=(name,)) for name in "abcd"]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(cache) == 50


class TestDeletionsSince:
    """Deletions a reconnecting phone missed, in the order the Pi made them."""

    def test_deletions_after_the_anchor_was_sent(self):
        cache = PhoneShotCache()
        cache.remember("a", b"1")
        cache.remember_deletion("ts-old")
        cache.remember("b", b"2")
        cache.remember_deletion("ts-1")
        cache.remember_deletion("ts-2")

        assert cache.deletions_since("b") == ["ts-1", "ts-2"]

    def test_no_deletions_after_the_anchor(self):
        cache = PhoneShotCache()
        cache.remember_deletion("ts-old")
        cache.remember("a", b"1")

        assert cache.deletions_since("a") == []

    @pytest.mark.parametrize("anchor", [None, "unknown"])
    def test_no_or_unknown_anchor_returns_every_remembered_deletion(self, anchor):
        """The phone's anchor may itself have been deleted: replay all, they are idempotent."""
        cache = PhoneShotCache()
        cache.remember_deletion("ts-1")
        cache.remember("a", b"1")
        cache.remember_deletion("ts-2")

        assert cache.deletions_since(anchor) == ["ts-1", "ts-2"]

    def test_anchor_position_is_its_first_send(self):
        """A phone may hold only the provisional: deletions after it still count."""
        cache = PhoneShotCache()
        cache.remember("a", b"provisional")
        cache.remember_deletion("ts-1")
        cache.remember("a", b"final")

        assert cache.deletions_since("a") == ["ts-1"]

    def test_a_repeated_deletion_is_reported_once_at_its_latest_position(self):
        cache = PhoneShotCache()
        cache.remember_deletion("ts-1")
        cache.remember("a", b"1")
        cache.remember_deletion("ts-1")

        assert cache.deletions_since(None) == ["ts-1"]
        assert cache.deletions_since("a") == ["ts-1"]

    def test_keeps_only_the_most_recent_deletions(self):
        cache = PhoneShotCache()
        for index in range(MAX_REMEMBERED_DELETIONS + 3):
            cache.remember_deletion(f"ts-{index}")

        deletions = cache.deletions_since(None)
        assert len(deletions) == MAX_REMEMBERED_DELETIONS
        assert deletions[0] == "ts-3"

    @pytest.mark.parametrize("timestamp", [None, "", 7])
    def test_ignores_invalid_timestamps(self, timestamp):
        cache = PhoneShotCache()
        cache.remember_deletion(timestamp)

        assert cache.deletions_since(None) == []
