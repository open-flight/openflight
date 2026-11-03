"""Parser for the Foresight GC2 serial/Bluetooth text stream.

Ported from GC2-to-OpenAPI_headless_v7.py: the same message decoding, merge
and WECO club-data logic, but emitting ``GC2ShotData`` objects instead of
Open API JSON.
"""

import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)

SHOT_MERGE_WINDOW_SECONDS = 1.0
UNTERMINATED_MESSAGE_IDLE_SECONDS = 0.15
LINE_BUFFER_MAX_SIZE = 2048
WECO_HEADER_LEN = 5
WECO_PAYLOAD_HEADER_LEN = 6
WECO_NEW_LAYOUT_MIN_TOKENS = 82
WECO_OLD_LAYOUT_MIN_TOKENS_FOR_SPEED = 14


@dataclass
class GC2ShotData:
    """Ball (and optionally club) data for one GC2 shot, in mph/degrees/rpm."""

    shot_number: int = 0
    speed: float = 0.0
    spin_axis: float = 0.0
    total_spin: float = 0.0
    back_spin: float = 0.0
    side_spin: float = 0.0
    hla: float = 0.0
    vla: float = 0.0
    carry_distance: float = 0.0
    smash: float = 0.0
    club_speed: float = 0.0
    weco: Optional[dict] = None
    impact_time: float = field(default_factory=time.time)


def message_value(message: str, key: str) -> Optional[float]:
    """Extract the value for a given key from a GC2 message, or None if not found."""
    match = re.search(r"(?:^|,)" + re.escape(key) + r"=([^,]*)", message)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def extract_tm(message: str) -> int:
    """Extract the shot number (TM) from a GC2 message, or -1 if not found."""
    value = message_value(message, "TM")
    return -1 if value is None else int(value)


def _weco_tokens(payload: str, payload_start: int) -> list:
    """Extract WECO tokens from the payload starting at the given index."""
    tokens = []
    index = payload_start
    while index + 2 < len(payload):
        if payload[index] in "XYZ":
            value = ord(payload[index + 1]) | (ord(payload[index + 2]) << 8)
            if value >= 32768:
                value -= 65536
            tokens.append(value)
            index += 3
        else:
            index += 1
    return tokens


def decode_weco(line: str) -> Optional[dict]:
    """Decode a #WECO club-data record; returns a dictionary of club data or None if malformed."""
    if len(line) < 15 or not line.startswith("#WECO"):
        return None
    payload_start = WECO_HEADER_LEN + WECO_PAYLOAD_HEADER_LEN
    end_mark = line.find("!END")
    if end_mark <= payload_start:
        return None
    tokens = _weco_tokens(line[:end_mark], payload_start)
    result = {
        "loft": 0.0,
        "angle_of_attack": 0.0,
        "horizontal_face_impact": 0.0,
        "path": 0.0,
        "face_to_target": 0.0,
        "lie": 0.0,
        "speed": 0.0,
        "vertical_face_impact": 0.0,
    }

    def token(index: int) -> int:
        return tokens[index] if index < len(tokens) else 0

    if len(tokens) >= WECO_NEW_LAYOUT_MIN_TOKENS:
        formulas = (
            ("speed", 13, 117989.7, -1065.19),
            ("angle_of_attack", 39, -54799.0, -22648.75),
            ("horizontal_face_impact", 63, 15858.8, 1369.57),
            ("loft", 65, 205440.2, -5777.46),
            ("lie", 69, 21773.1, -1036.82),
            ("face_to_target", 70, 26064.5, -1078.28),
            ("path", 72, 45962.0, -5475.47),
            ("vertical_face_impact", 74, 26072.7, 645.95),
        )
        for name, index, offset, scale in formulas:
            result[name] = (token(index) - offset) / scale
    else:
        result["loft"] = (token(20) + 28872.5) / 1823.15
        result["horizontal_face_impact"] = (token(46) - 22993.7) / 734.14
        result["angle_of_attack"] = (token(47) + 4422.2) / -7695.66
        if len(tokens) >= WECO_OLD_LAYOUT_MIN_TOKENS_FOR_SPEED:
            result["speed"] = (token(13) - 117989.7) / -1065.19
    return result


def _spin_axis(side_spin: float, total_spin: float) -> float:
    """Compute the spin axis angle from side spin and total spin.
    This calculation requires checked. The GC2 provides back spin and side spin
    separately. The spin axis is currently computed as the arcsine of the ratio
    of side spin to total spin.
    """
    if not total_spin:
        return 0.0
    ratio = max(-1.0, min(1.0, side_spin / total_spin))
    return math.degrees(math.asin(ratio))


