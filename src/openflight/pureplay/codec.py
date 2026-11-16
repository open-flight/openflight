"""Pureplay Connect codec: ResolvedShot <-> wire bytes.

Wraps the binary frames in protocol.py and the club map in state.py behind the
protocol-neutral Codec interface the sim transport expects. Spec:
https://github.com/PureplayGolf/Pureplay-Connect

Differences from the JSON/OpenConnect codecs, all handled through two optional
transport hooks (``find_frame_end`` and ``drain_outbound``, see sim.transport):

* frames are length-prefixed binary, not JSON objects;
* the host sends requests that need an answer (Ping, SetStrictMode), and the
  device must push ReadyState after the handshake. The codec queues those replies
  while parsing and the transport sends them.

Pureplay's validity mask means "this field was actually measured", so only
fields whose ``ResolvedShot.provenance`` is "measured" are marked valid;
estimated/fallback values are cleared rather than passed off as readings.
"""

import logging
import threading
from typing import List

from openflight.pureplay import protocol as wire
from openflight.pureplay.state import pureplay_code_to_club, pureplay_handedness
from openflight.sim.types import InboundEvent, PlayerUpdate, SimError

logger = logging.getLogger(__name__)

# Logical fields Pureplay actually receives (drives the UI provenance badges).
# Carry is not transmitted: Pureplay simulates the flight itself.
_PUREPLAY_FIELDS = [
    "ball_speed",
    "vla",
    "hla",
    "total_spin",
    "spin_axis",
    "back_spin",
    "side_spin",
    "club_speed",
    "club_path",
]

STRICT_REFUSAL_REASON = "OpenFlight cannot guarantee measured-only data"

_HOST_TO_DEVICE = {
    wire.MsgType.HELLO_ACK,
    wire.MsgType.PING,
    wire.MsgType.SET_CLUB_INFO,
    wire.MsgType.SET_DETECT,
    wire.MsgType.SET_BALL_TYPE,
    wire.MsgType.SET_SHOT_CONTEXT,
    wire.MsgType.SET_STRICT_MODE,
    wire.MsgType.ERROR,
}


def _firmware_version() -> str:
    try:
        from openflight import __version__  # pylint: disable=import-outside-toplevel

        return str(__version__)
    except ImportError:
        return "unknown"


