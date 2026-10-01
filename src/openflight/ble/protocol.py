"""Versioned OpenFlight shot payload and BLE framing helpers."""

from __future__ import annotations

import json
import math
import struct
import uuid
from typing import Iterable, Mapping

SERVICE_UUID = "B6F633F2-E6E3-45AE-84B4-968ECCA2D9C7"
SHOT_CHARACTERISTIC_UUID = "2B28F67E-9011-41D2-98ED-562B47D7A5E4"
CONTROL_CHARACTERISTIC_UUID = "7E3B5D6C-7F10-4D4A-9C39-25E2B77F4A11"

# Schema v2 lives on its own pair of characteristics in the same service.
# BlueZ notifies every subscribed central from one characteristic value, so a
# separate pair is the only way to keep version-one centrals on byte-identical
# version-one traffic (see "Schema v2 design decision" in docs/ios-ble.md).
SHOT_V2_CHARACTERISTIC_UUID = "ED365FE6-3ABF-4FC3-8E44-D9525A22DABD"
CONTROL_V2_CHARACTERISTIC_UUID = "7BA96E63-12C2-4CE0-BB84-3513C7FD1474"

SCHEMA_VERSION = 1
SCHEMA_VERSION_V2 = 2
MAX_SCHEMA_VERSION = SCHEMA_VERSION_V2
FRAME_VERSION = 1
FRAME_SIZE = 20
_HEADER = struct.Struct(">BHBB")
HEADER_SIZE = _HEADER.size
FRAGMENT_PAYLOAD_SIZE = FRAME_SIZE - HEADER_SIZE
MAX_FRAGMENT_COUNT = 255
MAX_MESSAGE_SIZE = FRAGMENT_PAYLOAD_SIZE * MAX_FRAGMENT_COUNT

_OPTIONAL_SHOT_FIELDS = (
    "club_speed_mph",
    "smash_factor",
    "launch_angle_vertical",
    "launch_angle_horizontal",
    "spin_rpm",
    "club_path_deg",
    "spin_axis_deg",
)

# Added by schema v2. Present on every v2 shot, ``null`` when unknown.
_V2_OPTIONAL_SHOT_FIELDS = (
    "shot_number",
    "profile_id",
    "profile_name",
    "carry_range",
    "spin_source",
    "launch_angle_confidence",
)

# Stable namespace for per-shot v2 event ids. Changing it changes every id.
_SHOT_EVENT_NAMESPACE = uuid.UUID("D49C99A9-A305-49CA-A8C2-7D30B7645988")

V2_FEATURES = (
    "provisional_shots",
    "shot_processing",
    "profiles",
    "power_status",
    "shot_deleted",
    "club",
    "shot_catch_up",
)

V2_EVENT_TYPES = (
    "shot",
    "shot_processing",
    "profiles",
    "power_status",
    "session_cleared",
    "shot_deleted",
    "club_changed",
)

ENRICHMENT_STATUSES = ("pending", "complete", "skipped")


def build_club_event(club: str) -> dict:
    """Build the V1 event broadcast whenever the authoritative club changes."""
    if not isinstance(club, str) or not club:
        raise ValueError("Club must be a non-empty string")
    return {
        "schema_version": SCHEMA_VERSION,
        "type": "club_changed",
        "club": club,
    }


def encode_club_event(club: str) -> bytes:
    """Encode a club-state event as deterministic, compact UTF-8 JSON."""
    return json.dumps(
        build_club_event(club),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def build_shot_event(shot_data: Mapping, *, event_id: str | None = None) -> dict:
    """Build the stable, display-focused V1 payload from ``shot_to_dict`` output."""
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id or str(uuid.uuid4()),
        "timestamp": shot_data["timestamp"],
        "club": shot_data["club"],
        "ball_speed_mph": shot_data["ball_speed_mph"],
        "estimated_carry_yards": shot_data["estimated_carry_yards"],
    }
    event.update({field: shot_data.get(field) for field in _OPTIONAL_SHOT_FIELDS})
    return event


