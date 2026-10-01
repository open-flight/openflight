"""BLE schema v2 catch-up: a reconnecting phone receives the session shots it missed.

Driven over the loopback harness with the real publisher and server dispatch.
The phone follows the documented negotiation: subscribe to v2 control, write
``hello`` (optionally naming ``last_event_id``), then subscribe to v2 shot.
"""

import json
from datetime import datetime

import pytest
from ble_harness import V2_UUIDS, server_loopback, settle

from openflight import server as server_module
from openflight.ble.protocol import (
    CONTROL_V2_CHARACTERISTIC_UUID,
    SHOT_V2_CHARACTERISTIC_UUID,
    stable_shot_event_id,
)
from openflight.launch_monitor import ClubType, Shot
from openflight.phone_catch_up import CATCH_UP_LIMIT


@pytest.fixture
def pi(monkeypatch, tmp_path):
    with server_loopback(monkeypatch, tmp_path) as loopback:
        yield loopback


def _session(monkeypatch, count, *, profile_id=""):
    """A mock monitor holding ``count`` shots; returns their event ids in order."""
    monitor = server_module.MockLaunchMonitor()
    for index in range(count):
        monitor._shots.append(
            Shot(
                ball_speed_mph=100.0 + index,
                timestamp=datetime(2026, 9, 28, 12, index // 60, index % 60, 1000),
                club=ClubType.DRIVER,
                shot_number=index + 1,
                profile_id=profile_id,
            )
        )
    monkeypatch.setattr(server_module, "monitor", monitor)
    return [stable_shot_event_id(server_module.shot_to_dict(shot)) for shot in monitor.get_shots()]


def _publish_latest_live():
    """Publish the session's last shot as the live path would (sets the latest replay)."""
    shot_data = server_module.shot_to_dict(server_module.monitor.get_shots()[-1])
    server_module._publish_phone_shot_v2(shot_data, final=True, enrichment=None)


def _hello(phone, **payload):
    return phone.request(
        "hello",
        {"client_schema_max": 2, **payload},
        control=CONTROL_V2_CHARACTERISTIC_UUID,
        schema_version=2,
    )


def _connect(pi, name="v2-app", **hello_payload):
    """Negotiate as the documented flow does: control, hello, then shot."""
    phone = pi.central(name)
    phone.subscribe(CONTROL_V2_CHARACTERISTIC_UUID)
    answer = _hello(phone, **hello_payload)
    assert answer["ok"] is True, answer
    phone.subscribe(SHOT_V2_CHARACTERISTIC_UUID)
    return phone


def _received_ids(phone):
    return [shot["event_id"] for shot in phone.decoded(SHOT_V2_CHARACTERISTIC_UUID)]


def test_hello_advertises_catch_up(pi):
    phone = pi.central()
    phone.subscribe(CONTROL_V2_CHARACTERISTIC_UUID)

    assert "shot_catch_up" in _hello(phone)["result"]["features"]


def test_hello_without_anchor_replays_whole_session_in_order(pi, monkeypatch):
    ids = _session(monkeypatch, 3)

    phone = _connect(pi)

    phone.wait_for_count(SHOT_V2_CHARACTERISTIC_UUID, 3)
    settle(pi)
    assert _received_ids(phone) == ids
    assert all(shot["final"] for shot in phone.decoded(SHOT_V2_CHARACTERISTIC_UUID))


def test_hello_with_known_anchor_resends_it_and_sends_missed_shots(pi, monkeypatch):
    ids = _session(monkeypatch, 5)

    phone = _connect(pi, last_event_id=ids[1])

    phone.wait_for_count(SHOT_V2_CHARACTERISTIC_UUID, 4)
    settle(pi)
    assert _received_ids(phone) == ids[1:]


def test_hello_with_latest_anchor_resends_only_that_shot_once(pi, monkeypatch):
    ids = _session(monkeypatch, 3)
    _publish_latest_live()

    phone = _connect(pi, last_event_id=ids[-1])
    phone.wait_for(SHOT_V2_CHARACTERISTIC_UUID)
    settle(pi, rounds=10)

    # The catch-up replaces the latest-shot replay rather than adding to it.
    assert _received_ids(phone) == [ids[-1]]


def test_anchor_held_as_provisional_is_brought_up_to_final(pi, monkeypatch):
    """The phone saw the provisional, disconnected, and missed the final."""
    ids = _session(monkeypatch, 2)
    shot_data = server_module.shot_to_dict(server_module.monitor.get_shots()[-1])
    server_module._publish_phone_shot_v2(shot_data, final=False, enrichment={"status": "pending"})
    server_module._publish_phone_shot_v2(shot_data, final=True, enrichment={"status": "complete"})

    phone = _connect(pi, last_event_id=ids[-1])

    shot = phone.wait_for(SHOT_V2_CHARACTERISTIC_UUID)
    assert shot["event_id"] == ids[-1]
    assert shot["final"] is True
    assert shot["enrichment"] == {"status": "complete"}


def test_unknown_anchor_replays_whole_session(pi, monkeypatch):
    ids = _session(monkeypatch, 2)

    phone = _connect(pi, last_event_id="11111111-2222-3333-4444-555555555555")

    phone.wait_for_count(SHOT_V2_CHARACTERISTIC_UUID, 2)
    settle(pi)
    assert _received_ids(phone) == ids


@pytest.mark.parametrize("anchor", [7, "", None, {"id": "x"}])
def test_invalid_anchor_does_not_fail_hello(pi, monkeypatch, anchor):
    ids = _session(monkeypatch, 2)

    phone = _connect(pi, last_event_id=anchor)

    phone.wait_for_count(SHOT_V2_CHARACTERISTIC_UUID, 2)
    settle(pi)
    assert _received_ids(phone) == ids


def test_more_missed_shots_than_the_queue_holds_are_all_delivered(pi, monkeypatch):
    count = pi.publisher.queue_size + 4
    ids = _session(monkeypatch, count)

    phone = _connect(pi)

    phone.wait_for_count(SHOT_V2_CHARACTERISTIC_UUID, count, timeout=15)
    settle(pi)
    assert _received_ids(phone) == ids


def test_catch_up_is_capped_to_most_recent_shots(pi, monkeypatch):
    ids = _session(monkeypatch, CATCH_UP_LIMIT + 3)

    phone = _connect(pi)

    phone.wait_for_count(SHOT_V2_CHARACTERISTIC_UUID, CATCH_UP_LIMIT, timeout=20)
    settle(pi, rounds=10)
    assert _received_ids(phone) == ids[-CATCH_UP_LIMIT:]


def test_hello_after_shot_subscription_sends_catch_up_immediately(pi, monkeypatch):
    ids = _session(monkeypatch, 3)
    phone = pi.central()
    phone.subscribe(*V2_UUIDS)
    settle(pi)

    assert _hello(phone, last_event_id=ids[1])["ok"] is True

    phone.wait_for_count(SHOT_V2_CHARACTERISTIC_UUID, 2)
    settle(pi)
    assert _received_ids(phone) == ids[1:]


def test_replayed_shot_is_byte_identical_to_last_published_version(pi, monkeypatch):
    """A provisional-then-final shot replays as the final, enrichment included."""
    _session(monkeypatch, 1)
    shot_data = server_module.shot_to_dict(server_module.monitor.get_shots()[0])
    server_module._publish_phone_shot_v2(shot_data, final=False, enrichment={"status": "pending"})
    server_module._publish_phone_shot_v2(shot_data, final=True, enrichment={"status": "complete"})
    published = server_module.phone_shot_cache.get(stable_shot_event_id(shot_data))

    phone = _connect(pi)

    shot = phone.wait_for(SHOT_V2_CHARACTERISTIC_UUID)
    settle(pi)
    assert published is not None
    assert shot == json.loads(published)
    assert shot["final"] is True
    assert shot["enrichment"] == {"status": "complete"}
    assert phone.decoded(SHOT_V2_CHARACTERISTIC_UUID) == [shot]


def test_deleted_shots_are_not_replayed(pi, monkeypatch):
    ids = _session(monkeypatch, 3)
    first = server_module.monitor.get_shots()[0]
    server_module.apply_delete_shot({"timestamp": first.timestamp.isoformat()})

    phone = _connect(pi)

    phone.wait_for_count(SHOT_V2_CHARACTERISTIC_UUID, 2)
    settle(pi)
    assert _received_ids(phone) == ids[1:]


def test_cleared_profile_shots_are_not_replayed(pi, monkeypatch):
    active_id = server_module.get_profile_store().get_active().id
    _session(monkeypatch, 2, profile_id=active_id)
    server_module.apply_clear_session({})

    phone = _connect(pi)
    settle(pi, rounds=10)

    assert _received_ids(phone) == []


def test_disconnect_before_shot_subscription_discards_pending_catch_up(pi, monkeypatch):
    ids = _session(monkeypatch, 3)
    _publish_latest_live()
    phone = pi.central()
    phone.subscribe(CONTROL_V2_CHARACTERISTIC_UUID)
    assert _hello(phone)["ok"] is True
    phone.disconnect()
    settle(pi)

    # Reconnects without a new hello: only the latest-shot replay, as before.
    phone.subscribe(SHOT_V2_CHARACTERISTIC_UUID)
    phone.wait_for(SHOT_V2_CHARACTERISTIC_UUID)
    settle(pi, rounds=10)
    assert _received_ids(phone) == [ids[-1]]


def test_v1_hello_does_not_trigger_catch_up(pi, monkeypatch):
    _session(monkeypatch, 3)
    phone = pi.central()
    phone.subscribe(CONTROL_V2_CHARACTERISTIC_UUID)

    answer = _hello(phone, client_schema_max=1)
    phone.subscribe(SHOT_V2_CHARACTERISTIC_UUID)
    settle(pi, rounds=10)

    assert answer["result"] == {"schema_version": 1, "features": []}
    assert _received_ids(phone) == []


def test_catch_up_failure_keeps_hello_and_falls_back_to_latest_replay(pi, monkeypatch):
    ids = _session(monkeypatch, 2)
    _publish_latest_live()

    def broken(_last_event_id):
        raise RuntimeError("session unavailable")

    monkeypatch.setattr(pi.publisher, "catch_up_provider", broken)

    phone = _connect(pi)

    phone.wait_for(SHOT_V2_CHARACTERISTIC_UUID)
    settle(pi, rounds=10)
    assert _received_ids(phone) == [ids[-1]]


def test_no_monitor_means_no_catch_up(pi):
    phone = _connect(pi)
    settle(pi, rounds=10)

    assert _received_ids(phone) == []
