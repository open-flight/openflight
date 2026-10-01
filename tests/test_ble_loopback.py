"""End-to-end BLE tests over the loopback harness: no Pi, radio or bless needed.

Each test drives the real ``BleShotPublisher`` (own thread and event loop) and
the real server command dispatch, with virtual centrals standing in for
phones.
"""

import json
import threading
from datetime import datetime

import pytest
from ble_harness import PHONE_UUIDS, server_loopback, settle

from openflight import server as server_module
from openflight.ble.protocol import (
    CONTROL_CHARACTERISTIC_UUID,
    SHOT_CHARACTERISTIC_UUID,
    build_club_event,
    encode_message,
)
from openflight.launch_monitor import ClubType, Shot


@pytest.fixture
def pi(monkeypatch, tmp_path):
    """A monitor-less OpenFlight server with BLE on the loopback harness."""
    with server_loopback(monkeypatch, tmp_path) as loopback:
        yield loopback


def _slow_enrichment(monkeypatch):
    """Take the provisional-then-final path that optional hardware triggers."""
    monkeypatch.setattr(server_module, "camera_capture_runtime", object())
    monkeypatch.setattr(server_module, "shot_enrichment_task", None)
    monkeypatch.setattr(
        server_module,
        "shot_enrichment_queue",
        server_module.queue.Queue(maxsize=server_module._SHOT_ENRICHMENT_QUEUE_CAPACITY),
    )
    monkeypatch.setattr(
        server_module,
        "_enrich_shot_from_optional_hardware",
        lambda _shot: server_module._ShotEnrichmentResult(camera_capture_ms=5.0),
    )

    def start_background_task(target, *args, **kwargs):
        thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
        thread.start()
        return thread

    monkeypatch.setattr(server_module.socketio, "start_background_task", start_background_task)


def _hardware_shot(second=0):
    return Shot(
        ball_speed_mph=151.4,
        club_speed_mph=103.2,
        timestamp=datetime(2026, 9, 25, 12, 0, second, 123456),
        impact_timestamp=100.0 + second,
        club=ClubType.DRIVER,
    )


def _wait_idle():
    with server_module._shot_finalization_condition:
        assert server_module._shot_finalization_condition.wait_for(
            lambda: (
                not server_module._shot_finalization_order
                and not server_module._shot_finalization_running
            ),
            timeout=5,
        )


def test_central_gets_latest_shot_replayed_on_subscribe(pi):
    pi.publisher.publish_shot(
        {
            "timestamp": "2026-09-25T12:00:00",
            "club": "driver",
            "ball_speed_mph": 150.0,
            "estimated_carry_yards": 250,
            "shot_number": 3,
        },
        final=True,
    )
    phone = pi.central("app")
    # Control first, as the negotiation flow does, then the shot characteristic.
    phone.subscribe(CONTROL_CHARACTERISTIC_UUID)
    settle(pi)
    phone.subscribe(SHOT_CHARACTERISTIC_UUID)

    shot = phone.wait_for(SHOT_CHARACTERISTIC_UUID)

    assert shot["schema_version"] == 2
    assert shot["shot_number"] == 3
    assert shot["final"] is True
    assert phone.raw(SHOT_CHARACTERISTIC_UUID)[0] == pi.publisher._latest_payload
    settle(pi)
    assert len(phone.decoded(SHOT_CHARACTERISTIC_UUID)) == 1


def test_hello_answers_in_either_request_envelope(pi):
    phone = pi.central("app")
    phone.subscribe(*PHONE_UUIDS)

    answer = phone.request("hello", {"client_schema_max": 2})
    v1_envelope = phone.request("hello", {"client_schema_max": 2}, schema_version=1)

    assert answer["schema_version"] == 2
    assert answer["ok"] is True
    assert answer["result"]["schema_version"] == 2
    assert answer["result"]["characteristics"] == {
        "shot": SHOT_CHARACTERISTIC_UUID,
        "control": CONTROL_CHARACTERISTIC_UUID,
    }
    assert "profiles" in answer["result"]["features"]
    assert v1_envelope["schema_version"] == 2
    assert v1_envelope["result"] == answer["result"]


