"""OpenFlight - DIY Golf Launch Monitor using OPS243-A Radar."""

__version__ = "0.2.0"

from .clubs import ClubType
from .launch_monitor import Shot, estimate_carry_distance
from .ops243 import Direction, OPS243Radar, SpeedReading, SpeedUnit

__all__ = [
    "OPS243Radar",
    "Shot",
    "ClubType",
    "SpeedUnit",
    "Direction",
    "SpeedReading",
    "estimate_carry_distance",
]
