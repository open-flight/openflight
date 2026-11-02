"""Tests for the bag model: parsing, validation and lookup of a profile's clubs."""

import math

import pytest

from openflight.clubs import ClubType
from openflight.clubs.bag import (
    MAX_BAG_CLUBS,
    MAX_NOTES_LENGTH,
    MAX_TEXT_LENGTH,
    BagClub,
    BagValidationError,
    find_bag_club,
    load_bag,
    parse_bag,
    sole_bag_club_of_type,
)


def _entry(**overrides):
    entry = {"id": "sw-a", "club": "sw", "brand": "Acme", "model": "Spinner", "loft_deg": 56}
    entry.update(overrides)
    return entry


class TestFromDict:
    """One entry, as a client or the profile file holds it."""

    def test_full_entry_round_trips(self):
        raw = {
            "id": "sw-a",
            "club": "sw",
            "brand": "Acme",
            "model": "Spinner",
            "loft_deg": 56.0,
            "bounce_deg": 10.0,
            "lie_deg": 64.0,
            "shaft": "Stiff steel",
            "notes": "Testing vs the old one",
            "hand": "left",
        }

        club = BagClub.from_dict(raw)

        assert club.club_type is ClubType.SW
        assert club.to_dict() == raw

    def test_only_club_is_required(self):
        club = BagClub.from_dict({"club": "7-iron"})

        assert club.club_type is ClubType.IRON_7
        assert club.brand == club.model == club.shaft == club.notes == ""
        assert club.loft_deg is None and club.bounce_deg is None and club.lie_deg is None
        assert club.hand is None

    def test_missing_id_gets_a_fresh_unique_one(self):
        first = BagClub.from_dict({"club": "sw"})
        second = BagClub.from_dict({"club": "sw"})

        assert first.id and second.id
        assert first.id != second.id

    def test_existing_id_is_kept(self):
        assert BagClub.from_dict(_entry(id="keep-me")).id == "keep-me"

    @pytest.mark.parametrize("club", ["putter", "", None, 7, "unknown"])
    def test_unselectable_club_is_rejected(self, club):
        with pytest.raises(BagValidationError):
            BagClub.from_dict(_entry(club=club))

    @pytest.mark.parametrize("raw", [None, "sw", ["sw"], 56])
    def test_non_object_is_rejected(self, raw):
        with pytest.raises(BagValidationError):
            BagClub.from_dict(raw)

    def test_numeric_strings_are_accepted_and_rounded(self):
        club = BagClub.from_dict(_entry(loft_deg="56.04", bounce_deg="  8.06 "))

        assert club.loft_deg == 56.0
        assert club.bounce_deg == 8.1

    @pytest.mark.parametrize("blank", [None, "", "   "])
    def test_blank_angles_mean_not_recorded(self, blank):
        club = BagClub.from_dict(_entry(loft_deg=blank, bounce_deg=blank, lie_deg=blank))

        assert club.loft_deg is None and club.bounce_deg is None and club.lie_deg is None

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("loft_deg", 560),
            ("loft_deg", -1),
            ("loft_deg", 80.01),
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
        with pytest.raises(BagValidationError, match=field):
            BagClub.from_dict(_entry(**{field: value}))

    @pytest.mark.parametrize(
        ("field", "value"),
        [("loft_deg", 0), ("loft_deg", 80), ("bounce_deg", -10), ("bounce_deg", 30)],
    )
    def test_range_bounds_are_inclusive(self, field, value):
        assert getattr(BagClub.from_dict(_entry(**{field: value})), field) == value

    def test_negative_bounce_is_allowed(self):
        assert BagClub.from_dict(_entry(bounce_deg=-2)).bounce_deg == -2

    def test_text_is_trimmed_collapsed_and_capped(self):
        club = BagClub.from_dict(
            _entry(
                brand="  Acme   Golf ",
                model="M" * (MAX_TEXT_LENGTH + 5),
                notes="n" * (MAX_NOTES_LENGTH + 5),
            )
        )

        assert club.brand == "Acme Golf"
        assert len(club.model) == MAX_TEXT_LENGTH
        assert len(club.notes) == MAX_NOTES_LENGTH

    @pytest.mark.parametrize(
        ("raw", "hand"), [("right", "right"), ("left", "left"), (" Left ", "left")]
    )
    def test_hand_is_normalised(self, raw, hand):
        assert BagClub.from_dict(_entry(hand=raw)).hand == hand

    @pytest.mark.parametrize("blank", [None, "", "  "])
    def test_blank_hand_means_not_recorded(self, blank):
        assert BagClub.from_dict(_entry(hand=blank)).hand is None

    @pytest.mark.parametrize("bad", ["both", "lefty", "r", 1, True, ["left"]])
    def test_unknown_hand_is_rejected(self, bad):
        with pytest.raises(BagValidationError, match="hand"):
            BagClub.from_dict(_entry(hand=bad))

    def test_stored_bag_without_hand_still_loads(self):
        # Bags saved before the hand field existed have no "hand" key.
        (club,) = load_bag([_entry()])

        assert club.hand is None

    def test_non_string_text_is_stringified(self):
        assert BagClub.from_dict(_entry(model=588)).model == "588"


