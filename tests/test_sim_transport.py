"""Transport tests: framing, send/recv, heartbeat, reconnect/backoff.

Exercises TcpSimClient through a real codec (GSProCodec) against the mock
sim server, plus framing unit tests for the brace-balanced JSON framer.
"""
import json
import time
from typing import Optional

import pytest

from openflight.clubs import ClubType
from openflight.gspro.codec import GSProCodec
from openflight.sim.transport import TcpSimClient, find_json_end
from openflight.sim.types import (
    ConnectionState,
    PlayerUpdate,
    ResolvedShot,
    ShotAck,
)

# --- framing unit tests ------------------------------------------------------


def test_complete_object_returns_end_index():
    assert find_json_end(b'{"a":1}') == 7


def test_partial_object_returns_none():
    assert find_json_end(b'{"a":') is None
    assert find_json_end(b'{"a":1') is None


def test_two_concatenated_objects_returns_first_end():
    raw = b'{"a":1}{"b":2}'
    end = find_json_end(raw)
    assert end == 7
    assert raw[end:] == b'{"b":2}'


def test_braces_inside_strings_dont_count():
    raw = b'{"msg":"hi {there} }"}'
    assert find_json_end(raw) == len(raw)


def test_escaped_quote_inside_string():
    raw = b'{"msg":"she said \\"hi\\" }"}'
    assert find_json_end(raw) == len(raw)


def test_nested_objects():
    raw = b'{"outer":{"inner":1}}'
    assert find_json_end(raw) == len(raw)


def test_empty_buffer_returns_none():
    assert find_json_end(b'') is None


def test_leading_whitespace_before_object():
    assert find_json_end(b'  \n{"a":1}') == len(b'  \n{"a":1}')


def test_non_ascii_inside_string():
    raw = b'{"msg":"caf\xc3\xa9"}'
    assert find_json_end(raw) == len(raw)


# --- transport behavior tests ------------------------------------------------


class _NoHeartbeatCodec:
    """Minimal codec whose protocol has no keepalive (no heartbeat thread)."""
    name = "noheartbeat"

    def build_shot(self, resolved) -> bytes:
        return b'{"shot":1}'

    def parse_inbound(self, frame: bytes):
        return []

    def heartbeat_bytes(self) -> Optional[bytes]:
        return None

    def on_connect_bytes(self) -> Optional[bytes]:
        return None

    def fields_for_target(self):
        return []


def _client(host, port, hb=60.0, codec=None, **kw) -> TcpSimClient:
    return TcpSimClient(host, port, codec or GSProCodec(), heartbeat_interval_s=hb, **kw)


def _wait_for_state(client, state, deadline=3.0):
    end = time.time() + deadline
    while time.time() < end:
        if client.state == state:
            return True
        time.sleep(0.05)
    return False


def _resolved() -> ResolvedShot:
    return ResolvedShot(
        shot_number=7, ball_speed_mph=140.0, vla=12.0, hla=0.0,
        total_spin_rpm=2500.0, spin_axis_deg=0.0, back_spin_rpm=2500.0,
        side_spin_rpm=0.0, carry_yards=255.0, club_path_deg=0.0,
        club=ClubType.DRIVER, club_speed_mph=None, provenance={},
    )


def test_send_shot_arrives_at_server(mock_sim):
    client = _client(mock_sim.host, mock_sim.port)
    client.start()
    try:
        assert _wait_for_state(client, ConnectionState.CONNECTED)
        client.send_raw(client._codec.build_shot(_resolved()))
        deadline = time.time() + 1.0
        while time.time() < deadline and not mock_sim.received:
            time.sleep(0.05)
        assert mock_sim.received
        obj = json.loads(mock_sim.received[0])
        assert obj["ShotNumber"] == 7
        assert obj["BallData"]["Speed"] == 140.0
    finally:
        client.stop()