class GC2StreamParser:
    """
    Feed raw bytes from the GC2; completed shots are passed to ``on_shot``.

    A shot is held for ``merge_window`` seconds after its first message so that
    follow-up messages for the same shot (refined spin, WECO club data) are
    merged before it is emitted. Call ``poll()`` regularly to flush it.
    """

    def __init__(
        self,
        on_shot: Callable[[GC2ShotData], None],
        merge_window: float = SHOT_MERGE_WINDOW_SECONDS,
    ):
        """Initialize the GC2 stream parser with a callback and merge window."""
        self._on_shot = on_shot
        self._merge_window = merge_window
        self._line_buffer = ""
        self._last_data_time = time.monotonic()
        self._previous_tm = -1
        self._detected_first_shot = True # first shot will be passed after connection.
        self._pending: Optional[GC2ShotData] = None
        self._pending_deadline = 0.0

    def feed(self, data: bytes) -> None:
        """Feed raw bytes from the GC2 device into the parser."""
        self._flush_expired()
        self._last_data_time = time.monotonic()
        self._line_buffer += data.decode("latin-1", errors="replace")
        self._process_line_buffer()

    def poll(self) -> None:
        """Handle idle time: flush an unterminated message and expired shots."""
        idle = time.monotonic() - self._last_data_time
        if self._line_buffer and idle >= UNTERMINATED_MESSAGE_IDLE_SECONDS:
            self._process_line_buffer(flush_unterminated=True)
        self._flush_expired()

    def _flush_expired(self) -> None:
        if self._pending is not None and time.monotonic() >= self._pending_deadline:
            self._emit_pending()

    def _emit_pending(self) -> None:
        shot, self._pending = self._pending, None
        self._pending_deadline = 0.0
        if shot is not None:
            self._on_shot(shot)

    def _process_line_buffer(self, flush_unterminated: bool = False) -> None:
        buffer = self._line_buffer
        while True:
            positions = [p for p in (buffer.find("\r"), buffer.find("\n")) if p >= 0]
            if not positions:
                break
            position = min(positions)
            line, buffer = buffer[:position], buffer[position + 1:]
            if "TM=" in line:
                self._process_message(line)
            elif line.startswith("#WECO"):
                if self._pending is not None:
                    self._pending.weco = decode_weco(line)

        if "TM=" in buffer and "\r" not in buffer and "\n" not in buffer:
            tm_position = buffer.find("TM=")
            next_position = buffer.find("TM=", tm_position + 3)
            if next_position >= 0:
                message = buffer[tm_position:next_position]
                if len(message) > 4:
                    self._process_message(message)
                buffer = buffer[next_position:]
            elif flush_unterminated:
                message = buffer[tm_position:]
                if len(message) > 4:
                    self._process_message(message)
                buffer = ""

        self._line_buffer = "" if len(buffer) > LINE_BUFFER_MAX_SIZE else buffer

    def _process_message(self, message: str) -> None:
        current_tm = extract_tm(message)
        if current_tm == -1 or current_tm == self._previous_tm:
            return
        self._previous_tm = current_tm

        shot_number = int(message_value(message, "ID") or 0)
        values = {
            "speed": message_value(message, "SP"),
            "total_spin": message_value(message, "TS"),
            "back_spin": message_value(message, "BS"),
            "side_spin": message_value(message, "SS"),
            "hla": message_value(message, "AZ"),
            "vla": message_value(message, "EL"),
            "carry_distance": message_value(message, "CY"),
            "smash": message_value(message, "SM"),
        }
        speed = values["speed"] or 0.0

        if speed == 0 and self._pending is None:
            logger.debug("GC2 speed zero, ignoring misread")
            return
        if not self._detected_first_shot:
            # The first reading after connecting is the device's stale last shot.
            if speed != 0:
                logger.info("GC2 ready")
                self._detected_first_shot = True
            return

        now = time.monotonic()
        if self._pending is not None:
            pending = self._pending
            same_shot = shot_number in (0, pending.shot_number) or pending.shot_number == 0
            if same_shot and now < self._pending_deadline:
                self._merge(pending, shot_number, values)
                return
            self._emit_pending()
            if speed == 0:
                return

        shot = GC2ShotData(shot_number=shot_number)
        self._merge(shot, shot_number, values)
        self._pending = shot
        self._pending_deadline = now + self._merge_window

    @staticmethod
    def _merge(shot: GC2ShotData, shot_number: int, values: dict) -> None:
        if shot_number > 0:
            shot.shot_number = shot_number
        for name, value in values.items():
            if value is not None:
                setattr(shot, name, value)
        if values["speed"] is not None or values["smash"] is not None:
            shot.club_speed = shot.speed / shot.smash if shot.smash > 0.001 else 0.0
        if values["total_spin"] is not None or values["side_spin"] is not None:
            shot.spin_axis = _spin_axis(shot.side_spin, shot.total_spin)