def test_hello_rejects_clients_that_cannot_speak_schema_2(pi):
    phone = pi.central("app")
    phone.subscribe(*PHONE_UUIDS)

    too_old = phone.request("hello", {"client_schema_max": 1})
    invalid = phone.request("hello", {"client_schema_max": "two"})

    assert too_old["ok"] is False
    assert "at least 2" in too_old["error"]
    assert invalid["ok"] is False
    assert "client_schema_max" in invalid["error"]


def test_provisional_and_final_shot_share_event_id_on_every_phone(pi, monkeypatch):
    _slow_enrichment(monkeypatch)
    first_phone = pi.central("first")
    second_phone = pi.central("second")
    first_phone.subscribe(*PHONE_UUIDS)
    second_phone.subscribe(*PHONE_UUIDS)

    server_module.on_shot_detected(_hardware_shot())
    _wait_idle()

    for phone in (first_phone, second_phone):
        provisional, final = phone.wait_for_count(SHOT_CHARACTERISTIC_UUID, 2)
        assert provisional["final"] is False
        assert provisional["enrichment"] == {"status": "pending"}
        assert final["final"] is True
        assert final["enrichment"] == {"status": "complete"}
        assert provisional["event_id"] == final["event_id"]
        assert final["schema_version"] == 2 and final["type"] == "shot"
        assert final["shot_number"] == provisional["shot_number"] is not None


def test_club_command_and_club_changed_reach_every_phone(pi):
    first_phone = pi.central("first")
    second_phone = pi.central("second")
    first_phone.subscribe(*PHONE_UUIDS)
    second_phone.subscribe(*PHONE_UUIDS)

    answer = first_phone.request("set_club", {"club": "7-iron"})
    assert answer == {
        "schema_version": 2,
        "request_id": answer["request_id"],
        "ok": True,
        "result": {"status": "applied", "club": "7-iron"},
    }

    for phone in (first_phone, second_phone):
        event = phone.wait_for(
            CONTROL_CHARACTERISTIC_UUID, lambda message: message.get("type") == "club_changed"
        )
        assert event == {"schema_version": 2, "type": "club_changed", "club": "7-iron"}
        raw_events = [
            raw for raw in phone.raw(CONTROL_CHARACTERISTIC_UUID) if b"club_changed" in raw
        ]
        assert raw_events == [encode_message(build_club_event("7-iron"))]
    assert ("club_changed", {"club": "7-iron"}) in pi.emitted

    current = second_phone.request("get_club")
    assert current["result"] == {"status": "current", "club": "7-iron"}


def test_profiles_commands_route_through_socketio_operations(pi):
    store = server_module.get_profile_store()
    second = store.add("Sam")
    store.set_active(store.list()[0].id)
    phone = pi.central("app")
    phone.subscribe(*PHONE_UUIDS)

    ack = phone.request("get_profiles")
    assert ack["result"] == {"status": "sent"}
    profiles = phone.wait_for(
        CONTROL_CHARACTERISTIC_UUID, lambda message: message.get("type") == "profiles"
    )
    assert [item["name"] for item in profiles["profiles"]] == ["Profile 1", "Sam"]
    assert set(profiles["profiles"][0]) == {"id", "name"}

    answer = phone.request("set_active_profile", {"profile_id": second.id})
    assert answer["result"] == {"status": "applied", "active_profile_id": second.id}
    assert store.get_active().id == second.id
    socket_profiles = [payload for event, payload in pi.emitted if event == "profiles"]
    assert socket_profiles[-1]["active_profile_id"] == second.id

    rejected = phone.request("set_active_profile", {"profile_id": "nope"})
    assert rejected["ok"] is False
    assert rejected["error"] == "Unknown profile"


def test_unknown_commands_and_envelopes_are_rejected(pi):
    phone = pi.central("app")
    phone.subscribe(*PHONE_UUIDS)

    unknown = phone.request("launch_rocket")
    future_envelope = phone.request("get_club", schema_version=3)

    assert unknown == {
        "schema_version": 2,
        "request_id": unknown["request_id"],
        "ok": False,
        "error": "Unsupported phone command: launch_rocket",
    }
    assert future_envelope["error"] == "Unsupported control schema version"


