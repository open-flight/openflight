"""Server-level phone behaviour: the SSE stream, event fan-out and command routing."""

import json
from datetime import datetime

import pytest

from openflight import server as server_module
from openflight.launch_monitor import ClubType, Shot
from openflight.power import PowerStatus
from openflight.power.models import PowerState
from openflight.profiles import ProfileStore
from openflight.shot_stream import HEARTBEAT_FRAME, ShotStreamBroker


def _shot_data(ball_speed=150.0, **extra):
    data = {
        "timestamp": "2026-09-25T12:00:00.000001",
        "club": "driver",
        "ball_speed_mph": ball_speed,
        "estimated_carry_yards": 250,
        "shot_number": 1,
    }
    data.update(extra)
    return data


def _decode(event):
    return event.name, json.loads(bytes(event).decode("utf-8"))


def _drain(subscriber):
    events = []
    while not subscriber.empty():
        events.append(_decode(subscriber.get_nowait()))
    return events


class _PhoneTransport:
    """Records what the server hands to one phone transport (BLE or SSE)."""

    def __init__(self):
        self.shots = []
        self.events = []
        self.clubs = []

    def publish_shot(self, shot_data, *, final, enrichment=None):
        self.shots.append((shot_data, final, enrichment))
        return True

    def publish_event(self, event):
        self.events.append(event)
        return True

    def publish_club(self, club):
        self.clubs.append(club)
        return True


@pytest.fixture
def phones(monkeypatch, tmp_path):
    """Capture Socket.IO, SSE and BLE output of a monitor-less server."""
    stream = _PhoneTransport()
    ble = _PhoneTransport()
    emitted = []
    monkeypatch.setattr(server_module, "shot_stream", stream)
    monkeypatch.setattr(server_module, "ble_publisher", ble)
    monkeypatch.setattr(server_module, "monitor", None)
    monkeypatch.setattr(server_module, "power_monitor", None)
    monkeypatch.setattr(server_module, "active_club", ClubType.DRIVER)
    monkeypatch.setattr(server_module, "profile_store", ProfileStore(tmp_path / "profiles.json"))
    monkeypatch.setattr(
        server_module.socketio,
        "emit",
        lambda event, data=None, **_kwargs: emitted.append((event, data)),
    )
    return stream, ble, emitted


# -- SSE broker ----------------------------------------------------------------------


def test_every_subscriber_gets_shots_events_and_club_changes():
    broker = ShotStreamBroker()
    first = broker.subscribe()
    second = broker.subscribe()

    broker.publish_shot(_shot_data(), final=False, enrichment={"status": "pending"})
    broker.publish_event({"schema_version": 2, "type": "shot_processing", "state": "failed"})
    broker.publish_club("pw")

    for subscriber in (first, second):
        events = _drain(subscriber)
        assert [name for name, _ in events] == ["shot", "shot_processing", "club_changed"]
        assert events[0][1]["final"] is False
        assert events[2][1] == {"schema_version": 2, "type": "club_changed", "club": "pw"}


def test_subscriber_is_seeded_with_state_then_latest_shot():
    broker = ShotStreamBroker()
    broker.publish_shot(_shot_data(140.0), final=True)
    broker.publish_shot(_shot_data(151.0), final=True)
    seed = [{"schema_version": 2, "type": "club_changed", "club": "7-iron"}]

    subscriber = broker.subscribe(initial_events=seed)

    events = _drain(subscriber)
    assert [name for name, _ in events] == ["club_changed", "shot"]
    assert events[1][1]["ball_speed_mph"] == 151.0


