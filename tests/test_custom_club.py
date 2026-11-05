"""Tests for Club and CustomClub: inheritance, validation and lookup."""

import math

import pytest

from openflight.clubs.club import (
    CLUB_NAMES,
    MAX_NOTES_LENGTH,
    MAX_TEXT_LENGTH,
    Club,
    ClubValidationError,
    CustomClub,
    find_custom_club,
    sole_custom_club_of_type,
)
from openflight.clubs.physics import get_club_physics
from openflight.clubs.types import ClubType


def _club(club_id="sw-a", bag="bag-1", **fields):
    raw = {"club": "sw", "brand": "Acme", "model": "Spinner", "loft_deg": 56}
    raw.update(fields)
    return CustomClub.from_dict(raw, club_id=club_id, bag=bag)


class TestBuiltinClub:
    """The built-in club every custom club inherits from."""

    def test_every_selectable_type_has_a_name_and_label(self):
        selectable = {club_type for club_type in ClubType if club_type is not ClubType.UNKNOWN}

        assert set(CLUB_NAMES) == selectable

    def test_builtin_takes_the_nominal_loft(self):
        club = Club.builtin(ClubType.IRON_7)

        assert (club.name, club.label) == ("7 Iron", "7i")
        assert club.loft_deg == get_club_physics(ClubType.IRON_7).nominal_loft_deg

    def test_physics_come_from_the_type(self):
        assert Club.builtin(ClubType.PW).physics == get_club_physics(ClubType.PW)


class TestInheritance:
    """A custom club inherits its built-in club's variables."""

    def test_is_a_club(self):
        assert isinstance(_club(), Club)

    def test_inherits_name_and_label(self):
        club = _club()

        assert (club.name, club.label) == ("Sand Wedge", "SW")

    def test_client_cannot_override_name_or_label(self):
        club = _club(name="Lob Wedge", label="LW")

        assert (club.name, club.label) == ("Sand Wedge", "SW")

    @pytest.mark.parametrize("blank", [None, "", "  "])
    def test_blank_loft_inherits_the_nominal_loft(self, blank):
        assert _club(loft_deg=blank).loft_deg == get_club_physics(ClubType.SW).nominal_loft_deg

    def test_own_loft_overrides_the_inherited_one(self):
        assert _club(loft_deg=58).loft_deg == 58.0

    def test_physics_follow_the_own_loft(self):
        assert _club(loft_deg=58).physics == get_club_physics(ClubType.SW, 58.0)
        lob_wedge = get_club_physics(ClubType.LW)
        assert _club(loft_deg=58).physics.optimal_launch_deg == lob_wedge.optimal_launch_deg

    def test_blank_loft_keeps_the_types_physics(self):
        assert _club(loft_deg=None).physics == get_club_physics(ClubType.SW)


class TestFromDict:
    """Validation of client and stored fields."""

    def test_full_club_round_trips(self):
        raw = {
            "id": "sw-a",
            "bag": "bag-1",
            "club": "sw",
            "name": "Sand Wedge",
            "label": "SW",
            "loft_deg": 56.0,
            "brand": "Acme",
            "model": "Spinner",
            "bounce_deg": 10.0,
            "lie_deg": 64.0,
            "shaft": "Stiff steel",
            "hand": "left",
            "notes": "Testing vs the old one",
        }

        club = CustomClub.from_dict(raw, club_id="sw-a", bag="bag-1")

        assert club.to_dict() == raw

    def test_id_and_bag_come_from_the_caller(self):
        club = CustomClub.from_dict(
            {"club": "pw", "id": "spoofed", "bag": "spoofed"}, club_id="real", bag="mine"
        )

        assert (club.id, club.bag) == ("real", "mine")

    def test_only_club_is_required(self):
        club = CustomClub.from_dict({"club": "7-iron"}, club_id="a", bag="b")

        assert club.brand == club.model == club.shaft == club.notes == ""
        assert club.bounce_deg is None and club.lie_deg is None and club.hand is None

    @pytest.mark.parametrize("club", ["putter", "", None, 7, "unknown"])
    def test_unselectable_club_is_rejected(self, club):
        with pytest.raises(ClubValidationError):
            _club(club=club)

    @pytest.mark.parametrize("raw", [None, "sw", ["sw"], 56])
    def test_non_object_is_rejected(self, raw):
        with pytest.raises(ClubValidationError):
            CustomClub.from_dict(raw, club_id="a", bag="b")

    def test_numeric_strings_are_accepted_and_rounded(self):
        club = _club(loft_deg="56.04", bounce_deg="  8.06 ")

        assert (club.loft_deg, club.bounce_deg) == (56.0, 8.1)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("loft_deg", 560),
            ("loft_deg", -1),
            ("bounce_deg", 31),
            ("bounce_deg", -10.5),
            ("lie_deg", 39.9),
            ("lie_deg", 81),
            ("loft_deg", "fifty"),
            ("loft_deg", math.nan),
            ("loft_deg", math.inf),
            ("loft_deg", True),
            ("loft_deg", [56]),
        ],
    )
    def test_bad_angles_are_rejected(self, field, value):
        with pytest.raises(ClubValidationError, match=field):
            _club(**{field: value})

    @pytest.mark.parametrize(
        ("field", "value"),
        [("loft_deg", 0), ("loft_deg", 80), ("bounce_deg", -10), ("bounce_deg", 30)],
    )
    def test_range_bounds_are_inclusive(self, field, value):
        assert getattr(_club(**{field: value}), field) == value

    @pytest.mark.parametrize(
        ("raw", "hand"), [("right", "right"), ("left", "left"), (" Left ", "left")]
    )
    def test_hand_is_normalised(self, raw, hand):
        assert _club(hand=raw).hand == hand

    @pytest.mark.parametrize("bad", ["both", "lefty", "r", 1, True, ["left"]])
    def test_unknown_hand_is_rejected(self, bad):
        with pytest.raises(ClubValidationError, match="hand"):
            _club(hand=bad)

    def test_text_is_trimmed_collapsed_and_capped(self):
        club = _club(
            brand="  Acme   Golf ",
            model="M" * (MAX_TEXT_LENGTH + 5),
            notes="n" * (MAX_NOTES_LENGTH + 5),
        )

        assert club.brand == "Acme Golf"
        assert len(club.model) == MAX_TEXT_LENGTH
        assert len(club.notes) == MAX_NOTES_LENGTH

    def test_control_characters_are_dropped(self):
        club = _club(brand="\x01Ac\x00me\x7f", model="Spin​ner ⛳")

        assert club.brand == "Acme"
        assert club.model == "Spinner ⛳"


class TestLookup:
    """Finding clubs for a selection."""

    def test_find_by_id(self):
        clubs = [_club("a"), _club("b")]

        assert find_custom_club(clubs, " b ").id == "b"
        assert find_custom_club(clubs, "missing") is None
        assert find_custom_club(clubs, "") is None
        assert find_custom_club(clubs, None) is None

    def test_sole_club_of_type(self):
        clubs = [_club("sw"), _club("pw", club="pw")]

        assert sole_custom_club_of_type(clubs, ClubType.PW).id == "pw"

    def test_sole_club_is_none_when_ambiguous_or_absent(self):
        clubs = [_club("a"), _club("b")]

        assert sole_custom_club_of_type(clubs, ClubType.SW) is None
        assert sole_custom_club_of_type(clubs, ClubType.DRIVER) is None
        assert sole_custom_club_of_type([], ClubType.DRIVER) is None
