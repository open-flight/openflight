"""Core golf club domain types."""

from enum import Enum


class ClubType(Enum):
    """Built-in golf club categories used by protocols and physics."""

    DRIVER = "driver"
    WOOD_3 = "3-wood"
    WOOD_5 = "5-wood"
    WOOD_7 = "7-wood"
    HYBRID_3 = "3-hybrid"
    HYBRID_5 = "5-hybrid"
    HYBRID_7 = "7-hybrid"
    HYBRID_9 = "9-hybrid"
    IRON_2 = "2-iron"
    IRON_3 = "3-iron"
    IRON_4 = "4-iron"
    IRON_5 = "5-iron"
    IRON_6 = "6-iron"
    IRON_7 = "7-iron"
    IRON_8 = "8-iron"
    IRON_9 = "9-iron"
    PW = "pw"
    GW = "gw"
    SW = "sw"
    LW = "lw"
    UNKNOWN = "unknown"
