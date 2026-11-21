"""Tests for the Pureplay club and handedness mapping."""

import pytest

from openflight.clubs import ClubType
from openflight.pureplay.state import pureplay_code_to_club, pureplay_handedness


@pytest.mark.parametrize(
    "code,expected",
    [
        (0, ClubType.DRIVER),
        (2, ClubType.WOOD_3),
        (4, ClubType.WOOD_5),
        (6, ClubType.WOOD_7),
        (10, ClubType.HYBRID_3),
        (16, ClubType.HYBRID_9),
        (18, ClubType.IRON_2),
        (23, ClubType.IRON_7),
        (25, ClubType.IRON_9),
        (26, ClubType.PW),
        (27, ClubType.GW),
        (28, ClubType.SW),
        (29, ClubType.LW),
    ],
)
def test_known_clubs(code, expected):
    assert pureplay_code_to_club(code) == expected


@pytest.mark.parametrize("code", [1, 17, 30, 99])
def test_unrepresentable_or_unknown_clubs_map_to_unknown(code):
    # 1 = Wood2 and 17 = Iron1 (no ClubType), 30 = putter (out of scope), 99 = invalid
    assert pureplay_code_to_club(code) == ClubType.UNKNOWN


def test_handedness():
    assert pureplay_handedness(0) == "RH"
    assert pureplay_handedness(1) == "LH"