def test_session_and_power_commands(pi, monkeypatch):
    class _Power:
        status = None

    phone = pi.central("app")
    phone.subscribe(*PHONE_UUIDS)

    disabled = phone.request("get_power_status")
    assert disabled["error"] == "Battery monitoring is not enabled"
    monkeypatch.setattr(server_module, "power_monitor", _Power())
    waiting = phone.request("get_power_status")
    assert waiting["error"] == "No battery reading yet"


def test_destructive_commands_are_not_available_over_ble(pi):
    """BLE is unauthenticated, so it is read-and-select only."""
    phone = pi.central("app")
    phone.subscribe(*PHONE_UUIDS)

    for command, payload in (
        ("clear_session", {}),
        ("delete_shot", {"timestamp": "2026-09-25T12:00:00"}),
    ):
        for envelope in (1, 2):
            answer = phone.request(command, payload, schema_version=envelope)
            assert answer["ok"] is False
            assert answer["error"] == f"Unsupported phone command: {command}"
    assert pi.emitted == []


def test_network_clear_and_delete_reach_ble_phones_as_events(pi, monkeypatch):
    shot = Shot(
        ball_speed_mph=150.0,
        timestamp=datetime(2026, 9, 25, 12, 0, 0, 5),
        club=ClubType.DRIVER,
    )
    monitor = server_module.MockLaunchMonitor()
    monitor._shots.append(shot)
    monkeypatch.setattr(server_module, "monitor", monitor)
    phone = pi.central("app")
    phone.subscribe(*PHONE_UUIDS)
    active_id = server_module.get_profile_store().get_active().id

    server_module.handle_delete_shot({"timestamp": shot.timestamp.isoformat()})
    server_module.handle_clear_session({})

    deleted = phone.wait_for(
        CONTROL_CHARACTERISTIC_UUID, lambda message: message.get("type") == "shot_deleted"
    )
    cleared = phone.wait_for(
        CONTROL_CHARACTERISTIC_UUID,
        lambda message: message.get("type") == "session_cleared",
    )
    assert deleted == {
        "schema_version": 2,
        "type": "shot_deleted",
        "timestamp": shot.timestamp.isoformat(),
    }
    assert cleared == {"schema_version": 2, "type": "session_cleared", "profile_id": active_id}


def test_one_phone_leaving_keeps_the_other_receiving(pi):
    staying = pi.central("staying")
    leaving = pi.central("leaving")
    staying.subscribe(*PHONE_UUIDS)
    leaving.subscribe(*PHONE_UUIDS)
    assert pi.publisher.subscribed

    leaving.disconnect()
    settle(pi)
    assert pi.publisher.subscribed

    server_module.apply_club_selection({"club": "pw"})
    event = staying.wait_for(
        CONTROL_CHARACTERISTIC_UUID, lambda message: message.get("type") == "club_changed"
    )
    assert event["club"] == "pw"

    staying.disconnect()
    settle(pi)
    assert not pi.publisher.subscribed


def test_client_frames_for_two_commands_do_not_interleave_responses(pi):
    phone = pi.central("app")
    phone.subscribe(*PHONE_UUIDS)
    first = phone.command("get_club")
    second = phone.command("hello", {"client_schema_max": 2})

    answers = {
        message["request_id"]: message
        for message in (
            phone.wait_for(CONTROL_CHARACTERISTIC_UUID, lambda m: m.get("request_id") == first),
            phone.wait_for(CONTROL_CHARACTERISTIC_UUID, lambda m: m.get("request_id") == second),
        )
    }
    assert answers[first]["result"]["club"] == "driver"
    assert answers[second]["result"]["schema_version"] == 2
    # Every notification reassembled into valid JSON: no interleaved fragments.
    for raw in phone.raw(CONTROL_CHARACTERISTIC_UUID):
        json.loads(raw)
