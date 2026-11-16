"""Tests for PureplayCodec: provenance-aware shots, inbound events and queued replies."""

import struct

import pytest

from openflight.clubs import ClubType
from openflight.pureplay import protocol as wire
from openflight.pureplay.codec import PureplayCodec
from openflight.sim.types import PlayerUpdate, ResolvedShot, SimError


def make_shot(**provenance):
    prov = {
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
    }
    prov.update(provenance)
    return ResolvedShot(
        shot_number=1_780_000_123,
        ball_speed_mph=100.0,
        vla=12.5,
        hla=-1.5,
        total_spin_rpm=5000.0,
        spin_axis_deg=0.0,
        back_spin_rpm=5000.0,
        side_spin_rpm=0.0,
        carry_yards=150.0,
        club_path_deg=0.0,
        club=ClubType.IRON_7,
        club_speed_mph=75.0,
        provenance=prov,
    )


def frames_of(data: bytes):
    return wire.FrameDecoder().feed(data)


def ball(frame):
    shot_id, mask, *vals = struct.unpack("<IH7f", frame.payload)
    return shot_id, mask, vals


def club(frame):
    shot_id, mask, *vals = struct.unpack("<IH8f", frame.payload)
    return shot_id, mask, vals


# --- outbound shots ----------------------------------------------------------


def strict(codec):
    codec.parse_inbound(wire.encode_set_strict_mode(True))
    codec.drain_outbound()
    return codec


def test_default_sends_every_resolved_field_including_estimates():
    frames = frames_of(PureplayCodec().build_shot(make_shot()))
    _, mask, vals = ball(frames[0])
    assert mask == (
        wire.BALL_SPEED
        | wire.BALL_VLA
        | wire.BALL_HLA
        | wire.BALL_TOTAL_SPIN
        | wire.BALL_BACK_SPIN
        | wire.BALL_SIDE_SPIN
        | wire.BALL_SPIN_AXIS
    )
    assert vals[3] == pytest.approx(5000.0)  # estimated total spin is sent
    _, club_mask, _ = club(frames[1])
    assert club_mask == wire.CLUB_HEAD_SPEED | wire.CLUB_PATH


def test_strict_mode_sends_only_measured_fields():
    codec = strict(PureplayCodec())
    frames = frames_of(codec.build_shot(make_shot()))
    assert [f.msg_type for f in frames] == [wire.MsgType.BALL_DATA, wire.MsgType.CLUB_DATA]
    _, mask, vals = ball(frames[0])
    assert mask == wire.BALL_SPEED | wire.BALL_VLA | wire.BALL_HLA
    assert vals[0] == pytest.approx(44.704)  # 100 mph in m/s
    assert vals[1] == pytest.approx(12.5)
    assert vals[2] == pytest.approx(-1.5)
    assert vals[3:] == [0.0, 0.0, 0.0, 0.0]  # unmeasured spin fields are zeroed


def test_measured_spin_is_sent_in_strict_mode_when_provenance_says_so():
    shot = make_shot(
        total_spin="measured", spin_axis="measured", back_spin="measured", side_spin="measured"
    )
    _, mask, _ = ball(frames_of(strict(PureplayCodec()).build_shot(shot))[0])
    spin_bits = (
        wire.BALL_TOTAL_SPIN | wire.BALL_BACK_SPIN | wire.BALL_SIDE_SPIN | wire.BALL_SPIN_AXIS
    )
    assert mask & spin_bits == spin_bits


def test_club_data_carries_speed_in_ms_and_leaves_smash_to_host():
    frames = frames_of(strict(PureplayCodec()).build_shot(make_shot()))
    _, mask, vals = club(frames[1])
    assert mask == wire.CLUB_HEAD_SPEED  # path is estimated, smash is host-derived
    assert vals[0] == pytest.approx(33.528)  # 75 mph in m/s


def test_club_data_includes_measured_path():
    shot = make_shot(club_path="measured")
    shot.club_path_deg = 2.5
    _, mask, vals = club(frames_of(strict(PureplayCodec()).build_shot(shot))[1])
    assert mask == wire.CLUB_HEAD_SPEED | wire.CLUB_PATH
    assert vals[4] == pytest.approx(2.5)


def test_no_club_data_without_club_readings():
    shot = make_shot(club_speed="estimated")
    shot.club_speed_mph = None
    shot.club_path_deg = None
    frames = frames_of(PureplayCodec().build_shot(shot))
    assert [f.msg_type for f in frames] == [wire.MsgType.BALL_DATA]


def test_club_path_alone_is_sent_when_measured():
    shot = make_shot(club_speed="estimated", club_path="measured")
    shot.club_speed_mph = None
    shot.club_path_deg = -3.0
    frames = frames_of(strict(PureplayCodec()).build_shot(shot))
    _, mask, _ = club(frames[1])
    assert mask == wire.CLUB_PATH


def test_strict_mode_never_sends_estimated_club_data():
    shot = make_shot(club_speed="estimated", club_path="estimated")
    frames = frames_of(strict(PureplayCodec()).build_shot(shot))
    assert [f.msg_type for f in frames] == [wire.MsgType.BALL_DATA]


