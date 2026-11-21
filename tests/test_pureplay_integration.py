"""End-to-end test: the real sim transport talking to a fake Pureplay host over localhost.

Exercises the pieces no unit test can: the transport's find_frame_end / drain_outbound
hooks, the handshake order, and replies to host requests.
"""

import socket
import struct
import threading
import time

import pytest

from openflight.clubs import ClubType
from openflight.pureplay import protocol as wire
from openflight.sim.codec import build_connector
from openflight.sim.config import ConnectorConfig
from openflight.sim.types import PlayerUpdate, ResolvedShot


class FakeHost:
    """Minimal Pureplay host: listens, decodes frames from the device, sends on demand."""

    def __init__(self):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(1)
        self._server.settimeout(5.0)
        self.port = self._server.getsockname()[1]
        self._conn = None
        self._decoder = wire.FrameDecoder()
        self.frames = []

    def accept(self):
        self._conn, _ = self._server.accept()
        self._conn.settimeout(0.05)

    def send(self, data: bytes):
        self._conn.sendall(data)

    def wait_for(self, msg_type, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for i, frame in enumerate(self.frames):
                if frame.msg_type == msg_type:
                    return self.frames.pop(i)
            try:
                data = self._conn.recv(4096)
            except socket.timeout:
                continue
            if not data:
                break
            self.frames.extend(self._decoder.feed(data))
        raise AssertionError(f"no frame of type 0x{int(msg_type):02x} within {timeout}s")

    def close(self):
        for s in (self._conn, self._server):
            if s is not None:
                s.close()


@pytest.fixture
def link():
    host = FakeHost()
    events = []
    lock = threading.Lock()

    def on_inbound(target, event):
        with lock:
            events.append((target, event))

    cfg = ConnectorConfig(
        type="pureplay",
        enabled=True,
        host="127.0.0.1",
        port=host.port,
        heartbeat_interval_s=30.0,  # keep periodic heartbeats out of the way
    )
    connector = build_connector(cfg, on_inbound=on_inbound)
    connector.start()
    host.accept()
    try:
        yield connector, host, events
    finally:
        connector.stop()
        host.close()


def wait_until(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def resolved_shot():
    return ResolvedShot(
        shot_number=1_780_000_001,
        ball_speed_mph=120.0,
        vla=14.0,
        hla=2.0,
        total_spin_rpm=3000.0,
        spin_axis_deg=0.0,
        back_spin_rpm=3000.0,
        side_spin_rpm=0.0,
        carry_yards=180.0,
        club_path_deg=0.0,
        club=ClubType.IRON_7,
        club_speed_mph=85.0,
        provenance={
            "ball_speed": "measured",
            "vla": "measured",
            "hla": "measured",
            "total_spin": "estimated",
            "spin_axis": "estimated",
            "back_spin": "estimated",
            "side_spin": "estimated",
            "carry": "measured",
            "club_speed": "measured",
            "club_path": "estimated",
        },
    )


def test_handshake_then_ready_state(link):
    _, host, _ = link
    hello = host.wait_for(wire.MsgType.HELLO)
    assert b"OpenFlight" in hello.payload
    host.send(wire.encode_hello_ack())
    ready = host.wait_for(wire.MsgType.READY_STATE)
    assert ready.payload == bytes([wire.ReadyState.READY])


def test_ping_is_answered_with_heartbeat(link):
    _, host, _ = link
    host.wait_for(wire.MsgType.HELLO)
    host.send(wire.encode_ping())
    assert host.wait_for(wire.MsgType.HEARTBEAT).payload[0] == wire.ConnectionState.CONNECTED


def test_frame_split_across_packets_and_leading_garbage(link):
    _, host, _ = link
    host.wait_for(wire.MsgType.HELLO)
    ping = b"\x00\xffPL" + wire.encode_ping()  # junk, then a real frame
    host.send(ping[:6])
    time.sleep(0.15)
    host.send(ping[6:])
    host.wait_for(wire.MsgType.HEARTBEAT)


def test_club_change_reaches_the_server_as_player_update(link):
    _, host, events = link
    host.wait_for(wire.MsgType.HELLO)
    host.send(wire.encode_frame(wire.MsgType.SET_CLUB_INFO, bytes([wire.Handedness.RIGHT, 26])))
    assert wait_until(lambda: len(events) == 1)
    target, event = events[0]
    assert target == "pureplay"
    assert event == PlayerUpdate(handed="RH", club=ClubType.PW)


def test_shot_is_delivered_as_ball_then_club_frames(link):
    connector, host, _ = link
    host.wait_for(wire.MsgType.HELLO)
    connector.send_shot(resolved_shot())
    ball = host.wait_for(wire.MsgType.BALL_DATA)
    club = host.wait_for(wire.MsgType.CLUB_DATA)
    shot_id, mask, speed, *_ = struct.unpack("<IH7f", ball.payload)
    all_ball_bits = (1 << 7) - 1  # outside strict mode estimates are sent too
    assert (shot_id, mask) == (1, all_ball_bits)
    assert speed == pytest.approx(53.6448)  # 120 mph in m/s
    assert struct.unpack_from("<I", club.payload)[0] == shot_id


def test_strict_mode_limits_the_wire_shot_to_measured_fields(link):
    connector, host, _ = link
    host.wait_for(wire.MsgType.HELLO)
    host.send(wire.encode_set_strict_mode(True))
    host.wait_for(wire.MsgType.STRICT_MODE_ACK)
    connector.send_shot(resolved_shot())
    ball = host.wait_for(wire.MsgType.BALL_DATA)
    _, mask, *_ = struct.unpack("<IH7f", ball.payload)
    assert mask == wire.BALL_SPEED | wire.BALL_VLA | wire.BALL_HLA


def test_strict_mode_request_is_acknowledged_on_the_wire(link):
    _, host, _ = link
    host.wait_for(wire.MsgType.HELLO)
    host.send(wire.encode_set_strict_mode(True))
    ack = host.wait_for(wire.MsgType.STRICT_MODE_ACK)
    assert ack.payload[0] == 1
