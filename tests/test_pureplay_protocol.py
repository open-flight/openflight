"""Tests for the Pureplay Connect codec, using the byte-exact worked examples
from the protocol README (section 10) as test vectors."""

import struct

import pytest

from openflight.pureplay import protocol as pp


def h(text: str) -> bytes:
    return bytes.fromhex(text.replace("\n", " "))


BALL_DATA = h(
    "50 4C 01 00 10 22 00 01 00 00 00 7F 00 00 00 80 42 00 00 48 41 "
    "00 00 C0 BF 00 40 1C 45 00 20 19 45 00 00 96 C3 29 5C DF 40 0A 14 D6 31"
)
CLUB_DATA = h(
    "50 4C 01 00 11 26 00 01 00 00 00 FF 00 00 00 3E 42 7F 6A AC 3F "
    "33 33 63 41 CD CC 4C BF 66 66 06 40 9A 99 59 C0 00 00 A0 40 00 00 00 C0 5D D2 D9 F1"
)
HELLO = h("50 4C 01 00 01 0F 00 01 10 00 07 41 43 4D 45 20 4C 4D 03 31 2E 30 C0 C9 B1 D0")
HELLO_ACK = h("50 4C 01 00 81 04 00 01 00 00 00 64 CA B4 DA")
PING = h("50 4C 01 00 82 00 00 D0 A9 7C 7D")
SHOT_CTX_142 = h("50 4C 01 00 93 04 00 00 80 0E 43 6B C8 7B 66")
SHOT_CTX_UNKNOWN = h("50 4C 01 00 93 04 00 00 00 00 00 EA C3 AC 4B")
STRICT_ON = h("50 4C 01 00 94 01 00 01 61 7C D8 FB")
STRICT_OFF = h("50 4C 01 00 94 01 00 00 62 FF B3 09")
STRICT_ACK_OK = h("50 4C 01 00 04 02 00 01 00 FA A6 89 42")
STRICT_ACK_REFUSED = h(
    "50 4C 01 00 04 0F 00 00 0D 4E 6F 74 20 73 75 70 70 6F 72 74 65 64 1B 9D FD 27"
)
ERROR_STRICT = h(
    "50 4C 01 00 1F 17 00 02 00 14 53 74 72 69 63 74 20 6D 6F 64 65 20 72 65 71 75 "
    "69 72 65 64 31 B3 14 C5"
)


def test_crc32c_check_value():
    # Standard CRC-32C check value for the ASCII string "123456789".
    assert pp.crc32c(b"123456789") == 0xE3069283


def test_encode_ball_data_matches_spec():
    frame = pp.encode_ball_data(
        1,
        ball_speed_ms=64.0,
        vla_deg=12.5,
        hla_deg=-1.5,
        total_spin_rpm=2500,
        back_spin_rpm=2450,
        side_spin_rpm=-300,
        spin_axis_deg=6.98,
    )
    assert frame == BALL_DATA


def test_encode_club_data_matches_spec():
    frame = pp.encode_club_data(
        1,
        club_head_speed_ms=47.5,
        smash_factor=1.347,
        dynamic_loft_deg=14.2,
        face_angle_deg=-0.8,
        club_path_deg=2.1,
        attack_angle_deg=-3.4,
        impact_x_mm=5.0,
        impact_y_mm=-2.0,
    )
    assert frame == CLUB_DATA


def test_encode_hello_matches_spec():
    assert pp.encode_hello("ACME LM", "1.0", battery_powered=True) == HELLO


def test_encode_hello_ack_matches_spec():
    assert pp.encode_hello_ack() == HELLO_ACK


def test_encode_ping_matches_spec():
    assert pp.encode_ping() == PING


def test_encode_shot_context_matches_spec():
    assert pp.encode_set_shot_context(142.5) == SHOT_CTX_142
    assert pp.encode_set_shot_context(0.0) == SHOT_CTX_UNKNOWN


def test_encode_strict_mode_matches_spec():
    assert pp.encode_set_strict_mode(True) == STRICT_ON
    assert pp.encode_set_strict_mode(False) == STRICT_OFF