def test_recv_dispatches_inbound_events(mock_sim):
    events = []
    client = _client(mock_sim.host, mock_sim.port, on_inbound=events.append)
    mock_sim.queue_reply({"Code": 201, "Player": {"Handed": "LH", "Club": "I7"}})
    client.start()
    try:
        deadline = time.time() + 1.5
        while time.time() < deadline and not events:
            time.sleep(0.05)
        assert len(events) == 1
        assert isinstance(events[0], PlayerUpdate)
        assert events[0].club is ClubType.IRON_7
    finally:
        client.stop()


def test_recv_handles_split_and_concatenated_frames(mock_sim):
    events = []
    client = _client(mock_sim.host, mock_sim.port, on_inbound=events.append)
    # Two acks in one TCP write — framer must split them.
    mock_sim.queue_raw(b'{"Code":200}{"Code":200}')
    client.start()
    try:
        deadline = time.time() + 1.5
        while time.time() < deadline and len(events) < 2:
            time.sleep(0.05)
        assert len(events) == 2
        assert all(isinstance(e, ShotAck) for e in events)
    finally:
        client.stop()


def test_heartbeats_are_sent_periodically(mock_sim):
    client = _client(mock_sim.host, mock_sim.port, hb=0.2)
    client.start()
    try:
        assert _wait_for_state(client, ConnectionState.CONNECTED)
        time.sleep(0.7)
        beats = [m for m in mock_sim.received if b'"IsHeartBeat":true' in m]
        assert len(beats) >= 2
    finally:
        client.stop()


def test_heartbeat_suppressed_after_recent_send(mock_sim):
    client = _client(mock_sim.host, mock_sim.port, hb=0.5)
    client.start()
    try:
        assert _wait_for_state(client, ConnectionState.CONNECTED)
        time.sleep(0.05)
        for _ in range(3):
            client.send_raw(b'{"hello":"world"}')
            time.sleep(0.3)
        beats = [m for m in mock_sim.received if b'"IsHeartBeat":true' in m]
        assert len(beats) <= 1
    finally:
        client.stop()


def test_no_heartbeat_thread_when_codec_has_none(mock_sim):
    client = _client(mock_sim.host, mock_sim.port, hb=0.1, codec=_NoHeartbeatCodec())
    client.start()
    try:
        assert _wait_for_state(client, ConnectionState.CONNECTED)
        time.sleep(0.5)
        # Codec sends no heartbeat; the server should have received nothing.
        assert not mock_sim.received
        assert client._hb_thread is None
    finally:
        client.stop()


def test_on_connect_bytes_sent_on_connect(mock_sim):
    class _HelloCodec(_NoHeartbeatCodec):
        def on_connect_bytes(self):
            return b'{"type":"device","status":"ready"}'

    client = _client(mock_sim.host, mock_sim.port, codec=_HelloCodec())
    client.start()
    try:
        assert _wait_for_state(client, ConnectionState.CONNECTED)
        deadline = time.time() + 1.0
        while time.time() < deadline and not mock_sim.received:
            time.sleep(0.05)
        assert mock_sim.received
        assert json.loads(mock_sim.received[0]) == {"type": "device", "status": "ready"}
    finally:
        client.stop()


def test_start_transitions_to_connected_then_stopped(mock_sim):
    statuses = []
    client = _client(mock_sim.host, mock_sim.port, on_status=statuses.append)
    client.start()
    try:
        assert _wait_for_state(client, ConnectionState.CONNECTED)
        assert any(s.state == ConnectionState.CONNECTED for s in statuses)
        # Every status event carries the transport's name as its target.
        assert all(s.target == "sim" for s in statuses)
    finally:
        client.stop()
    assert _wait_for_state(client, ConnectionState.STOPPED)