class PureplayCodec:
    """Pureplay Connect v1, device side."""

    name = "pureplay"

    def __init__(
        self,
        device_name: str = "OpenFlight",
        firmware: str = "",
    ):
        """
        Args:
            device_name: Shown by Pureplay as the launch monitor's name.
            firmware: Firmware/version string sent in Hello (defaults to the
                OpenFlight version).
        """
        self.device_name = device_name
        self.firmware = firmware or _firmware_version()
        self._lock = threading.Lock()
        self._decoder = wire.FrameDecoder()
        self._outbound: List[bytes] = []
        self._shot_id = 0

    # --- optional transport hooks ------------------------------------------------

    @staticmethod
    def find_frame_end(buf: bytes):
        """Transport framer: index past the first complete frame, or None."""
        return wire.find_frame_end(buf)

    def drain_outbound(self) -> bytes:
        """Replies queued while parsing inbound frames (cleared on read)."""
        with self._lock:
            data = b"".join(self._outbound)
            self._outbound.clear()
        return data

    # --- Codec interface ---------------------------------------------------------

    def on_connect_bytes(self) -> bytes:
        """Start a fresh link: reset per-link state and send Hello."""
        with self._lock:
            self._decoder = wire.FrameDecoder()
            self._outbound.clear()
            self._shot_id = 0
        return wire.encode_hello(self.device_name, self.firmware, battery_powered=False)

    def heartbeat_bytes(self) -> bytes:
        return wire.encode_heartbeat(
            wire.ConnectionState.CONNECTED, wire.ReadyState.READY, battery=None
        )

    def fields_for_target(self) -> List[str]:
        return list(_PUREPLAY_FIELDS)

    def build_shot(self, resolved) -> bytes:
        """BallData, then ClubData if any club reading exists (spec section 4.3)."""
        with self._lock:
            self._shot_id += 1
            shot_id = self._shot_id

        def pick(field: str, value):
            if resolved.provenance.get(field) == "measured":
                return value
            return None

        speed_ms = pick("ball_speed", resolved.ball_speed_mph)
        frames = [
            wire.encode_ball_data(
                shot_id,
                ball_speed_ms=None if speed_ms is None else wire.mph_to_ms(speed_ms),
                vla_deg=pick("vla", resolved.vla),
                hla_deg=pick("hla", resolved.hla),
                total_spin_rpm=pick("total_spin", resolved.total_spin_rpm),
                back_spin_rpm=pick("back_spin", resolved.back_spin_rpm),
                side_spin_rpm=pick("side_spin", resolved.side_spin_rpm),
                spin_axis_deg=pick("spin_axis", resolved.spin_axis_deg),
            )
        ]

        club_speed = pick("club_speed", resolved.club_speed_mph)
        club_path = pick("club_path", resolved.club_path_deg)
        if club_speed is not None or club_path is not None:
            frames.append(
                wire.encode_club_data(
                    shot_id,
                    club_head_speed_ms=None if club_speed is None else wire.mph_to_ms(club_speed),
                    club_path_deg=club_path,
                )
            )
        return b"".join(frames)

    def parse_inbound(self, frame: bytes) -> List[InboundEvent]:
        """Decode host frames. Replies are queued for ``drain_outbound``.

        A malformed payload is logged and skipped rather than raised, so one bad
        message can't stall the link.
        """
        events: List[InboundEvent] = []
        with self._lock:
            decoder = self._decoder
        for received in decoder.feed(frame):
            try:
                events.extend(self._handle(received))
            except wire.ProtocolError as e:
                logger.warning(
                    "[pureplay] ignoring malformed 0x%02x frame: %s", received.msg_type, e
                )
        return events

    # --- inbound handling --------------------------------------------------------

    def _queue(self, data: bytes) -> None:
        with self._lock:
            self._outbound.append(data)

    def _handle(self, received: wire.Frame) -> List[InboundEvent]:
        msg_type, payload = received.msg_type, received.payload

        if msg_type == wire.MsgType.HELLO_ACK:
            wire.parse_hello_ack(payload)  # validates; version negotiation is v1-only
            self._queue(wire.encode_ready_state(wire.ReadyState.READY))
            return []

        if msg_type == wire.MsgType.PING:
            self._queue(self.heartbeat_bytes())
            return []

        if msg_type == wire.MsgType.SET_CLUB_INFO:
            info = wire.parse_set_club_info(payload)
            return [
                PlayerUpdate(
                    handed=pureplay_handedness(info.handedness),
                    club=pureplay_code_to_club(info.club),
                )
            ]

        if msg_type == wire.MsgType.SET_STRICT_MODE:
            if wire.parse_set_strict_mode(payload):
                logger.info("[pureplay] strict mode requested; refusing (not guaranteed)")
                self._queue(wire.encode_strict_mode_ack(False, STRICT_REFUSAL_REASON))
            else:
                self._queue(wire.encode_strict_mode_ack(True))
            return []

        if msg_type == wire.MsgType.ERROR:
            message = _error_message(payload)
            return [SimError(message=message)]

        if msg_type in (
            wire.MsgType.SET_DETECT,
            wire.MsgType.SET_BALL_TYPE,
            wire.MsgType.SET_SHOT_CONTEXT,
        ):
            return []  # advisory: OpenFlight captures the same way for every shot

        if msg_type not in _HOST_TO_DEVICE:
            self._queue(
                wire.encode_error(wire.ErrorCode.GENERIC, f"unsupported type 0x{msg_type:02x}")
            )
        return []


def _error_message(payload: bytes) -> str:
    """Text of a host Error frame (code, then a length-prefixed string)."""
    try:
        text, _ = wire.unpack_str(payload, 2)
        code = int.from_bytes(payload[:2], "little")
        return f"[{code}] {text}"
    except wire.ProtocolError:
        return "host reported an error"
