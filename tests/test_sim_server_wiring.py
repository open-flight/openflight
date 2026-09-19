"""Server-side registry wiring: shot fan-out and inbound handling.

Exercises server._forward_shot_to_simulators and server._sim_on_inbound with
fake connectors so no sockets or hardware are needed.
"""
from datetime import datetime

import pytest

from openflight.launch_monitor import ClubType, Shot
from openflight.sim.types import ConnectionState, PlayerUpdate, ShotAck, SimError


@pytest.fixture
def server(monkeypatch):
    """Import the server module with socketio.emit and session logger stubbed."""
    import openflight.server as srv

    emitted = []
    monkeypatch.setattr(srv.socketio, "emit", lambda *a, **k: emitted.append((a, k)))
    monkeypatch.setattr(srv, "get_session_logger", lambda: None)
    srv._emitted = emitted  # convenience handle for assertions
    # Reset shared state between tests
    srv.sim_connectors = []
    srv.sim_player_state = srv.SimPlayerState()
    yield srv
    # Don't leak fake connectors / player state into other test modules
    # (server.on_shot_detected reads these globals).
    srv.sim_connectors = []
    srv.sim_player_state = srv.SimPlayerState()


class _FakeConnector:
    def __init__(self, name, connected=True):
        self.name = name
        self.codec = type("C", (), {"fields_for_target": lambda self: ["ball_speed", "vla"]})()
        self._connected = connected
        self.sent = []
        self.host = "127.0.0.1"
        self.port = 921
        self.state = ConnectionState.CONNECTED if connected else ConnectionState.RECONNECT_BACKOFF

    def is_connected(self):
        return self._connected

    def send_shot(self, resolved):
        self.sent.append(resolved)


def _shot():
    return Shot(ball_speed_mph=140.0, timestamp=datetime(2026, 6, 13, 12, 0, 0),
                club=ClubType.DRIVER, launch_angle_vertical=12.0)


def test_forward_fans_out_to_connected_only(server):
    a = _FakeConnector("gspro", connected=True)
    b = _FakeConnector("opengolfsim", connected=False)
    server.sim_connectors = [a, b]

    server._forward_shot_to_simulators(_shot())

    assert len(a.sent) == 1
    assert len(b.sent) == 0
    # sim_shot emitted once for the connected connector
    shots = [a_ for a_, k in server._emitted if a_[0] == "sim_shot"]
    assert len(shots) == 1
    assert shots[0][1]["target"] == "gspro"


def test_forward_allocates_one_shot_number_across_connectors(server):
    a = _FakeConnector("gspro")
    b = _FakeConnector("opengolfsim")
    server.sim_connectors = [a, b]

    server._forward_shot_to_simulators(_shot())

    assert a.sent[0].shot_number == b.sent[0].shot_number == 1


def test_calculated_spin_stays_estimated_in_simulator_and_ui_payload(server):
    connector = _FakeConnector("gspro")
    server.sim_connectors = [connector]
    shot = _shot()
    shot.launch_angle_vertical_source = "radar"
    shot.spin_axis_deg = -3.0
    assert server._apply_calculated_spin(shot) is True
    calculated_rpm = shot.spin_rpm

    server._forward_shot_to_simulators(shot)

    assert len(connector.sent) == 1
    resolved = connector.sent[0]
    assert resolved.total_spin_rpm == calculated_rpm
    payloads = [args[1] for args, _ in server._emitted if args[0] == "sim_shot"]
    assert len(payloads) == 1
    assert payloads[0]["values"]["total_spin"] == calculated_rpm
    for field in ("total_spin", "back_spin", "side_spin"):
        assert resolved.provenance[field] == "estimated"
        assert payloads[0]["provenance"][field] == "estimated"


def test_forward_noop_when_no_connector_connected(server):
    server.sim_connectors = [_FakeConnector("gspro", connected=False)]
    server._forward_shot_to_simulators(_shot())
    # No shot number consumed while offline.
    assert server.sim_player_state.shot_counter == 0
    assert not any(a_[0] == "sim_shot" for a_, k in server._emitted)


def test_forward_drops_shot_without_ball_speed(server):
    server.sim_connectors = [_FakeConnector("gspro")]
    bad = Shot(ball_speed_mph=0.0, timestamp=datetime(2026, 6, 13, 12, 0, 0),
               club=ClubType.DRIVER)
    server._forward_shot_to_simulators(bad)
    dropped = [a_ for a_, k in server._emitted if a_[0] == "sim_shot_dropped"]
    assert len(dropped) == 1


