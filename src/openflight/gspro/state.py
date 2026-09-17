"""GSPro club-code mapping. (Connection/player state now lives in sim.types.)"""

import logging

from openflight.clubs import ClubType

logger = logging.getLogger(__name__)

_GSPRO_CLUB_MAP = {
    "DR": ClubType.DRIVER,
    "W3": ClubType.WOOD_3,
    "W5": ClubType.WOOD_5,
    "W7": ClubType.WOOD_7,
    "H3": ClubType.HYBRID_3,
    "H5": ClubType.HYBRID_5,
    "H7": ClubType.HYBRID_7,
    "H9": ClubType.HYBRID_9,
    "I2": ClubType.IRON_2,
    "I3": ClubType.IRON_3,
    "I4": ClubType.IRON_4,
    "I5": ClubType.IRON_5,
    "I6": ClubType.IRON_6,
    "I7": ClubType.IRON_7,
    "I8": ClubType.IRON_8,
    "I9": ClubType.IRON_9,
    "PW": ClubType.PW,
    "GW": ClubType.GW,
    "SW": ClubType.SW,
    "LW": ClubType.LW,
    # "PT" intentionally absent — putting is out of scope for v1
}


def gspro_code_to_club(code: str) -> ClubType:
    """Map a GSPro club code (e.g. 'DR', 'I7') to ClubType. Unknown -> UNKNOWN."""
    if code == "PT":
        logger.info("[gspro] putter received — putting is out of scope, mapping to UNKNOWN")
        return ClubType.UNKNOWN
    club = _GSPRO_CLUB_MAP.get(code)
    if club is None:
        logger.warning("[gspro] unknown club code %r, mapping to UNKNOWN", code)
        return ClubType.UNKNOWN
    return club
