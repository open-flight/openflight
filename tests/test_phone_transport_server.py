"""Server-level behaviour of the phone transports (Socket.IO and BLE)."""

import logging
import sys

import pytest

from openflight import server as server_module
from openflight.ble import BleShotPublisher
from openflight.launch_monitor import ClubType


class _ClubPublisher:
    def __init__(self):
        self.clubs = []

    def publish_club(self, club):
        self.clubs.append(club)
        return True


@pytest.fixture
def no_monitor_transports(monkeypatch):
    """No launch monitor, with every club transport captured."""
    ble = _ClubPublisher()
    emitted = []
    monkeypatch.setattr(server_module, "monitor", None)
    monkeypatch.setattr(server_module, "ble_publisher", ble)
    monkeypatch.setattr(server_module, "active_club", ClubType.DRIVER)
    monkeypatch.setattr(
        server_module.socketio,
        "emit",
        lambda event, data, **_kwargs: emitted.append((event, data)),
    )
    return ble, emitted


def test_socket_set_club_without_monitor_still_broadcasts(no_monitor_transports):
    """Before the monitor exists, a club change is recorded and broadcast, as before."""
    ble, emitted = no_monitor_transports

    server_module.handle_set_club({"club": "7-iron"})

    assert server_module.active_club is ClubType.IRON_7
    assert emitted == [("club_changed", {"club": "7-iron"})]
    assert ble.clubs == ["7-iron"]


@pytest.mark.parametrize("payload", [{"club": "putter"}, {"club": "unknown"}, None])
def test_socket_set_club_ignores_invalid_selection(no_monitor_transports, payload):
    ble, emitted = no_monitor_transports

    server_module.handle_set_club(payload)

    assert server_module.active_club is ClubType.DRIVER
    assert emitted == []
    assert ble.clubs == []


def test_ble_without_bless_logs_unavailable_and_continues(monkeypatch, caplog):
    """`--ble` on macOS or without the `ble` extra must not take the server down."""
    # A None entry makes `from bless import ...` raise ImportError.
    monkeypatch.setitem(sys.modules, "bless", None)
    publisher = BleShotPublisher()

    with caplog.at_level(logging.WARNING):
        publisher.start()
        publisher._thread.join(timeout=2)  # pylint: disable=protected-access

    assert not publisher._thread.is_alive()  # pylint: disable=protected-access
    assert "Bluetooth unavailable" in caplog.text
    assert publisher.publish_club("driver") is True
    publisher.stop()