def test_encode_strict_mode_ack_matches_spec():
    assert pp.encode_strict_mode_ack(True) == STRICT_ACK_OK
    assert pp.encode_strict_mode_ack(False, "Not supported") == STRICT_ACK_REFUSED


def test_encode_error_matches_spec():
    frame = pp.encode_error(pp.ErrorCode.STRICT_MODE_REQUIRED, "Strict mode required")
    assert frame == ERROR_STRICT


def test_decode_each_spec_frame():
    cases = [
        (HELLO, pp.MsgType.HELLO),
        (HELLO_ACK, pp.MsgType.HELLO_ACK),
        (PING, pp.MsgType.PING),
        (BALL_DATA, pp.MsgType.BALL_DATA),
        (CLUB_DATA, pp.MsgType.CLUB_DATA),
        (SHOT_CTX_142, pp.MsgType.SET_SHOT_CONTEXT),
        (STRICT_ON, pp.MsgType.SET_STRICT_MODE),
        (STRICT_ACK_REFUSED, pp.MsgType.STRICT_MODE_ACK),
        (ERROR_STRICT, pp.MsgType.ERROR),
    ]
    for raw, expected in cases:
        frames = pp.FrameDecoder().feed(raw)
        assert len(frames) == 1
        assert frames[0].msg_type == expected


def test_decode_byte_at_a_time():
    decoder = pp.FrameDecoder()
    frames = []
    for byte in HELLO_ACK + PING:
        frames.extend(decoder.feed(bytes([byte])))
    assert [f.msg_type for f in frames] == [pp.MsgType.HELLO_ACK, pp.MsgType.PING]


def test_decode_several_frames_in_one_chunk():
    frames = pp.FrameDecoder().feed(HELLO_ACK + SHOT_CTX_142 + STRICT_ON)
    assert [f.msg_type for f in frames] == [
        pp.MsgType.HELLO_ACK,
        pp.MsgType.SET_SHOT_CONTEXT,
        pp.MsgType.SET_STRICT_MODE,
    ]


def test_resync_after_garbage_prefix():
    decoder = pp.FrameDecoder()
    frames = decoder.feed(b"\x00\x01\x02PL\xff" + PING)
    assert [f.msg_type for f in frames] == [pp.MsgType.PING]
    assert decoder.dropped_bytes > 0


def test_resync_after_corrupted_frame():
    corrupted = bytearray(SHOT_CTX_142)
    corrupted[8] ^= 0xFF  # flip payload bits so the CRC no longer matches
    frames = pp.FrameDecoder().feed(bytes(corrupted) + PING)
    assert [f.msg_type for f in frames] == [pp.MsgType.PING]


def test_truncated_frame_waits_for_more_data():
    decoder = pp.FrameDecoder()
    assert decoder.feed(BALL_DATA[:20]) == []
    frames = decoder.feed(BALL_DATA[20:])
    assert len(frames) == 1 and frames[0].msg_type == pp.MsgType.BALL_DATA


def test_parse_hello_ack():
    frame = pp.FrameDecoder().feed(HELLO_ACK)[0]
    ack = pp.parse_hello_ack(frame.payload)
    assert ack.protocol_version == 1 and ack.host_caps == 0


def test_parse_strict_mode_and_club_info():
    assert pp.parse_set_strict_mode(pp.FrameDecoder().feed(STRICT_ON)[0].payload) is True
    assert pp.parse_set_strict_mode(pp.FrameDecoder().feed(STRICT_OFF)[0].payload) is False
    info = pp.parse_set_club_info(bytes([pp.Handedness.LEFT, 26]))
    assert info.handedness == pp.Handedness.LEFT and info.club == 26


@pytest.mark.parametrize(
    "parser",
    [pp.parse_hello_ack, pp.parse_set_club_info, pp.parse_set_strict_mode],
)
def test_parsers_reject_short_payloads(parser):
    with pytest.raises(pp.ProtocolError):
        parser(b"")


def test_ball_data_unmeasured_fields_are_zero_and_masked_off():
    frame = pp.encode_ball_data(7, ball_speed_ms=60.0)
    payload = pp.FrameDecoder().feed(frame)[0].payload
    shot_id, mask, *values = struct.unpack("<IH7f", payload)
    assert shot_id == 7
    assert mask == pp.BALL_SPEED
    assert values[0] == pytest.approx(60.0)
    assert all(v == 0.0 for v in values[1:])


