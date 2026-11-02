"""A profile's golf bag: the specific clubs a player carries.

A bag entry is one physical club. Several entries may share a ``ClubType``
(two 56-degree sand wedges on test, say), so each gets its own stable ``id``
and shots record which one hit them. The ``ClubType`` still drives physics and
simulator protocols; the brand, model and measured specs are annotations.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Tuple

from .types import ClubType

MAX_BAG_CLUBS = 30
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


class BagValidationError(ValueError):
    """A bag entry a client sent cannot be stored as given."""


@dataclass(frozen=True)
class BagClub:
    """One physical club in a bag."""

    id: str
    club_type: ClubType
    brand: str = ""
    model: str = ""
    loft_deg: Optional[float] = None
    bounce_deg: Optional[float] = None
    lie_deg: Optional[float] = None
    shaft: str = ""
    notes: str = ""
    hand: Optional[str] = None

    def to_dict(self) -> dict:
        """Wire/disk representation. ``club`` uses the ``ClubType`` value."""
        return {
            "id": self.id,
            "club": self.club_type.value,
            "brand": self.brand,
            "model": self.model,
            "loft_deg": self.loft_deg,
            "bounce_deg": self.bounce_deg,
            "lie_deg": self.lie_deg,
            "shaft": self.shaft,
            "notes": self.notes,
            "hand": self.hand,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "BagClub":
        """Parse one entry, assigning a fresh id when it has none.

        Raises:
            BagValidationError: the entry is not an object, names no selectable
                club, has an out-of-range or non-numeric angle, or names a
                hand other than ``right`` or ``left``.
        """
        if not isinstance(raw, dict):
            raise BagValidationError("Bag club must be an object")
        club_type = _parse_club_type(raw.get("club"))
        club_id = _clean_text(raw.get("id"), MAX_ID_LENGTH) or uuid.uuid4().hex
        return cls(
            id=club_id,
            club_type=club_type,
            brand=_clean_text(raw.get("brand"), MAX_TEXT_LENGTH),
            model=_clean_text(raw.get("model"), MAX_TEXT_LENGTH),
            loft_deg=_parse_angle(raw.get("loft_deg"), "loft_deg", LOFT_RANGE_DEG),
            bounce_deg=_parse_angle(raw.get("bounce_deg"), "bounce_deg", BOUNCE_RANGE_DEG),
            lie_deg=_parse_angle(raw.get("lie_deg"), "lie_deg", LIE_RANGE_DEG),
            shaft=_clean_text(raw.get("shaft"), MAX_TEXT_LENGTH),
            notes=_clean_text(raw.get("notes"), MAX_NOTES_LENGTH),
            hand=_parse_hand(raw.get("hand")),
        )


def parse_bag(raw: Any) -> List[BagClub]:
    """Strictly parse a bag a client sent; any bad entry rejects the whole bag.

    Raises:
        BagValidationError: ``raw`` is not a list, holds more than
            ``MAX_BAG_CLUBS`` entries or a duplicate id, or any entry is invalid.
    """
    if not isinstance(raw, list):
        raise BagValidationError("Bag must be a list of clubs")
    if len(raw) > MAX_BAG_CLUBS:
        raise BagValidationError(f"A bag holds at most {MAX_BAG_CLUBS} clubs")
    clubs = [BagClub.from_dict(entry) for entry in raw]
    seen: set[str] = set()
    for club in clubs:
        if club.id in seen:
            raise BagValidationError(f"Duplicate bag club id: {club.id}")
        seen.add(club.id)
    return clubs


def load_bag(raw: Any) -> List[BagClub]:
    """Leniently parse a stored bag, skipping unusable entries.

    A hand-edited or older ``profiles.json`` must never stop the kiosk from
    starting, so entries ``parse_bag`` would reject are dropped one by one.
    """
    if not isinstance(raw, list):
        return []
    clubs: List[BagClub] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict) or not _clean_text(entry.get("id"), MAX_ID_LENGTH):
            continue
        try:
            club = BagClub.from_dict(entry)
        except BagValidationError:
            continue
        if club.id in seen:
            continue
        seen.add(club.id)
        clubs.append(club)
        if len(clubs) >= MAX_BAG_CLUBS:
            break
    return clubs


def find_bag_club(bag: Iterable[BagClub], club_id: Any) -> Optional[BagClub]:
    """The entry with this id, or None."""
    wanted = str(club_id or "").strip()
    if not wanted:
        return None
    return next((club for club in bag if club.id == wanted), None)


def sole_bag_club_of_type(bag: Iterable[BagClub], club_type: ClubType) -> Optional[BagClub]:
    """The only entry of this type, or None when there are none or several.

    Lets a type-only selection (a simulator, a phone, ``/api/club``) land on a
    specific club whenever that is unambiguous.
    """
    matches = [club for club in bag if club.club_type is club_type]
    return matches[0] if len(matches) == 1 else None


def _parse_club_type(raw: Any) -> ClubType:
    try:
        club_type = ClubType(raw)
    except (TypeError, ValueError):
        raise BagValidationError(f"Unknown club: {raw!r}") from None
    if club_type is ClubType.UNKNOWN:
        raise BagValidationError("Unknown is not a club you can carry")
    return club_type


def _parse_angle(raw: Any, name: str, bounds: Tuple[float, float]) -> Optional[float]:
    """An optional angle in degrees, rounded to 0.1. Blank means not recorded."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if isinstance(raw, bool):
        raise BagValidationError(f"{name} must be a number")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise BagValidationError(f"{name} must be a number") from None
    low, high = bounds
    if not math.isfinite(value) or not low <= value <= high:
        raise BagValidationError(f"{name} must be between {low:g} and {high:g} degrees")
    return round(value, 1)


def _parse_hand(raw: Any) -> Optional[str]:
    """``right`` or ``left``, any case. Blank means not recorded."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    hand = raw.strip().lower() if isinstance(raw, str) else raw
    if hand not in HANDS:
        raise BagValidationError("hand must be right or left")
    return hand


def _clean_text(raw: Any, max_length: int) -> str:
    if raw is None:
        return ""
    return " ".join(str(raw).split())[:max_length]


__all__ = [
    "BagClub",
    "BagValidationError",
    "BOUNCE_RANGE_DEG",
    "HANDS",
    "LIE_RANGE_DEG",
    "LOFT_RANGE_DEG",
    "MAX_BAG_CLUBS",
    "MAX_NOTES_LENGTH",
    "MAX_TEXT_LENGTH",
    "find_bag_club",
    "load_bag",
    "parse_bag",
    "sole_bag_club_of_type",
]