def test_stream_route_seeds_current_state_then_session_shots(monkeypatch, tmp_path):
    broker = ShotStreamBroker(heartbeat_interval_s=0.01)
    # The session, not the broker's latest shot, decides what a client is
    # seeded with (see tests/test_shot_stream_catch_up.py).
    monitor = server_module.MockLaunchMonitor()
    monitor._shots.append(
        Shot(
            ball_speed_mph=150.0,
            timestamp=datetime(2026, 9, 25, 12, 0, 0, 1),
            club=ClubType.DRIVER,
            shot_number=1,
        )
    )
    monkeypatch.setattr(server_module, "monitor", monitor)
    monkeypatch.setattr(server_module, "phone_shot_cache", server_module.PhoneShotCache())
    monkeypatch.setattr(server_module, "shot_stream", broker)
    monkeypatch.setattr(server_module, "active_club", ClubType.IRON_7)
    monkeypatch.setattr(server_module, "power_monitor", None)
    monkeypatch.setattr(server_module, "profile_store", ProfileStore(tmp_path / "profiles.json"))

    response = server_module.app.test_client().get("/api/shots/stream")
    try:
        assert response.status_code == 200
        frames = response.response
        assert next(frames).decode("utf-8") == HEARTBEAT_FRAME
        events = []
        for _ in range(3):
            frame = next(frames).decode("utf-8")
            name = frame.split("\n", 1)[0].removeprefix("event: ")
            events.append((name, json.loads(frame.split("data: ", 1)[1])))
    finally:
        response.close()

    assert [name for name, _ in events] == ["club_changed", "profiles", "shot"]
    assert events[0][1] == {"schema_version": 2, "type": "club_changed", "club": "7-iron"}
    assert events[1][1]["profiles"][0]["name"] == "Profile 1"
    assert events[2][1]["schema_version"] == 2 and events[2][1]["final"] is True
    assert broker.subscriber_count == 0


@pytest.mark.parametrize("schema", ["1", "3"])
def test_stream_route_rejects_other_schemas(monkeypatch, schema):
    monkeypatch.setattr(server_module, "shot_stream", ShotStreamBroker())

    response = server_module.app.test_client().get(f"/api/shots/stream?schema={schema}")

    assert response.status_code == 400


# -- event fan-out ---------------------------------------------------------------------


def test_shot_processing_power_and_profiles_reach_phones(phones):
    stream, ble, emitted = phones
    status = PowerStatus(
        available=True,
        provider="geekworm",
        state=PowerState.ON_BATTERY,
        battery_percent=80.0,
        battery_voltage_v=4.0,
        external_power=False,
        updated_at="2026-09-25T12:00:00+00:00",
    )

    server_module.on_shot_processing("calculating")
    server_module._on_power_status(status)
    server_module.handle_get_profiles()

    for transport in (stream, ble):
        types = [event["type"] for event in transport.events]
        assert types == ["shot_processing", "power_status", "profiles"]
        assert transport.events[0]["state"] == "calculating"
        assert transport.events[1] == {
            "schema_version": 2,
            "type": "power_status",
            **status.to_dict(),
        }
    assert [event for event, _ in emitted] == ["shot_processing", "power_status", "profiles"]


def test_club_changes_reach_every_transport(phones):
    stream, ble, emitted = phones

    server_module.apply_club_selection({"club": "pw"})

    assert stream.clubs == ble.clubs == ["pw"]
    assert ("club_changed", {"club": "pw"}) in emitted


# -- command routing -------------------------------------------------------------------


def test_dispatch_uses_the_socketio_operations(phones):
    stream, _ble, emitted = phones
    store = server_module.get_profile_store()
    first = store.get_active()
    second = store.add("Sam")

    response, status = server_module.dispatch_phone_control_command(
        "set_active_profile", {"profile_id": first.id}
    )
    via_ble = emitted[-1]
    server_module.handle_set_active_profile({"profile_id": first.id})
    via_socket = emitted[-1]

    assert (status, response) == (200, {"status": "applied", "active_profile_id": first.id})
    assert via_ble == via_socket
    assert via_ble[0] == "profiles"
    assert stream.events[-1]["active_profile_id"] == first.id
    assert second.id in {item["id"] for item in stream.events[-1]["profiles"]}


def test_socket_clear_session_also_notifies_phones(phones):
    stream, ble, emitted = phones
    active = server_module.get_profile_store().get_active().id

    server_module.handle_clear_session({})

    assert emitted == [("session_cleared", {"profile_id": active, "shots": []})]
    for transport in (stream, ble):
        assert transport.events == [
            {"schema_version": 2, "type": "session_cleared", "profile_id": active}
        ]