def test_inbound_player_update_sets_state_and_monitor(server, monkeypatch):
    set_clubs = []
    fake_monitor = type("M", (), {"set_club": lambda self, c: set_clubs.append(c)})()
    monkeypatch.setattr(server, "monitor", fake_monitor)

    server._sim_on_inbound("gspro", PlayerUpdate(handed="LH", club=ClubType.IRON_7))

    assert server.sim_player_state.handed == "LH"
    assert server.sim_player_state.club is ClubType.IRON_7
    assert set_clubs == [ClubType.IRON_7]
    players = [a_ for a_, k in server._emitted if a_[0] == "sim_player"]
    assert players and players[0][1]["club"] == ClubType.IRON_7.value


def test_inbound_error_emits_status(server):
    server._sim_on_inbound("opengolfsim", SimError(message="boom"))
    errs = [a_ for a_, k in server._emitted
            if a_[0] == "sim_status" and a_[1].get("state") == "error"]
    assert errs and errs[0][1]["message"] == "boom"


def test_inbound_rejected_ack_is_tolerated(server):
    # Should not raise or emit; just informational.
    server._sim_on_inbound("gspro", ShotAck(shot_number=4, ok=False, message="nope"))


def test_send_logged_only_in_debug_mode(server, monkeypatch, caplog):
    server.sim_connectors = [_FakeConnector("gspro")]

    monkeypatch.setattr(server, "debug_mode", False)
    with caplog.at_level("INFO", logger="openflight.server"):
        server._forward_shot_to_simulators(_shot())
    assert "shot #1" not in caplog.text

    caplog.clear()
    monkeypatch.setattr(server, "debug_mode", True)
    with caplog.at_level("INFO", logger="openflight.server"):
        server._forward_shot_to_simulators(_shot())
    assert "gspro shot #2" in caplog.text


def test_player_update_logged_always(server, caplog):
    with caplog.at_level("INFO", logger="openflight.server"):
        server._sim_on_inbound("opengolfsim", PlayerUpdate(club=ClubType.IRON_7))
    assert "player update: club=" in caplog.text


def test_status_connected_logged_always(server, caplog):
    from openflight.sim.types import ConnectionState, StatusEvent

    with caplog.at_level("INFO", logger="openflight.server"):
        server._sim_on_status(
            "gspro",
            StatusEvent(state=ConnectionState.CONNECTED, target="gspro",
                        host="127.0.0.1", port=921),
        )
    assert "gspro connected" in caplog.text


def test_emit_sim_snapshot_sends_status_for_every_connector(server):
    # The UI builds connector buttons from sim_status events, which otherwise
    # only fire on state *changes*. A client that connects after a connector
    # already settled would miss the event and show no button — so the server
    # replays a snapshot on connect. Every enabled connector must get a
    # sim_status carrying its current state, regardless of what that state is.
    a = _FakeConnector("gspro", connected=True)
    b = _FakeConnector("opengolfsim", connected=False)
    b.state = ConnectionState.RECONNECT_BACKOFF
    server.sim_connectors = [a, b]

    server._emit_sim_snapshot()

    by_target = {
        a_[1]["target"]: a_[1] for a_, _k in server._emitted if a_[0] == "sim_status"
    }
    assert set(by_target) == {"gspro", "opengolfsim"}
    assert by_target["gspro"]["state"] == "connected"
    assert by_target["opengolfsim"]["state"] == "reconnecting"
    assert by_target["gspro"]["port"] == 921


def test_emit_sim_snapshot_noop_without_connectors(server):
    # No connectors configured (no --sim / none enabled) → no sim_status emitted.
    server.sim_connectors = []
    server._emit_sim_snapshot()
    assert not any(a_[0] == "sim_status" for a_, _k in server._emitted)


def test_forward_swallows_send_failure(server):
    """A connector that drops between is_connected() and send must not raise into
    the shot pipeline — the failure is logged + emitted as sim_send_failed instead
    (PR #115 review #1). send_raw raises ConnectionError on a raced disconnect, and
    the OSError guard catches it.
    """

    def _raise_disconnect(resolved):
        raise ConnectionError("send_raw called while not connected")

    boom = _FakeConnector("gspro", connected=True)
    boom.send_shot = _raise_disconnect
    other = _FakeConnector("opengolfsim", connected=True)
    server.sim_connectors = [boom, other]

    server._forward_shot_to_simulators(_shot())  # must not raise

    failed = [a_ for a_, k in server._emitted if a_[0] == "sim_send_failed"]
    assert len(failed) == 1 and failed[0][1]["target"] == "gspro"
    # a failed connector must not block delivery to the others
    assert len(other.sent) == 1
