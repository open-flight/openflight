"""Translate a Shot into a protocol-neutral ResolvedShot with provenance.

This is the single place the measured-vs-estimated fallback table lives, so
every simulator connector inherits identical fallback behavior. Codecs only
serialize the ResolvedShot into their own wire format.
"""

import math
from typing import Dict, Tuple

from openflight.clubs import ClubType
from openflight.clubs.physics import get_club_physics
from openflight.launch_monitor import (
    SPIN_CONFIDENCE_HIGH,
    Shot,
)
from openflight.sim.types import IncompleteShotError, PlayerState, ResolvedShot

# Temporary per-club spin model (rpm), used only when a measured spin is absent
# or low-confidence. Slated for replacement by the shared ballistics spin model.
SPIN_MODEL_RPM: Dict[ClubType, float] = {
    ClubType.DRIVER: 2500.0,
    ClubType.WOOD_3: 3000.0,
    ClubType.WOOD_5: 3500.0,
    ClubType.WOOD_7: 4000.0,
    ClubType.HYBRID_3: 3500.0,
    ClubType.HYBRID_5: 4000.0,
    ClubType.HYBRID_7: 4500.0,
    ClubType.HYBRID_9: 5000.0,
    ClubType.IRON_2: 4000.0,
    ClubType.IRON_3: 4500.0,
    ClubType.IRON_4: 5000.0,
    ClubType.IRON_5: 5500.0,
    ClubType.IRON_6: 6000.0,
    ClubType.IRON_7: 7000.0,
    ClubType.IRON_8: 8000.0,
    ClubType.IRON_9: 9000.0,
    ClubType.PW: 9500.0,
    ClubType.GW: 10000.0,
    ClubType.SW: 10500.0,
    ClubType.LW: 11000.0,
    ClubType.UNKNOWN: 5000.0,
}

_DEFAULT_SPIN_RPM = 5000.0


def _resolve_total_spin(shot: Shot) -> Tuple[float, str]:
    """Measured spin if present and high-confidence, else the per-club model."""
    if (
        shot.spin_rpm is not None
        and shot.spin_rpm > 0
        and shot.spin_confidence is not None
        and shot.spin_confidence >= SPIN_CONFIDENCE_HIGH
    ):
        return float(shot.spin_rpm), "measured"
    return SPIN_MODEL_RPM.get(shot.club, _DEFAULT_SPIN_RPM), "estimated"


def resolve_shot(shot: Shot, player_state: PlayerState) -> ResolvedShot:
    """Fill every simulator field from a Shot, applying the fallback table.

    Raises IncompleteShotError when ball speed is missing — the one field that
    has no honest model. Allocates exactly one shot number per physical shot
    (so all connectors share a number for the same shot).
    """
    if shot.ball_speed_mph is None or shot.ball_speed_mph <= 0:
        raise IncompleteShotError("ball_speed_mph is required")

    provenance: Dict[str, str] = {"ball_speed": "measured"}

    if shot.launch_angle_vertical is not None:
        vla = float(shot.launch_angle_vertical)
        provenance["vla"] = "measured"
    else:
        vla = get_club_physics(shot.club).optimal_launch_deg
        provenance["vla"] = "estimated"

    if shot.launch_angle_horizontal is not None:
        hla = float(shot.launch_angle_horizontal)
        provenance["hla"] = "measured"
    else:
        hla = 0.0
        provenance["hla"] = "estimated"

    total_spin, spin_prov = _resolve_total_spin(shot)
    provenance["total_spin"] = spin_prov

    if shot.spin_axis_deg is not None:
        spin_axis = float(shot.spin_axis_deg)
        axis_prov = "measured"
    else:
        spin_axis = 0.0
        axis_prov = "estimated"
    provenance["spin_axis"] = axis_prov

    axis_rad = math.radians(spin_axis)
    back_spin = total_spin * math.cos(axis_rad)
    side_spin = total_spin * math.sin(axis_rad)
    derived_prov = (
        "measured" if (spin_prov == "measured" and axis_prov == "measured") else "estimated"
    )
    provenance["back_spin"] = derived_prov
    provenance["side_spin"] = derived_prov

    carry = float(shot.estimated_carry_yards)
    # Carry is always model-derived (never directly observed), so "measured" here
    # means launch-angle-informed: the carry model was driven by a measured launch
    # angle rather than falling back to club-type defaults. The UI badge reflects
    # that distinction, not a claim that carry itself was measured (PR #115 review #6).
    provenance["carry"] = "measured" if shot.has_launch_angle else "estimated"

    if shot.club_speed_mph is not None and shot.club_speed_mph > 0:
        club_speed = float(shot.club_speed_mph)
        provenance["club_speed"] = "measured"
    else:
        club_speed = None
        provenance["club_speed"] = "estimated"

    if shot.club_path_deg is not None:
        club_path = float(shot.club_path_deg)
        provenance["club_path"] = "measured"
    else:
        club_path = 0.0
        provenance["club_path"] = "estimated"

    return ResolvedShot(
        shot_number=player_state.next_shot_number(),
        ball_speed_mph=float(shot.ball_speed_mph),
        vla=vla,
        hla=hla,
        total_spin_rpm=total_spin,
        spin_axis_deg=spin_axis,
        back_spin_rpm=back_spin,
        side_spin_rpm=side_spin,
        carry_yards=carry,
        club_path_deg=club_path,
        club=shot.club,
        club_speed_mph=club_speed,
        provenance=provenance,
    )
