"""OpenGolfSim codec — ResolvedShot <-> wire bytes.

Protocol: TCP :3111 (OGS "Developer API"), JSON, no auth. Documented at
https://help.opengolfsim.com/desktop/apis/ and verified against the OpenGolfSim
1.21.2 desktop source (lib/developerAPI.js, lib/launch/index.js):

- Inbound to OGS: {"type":"shot"|"device"|"club", ...}. Anything without a known
  "type" -- including OpenConnect V1 -- is answered with
  {"status":400,"error":"Unable to parse payload"} and never played.
- OGS parses each TCP read as one JSON document, so frames must not be batched.
- Outbound from OGS (newline-terminated, to every connected client): the
  accepted shot echoed back, {"type":"result","data":{result,club,shot,sessionId}}
  (distances in meters), and {"type":"player","data":{player,club}}.
- Shots are only *played* when OGS's launch monitor is set to "Developer API";
  otherwise they are echoed but ignored. There is no heartbeat.
"""

import json
from typing import List, Optional

from openflight.opengolfsim.clubs import ogs_club_to_club
from openflight.sim.types import (
    InboundEvent,
    PlayerUpdate,
    ResolvedShot,
    ShotAck,
    SimError,
)

# Logical fields OpenGolfSim actually transmits (drives the UI provenance badges).
# OGS computes carry itself and takes no club data, so those are omitted.
_OGS_FIELDS = ["ball_speed", "vla", "hla", "spin_axis", "total_spin"]

_PLAYER_TYPES = {"player", "playerupdate", "player_update"}


def _dumps(obj: dict) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")


class OpenGolfSimCodec:
    """OpenGolfSim JSON wire format."""

    name = "opengolfsim"

    def __init__(self, units: str = "imperial"):
        # Kept for config compatibility only. ResolvedShot speeds are mph, and OGS
        # rescales ballSpeed by 2.237 when told "unit":"metric", so the codec
        # always sends imperial (OGS's default) and never a "unit" field.
        self.units = units

    def build_shot(self, resolved: ResolvedShot) -> bytes:
        payload = {
            "type": "shot",
            "shot": {
                "ballSpeed": round(resolved.ball_speed_mph, 1),
                "verticalLaunchAngle": round(resolved.vla, 1),
                "horizontalLaunchAngle": round(resolved.hla, 1),
                "spinAxis": round(resolved.spin_axis_deg, 1),
                "spinSpeed": int(round(resolved.total_spin_rpm)),
            },
        }
        return _dumps(payload)

    def parse_inbound(self, frame: bytes) -> List[InboundEvent]:
        try:
            obj = json.loads(frame.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"Malformed OpenGolfSim message: {e}") from e
        if not isinstance(obj, dict):
            return []
        if "type" not in obj and "error" in obj:
            return [SimError(message=f"{obj.get('status', '')} {obj['error']}".strip())]
        mtype = str(obj.get("type", "")).lower()
        if mtype in _PLAYER_TYPES:
            # The documented player message nests the player block under "data":
            #   {"type":"player","data":{"club":{"name","id","distance"}, ...}}
            # Fall back to a flat object for forward/backward compatibility.
            player = obj.get("data") if isinstance(obj.get("data"), dict) else obj
            handed = player.get("handed") or player.get("hand")
            return [
                PlayerUpdate(
                    handed=str(handed) if handed else None,
                    club=ogs_club_to_club(player.get("club")),
                )
            ]
        if mtype == "shot":
            return [ShotAck(ok=True)]  # OGS echoes each accepted shot
        # "result" (carry/total in meters) follows the echo; not a second ack.
        return []

    def heartbeat_bytes(self) -> Optional[bytes]:
        return None  # OpenGolfSim documents no keepalive

    def on_connect_bytes(self) -> Optional[bytes]:
        return _dumps({"type": "device", "status": "ready"})

    def fields_for_target(self) -> List[str]:
        return list(_OGS_FIELDS)
