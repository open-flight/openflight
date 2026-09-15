"""Tests for sim.codec — SimConnector wiring and the codec registry."""
import json
import socket
import time

import pytest

from openflight.gspro.codec import GSProCodec
from openflight.launch_monitor import ClubType
from openflight.sim.codec import build_connector, SimConnector
from openflight.sim.config import ConnectorConfig
from openflight.sim.types import ConnectionState, PlayerUpdate, ResolvedShot


def _resolved() -> ResolvedShot:
    return ResolvedShot(
        shot_number=3, ball_speed_mph=120.0, vla=14.0, hla=0.0,
        total_spin_rpm=3000.0, spin_axis_deg=0.0, back_spin_rpm=3000.0,
        side_spin_rpm=0.0, carry_yards=210.0, club_path_deg=0.0,
        club=ClubType.IRON_7, club_speed_mph=90.0, provenance={},
    )


def _wait(connector, state, deadline=3.0):
    end = time.time() + deadline
    while time.time() < end:
        if connector.state == state:
            return True
        time.sleep(0.05)
    return False


def test_connector_routes_status_with_target(mock_sim):
    statuses = []
    c = SimConnector(GSProCodec(), mock_sim.host, mock_sim.port,
                     on_status=lambda name, evt: statuses.append((name, evt)))
    c.start()
    try:
        assert _wait(c, ConnectionState.CONNECTED)
        assert statuses
        assert all(name == "gspro" for name, _ in statuses)
    finally:
        c.stop()


def test_connector_routes_inbound_with_target(mock_sim):
    inbound = []
    c = SimConnector(GSProCodec(), mock_sim.host, mock_sim.port,
                     on_inbound=lambda name, evt: inbound.append((name, evt)))
    mock_sim.queue_reply({"Code": 201, "Player": {"Club": "I7"}})
    c.start()
    try:
        deadline = time.time() + 1.5
        while time.time() < deadline and not inbound:
            time.sleep(0.05)
        assert inbound
        name, evt = inbound[0]
        assert name == "gspro"
        assert isinstance(evt, PlayerUpdate)
        assert evt.club is ClubType.IRON_7
    finally:
        c.stop()


def test_connector_send_shot_serializes(mock_sim):
    c = SimConnector(GSProCodec(), mock_sim.host, mock_sim.port)
    c.start()
    try:
        assert _wait(c, ConnectionState.CONNECTED)
        c.send_shot(_resolved())
        deadline = time.time() + 1.0
        while time.time() < deadline and not mock_sim.received:
            time.sleep(0.05)
        assert mock_sim.received
        assert json.loads(mock_sim.received[0])["ShotNumber"] == 3
    finally:
        c.stop()


def test_first_connect_failure_stays_connecting_not_reconnecting():
    # Point at a closed port so every connect attempt fails. Until the client has
    # connected at least once, retries must report CONNECTING — reporting
    # RECONNECT_BACKOFF ("reconnecting") would falsely imply a prior connection
    # was established and then lost.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    closed_port = probe.getsockname()[1]
    probe.close()

    states = []
    c = SimConnector(GSProCodec(), "127.0.0.1", closed_port,
                     on_status=lambda name, evt: states.append(evt.state),
                     backoff_seconds=(0.05,))
    c.start()
    try:
        # Wait for at least two connect attempts (so we've gone through the
        # post-failure backoff state, not just the very first CONNECTING).
        deadline = time.time() + 1.5
        while time.time() < deadline and states.count(ConnectionState.CONNECTING) < 2:
            time.sleep(0.02)
        assert ConnectionState.CONNECTING in states
        assert ConnectionState.RECONNECT_BACKOFF not in states
    finally:
        c.stop()


def test_reconnect_after_drop_reports_reconnecting(mock_sim):
    # Once a real connection has been established and then dropped, retries must
    # report RECONNECT_BACKOFF ("reconnecting").
    states = []
    c = SimConnector(GSProCodec(), mock_sim.host, mock_sim.port,
                     on_status=lambda name, evt: states.append(evt.state),
                     backoff_seconds=(0.05,))
    c.start()
    try:
        assert _wait(c, ConnectionState.CONNECTED)
        # Our side can reach CONNECTED before the server's accept() sets its
        # client socket. Send a shot and wait for the server to receive it so we
        # know there's a live client socket to drop (otherwise disconnect_client
        # no-ops and the test flakes under load).
        c.send_shot(_resolved())
        recv_deadline = time.time() + 2.0
        while time.time() < recv_deadline and not mock_sim.received:
            time.sleep(0.02)
        assert mock_sim.received
        states.clear()
        mock_sim.disconnect_client()
        deadline = time.time() + 3.0
        while time.time() < deadline and ConnectionState.RECONNECT_BACKOFF not in states:
            time.sleep(0.02)
        assert ConnectionState.RECONNECT_BACKOFF in states
    finally:
        c.stop()


def test_build_connector_gspro():
    c = build_connector(ConnectorConfig(
        type="gspro", host="127.0.0.1", port=921, device_id="Bay7", units="Yards"))
    assert isinstance(c, SimConnector)
    assert c.name == "gspro"
    assert c.codec.device_id == "Bay7"


def test_build_connector_opengolfsim_uses_shared_codec_named_ogs():
    from openflight.gspro.codec import GSProCodec

    c = build_connector(ConnectorConfig(type="opengolfsim", host="127.0.0.1", port=3111))
    # OpenGolfSim reuses the shared OpenConnect (GSPro) codec, named "opengolfsim".
    assert isinstance(c.codec, GSProCodec)
    assert c.name == "opengolfsim"


def test_build_connector_partee_uses_shared_codec_named_partee():
    c = build_connector(ConnectorConfig(type="partee", host="192.168.1.70", port=921))
    assert isinstance(c.codec, GSProCodec)
    assert c.name == "partee"


def test_build_connector_unknown_type_raises():
    with pytest.raises(ValueError):
        build_connector(ConnectorConfig(type="nope", port=1))
