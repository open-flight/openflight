"""Tests for transport-independent phone control commands."""

import pytest

from openflight import server as server_module
from openflight.launch_monitor import ClubType


class _Monitor:
    def __init__(self):
        self.clubs = []

    def set_club(self, club):
        self.clubs.append(club)


class _ClubPublisher:
    def __init__(self):
        self.clubs = []

    def publish_club(self, club):
        self.clubs.append(club)
        return True


@pytest.fixture(autouse=True)
def _isolate_club_state(monkeypatch):
    """Keep club changes and their broadcasts from leaking into other tests.

    ``apply_club_selection`` writes the module-global ``active_club`` and fans
    out over Socket.IO and BLE, so every test gets its own.
    """
    monkeypatch.setattr(server_module, "active_club", ClubType.DRIVER)
    monkeypatch.setattr(server_module, "ble_publisher", None)
    monkeypatch.setattr(server_module.socketio, "emit", lambda *_args, **_kwargs: None)


def test_apply_club_selection_updates_monitor_and_broadcasts(monkeypatch):
    monitor = _Monitor()
    ble = _ClubPublisher()
    emitted = []
    monkeypatch.setattr(server_module, "monitor", monitor)
    monkeypatch.setattr(server_module, "ble_publisher", ble)
    monkeypatch.setattr(server_module, "active_club", ClubType.DRIVER)
    monkeypatch.setattr(
        server_module.socketio, "emit", lambda event, data: emitted.append((event, data))
    )

    response, status = server_module.apply_club_selection({"club": "7-iron"})

    assert status == 200
    assert response == {"status": "applied", "club": "7-iron"}
    assert monitor.clubs == [ClubType.IRON_7]
    assert emitted == [("club_changed", {"club": "7-iron"})]
    assert server_module.active_club is ClubType.IRON_7
    assert ble.clubs == ["7-iron"]


def test_apply_club_selection_rejects_unknown_club(monkeypatch):
    monitor = _Monitor()
    monkeypatch.setattr(server_module, "monitor", monitor)

    response, status = server_module.apply_club_selection({"club": "putter"})

    assert status == 400
    assert "Unknown club" in response["error"]
    assert monitor.clubs == []


def test_control_dispatch_routes_club_command(monkeypatch):
    monitor = _Monitor()
    monkeypatch.setattr(server_module, "monitor", monitor)

    response, status = server_module.dispatch_phone_control_command("set_club", {"club": "3-wood"})

    assert status == 200
    assert response["club"] == "3-wood"
    assert monitor.clubs == [ClubType.WOOD_3]


def test_control_dispatch_returns_authoritative_club(monkeypatch):
    monkeypatch.setattr(server_module, "active_club", ClubType.WOOD_5)

    response, status = server_module.dispatch_phone_control_command("get_club", {})

    assert status == 200
    assert response == {"status": "current", "club": "5-wood"}
