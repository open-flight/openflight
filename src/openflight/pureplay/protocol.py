"""Pureplay Connect wire protocol (version 1): framing, CRC32C and message codecs.

This module is transport-agnostic and has no dependencies beyond the standard
library. It implements the device side of the protocol described at
https://github.com/PureplayGolf/Pureplay-Connect: OpenFlight is the "device"
and connects out to the Pureplay host over TCP (default port 9760).

Units on the wire: speed in m/s, angles in degrees, spin in RPM, distance in
metres, face impact point in millimetres. All multi-byte values are
little-endian.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Iterator, Optional

DEFAULT_PORT = 9760
MAGIC = b"PL"
PROTOCOL_VERSION = 1
HEADER_SIZE = 7  # magic(2) + version(1) + reserved(1) + type(1) + length(2)
TRAILER_SIZE = 4  # CRC32C
MAX_PAYLOAD = 0xFFFF
MPH_TO_MS = 0.44704


# --------------------------------------------------------------------------
# Enumerations (wire frozen, spec section 9)
# --------------------------------------------------------------------------


class MsgType(IntEnum):
    # device -> host
    HELLO = 0x01
    HEARTBEAT = 0x02
    STRICT_MODE_ACK = 0x04
    BALL_DATA = 0x10
    CLUB_DATA = 0x11
    READY_STATE = 0x12
    ERROR = 0x1F
    # host -> device
    HELLO_ACK = 0x81
    PING = 0x82
    SET_CLUB_INFO = 0x90
    SET_DETECT = 0x91
    SET_BALL_TYPE = 0x92
    SET_SHOT_CONTEXT = 0x93
    SET_STRICT_MODE = 0x94


class ConnectionState(IntEnum):
    DISCONNECTED = 0
    CONNECTING = 1
    CONNECTED = 2
    RECONNECTING = 3
    ERROR = 4


class ReadyState(IntEnum):
    NOT_READY = 0
    READY = 1
    BALL_DETECTED = 2
    PROCESSING = 3


class Handedness(IntEnum):
    RIGHT = 0
    LEFT = 1


class BallType(IntEnum):
    MARKER = 0
    MARKERLESS = 1


class ErrorCode(IntEnum):
    GENERIC = 0
    STRICT_MODE_REQUIRED = 2


# Club byte: 0 Driver; 1-8 Wood2-9; 9-16 Hybrid2-9; 17-25 Iron1-9;
# 26 PW, 27 GW, 28 SW, 29 LW; 30 Putter.

DEVICE_CAP_BATTERY_POWERED = 1 << 4
BATTERY_UNKNOWN = 0xFF

# ValidMask bits
BALL_SPEED, BALL_VLA, BALL_HLA, BALL_TOTAL_SPIN, BALL_BACK_SPIN, BALL_SIDE_SPIN, BALL_SPIN_AXIS = (
    1 << i for i in range(7)
)
(
    CLUB_HEAD_SPEED,
    CLUB_SMASH,
    CLUB_DYNAMIC_LOFT,
    CLUB_FACE_ANGLE,
    CLUB_PATH,
    CLUB_ATTACK_ANGLE,
    CLUB_IMPACT_X,
    CLUB_IMPACT_Y,
) = (1 << i for i in range(8))


class ProtocolError(ValueError):
    """Raised when a payload cannot be encoded or decoded."""


# --------------------------------------------------------------------------
# CRC32C (Castagnoli), reflected polynomial 0x82F63B78
# --------------------------------------------------------------------------


def _make_crc_table() -> tuple[int, ...]:
    table = []
    for n in range(256):
        crc = n
        for _ in range(8):
            crc = (crc >> 1) ^ 0x82F63B78 if crc & 1 else crc >> 1
        table.append(crc)
    return tuple(table)


_CRC_TABLE = _make_crc_table()


def crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc = _CRC_TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


# --------------------------------------------------------------------------
# Framing
# --------------------------------------------------------------------------


def encode_frame(msg_type: int, payload: bytes = b"") -> bytes:
    """Wrap a payload in a complete frame (header, payload, CRC32C)."""
    if len(payload) > MAX_PAYLOAD:
        raise ProtocolError(f"payload too large: {len(payload)} bytes")
    header = MAGIC + struct.pack("<BBBH", PROTOCOL_VERSION, 0, int(msg_type), len(payload))
    body = header + payload
    return body + struct.pack("<I", crc32c(body))


def _locate_frame(buf) -> Optional[tuple]:
    """Find the first complete, CRC-valid frame in ``buf``: (start, end), or None.

    A candidate "PL" start that is still incomplete is skipped, not waited on: a
    stray "PL" in line noise followed by real data can claim a huge payload length,
    and waiting for it would stall every real frame behind it. A genuine frame that
    is merely still arriving is found on a later call, once its bytes are in. (The
    only way this misreads a real frame is a CRC-valid frame hidden inside another
    frame's payload, which the CRC makes vanishingly unlikely.)
    """
    n = len(buf)
    i = 0
    while True:
        i = buf.find(MAGIC, i)
        if i < 0 or n - i < HEADER_SIZE:
            return None
        length = buf[i + 5] | (buf[i + 6] << 8)
        end = i + HEADER_SIZE + length + TRAILER_SIZE
        if end <= n:
            expected = struct.unpack_from("<I", buf, end - TRAILER_SIZE)[0]
            if crc32c(bytes(buf[i : end - TRAILER_SIZE])) == expected:
                return i, end
        i += 1  # incomplete or CRC mismatch: not a frame start, look further


@dataclass(frozen=True)
class Frame:
    msg_type: int
    payload: bytes
    version: int = PROTOCOL_VERSION


class FrameDecoder:
    """Splits frames out of a TCP byte stream.

    On a bad magic or CRC mismatch, drops one byte and resynchronises, as
    required by the spec (section 2). Feed it whatever recv() returned.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self.dropped_bytes = 0

    def feed(self, data: bytes) -> list[Frame]:
        self._buf.extend(data)
        return list(self._drain())

    def _drain(self) -> Iterator[Frame]:
        buf = self._buf
        while True:
            found = _locate_frame(buf)
            if found is None:
                self._discard_unframeable()
                return
            start, end = found
            self.dropped_bytes += start  # noise before the frame
            payload_len = end - start - HEADER_SIZE - TRAILER_SIZE
            frame = Frame(
                msg_type=buf[start + 4],
                payload=bytes(buf[start + HEADER_SIZE : start + HEADER_SIZE + payload_len]),
                version=buf[start + 2],
            )
            del buf[:end]
            yield frame

    def _discard_unframeable(self) -> None:
        """Keep only what could still become a frame: from the first "PL" onward
        (or a trailing "P" that may be half a magic); everything before is noise."""
        buf = self._buf
        k = buf.find(MAGIC)
        if k < 0:
            keep = 1 if buf.endswith(MAGIC[:1]) else 0
            k = len(buf) - keep
        if k > 0:
            self.dropped_bytes += k
            del buf[:k]


def find_frame_end(buf: bytes) -> Optional[int]:
    """Index just past the first complete, CRC-valid frame in ``buf``, or None.

    None means more data is needed. Bytes before the frame (line noise, or a false
    "PL" start) are included in the returned span; FrameDecoder discards them when
    the span is parsed. Used as the transport's framer.
    """
    found = _locate_frame(buf)
    return None if found is None else found[1]


# --------------------------------------------------------------------------
# Strings
# --------------------------------------------------------------------------


def pack_str(value: str) -> bytes:
    raw = value.encode("utf-8")
    if len(raw) > 255:
        raise ProtocolError("string longer than 255 bytes")
    return bytes([len(raw)]) + raw


def unpack_str(payload: bytes, offset: int) -> tuple[str, int]:
    if offset >= len(payload):
        raise ProtocolError("truncated string")
    n = payload[offset]
    end = offset + 1 + n
    if end > len(payload):
        raise ProtocolError("truncated string")
    return payload[offset + 1 : end].decode("utf-8", errors="replace"), end


# --------------------------------------------------------------------------
# Device -> host messages
# --------------------------------------------------------------------------


def encode_hello(
    device_name: str,
    firmware: str,
    battery_powered: bool = False,
    protocol_version: int = PROTOCOL_VERSION,
) -> bytes:
    caps = DEVICE_CAP_BATTERY_POWERED if battery_powered else 0
    payload = (
        struct.pack("<BH", protocol_version, caps) + pack_str(device_name) + pack_str(firmware)
    )
    return encode_frame(MsgType.HELLO, payload)


def encode_heartbeat(
    connection_state: int = ConnectionState.CONNECTED,
    ready_state: int = ReadyState.NOT_READY,
    battery: Optional[int] = None,
) -> bytes:
    """Battery is 0-100, or None when unknown / externally powered (0xFF)."""
    level = BATTERY_UNKNOWN if battery is None else max(0, min(100, int(battery)))
    return encode_frame(MsgType.HEARTBEAT, bytes([int(connection_state), int(ready_state), level]))


def encode_ready_state(ready_state: int) -> bytes:
    return encode_frame(MsgType.READY_STATE, bytes([int(ready_state)]))


def encode_strict_mode_ack(accepted: bool, reason: str = "") -> bytes:
    """Reply to SetStrictMode. Reason must be empty when accepted."""
    body = bytes([1 if accepted else 0]) + pack_str("" if accepted else reason)
    return encode_frame(MsgType.STRICT_MODE_ACK, body)


def encode_error(code: int, message: str) -> bytes:
    return encode_frame(MsgType.ERROR, struct.pack("<H", int(code)) + pack_str(message))


def _opt(value: Optional[float]) -> tuple[float, bool]:
    """A field that was not measured is sent as 0.0 with its valid bit clear."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 0.0, False
    return float(value), True


def encode_ball_data(
    shot_id: int,
    *,
    ball_speed_ms: Optional[float] = None,
    vla_deg: Optional[float] = None,
    hla_deg: Optional[float] = None,
    total_spin_rpm: Optional[float] = None,
    back_spin_rpm: Optional[float] = None,
    side_spin_rpm: Optional[float] = None,
    spin_axis_deg: Optional[float] = None,
) -> bytes:
    """Build a BallData frame. Pass None for anything that was not *measured*.

    Do not pass estimated or fallback values: the valid bit tells the host a
    field is a real measurement, and strict-mode rounds require exactly that.
    """
    fields = [
        ball_speed_ms,
        vla_deg,
        hla_deg,
        total_spin_rpm,
        back_spin_rpm,
        side_spin_rpm,
        spin_axis_deg,
    ]
    masks = [
        BALL_SPEED,
        BALL_VLA,
        BALL_HLA,
        BALL_TOTAL_SPIN,
        BALL_BACK_SPIN,
        BALL_SIDE_SPIN,
        BALL_SPIN_AXIS,
    ]
    values, mask = [], 0
    for field, bit in zip(fields, masks):
        value, valid = _opt(field)
        values.append(value)
        if valid:
            mask |= bit
    payload = struct.pack("<IH7f", shot_id, mask, *values)
    return encode_frame(MsgType.BALL_DATA, payload)


def encode_club_data(
    shot_id: int,
    *,
    club_head_speed_ms: Optional[float] = None,
    smash_factor: Optional[float] = None,
    dynamic_loft_deg: Optional[float] = None,
    face_angle_deg: Optional[float] = None,
    club_path_deg: Optional[float] = None,
    attack_angle_deg: Optional[float] = None,
    impact_x_mm: Optional[float] = None,
    impact_y_mm: Optional[float] = None,
) -> bytes:
    """Build a ClubData frame. Leave smash_factor as None to let the host derive it."""
    fields = [
        club_head_speed_ms,
        smash_factor,
        dynamic_loft_deg,
        face_angle_deg,
        club_path_deg,
        attack_angle_deg,
        impact_x_mm,
        impact_y_mm,
    ]
    masks = [
        CLUB_HEAD_SPEED,
        CLUB_SMASH,
        CLUB_DYNAMIC_LOFT,
        CLUB_FACE_ANGLE,
        CLUB_PATH,
        CLUB_ATTACK_ANGLE,
        CLUB_IMPACT_X,
        CLUB_IMPACT_Y,
    ]
    values, mask = [], 0
    for field, bit in zip(fields, masks):
        value, valid = _opt(field)
        values.append(value)
        if valid:
            mask |= bit
    payload = struct.pack("<IH8f", shot_id, mask, *values)
    return encode_frame(MsgType.CLUB_DATA, payload)


# --------------------------------------------------------------------------
# Host -> device messages (parsers) and test-side encoders
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class HelloAck:
    protocol_version: int
    host_caps: int


def parse_hello_ack(payload: bytes) -> HelloAck:
    if len(payload) < 4:
        raise ProtocolError("HelloAck too short")
    version, caps, _reserved = struct.unpack_from("<BHB", payload)
    return HelloAck(version, caps)


@dataclass(frozen=True)
class ClubInfo:
    handedness: int
    club: int


def parse_set_club_info(payload: bytes) -> ClubInfo:
    if len(payload) < 2:
        raise ProtocolError("SetClubInfo too short")
    return ClubInfo(payload[0], payload[1])


def parse_set_strict_mode(payload: bytes) -> bool:
    if len(payload) < 1:
        raise ProtocolError("SetStrictMode too short")
    return payload[0] == 1


def encode_hello_ack(protocol_version: int = PROTOCOL_VERSION, host_caps: int = 0) -> bytes:
    return encode_frame(MsgType.HELLO_ACK, struct.pack("<BHB", protocol_version, host_caps, 0))


def encode_ping() -> bytes:
    return encode_frame(MsgType.PING)


def encode_set_shot_context(distance_m: float) -> bytes:
    return encode_frame(MsgType.SET_SHOT_CONTEXT, struct.pack("<f", distance_m))


def encode_set_strict_mode(strict: bool) -> bytes:
    return encode_frame(MsgType.SET_STRICT_MODE, bytes([1 if strict else 0]))


# --------------------------------------------------------------------------
# Unit helpers
# --------------------------------------------------------------------------


def mph_to_ms(mph: float) -> float:
    return mph * MPH_TO_MS