class TestParseBag:
    """A whole bag from a client: all or nothing."""

    def test_two_clubs_of_the_same_type_are_allowed(self):
        bag = parse_bag([_entry(id="a"), _entry(id="b", model="Other")])

        assert [club.id for club in bag] == ["a", "b"]
        assert all(club.club_type is ClubType.SW for club in bag)

    def test_order_is_preserved(self):
        bag = parse_bag([_entry(id="1", club="pw"), _entry(id="2", club="driver")])

        assert [club.club_type for club in bag] == [ClubType.PW, ClubType.DRIVER]

    def test_empty_bag_is_valid(self):
        assert parse_bag([]) == []

    @pytest.mark.parametrize("raw", [None, {}, "bag", 3])
    def test_non_list_is_rejected(self, raw):
        with pytest.raises(BagValidationError):
            parse_bag(raw)

    def test_one_bad_entry_rejects_the_whole_bag(self):
        with pytest.raises(BagValidationError):
            parse_bag([_entry(id="a"), _entry(id="b", loft_deg=500)])

    def test_duplicate_ids_are_rejected(self):
        with pytest.raises(BagValidationError, match="Duplicate"):
            parse_bag([_entry(id="a"), _entry(id="a")])

    def test_size_cap(self):
        parse_bag([_entry(id=str(n)) for n in range(MAX_BAG_CLUBS)])
        with pytest.raises(BagValidationError):
            parse_bag([_entry(id=str(n)) for n in range(MAX_BAG_CLUBS + 1)])


class TestLoadBag:
    """A stored bag: tolerant so a bad file never blocks startup."""

    @pytest.mark.parametrize("raw", [None, {}, "bag", 3])
    def test_non_list_is_empty(self, raw):
        assert load_bag(raw) == []

    def test_bad_entries_are_skipped_not_fatal(self):
        bag = load_bag(
            [
                _entry(id="good"),
                _entry(id="bad-loft", loft_deg=500),
                _entry(id="bad-club", club="putter"),
                "not an object",
                _entry(id="good-2", club="pw"),
            ]
        )

        assert [club.id for club in bag] == ["good", "good-2"]

    def test_entries_without_an_id_are_skipped(self):
        # A stored club must keep a stable id; inventing one on every read
        # would orphan the selection and every shot attributed to it.
        assert load_bag([{"club": "sw"}, _entry(id=" ")]) == []

    def test_duplicate_ids_keep_the_first(self):
        bag = load_bag([_entry(id="a", model="First"), _entry(id="a", model="Second")])

        assert [club.model for club in bag] == ["First"]

    def test_size_cap(self):
        bag = load_bag([_entry(id=str(n)) for n in range(MAX_BAG_CLUBS + 5)])

        assert len(bag) == MAX_BAG_CLUBS


class TestLookup:
    """Finding clubs for a selection."""

    def test_find_by_id(self):
        bag = parse_bag([_entry(id="a"), _entry(id="b")])

        assert find_bag_club(bag, "b").id == "b"
        assert find_bag_club(bag, " b ").id == "b"

    @pytest.mark.parametrize("wanted", ["missing", "", None])
    def test_find_misses(self, wanted):
        assert find_bag_club(parse_bag([_entry(id="a")]), wanted) is None

    def test_sole_club_of_type(self):
        bag = parse_bag([_entry(id="sw"), _entry(id="pw", club="pw")])

        assert sole_bag_club_of_type(bag, ClubType.PW).id == "pw"

    def test_sole_club_is_none_when_ambiguous(self):
        bag = parse_bag([_entry(id="a"), _entry(id="b")])

        assert sole_bag_club_of_type(bag, ClubType.SW) is None

    def test_sole_club_is_none_when_absent(self):
        assert sole_bag_club_of_type(parse_bag([_entry()]), ClubType.DRIVER) is None
        assert sole_bag_club_of_type([], ClubType.DRIVER) is None
