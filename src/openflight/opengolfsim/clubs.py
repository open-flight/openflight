"""Best-effort club mapping for OpenGolfSim inbound player updates.

OpenGolfSim's inbound (server -> device) messages are not documented beyond
"club details include name and ID", so this maps on club *name* with an
abbreviation fallback. It is intentionally lenient and UNVERIFIED against real
hardware — see docs/using/simulator/opengolfsim.md. Outbound shots do not depend on
any of this.
"""

import re
from typing import Optional, Union

from openflight.launch_monitor import ClubType


def _norm(token: str) -> str:
    return " ".join(token.lower().replace("-", " ").split())


# Normalized name/abbreviation -> ClubType.
_NAME_MAP = {
    "driver": ClubType.DRIVER,
    "dr": ClubType.DRIVER,
    "3 wood": ClubType.WOOD_3,
    "w3": ClubType.WOOD_3,
    "3w": ClubType.WOOD_3,
    "5 wood": ClubType.WOOD_5,
    "w5": ClubType.WOOD_5,
    "5w": ClubType.WOOD_5,
    "7 wood": ClubType.WOOD_7,
    "w7": ClubType.WOOD_7,
    "7w": ClubType.WOOD_7,
    "3 hybrid": ClubType.HYBRID_3,
    "h3": ClubType.HYBRID_3,
    "5 hybrid": ClubType.HYBRID_5,
    "h5": ClubType.HYBRID_5,
    "7 hybrid": ClubType.HYBRID_7,
    "h7": ClubType.HYBRID_7,
    "9 hybrid": ClubType.HYBRID_9,
    "h9": ClubType.HYBRID_9,
    "pitching wedge": ClubType.PW,
    "pw": ClubType.PW,
    "gap wedge": ClubType.GW,
    "gw": ClubType.GW,
    "approach wedge": ClubType.GW,
    "aw": ClubType.GW,
    "sand wedge": ClubType.SW,
    "sw": ClubType.SW,
    "lob wedge": ClubType.LW,
    "lw": ClubType.LW,
}

_IRON_BY_NUMBER = {
    2: ClubType.IRON_2,
    3: ClubType.IRON_3,
    4: ClubType.IRON_4,
    5: ClubType.IRON_5,
    6: ClubType.IRON_6,
    7: ClubType.IRON_7,
    8: ClubType.IRON_8,
    9: ClubType.IRON_9,
}

# Matches "7 iron", "7iron", "7i", "i7" (the irons OGS is likely to send).
_IRON_RE = re.compile(r"^(?:i\s*(\d)|(\d)\s*(?:i|iron))$")


def ogs_club_to_club(club: Union[str, dict, None]) -> Optional[ClubType]:
    """Map an OpenGolfSim club name/id to a ClubType.

    Returns None when no club token is present (leave current club unchanged).
    Putters and unrecognized non-empty tokens map to UNKNOWN, matching the
    GSPro convention (putting is out of scope for v1).
    """
    if club is None:
        return None
    token = club.get("name") or club.get("id") if isinstance(club, dict) else club
    if not token:
        return None
    norm = _norm(str(token))
    if norm in ("putter", "pt", "pu"):
        return ClubType.UNKNOWN
    if norm in _NAME_MAP:
        return _NAME_MAP[norm]
    m = _IRON_RE.match(norm)
    if m:
        number = int(m.group(1) or m.group(2))
        if number in _IRON_BY_NUMBER:
            return _IRON_BY_NUMBER[number]
    return ClubType.UNKNOWN
