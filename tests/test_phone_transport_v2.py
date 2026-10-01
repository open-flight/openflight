"""Server-level schema v2 behaviour: SSE opt-in, event fan-out and command routing."""

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
    """Records what the server hands to one phone transport."""

    def __init__(self):
        self.v1_shots = []
        self.v2_shots = []
        self.events = []
        self.clubs = []

    def publish(self, shot_data):
        self.v1_shots.append(shot_data)
        return True

    def publish_v2_shot(self, shot_data, *, final, enrichment=None):
        self.v2_shots.append((shot_data, final, enrichment))
        return True

    def publish_event_v2(self, event):
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


def test_default_subscriber_stays_on_version_one():
    broker = ShotStreamBroker()
    v1 = broker.subscribe()
    v2 = broker.subscribe(schema=2)

    broker.publish(_shot_data())
    broker.publish_v2_shot(_shot_data(), final=False, enrichment={"status": "pending"})
    broker.publish_event_v2({"schema_version": 2, "type": "shot_processing", "state": "failed"})
    broker.publish_club("pw")

    v1_events = _drain(v1)
    assert [name for name, _ in v1_events] == ["shot", "club_changed"]
    assert v1_events[0][1]["schema_version"] == 1
    assert "final" not in v1_events[0][1]
    assert v1_events[1][1] == {"schema_version": 1, "type": "club_changed", "club": "pw"}
    v2_events = _drain(v2)
    assert [name for name, _ in v2_events] == ["shot", "shot_processing", "club_changed"]
    assert v2_events[0][1]["final"] is False
    assert v2_events[2][1] == {"schema_version": 2, "type": "club_changed", "club": "pw"}


def test_v2_subscriber_is_seeded_with_state_then_latest_v2_shot():
    broker = ShotStreamBroker()
    broker.publish(_shot_data(140.0))
    broker.publish_v2_shot(_shot_data(151.0), final=True)
    seed = [{"schema_version": 2, "type": "club_changed", "club": "7-iron"}]

    v2 = broker.subscribe(schema=2, initial_events=seed)
    v1 = broker.subscribe()

    v2_events = _drain(v2)
    assert [name for name, _ in v2_events] == ["club_changed", "shot"]
    assert v2_events[1][1]["ball_speed_mph"] == 151.0
    assert _drain(v1)[0][1]["ball_speed_mph"] == 140.0


def test_unsupported_stream_schema_is_rejected():
    with pytest.raises(ValueError):
        ShotStreamBroker().subscribe(schema=3)


def test_v2_stream_route_opts_in_and_seeds_current_state(monkeypatch, tmp_path):
    broker = ShotStreamBroker(heartbeat_interval_s=0.01)
    # The session, not the broker's latest shot, decides what a v2 client is
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

    response = server_module.app.test_client().get("/api/shots/stream?schema=2")
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


def test_stream_route_rejects_unknown_schema(monkeypatch):
    monkeypatch.setattr(server_module, "shot_stream", ShotStreamBroker())

    response = server_module.app.test_client().get("/api/shots/stream?schema=3")

    assert response.status_code == 400


# -- event fan-out ---------------------------------------------------------------------


def test_shot_processing_power_and_profiles_reach_v2_phones(phones):
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


# -- v2 command routing ------------------------------------------------------------------


def test_v2_dispatch_uses_the_socketio_operations(phones):
    stream, _ble, emitted = phones
    store = server_module.get_profile_store()
    first = store.get_active()
    second = store.add("Sam")

    response, status = server_module.dispatch_phone_control_command_v2(
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


def test_socket_clear_session_also_notifies_v2_phones(phones):
    stream, ble, emitted = phones
    active = server_module.get_profile_store().get_active().id

    server_module.handle_clear_session({})

    assert emitted == [("session_cleared", {"profile_id": active, "shots": []})]
    for transport in (stream, ble):
        assert transport.events == [
            {"schema_version": 2, "type": "session_cleared", "profile_id": active}
        ]


def test_socket_delete_shot_notifies_v2_phones_only_on_success(phones, monkeypatch):
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

    for dispatch in (
        server_module.dispatch_phone_control_command,
        server_module.dispatch_phone_control_command_v2,
    ):
        response, status = dispatch(command, {"timestamp": "2026-09-25T12:00:00"})
        assert status == 400
        assert response["error"] == f"Unsupported phone command: {command}"
    assert emitted == []


def test_v2_power_status_command_returns_the_socketio_payload(phones, monkeypatch):
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

    assert server_module.dispatch_phone_control_command_v2("get_power_status", {}) == (
        status.to_dict(),
        200,
    )


def test_v1_dispatch_does_not_grow_v2_commands(phones):
    for command in ("get_profiles", "set_active_profile", "get_power_status"):
        response, status = server_module.dispatch_phone_control_command(command, {})
        assert status == 400
        assert response["error"] == f"Unsupported phone command: {command}"


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
        assert len(transport.v1_shots) == 1
        [(shot_data, final, enrichment)] = transport.v2_shots
        assert final is True
        assert enrichment is None
        assert shot_data is transport.v1_shots[0]


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
