"""GSPro OpenConnectV1 codec — ResolvedShot <-> wire bytes.

Wraps the OpenConnectV1 dataclasses in messages.py and the GSPro club map in
state.py behind the protocol-neutral Codec interface the transport expects.
Spec: https://gsprogolf.com/GSProConnectV1.html
"""

from typing import List, Optional

from openflight.gspro.messages import (
    BallData,
    ClubData,
    ShotDataOptions,
    ShotPayload,
    build_heartbeat,
    parse_response,
    serialize_payload,
)
from openflight.gspro.state import gspro_code_to_club
from openflight.sim.types import (
    InboundEvent,
    PlayerUpdate,
    ResolvedShot,
    ShotAck,
    SimError,
)

# Logical fields GSPro actually transmits (drives the UI provenance badges).
_GSPRO_FIELDS = [
    "ball_speed",
    "vla",
    "hla",
    "total_spin",
    "spin_axis",
    "back_spin",
    "side_spin",
    "carry",
    "club_speed",
    "club_path",
]


class GSProCodec:
    """OpenConnect V1 wire format (used by GSPro and by OpenGolfSim's
    OpenConnect plugin). ``name`` is the connector/display target — "gspro" for
    GSPro, "opengolfsim" when this codec drives OGS over its OpenConnect plugin.
    """

    def __init__(
        self, device_id: str = "OpenFlight", units: str = "Yards", name: str = "gspro"
    ):
        self.name = name
        self.device_id = device_id
        self.units = units

    def build_shot(self, resolved: ResolvedShot) -> bytes:
        """Encode a resolved shot into an OpenConnectV1 JSON payload."""
        has_club_speed = resolved.club_speed_mph is not None
        payload = ShotPayload(
            DeviceID=self.device_id,
            Units=self.units,
            ShotNumber=resolved.shot_number,
            APIversion="1",
            BallData=BallData(
                Speed=round(resolved.ball_speed_mph, 1),
                SpinAxis=round(resolved.spin_axis_deg, 1),
                TotalSpin=round(resolved.total_spin_rpm, 0),
                BackSpin=round(resolved.back_spin_rpm, 0),
                SideSpin=round(resolved.side_spin_rpm, 0),
                HLA=round(resolved.hla, 1),
                VLA=round(resolved.vla, 1),
                CarryDistance=round(resolved.carry_yards, 1),
            ),
            ClubData=ClubData(
                Speed=round(resolved.club_speed_mph, 1) if has_club_speed else 0.0,
                Path=round(resolved.club_path_deg, 1),
            ),
            ShotDataOptions=ShotDataOptions(
                ContainsBallData=True,
                ContainsClubData=has_club_speed,
                LaunchMonitorIsReady=True,
                LaunchMonitorBallDetected=True,
                IsHeartBeat=False,
            ),
        )
        return serialize_payload(payload)

    def parse_inbound(self, frame: bytes) -> List[InboundEvent]:
        """Parse an inbound OpenConnectV1 response frame into domain events."""
        resp = parse_response(frame)  # raises ValueError on malformed JSON
        if resp.Code == 201 and resp.Player:
            return [
                PlayerUpdate(
                    handed=str(resp.Player["Handed"]) if "Handed" in resp.Player else None,
                    club=gspro_code_to_club(str(resp.Player["Club"]))
                    if "Club" in resp.Player
                    else None,
                )
            ]
        if resp.Code >= 500:
            return [SimError(message=resp.Message)]
        if resp.Code == 200:
            return [ShotAck(ok=True, message=resp.Message)]
        return []

    def heartbeat_bytes(self) -> Optional[bytes]:
        """Return serialized OpenConnectV1 heartbeat bytes."""
        return build_heartbeat(self.device_id, self.units, shot_number=0)

    def on_connect_bytes(self) -> Optional[bytes]:
        """Return on-connect handshake payload (None for OpenConnectV1)."""
        return None

    def fields_for_target(self) -> List[str]:
        """Return fields targeted by this simulator."""
        return list(_GSPRO_FIELDS)