def test_shot_ids_count_from_one_and_reset_on_new_link():
    codec = PureplayCodec()
    ids = [ball(frames_of(codec.build_shot(make_shot()))[0])[0] for _ in range(3)]
    assert ids == [1, 2, 3]
    codec.on_connect_bytes()
    assert ball(frames_of(codec.build_shot(make_shot()))[0])[0] == 1


# --- connection ---------------------------------------------------------------


def test_on_connect_sends_hello_with_device_name():
    codec = PureplayCodec(device_name="My Pi", firmware="1.2.3")
    (hello,) = frames_of(codec.on_connect_bytes())
    assert hello.msg_type == wire.MsgType.HELLO
    assert b"My Pi" in hello.payload and b"1.2.3" in hello.payload


def test_heartbeat_is_connected_ready_unknown_battery():
    (hb,) = frames_of(PureplayCodec().heartbeat_bytes())
    assert hb.msg_type == wire.MsgType.HEARTBEAT
    assert list(hb.payload) == [wire.ConnectionState.CONNECTED, wire.ReadyState.READY, 0xFF]


def test_fields_for_target_omits_carry():
    fields = PureplayCodec().fields_for_target()
    assert "carry" not in fields and "ball_speed" in fields


# --- inbound events and replies ------------------------------------------------


def test_hello_ack_queues_ready_state():
    codec = PureplayCodec()
    assert codec.parse_inbound(wire.encode_hello_ack()) == []
    (reply,) = frames_of(codec.drain_outbound())
    assert reply.msg_type == wire.MsgType.READY_STATE
    assert reply.payload == bytes([wire.ReadyState.READY])
    assert codec.drain_outbound() == b""  # drained


def test_ping_is_answered_with_heartbeat():
    codec = PureplayCodec()
    assert codec.parse_inbound(wire.encode_ping()) == []
    (reply,) = frames_of(codec.drain_outbound())
    assert reply.msg_type == wire.MsgType.HEARTBEAT


def test_set_club_info_becomes_player_update():
    codec = PureplayCodec()
    frame = wire.encode_frame(wire.MsgType.SET_CLUB_INFO, bytes([wire.Handedness.LEFT, 23]))
    assert codec.parse_inbound(frame) == [PlayerUpdate(handed="LH", club=ClubType.IRON_7)]


def test_strict_mode_is_acknowledged_both_ways():
    codec = PureplayCodec()
    for flag in (True, False):
        codec.parse_inbound(wire.encode_set_strict_mode(flag))
        (ack,) = frames_of(codec.drain_outbound())
        assert ack.msg_type == wire.MsgType.STRICT_MODE_ACK
        assert ack.payload == bytes([1, 0])  # accepted, empty reason


def test_leaving_strict_mode_sends_estimates_again():
    codec = strict(PureplayCodec())
    codec.parse_inbound(wire.encode_set_strict_mode(False))
    _, mask, _ = ball(frames_of(codec.build_shot(make_shot()))[0])
    assert mask & wire.BALL_TOTAL_SPIN


def test_strict_mode_resets_on_a_new_link():
    codec = strict(PureplayCodec())
    codec.on_connect_bytes()
    _, mask, _ = ball(frames_of(codec.build_shot(make_shot()))[0])
    assert mask & wire.BALL_TOTAL_SPIN


def test_host_error_becomes_sim_error():
    codec = PureplayCodec()
    events = codec.parse_inbound(
        wire.encode_error(wire.ErrorCode.STRICT_MODE_REQUIRED, "Strict mode required")
    )
    assert len(events) == 1 and isinstance(events[0], SimError)
    assert "Strict mode required" in events[0].message and "[2]" in events[0].message


def test_advisory_commands_are_ignored_quietly():
    codec = PureplayCodec()
    data = (
        wire.encode_set_shot_context(120.0)
        + wire.encode_frame(wire.MsgType.SET_DETECT, bytes([1]))
        + wire.encode_frame(wire.MsgType.SET_BALL_TYPE, bytes([wire.BallType.MARKERLESS]))
    )
    assert codec.parse_inbound(data) == []
    assert codec.drain_outbound() == b""


def test_unknown_type_is_answered_with_error():
    codec = PureplayCodec()
    codec.parse_inbound(wire.encode_frame(0x6E, b""))
    (reply,) = frames_of(codec.drain_outbound())
    assert reply.msg_type == wire.MsgType.ERROR


def test_malformed_payload_is_skipped_without_raising():
    codec = PureplayCodec()
    bad = wire.encode_frame(wire.MsgType.SET_CLUB_INFO, bytes([0]))  # needs 2 bytes
    assert codec.parse_inbound(bad + wire.encode_ping()) == []
    (reply,) = frames_of(codec.drain_outbound())
    assert reply.msg_type == wire.MsgType.HEARTBEAT  # the next frame still worked


def test_frames_split_across_reads_are_reassembled():
    codec = PureplayCodec()
    data = wire.encode_ping()
    assert codec.parse_inbound(data[:4]) == []
    assert codec.drain_outbound() == b""
    codec.parse_inbound(data[4:])
    assert len(frames_of(codec.drain_outbound())) == 1
