"""End-to-end BLE tests over the loopback harness: no Pi, radio or bless needed.

Each test drives the real ``BleShotPublisher`` (own thread and event loop) and
the real server command dispatch, with virtual centrals standing in for
phones. A version-one central models jake-fishtech's iOS app and must only
ever see version-one bytes.
"""

import json
import threading
from datetime import datetime

import pytest
from ble_harness import V1_UUIDS, V2_UUIDS, server_loopback, settle

from openflight import server as server_module
from openflight.ble.protocol import (
    CONTROL_CHARACTERISTIC_UUID,
    CONTROL_V2_CHARACTERISTIC_UUID,
    SHOT_CHARACTERISTIC_UUID,
    SHOT_V2_CHARACTERISTIC_UUID,
    encode_club_event,
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


def test_v1_central_replays_latest_shot_on_subscribe(pi):
    pi.publisher.publish(
        {
            "timestamp": "2026-09-25T12:00:00",
            "club": "driver",
            "ball_speed_mph": 150.0,
            "estimated_carry_yards": 250,
        }
    )
    phone = pi.central("v1-app")
    phone.subscribe(*V1_UUIDS)

    shot = phone.wait_for(SHOT_CHARACTERISTIC_UUID)
    assert shot["schema_version"] == 1
    assert shot["ball_speed_mph"] == 150.0
    assert phone.raw(SHOT_CHARACTERISTIC_UUID)[0] == pi.publisher._latest_payload


def test_hello_negotiates_on_both_control_characteristics(pi):
    phone = pi.central("v2-app")
    phone.subscribe(*V1_UUIDS, *V2_UUIDS)

    v1_answer = phone.request("hello", {"client_schema_max": 2})
    v2_answer = phone.request(
        "hello",
        {"client_schema_max": 2},
        control=CONTROL_V2_CHARACTERISTIC_UUID,
        schema_version=2,
    )

    assert v1_answer["schema_version"] == 1  # v1 envelope on the v1 characteristic
    assert v1_answer["ok"] is True
    assert v1_answer["result"]["schema_version"] == 2
    assert v1_answer["result"]["characteristics"] == {
        "shot": SHOT_V2_CHARACTERISTIC_UUID,
        "control": CONTROL_V2_CHARACTERISTIC_UUID,
    }
    assert "profiles" in v1_answer["result"]["features"]
    assert v2_answer["schema_version"] == 2
    assert v2_answer["result"] == v1_answer["result"]

    v1_client = phone.request("hello", {"client_schema_max": 1})
    assert v1_client["result"] == {"schema_version": 1, "features": []}

    invalid = phone.request("hello", {"client_schema_max": "two"})
    assert invalid["ok"] is False
    assert "client_schema_max" in invalid["error"]


def test_provisional_and_final_shot_share_event_id_and_v1_sees_only_final(pi, monkeypatch):
    _slow_enrichment(monkeypatch)
    v1_phone = pi.central("v1-app")
    v2_phone = pi.central("v2-app")
    v1_phone.subscribe(*V1_UUIDS)
    v2_phone.subscribe(*V2_UUIDS)

    server_module.on_shot_detected(_hardware_shot())
    _wait_idle()

    provisional, final = v2_phone.wait_for_count(SHOT_V2_CHARACTERISTIC_UUID, 2)
    assert provisional["final"] is False
    assert provisional["enrichment"] == {"status": "pending"}
    assert final["final"] is True
    assert final["enrichment"] == {"status": "complete"}
    assert provisional["event_id"] == final["event_id"]
    assert final["schema_version"] == 2 and final["type"] == "shot"
    assert final["shot_number"] == provisional["shot_number"] is not None

    v1_shot = v1_phone.wait_for(SHOT_CHARACTERISTIC_UUID)
    settle(pi)
    assert len(v1_phone.decoded(SHOT_CHARACTERISTIC_UUID)) == 1
    assert v1_shot["schema_version"] == 1
    assert "final" not in v1_shot and "type" not in v1_shot
    assert v1_shot["event_id"] != final["event_id"]  # v1 keeps its per-publish uuid4
    # The v1 central never subscribed to the v2 pair, so it saw nothing there.
    assert v1_phone.decoded(SHOT_V2_CHARACTERISTIC_UUID) == []
    assert v1_phone.decoded(CONTROL_V2_CHARACTERISTIC_UUID) == []


def test_club_commands_and_club_changed_reach_each_schema(pi):
    v1_phone = pi.central("v1-app")
    v2_phone = pi.central("v2-app")
    v1_phone.subscribe(*V1_UUIDS)
    v2_phone.subscribe(*V2_UUIDS)

    answer = v2_phone.request(
        "set_club",
        {"club": "7-iron"},
        control=CONTROL_V2_CHARACTERISTIC_UUID,
        schema_version=2,
    )
    assert answer == {
        "schema_version": 2,
        "request_id": answer["request_id"],
        "ok": True,
        "result": {"status": "applied", "club": "7-iron"},
    }

    v1_event = v1_phone.wait_for(
        CONTROL_CHARACTERISTIC_UUID, lambda message: message.get("type") == "club_changed"
    )
    v2_event = v2_phone.wait_for(
        CONTROL_V2_CHARACTERISTIC_UUID, lambda message: message.get("type") == "club_changed"
    )
    assert v1_event == {"schema_version": 1, "type": "club_changed", "club": "7-iron"}
    assert v2_event == {"schema_version": 2, "type": "club_changed", "club": "7-iron"}
    raw_v1_events = [
        raw for raw in v1_phone.raw(CONTROL_CHARACTERISTIC_UUID) if b"club_changed" in raw
    ]
    assert raw_v1_events == [encode_club_event("7-iron")]
    assert ("club_changed", {"club": "7-iron"}) in pi.emitted

    current = v1_phone.request("get_club")
    assert current["result"] == {"status": "current", "club": "7-iron"}


def test_profiles_commands_route_through_socketio_operations(pi):
    store = server_module.get_profile_store()
    second = store.add("Sam")
    store.set_active(store.list()[0].id)
    phone = pi.central("v2-app")
    phone.subscribe(*V2_UUIDS)

    ack = phone.request("get_profiles", control=CONTROL_V2_CHARACTERISTIC_UUID, schema_version=2)
    assert ack["result"] == {"status": "sent"}
    profiles = phone.wait_for(
        CONTROL_V2_CHARACTERISTIC_UUID, lambda message: message.get("type") == "profiles"
    )
    assert [item["name"] for item in profiles["profiles"]] == ["Profile 1", "Sam"]
    assert set(profiles["profiles"][0]) == {"id", "name"}

    answer = phone.request(
        "set_active_profile",
        {"profile_id": second.id},
        control=CONTROL_V2_CHARACTERISTIC_UUID,
        schema_version=2,
    )
    assert answer["result"] == {"status": "applied", "active_profile_id": second.id}
    assert store.get_active().id == second.id
    socket_profiles = [payload for event, payload in pi.emitted if event == "profiles"]
    assert socket_profiles[-1]["active_profile_id"] == second.id

    rejected = phone.request(
        "set_active_profile",
        {"profile_id": "nope"},
        control=CONTROL_V2_CHARACTERISTIC_UUID,
        schema_version=2,
    )
    assert rejected["ok"] is False
    assert rejected["error"] == "Unknown profile"


def test_calibration_without_iwr6843_returns_the_409_error(pi):
    phone = pi.central("v1-app")
    phone.subscribe(*V1_UUIDS)

    answer = phone.request("iwr6843_orientation_calibration", {"mount_tilt_deg": 12.0})

    assert answer["ok"] is False
    assert answer["error"] == "TI IWR6843 radar is not enabled"


def test_unknown_and_v2_only_commands_error_on_the_v1_characteristic(pi):
    phone = pi.central("v1-app")
    phone.subscribe(*V1_UUIDS, *V2_UUIDS)

    unknown = phone.request("launch_rocket")
    v2_only_on_v1 = phone.request("get_profiles")
    v2_envelope_on_v1 = phone.request("get_club", schema_version=2)
    unknown_v2 = phone.request(
        "launch_rocket", control=CONTROL_V2_CHARACTERISTIC_UUID, schema_version=2
    )

    assert unknown == {
        "schema_version": 1,
        "request_id": unknown["request_id"],
        "ok": False,
        "error": "Unsupported phone command: launch_rocket",
    }
    assert v2_only_on_v1["error"] == "Unsupported phone command: get_profiles"
    assert v2_envelope_on_v1["error"] == "Unsupported control schema version"
    assert unknown_v2["schema_version"] == 2
    assert unknown_v2["error"] == "Unsupported phone command: launch_rocket"


def test_v2_session_and_power_commands(pi, monkeypatch):
    class _Power:
        status = None

    phone = pi.central("v2-app")
    phone.subscribe(*V2_UUIDS)

    disabled = phone.request(
        "get_power_status", control=CONTROL_V2_CHARACTERISTIC_UUID, schema_version=2
    )
    assert disabled["error"] == "Battery monitoring is not enabled"
    monkeypatch.setattr(server_module, "power_monitor", _Power())
    waiting = phone.request(
        "get_power_status", control=CONTROL_V2_CHARACTERISTIC_UUID, schema_version=2
    )
    assert waiting["error"] == "No battery reading yet"


def test_destructive_commands_are_not_available_over_ble(pi):
    """BLE is unauthenticated, so it is read-and-select only."""
    phone = pi.central("v2-app")
    phone.subscribe(*V1_UUIDS, *V2_UUIDS)

    for command, payload in (
        ("clear_session", {}),
        ("delete_shot", {"timestamp": "2026-09-25T12:00:00"}),
    ):
        for control, schema in (
            (CONTROL_CHARACTERISTIC_UUID, 1),
            (CONTROL_V2_CHARACTERISTIC_UUID, 2),
        ):
            answer = phone.request(command, payload, control=control, schema_version=schema)
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
    phone = pi.central("v2-app")
    phone.subscribe(*V2_UUIDS)
    active_id = server_module.get_profile_store().get_active().id

    server_module.handle_delete_shot({"timestamp": shot.timestamp.isoformat()})
    server_module.handle_clear_session({})

    deleted = phone.wait_for(
        CONTROL_V2_CHARACTERISTIC_UUID, lambda message: message.get("type") == "shot_deleted"
    )
    cleared = phone.wait_for(
        CONTROL_V2_CHARACTERISTIC_UUID,
        lambda message: message.get("type") == "session_cleared",
    )
    assert deleted == {
        "schema_version": 2,
        "type": "shot_deleted",
        "timestamp": shot.timestamp.isoformat(),
    }
    assert cleared == {"schema_version": 2, "type": "session_cleared", "profile_id": active_id}


def test_unsubscribing_one_pair_keeps_the_other_flowing(pi):
    v1_phone = pi.central("v1-app")
    v2_phone = pi.central("v2-app")
    v1_phone.subscribe(*V1_UUIDS)
    v2_phone.subscribe(*V2_UUIDS)
    assert pi.publisher.subscribed and pi.publisher.v2_subscribed

    v2_phone.disconnect()
    assert pi.publisher.subscribed
    assert not pi.publisher.v2_subscribed

    server_module.apply_club_selection({"club": "pw"})
    event = v1_phone.wait_for(
        CONTROL_CHARACTERISTIC_UUID, lambda message: message.get("type") == "club_changed"
    )
    assert event["club"] == "pw"

    v1_phone.disconnect()
    assert not pi.publisher.subscribed


def test_v2_central_gets_latest_v2_shot_replayed(pi):
    pi.publisher.publish_v2_shot(
        {
            "timestamp": "2026-09-25T12:00:00",
            "club": "driver",
            "ball_speed_mph": 150.0,
            "estimated_carry_yards": 250,
            "shot_number": 3,
        },
        final=True,
    )
    phone = pi.central("v2-app")
    # Control first, as the negotiation flow does, then the shot characteristic.
    phone.subscribe(CONTROL_V2_CHARACTERISTIC_UUID)
    settle(pi)
    phone.subscribe(SHOT_V2_CHARACTERISTIC_UUID)

    shot = phone.wait_for(SHOT_V2_CHARACTERISTIC_UUID)

    assert shot["shot_number"] == 3
    assert shot["final"] is True
    settle(pi)
    assert len(phone.decoded(SHOT_V2_CHARACTERISTIC_UUID)) == 1


def test_client_frames_for_two_commands_do_not_interleave_responses(pi):
    phone = pi.central("v2-app")
    phone.subscribe(*V2_UUIDS)
    first = phone.command("get_club", control=CONTROL_V2_CHARACTERISTIC_UUID, schema_version=2)
    second = phone.command(
        "hello",
        {"client_schema_max": 2},
        control=CONTROL_V2_CHARACTERISTIC_UUID,
        schema_version=2,
    )

    answers = {
        message["request_id"]: message
        for message in (
            phone.wait_for(CONTROL_V2_CHARACTERISTIC_UUID, lambda m: m.get("request_id") == first),
            phone.wait_for(CONTROL_V2_CHARACTERISTIC_UUID, lambda m: m.get("request_id") == second),
        )
    }
    assert answers[first]["result"]["club"] == "driver"
    assert answers[second]["result"]["schema_version"] == 2
    # Every notification reassembled into valid JSON: no interleaved fragments.
    for raw in phone.raw(CONTROL_V2_CHARACTERISTIC_UUID):
        json.loads(raw)