def encode_shot_event(shot_data: Mapping, *, event_id: str | None = None) -> bytes:
    """Encode a shot event as deterministic, compact UTF-8 JSON."""
    event = build_shot_event(shot_data, event_id=event_id)
    return json.dumps(
        event,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def encode_message(message: Mapping) -> bytes:
    """Encode a version-one message: compact, sorted, ASCII-only JSON."""
    return json.dumps(
        message,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def encode_message_v2(message: Mapping) -> bytes:
    """Encode a schema v2 message: compact, sorted UTF-8 JSON.

    Unlike version one, non-ASCII text is sent as UTF-8 rather than ``\\uXXXX``
    escapes, which keeps twelve 40-character profile names inside one BLE
    message. Clients must decode UTF-8 only after reassembling every fragment.
    """
    return json.dumps(
        message,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8", "replace")


def stable_shot_event_id(shot_data: Mapping) -> str:
    """Derive one event id per shot, shared by its provisional and final v2 events.

    ``timestamp`` is fixed at detection and ``shot_number`` at callback time,
    so both publications of the same shot hash to the same UUID while shots
    from different logging sessions (whose numbers restart at one) do not.
    """
    timestamp = shot_data["timestamp"]
    if not isinstance(timestamp, str) or not timestamp:
        raise ValueError("Shot timestamp must be a non-empty string")
    shot_number = shot_data.get("shot_number")
    return str(uuid.uuid5(_SHOT_EVENT_NAMESPACE, f"{timestamp}#{shot_number}"))


def _blank_to_none(value):
    return None if value == "" else value


def build_enrichment(status: str, reason: str | None = None) -> dict:
    """Build the v2 ``enrichment`` object describing optional-hardware progress."""
    if status not in ENRICHMENT_STATUSES:
        raise ValueError(f"Unknown enrichment status: {status!r}")
    enrichment = {"status": status}
    if reason:
        enrichment["reason"] = str(reason)
    return enrichment


def build_shot_event_v2(
    shot_data: Mapping,
    *,
    final: bool,
    enrichment: Mapping | None = None,
    event_id: str | None = None,
) -> dict:
    """Build a v2 shot: the v1 fields plus identity, profile and state fields."""
    event = build_shot_event(shot_data, event_id=event_id or stable_shot_event_id(shot_data))
    event["schema_version"] = SCHEMA_VERSION_V2
    event["type"] = "shot"
    event["final"] = bool(final)
    for field in _V2_OPTIONAL_SHOT_FIELDS:
        event[field] = _blank_to_none(shot_data.get(field))
    if event["carry_range"] is not None:
        event["carry_range"] = list(event["carry_range"])
    event["enrichment"] = (
        build_enrichment(enrichment["status"], enrichment.get("reason"))
        if enrichment is not None
        else None
    )
    return event


def encode_shot_event_v2(
    shot_data: Mapping,
    *,
    final: bool,
    enrichment: Mapping | None = None,
    event_id: str | None = None,
) -> bytes:
    """Encode a v2 shot event."""
    return encode_message_v2(
        build_shot_event_v2(shot_data, final=final, enrichment=enrichment, event_id=event_id)
    )


def build_event_v2(event_type: str, fields: Mapping | None = None) -> dict:
    """Build one v2 notify event: ``{"schema_version":2,"type":...,**fields}``."""
    if event_type not in V2_EVENT_TYPES:
        raise ValueError(f"Unknown v2 event type: {event_type!r}")
    fields = dict(fields or {})
    if "schema_version" in fields or "type" in fields:
        raise ValueError("v2 event fields must not override schema_version or type")
    return {"schema_version": SCHEMA_VERSION_V2, "type": event_type, **fields}


def build_club_event_v2(club: str) -> dict:
    """The v2 counterpart of ``build_club_event``."""
    if not isinstance(club, str) or not club:
        raise ValueError("Club must be a non-empty string")
    return build_event_v2("club_changed", {"club": club})


def build_shot_processing_event(state: str) -> dict:
    """``shot_processing``: ``capturing``, ``calculating`` or ``failed``."""
    if not isinstance(state, str) or not state:
        raise ValueError("Processing state must be a non-empty string")
    return build_event_v2("shot_processing", {"state": state})


def build_profiles_event(snapshot: Mapping) -> dict:
    """``profiles`` for phones: ids and names only.

    ``created_at`` and the opaque ``settings`` dict stay on Socket.IO. Phones
    only select profiles, and an unbounded ``settings`` dict could not be
    guaranteed to fit in one BLE message.
    """
    profiles = [
        {"id": str(profile["id"]), "name": str(profile["name"])}
        for profile in snapshot.get("profiles") or []
    ]
    return build_event_v2(
        "profiles",
        {"profiles": profiles, "active_profile_id": snapshot.get("active_profile_id")},
    )


def build_power_status_event(status: Mapping) -> dict:
    """``power_status``: the Socket.IO payload, flattened beside ``type``."""
    return build_event_v2("power_status", status)


def build_session_cleared_event(profile_id: str) -> dict:
    """``session_cleared``: the profile whose shots were removed."""
    return build_event_v2("session_cleared", {"profile_id": profile_id})


def build_control_response(
    request_id: str,
    *,
    result: Mapping | None = None,
    error: str | None = None,
    schema_version: int = SCHEMA_VERSION,
) -> dict:
    """Build the response notified for one control command."""
    response = {
        "schema_version": schema_version,
        "request_id": request_id,
        "ok": error is None,
    }
    if error is None:
        response["result"] = dict(result or {})
    else:
        response["error"] = error
    return response


def build_shot_deleted_event(timestamp: str) -> dict:
    """``shot_deleted``: the timestamp (the delete key) of a removed shot."""
    if not isinstance(timestamp, str) or not timestamp:
        raise ValueError("Deleted shot timestamp must be a non-empty string")
    return build_event_v2("shot_deleted", {"timestamp": timestamp})


def build_hello_result(client_schema_max) -> dict:
    """Answer a ``hello`` command with the negotiated schema and features."""
    if isinstance(client_schema_max, bool) or not isinstance(client_schema_max, int):
        raise ValueError("hello requires an integer client_schema_max")
    if client_schema_max < SCHEMA_VERSION:
        raise ValueError("client_schema_max must be at least 1")
    negotiated = min(client_schema_max, MAX_SCHEMA_VERSION)
    if negotiated < SCHEMA_VERSION_V2:
        return {"schema_version": negotiated, "features": []}
    return {
        "schema_version": negotiated,
        "features": list(V2_FEATURES),
        "characteristics": {
            "shot": SHOT_V2_CHARACTERISTIC_UUID,
            "control": CONTROL_V2_CHARACTERISTIC_UUID,
        },
    }


def fragment_payload(payload: bytes, *, sequence: int) -> list[bytes]:
    """Split a message into conservative 20-byte BLE notification frames."""
    if not payload:
        raise ValueError("BLE payload must not be empty")
    if not 0 <= sequence <= 0xFFFF:
        raise ValueError("BLE sequence must fit in an unsigned 16-bit integer")

    fragment_count = math.ceil(len(payload) / FRAGMENT_PAYLOAD_SIZE)
    if fragment_count > MAX_FRAGMENT_COUNT:
        raise ValueError(
            f"BLE payload is {len(payload)} bytes; maximum is {MAX_MESSAGE_SIZE} bytes"
        )

    frames = []
    for index in range(fragment_count):
        start = index * FRAGMENT_PAYLOAD_SIZE
        chunk = payload[start : start + FRAGMENT_PAYLOAD_SIZE]
        frames.append(_HEADER.pack(FRAME_VERSION, sequence, index, fragment_count) + chunk)
    return frames


def parse_fragment(frame: bytes) -> tuple[int, int, int, bytes]:
    """Return ``(sequence, index, count, payload)`` after validating one frame."""
    if len(frame) < HEADER_SIZE or len(frame) > FRAME_SIZE:
        raise ValueError("BLE frame has an invalid size")
    version, sequence, index, fragment_count = _HEADER.unpack(frame[:HEADER_SIZE])
    if version != FRAME_VERSION:
        raise ValueError(f"unsupported BLE frame version: {version}")
    if fragment_count == 0 or index >= fragment_count:
        raise ValueError("BLE frame has invalid fragment metadata")
    return sequence, index, fragment_count, frame[HEADER_SIZE:]


def reassemble_fragments(frames: Iterable[bytes]) -> bytes:
    """Reassemble a complete message; duplicate fragments are harmless."""
    sequence = None
    fragment_count = None
    fragments: dict[int, bytes] = {}

    for frame in frames:
        frame_sequence, index, frame_count, payload = parse_fragment(frame)
        if sequence is None:
            sequence = frame_sequence
            fragment_count = frame_count
        elif frame_sequence != sequence or frame_count != fragment_count:
            raise ValueError("BLE frames belong to different messages")
        fragments[index] = payload

    if fragment_count is None or len(fragments) != fragment_count:
        raise ValueError("BLE message is incomplete")
    return b"".join(fragments[index] for index in range(fragment_count))


class FragmentReassembler:
    """Incrementally reassemble one message, replacing stale partial messages."""

    def __init__(self):
        self._sequence: int | None = None
        self._fragment_count: int | None = None
        self._fragments: dict[int, bytes] = {}

    def reset(self) -> None:
        """Discard the current incomplete message."""
        self._sequence = None
        self._fragment_count = None
        self._fragments = {}

    def append(self, frame: bytes) -> bytes | None:
        """Append one frame and return the complete payload when available."""
        sequence, index, fragment_count, payload = parse_fragment(frame)
        if self._sequence != sequence:
            self.reset()
            self._sequence = sequence
            self._fragment_count = fragment_count
        elif self._fragment_count != fragment_count:
            self.reset()
            raise ValueError("BLE frames disagree about fragment count")

        self._fragments[index] = payload
        if len(self._fragments) != fragment_count:
            return None

        message = b"".join(self._fragments[item] for item in range(fragment_count))
        self.reset()
        return message
