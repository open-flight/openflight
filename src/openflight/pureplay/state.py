"""Pureplay Connect club-code mapping and handedness. Mirrors openflight.gspro.state."""

import logging

from openflight.clubs import ClubType

logger = logging.getLogger(__name__)

# Pureplay wire values (protocol section 9): 0 Driver; 1-8 Wood2-9; 9-16 Hybrid2-9;
# 17-25 Iron1-9; 26 PW, 27 GW, 28 SW, 29 LW; 30 Putter.
# Only clubs OpenFlight's ClubType can represent are mapped; the rest become UNKNOWN.
_PUREPLAY_CLUB_MAP = {
    0: ClubType.DRIVER,
    # Woods: wire value = wood number - 1
    3 - 1: ClubType.WOOD_3,  # Wood3 -> 2
    5 - 1: ClubType.WOOD_5,  # Wood5 -> 4
    7 - 1: ClubType.WOOD_7,  # Wood7 -> 6
    # Hybrids: wire value = 7 + hybrid number
    7 + 3: ClubType.HYBRID_3,
    7 + 5: ClubType.HYBRID_5,
    7 + 7: ClubType.HYBRID_7,
    7 + 9: ClubType.HYBRID_9,
    # Irons: wire value = 16 + iron number
    16 + 2: ClubType.IRON_2,
    16 + 3: ClubType.IRON_3,
    16 + 4: ClubType.IRON_4,
    16 + 5: ClubType.IRON_5,
    16 + 6: ClubType.IRON_6,
    16 + 7: ClubType.IRON_7,
    16 + 8: ClubType.IRON_8,
    16 + 9: ClubType.IRON_9,
    26: ClubType.PW,
    27: ClubType.GW,
    28: ClubType.SW,
    29: ClubType.LW,
    # 30 (putter) intentionally absent — putting is out of scope, same as gspro
}

PUTTER = 30


def pureplay_code_to_club(code: int) -> ClubType:
    """Map a Pureplay club byte to ClubType. Unknown -> UNKNOWN."""
    if code == PUTTER:
        logger.info("[pureplay] putter received — putting is out of scope, mapping to UNKNOWN")
        return ClubType.UNKNOWN
    club = _PUREPLAY_CLUB_MAP.get(code)
    if club is None:
        logger.warning("[pureplay] unmapped club code %r, mapping to UNKNOWN", code)
        return ClubType.UNKNOWN
    return club


def pureplay_handedness(code: int) -> str:
    """Pureplay handedness byte (0 right, 1 left) -> 'RH' / 'LH'.

    ASSUMPTION: PlayerUpdate.handed uses the GSPro strings 'RH'/'LH'. Confirm against
    openflight.sim.types before submitting.
    """
    return "LH" if code == 1 else "RH"
