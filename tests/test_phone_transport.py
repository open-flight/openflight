"""Server-level phone behaviour: event fan-out and command routing."""

from datetime import datetime

import pytest

from openflight import server as server_module
from openflight.launch_monitor import ClubType, Shot
from openflight.power import PowerStatus
from openflight.power.models import PowerState
from openflight.profiles import ProfileStore


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


class _PhoneTransport:
    """Records what the server hands to the phone ble."""

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
    """Capture Socket.IO and BLE output of a monitor-less server."""
    ble = _PhoneTransport()
    emitted = []
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
    return ble, emitted


# -- event fan-out ---------------------------------------------------------------------


def test_shot_processing_power_and_profiles_reach_phones(phones):
    ble, emitted = phones
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

    types = [event["type"] for event in ble.events]
    assert types == ["shot_processing", "power_status", "profiles"]
    assert ble.events[0]["state"] == "calculating"
    assert ble.events[1] == {
        "schema_version": 2,
        "type": "power_status",
        **status.to_dict(),
    }
    assert [event for event, _ in emitted] == ["shot_processing", "power_status", "profiles"]


def test_club_changes_reach_every_transport(phones):
    ble, emitted = phones

    server_module.apply_club_selection({"club": "pw"})

    assert ble.clubs == ["pw"]
    assert ("club_changed", {"club": "pw"}) in emitted


# -- command routing -------------------------------------------------------------------


def test_dispatch_uses_the_socketio_operations(phones):
    ble, emitted = phones
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
    assert ble.events[-1]["active_profile_id"] == first.id
    assert second.id in {item["id"] for item in ble.events[-1]["profiles"]}


def test_socket_clear_session_also_notifies_phones(phones):
    ble, emitted = phones
    active = server_module.get_profile_store().get_active().id

    server_module.handle_clear_session({})

    assert emitted == [("session_cleared", {"profile_id": active, "shots": []})]
    assert ble.events == [{"schema_version": 2, "type": "session_cleared", "profile_id": active}]


def test_socket_delete_shot_notifies_phones_only_on_success(phones, monkeypatch):
    ble, emitted = phones
    shot = Shot(
        ball_speed_mph=150.0,
        timestamp=datetime(2026, 9, 25, 12, 0, 0, 1),
        club=ClubType.DRIVER,
    )
    monitor = server_module.MockLaunchMonitor()
    monitor._shots.append(shot)
    monkeypatch.setattr(server_module, "monitor", monitor)

    server_module.handle_delete_shot({"timestamp": "2026-01-01T00:00:00"})
    assert ble.events == []
    server_module.handle_delete_shot({"timestamp": shot.timestamp.isoformat()})

    assert [event for event, _ in emitted] == ["delete_shot_error", "session_state"]
    assert monitor.get_shots() == []
    assert ble.events == [
        {
            "schema_version": 2,
            "type": "shot_deleted",
            "timestamp": shot.timestamp.isoformat(),
        }
    ]


@pytest.mark.parametrize("command", ["clear_session", "delete_shot"])
def test_destructive_commands_are_not_routed_over_ble(phones, command):
    _ble, emitted = phones

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
    ble, _emitted = phones
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

    [(shot_data, final, enrichment)] = ble.shots
    assert final is True
    assert enrichment is None
    assert shot_data["ball_speed_mph"] == 150.0


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
