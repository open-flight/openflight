"""Selectable clubs: the built-in ``Club`` and a player's ``CustomClub``.

A ``CustomClub`` is one physical club in a bag. It inherits the variables of
the built-in ``Club`` of its type (name, label, loft) and adds what the player
records about it. Several custom clubs may share a type (two 56-degree sand
wedges on test), so each has a stable ``id`` that shots record. ``bag`` names
the bag the club belongs to; today each profile has one bag whose id is the
profile id.

Physics always comes from the built-in type, even where a custom club
overrides the loft, until issue #280 settles which reference values to use.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Optional, Tuple

from .physics import ClubPhysics, get_club_physics
from .types import ClubType

MAX_TEXT_LENGTH = 40
MAX_NOTES_LENGTH = 120
MAX_ID_LENGTH = 64

# Inclusive bounds, in degrees. Wide enough for any real club, narrow enough
# to reject a typo such as 560 for 56.0.
LOFT_RANGE_DEG: Tuple[float, float] = (0.0, 80.0)
BOUNCE_RANGE_DEG: Tuple[float, float] = (-10.0, 30.0)
LIE_RANGE_DEG: Tuple[float, float] = (40.0, 80.0)

# Which hand the club is built for. None means not recorded.
HANDS = ("right", "left")

# Display name and picker label per built-in type; mirrors ui/src/data/clubs.ts.
CLUB_NAMES: Mapping[ClubType, Tuple[str, str]] = MappingProxyType(
    {
        ClubType.DRIVER: ("Driver", "DR"),
        ClubType.WOOD_3: ("3 Wood", "3W"),
        ClubType.WOOD_5: ("5 Wood", "5W"),
        ClubType.WOOD_7: ("7 Wood", "7W"),
        ClubType.HYBRID_3: ("3 Hybrid", "3H"),
        ClubType.HYBRID_5: ("5 Hybrid", "5H"),
        ClubType.HYBRID_7: ("7 Hybrid", "7H"),
        ClubType.HYBRID_9: ("9 Hybrid", "9H"),
        ClubType.IRON_2: ("2 Iron", "2i"),
        ClubType.IRON_3: ("3 Iron", "3i"),
        ClubType.IRON_4: ("4 Iron", "4i"),
        ClubType.IRON_5: ("5 Iron", "5i"),
        ClubType.IRON_6: ("6 Iron", "6i"),
        ClubType.IRON_7: ("7 Iron", "7i"),
        ClubType.IRON_8: ("8 Iron", "8i"),
        ClubType.IRON_9: ("9 Iron", "9i"),
        ClubType.PW: ("Pitching Wedge", "PW"),
        ClubType.GW: ("Gap Wedge", "GW"),
        ClubType.SW: ("Sand Wedge", "SW"),
        ClubType.LW: ("Lob Wedge", "LW"),
    }
)


class ClubValidationError(ValueError):
    """A club a client sent cannot be stored as given."""


@dataclass(frozen=True)
class Club:
    """A selectable built-in club."""

    club_type: ClubType
    name: str
    label: str
    loft_deg: float

    @classmethod
    def builtin(cls, club_type: ClubType) -> "Club":
        """The built-in club of a type, with its nominal loft."""
        name, label = CLUB_NAMES[club_type]
        return cls(club_type, name, label, get_club_physics(club_type).nominal_loft_deg)

    @property
    def physics(self) -> ClubPhysics:
        """Physics defaults; always those of the built-in type (see issue #280)."""
        return get_club_physics(self.club_type)


@dataclass(frozen=True, kw_only=True)
class CustomClub(Club):
    """One physical club in a bag, inheriting its built-in club's variables."""

    id: str
    bag: str
    brand: str = ""
    model: str = ""
    bounce_deg: Optional[float] = None
    lie_deg: Optional[float] = None
    shaft: str = ""
    hand: Optional[str] = None
    notes: str = ""

    def to_dict(self) -> dict:
        """Wire/disk representation. ``club`` is the ``ClubType`` value.

        ``name`` and ``label`` are always inherited, so they are sent for
        convenience and ignored when read back.
        """
        return {
            "id": self.id,
            "bag": self.bag,
            "club": self.club_type.value,
            "name": self.name,
            "label": self.label,
            "loft_deg": self.loft_deg,
            "brand": self.brand,
            "model": self.model,
            "bounce_deg": self.bounce_deg,
            "lie_deg": self.lie_deg,
            "shaft": self.shaft,
            "hand": self.hand,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, raw: Any, *, club_id: str, bag: str) -> "CustomClub":
        """Build a club from client or disk fields; ``club_id`` and ``bag`` are trusted.

        A blank loft inherits the built-in club's nominal loft.

        Raises:
            ClubValidationError: ``raw`` is not an object, names no selectable
                club, has an out-of-range or non-numeric angle, or names a hand
                other than ``right`` or ``left``.
        """
        if not isinstance(raw, dict):
            raise ClubValidationError("Club must be an object")
        base = Club.builtin(_parse_club_type(raw.get("club")))
        loft = _parse_angle(raw.get("loft_deg"), "loft_deg", LOFT_RANGE_DEG)
        return cls(
            club_type=base.club_type,
            name=base.name,
            label=base.label,
            loft_deg=base.loft_deg if loft is None else loft,
            id=club_id,
            bag=bag,
            brand=_clean_text(raw.get("brand"), MAX_TEXT_LENGTH),
            model=_clean_text(raw.get("model"), MAX_TEXT_LENGTH),
            bounce_deg=_parse_angle(raw.get("bounce_deg"), "bounce_deg", BOUNCE_RANGE_DEG),
            lie_deg=_parse_angle(raw.get("lie_deg"), "lie_deg", LIE_RANGE_DEG),
            shaft=_clean_text(raw.get("shaft"), MAX_TEXT_LENGTH),
            hand=_parse_hand(raw.get("hand")),
            notes=_clean_text(raw.get("notes"), MAX_NOTES_LENGTH),
        )


def find_custom_club(clubs: Iterable[CustomClub], club_id: Any) -> Optional[CustomClub]:
    """The club with this id, or None."""
    wanted = clean_id(club_id)
    if not wanted:
        return None
    return next((club for club in clubs if club.id == wanted), None)


def sole_custom_club_of_type(
    clubs: Iterable[CustomClub], club_type: ClubType
) -> Optional[CustomClub]:
    """The only club of this type, or None when there are none or several.

    Lets a type-only selection (a simulator, a phone, ``/api/club``) land on a
    specific club whenever that is unambiguous.
    """
    matches = [club for club in clubs if club.club_type is club_type]
    return matches[0] if len(matches) == 1 else None


def clean_id(raw: Any) -> str:
    """A trimmed, capped id; "" when blank."""
    return _clean_text(raw, MAX_ID_LENGTH)


def _parse_club_type(raw: Any) -> ClubType:
    try:
        club_type = ClubType(raw)
    except (TypeError, ValueError):
        raise ClubValidationError(f"Unknown club: {raw!r}") from None
    if club_type is ClubType.UNKNOWN:
        raise ClubValidationError("Unknown is not a club you can carry")
    return club_type


def _parse_angle(raw: Any, name: str, bounds: Tuple[float, float]) -> Optional[float]:
    """An optional angle in degrees, rounded to 0.1. Blank means not recorded."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if isinstance(raw, bool):
        raise ClubValidationError(f"{name} must be a number")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ClubValidationError(f"{name} must be a number") from None
    low, high = bounds
    if not math.isfinite(value) or not low <= value <= high:
        raise ClubValidationError(f"{name} must be between {low:g} and {high:g} degrees")
    return round(value, 1)


def _parse_hand(raw: Any) -> Optional[str]:
    """``right`` or ``left``, any case. Blank means not recorded."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    hand = raw.strip().lower() if isinstance(raw, str) else raw
    if hand not in HANDS:
        raise ClubValidationError("hand must be right or left")
    return hand


def _clean_text(raw: Any, max_length: int) -> str:
    """Printable text with whitespace collapsed, capped at ``max_length``.

    Control characters are dropped: they never belong in a club's details and
    would bloat the BLE ``clubs`` event (JSON escapes each one to six bytes).
    """
    if raw is None:
        return ""
    printable = "".join(char for char in str(raw) if char.isprintable() or char.isspace())
    return " ".join(printable.split())[:max_length]