def test_club_data_omits_smash_factor_by_default():
    frame = pp.encode_club_data(3, club_head_speed_ms=45.0)
    payload = pp.FrameDecoder().feed(frame)[0].payload
    _, mask, *_ = struct.unpack("<IH8f", payload)
    assert mask == pp.CLUB_HEAD_SPEED
    assert not mask & pp.CLUB_SMASH


def test_nan_is_treated_as_not_measured():
    frame = pp.encode_ball_data(1, ball_speed_ms=50.0, vla_deg=float("nan"))
    _, mask, *_ = struct.unpack("<IH7f", pp.FrameDecoder().feed(frame)[0].payload)
    assert mask == pp.BALL_SPEED


def test_heartbeat_battery_unknown_and_clamped():
    unknown = pp.FrameDecoder().feed(pp.encode_heartbeat())[0].payload
    assert unknown[2] == 0xFF
    clamped = pp.FrameDecoder().feed(pp.encode_heartbeat(battery=250))[0].payload
    assert clamped[2] == 100


def test_mph_to_ms():
    assert pp.mph_to_ms(100) == pytest.approx(44.704)


def test_oversized_payload_rejected():
    with pytest.raises(pp.ProtocolError):
        pp.encode_frame(pp.MsgType.HELLO, b"x" * 70000)


def test_find_frame_end_needs_complete_frame():
    assert pp.find_frame_end(b"") is None
    assert pp.find_frame_end(PING[:5]) is None
    assert pp.find_frame_end(BALL_DATA[:-1]) is None
    assert pp.find_frame_end(BALL_DATA) == len(BALL_DATA)


def test_find_frame_end_returns_first_frame_only():
    assert pp.find_frame_end(PING + BALL_DATA) == len(PING)


def test_find_frame_end_spans_leading_junk():
    junk = b"\x01\x02PX"
    end = pp.find_frame_end(junk + PING)
    assert end == len(junk) + len(PING)
    frames = pp.FrameDecoder().feed((junk + PING)[:end])
    assert [f.msg_type for f in frames] == [pp.MsgType.PING]


def test_find_frame_end_skips_false_start_with_bad_crc():
    bad = bytearray(PING)
    bad[-1] ^= 0xFF  # corrupt the CRC of what looks like a frame
    assert pp.find_frame_end(bytes(bad) + HELLO_ACK) == len(PING) + len(HELLO_ACK)


def test_stray_magic_claiming_huge_length_does_not_stall_framer():
    # The junk's "PL" plus the real frame's bytes form a header claiming 33,280 bytes.
    data = b"\x00\xffPL" + PING
    assert pp.find_frame_end(data) == len(data)


def test_stray_magic_does_not_stall_decoder():
    decoder = pp.FrameDecoder()
    frames = decoder.feed(b"\x00\xffPL" + PING + HELLO_ACK)
    assert [f.msg_type for f in frames] == [pp.MsgType.PING, pp.MsgType.HELLO_ACK]
    assert decoder.dropped_bytes == 4


def test_frame_still_assembles_across_reads_after_stray_magic():
    decoder = pp.FrameDecoder()
    data = b"\x00\xffPL" + BALL_DATA
    frames = []
    for i in range(0, len(data), 3):
        frames.extend(decoder.feed(data[i : i + 3]))
    assert [f.msg_type for f in frames] == [pp.MsgType.BALL_DATA]


def test_decoder_buffer_does_not_grow_on_pure_noise():
    decoder = pp.FrameDecoder()
    for _ in range(50):
        decoder.feed(b"\x00\x01\x02\x03" * 100)
    # only a possible half-magic byte is retained
    assert len(decoder._buf) <= 1  # pylint: disable=protected-access


def test_trailing_half_magic_is_kept_for_next_read():
    decoder = pp.FrameDecoder()
    assert decoder.feed(b"\x00\x00P") == []
    frames = decoder.feed(PING[1:])
    assert [f.msg_type for f in frames] == [pp.MsgType.PING]