def test_socket_delete_shot_notifies_phones_only_on_success(phones, monkeypatch):
    stream, ble, emitted = phones
    shot = Shot(
        ball_speed_mph=150.0,
        timestamp=datetime(2026, 9, 25, 12, 0, 0, 1),
        club=ClubType.DRIVER,
    )
    monitor = server_module.MockLaunchMonitor()
    monitor._shots.append(shot)
    monkeypatch.setattr(server_module, "monitor", monitor)

    server_module.handle_delete_shot({"timestamp": "2026-01-01T00:00:00"})
    for transport in (stream, ble):
        assert transport.events == []
    server_module.handle_delete_shot({"timestamp": shot.timestamp.isoformat()})

    assert [event for event, _ in emitted] == ["delete_shot_error", "session_state"]
    assert monitor.get_shots() == []
    for transport in (stream, ble):
        assert transport.events == [
            {
                "schema_version": 2,
                "type": "shot_deleted",
                "timestamp": shot.timestamp.isoformat(),
            }
        ]


@pytest.mark.parametrize("command", ["clear_session", "delete_shot"])
def test_destructive_commands_are_not_routed_over_ble(phones, command):
    _stream, _ble, emitted = phones

    response, status = server_module.dispatch_phone_control_command(
        command, {"timestamp": "2026-09-25T12:00:00"}
    )

    assert status == 400
    assert response["error"] == f"Unsupported phone command: {command}"
    assert emitted == []


def test_power_status_command_returns_the_socketio_payload(phones, monkeypatch):
    status = PowerStatus(
        available=True,
        provider="geekworm",
        state=PowerState.PLUGGED_IN,
        battery_percent=100.0,
        battery_voltage_v=4.2,
        external_power=True,
        updated_at="2026-09-25T12:00:00+00:00",
    )

    class _Monitor:
        pass

    power = _Monitor()
    power.status = status
    monkeypatch.setattr(server_module, "power_monitor", power)

    assert server_module.dispatch_phone_control_command("get_power_status", {}) == (
        status.to_dict(),
        200,
    )


# -- shot publication ----------------------------------------------------------------------


def test_fast_path_publishes_one_final_shot_without_enrichment(phones, monkeypatch):
    stream, ble, _emitted = phones
    for name in ("kld7_vertical", "kld7_horizontal", "camera_capture_runtime", "iwr6843_runtime"):
        monkeypatch.setattr(server_module, name, None)
    for name in ("ball_speed_correction_enabled", "calculated_spin_enabled", "ballistics_enabled"):
        monkeypatch.setattr(server_module, name, False)
    monkeypatch.setattr(server_module, "sim_connectors", [])
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)

    server_module.on_shot_detected(
        Shot(
            ball_speed_mph=150.0,
            timestamp=datetime(2026, 9, 25, 12, 0, 1),
            club=ClubType.DRIVER,
        )
    )
    with server_module._shot_finalization_condition:
        assert server_module._shot_finalization_condition.wait_for(
            lambda: (
                not server_module._shot_finalization_order
                and not server_module._shot_finalization_running
            ),
            timeout=5,
        )

    for transport in (stream, ble):
        [(shot_data, final, enrichment)] = transport.shots
        assert final is True
        assert enrichment is None
        assert shot_data["ball_speed_mph"] == 150.0
    assert stream.shots[0][0] is ble.shots[0][0]


@pytest.mark.parametrize(
    ("emit_event", "skipped_reason", "expected"),
    [
        ("shot", None, None),
        ("shot_update", None, {"status": "complete"}),
        ("shot_update", "deadline", {"status": "skipped", "reason": "deadline"}),
        ("shot_update", "queue_full", {"status": "skipped", "reason": "queue_full"}),
    ],
)
def test_final_enrichment_describes_what_happened(emit_event, skipped_reason, expected):
    enrichment = server_module._ShotEnrichmentResult(skipped_reason=skipped_reason)

    assert server_module._final_phone_enrichment(emit_event, enrichment) == expected