def test_reconnect_after_server_drop(mock_sim):
    statuses = []
    client = _client(
        mock_sim.host,
        mock_sim.port,
        backoff_seconds=(0.1, 0.2, 0.4),
        on_status=statuses.append,
    )
    client.start()
    try:
        assert _wait_for_state(client, ConnectionState.CONNECTED)
        # CONNECTED on our side can precede the server's accept(); send a shot and
        # wait for the server to receive it so there's a live client socket to
        # drop (otherwise disconnect_client() no-ops and the test flakes).
        client.send_raw(client._codec.build_shot(_resolved()))
        recv_deadline = time.time() + 2.0
        while time.time() < recv_deadline and not mock_sim.received:
            time.sleep(0.02)
        assert mock_sim.received
        statuses.clear()
        mock_sim.disconnect_client()
        assert _wait_for_state(client, ConnectionState.RECONNECT_BACKOFF, 2.0)
        reconnect = next(
            status
            for status in statuses
            if status.state == ConnectionState.RECONNECT_BACKOFF
        )
        assert reconnect.message == "peer closed connection (EOF)"
        assert _wait_for_state(client, ConnectionState.CONNECTED, 3.0)
    finally:
        client.stop()


def test_backoff_progression_capped():
    client = TcpSimClient("127.0.0.1", 1, GSProCodec(), heartbeat_interval_s=60,
                          backoff_seconds=(0.05, 0.1, 0.1))
    statuses = []
    client.on_status = statuses.append
    client.start()
    time.sleep(0.5)
    client.stop()
    # Before the first successful connection the client reports CONNECTING during
    # the retry backoff (RECONNECT_BACKOFF is reserved for a connection that was
    # established and then dropped). The backoff schedule is still carried on
    # next_retry_in_s, so assert on the CONNECTING retries here.
    backoffs = [s.next_retry_in_s for s in statuses
                if s.state == ConnectionState.CONNECTING and s.next_retry_in_s > 0]
    assert len(backoffs) >= 2
    assert max(backoffs) <= 0.1


def test_stop_is_idempotent(mock_sim):
    client = _client(mock_sim.host, mock_sim.port)
    client.start()
    _wait_for_state(client, ConnectionState.CONNECTED)
    client.stop()
    client.stop()  # should not raise
    assert client.state == ConnectionState.STOPPED


def test_send_raw_while_disconnected_raises_oserror_subclass():
    """Regression (PR #115 review): a send after the socket has gone away must
    raise an OSError subclass, not a bare RuntimeError.

    The shot pipeline (server._forward_shot_to_simulators) guards each send with
    ``except OSError`` and promises it "never raises into" on_shot_detected. There
    is a real TOCTOU window — is_connected() can return True, then _close_socket()
    sets _sock = None before the next send acquires the lock. If that path raises
    RuntimeError it slips past the OSError guard and propagates into the shot
    thread. ConnectionError is an OSError subclass, so it's caught.
    """
    client = TcpSimClient("127.0.0.1", 1, GSProCodec())  # never started → _sock is None
    with pytest.raises(ConnectionError):
        client.send_raw(b'{"x":1}')


def test_recv_buffer_resets_on_oversized_unclosed_frame():
    """A frame whose braces never close must not grow the recv buffer without
    bound or wedge the connection (PR #115 review #3). After the buffer overflows
    and resets, a subsequent valid frame is still parsed — proving it didn't stay
    stuck behind the unclosed one.
    """
    events = []
    client = _client("127.0.0.1", 1, on_inbound=events.append)
    valid = b'{"Code":201,"Player":{"Handed":"LH","Club":"I7"}}'
    # never-closing frame (open brace, no close) larger than the cap, then a real
    # frame, then EOF to end the loop.
    chunks = [b"{" + b"x" * (70 * 1024), valid, b""]

    class _FakeSock:
        def settimeout(self, _timeout):
            pass

        def recv(self, _n):
            return chunks.pop(0) if chunks else b""

    client._sock = _FakeSock()
    client._recv_loop()

    assert len(events) == 1
    assert isinstance(events[0], PlayerUpdate)


def test_recv_loop_reports_socket_error():
    client = _client("127.0.0.1", 1)

    class _ResetSock:
        def settimeout(self, _timeout):
            pass

        def recv(self, _size):
            raise ConnectionResetError(104, "Connection reset by peer")

    client._sock = _ResetSock()

    reason = client._recv_loop()

    assert reason == (
        "receive failed: ConnectionResetError: [Errno 104] Connection reset by peer"
    )
