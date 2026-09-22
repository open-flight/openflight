"""Tests for gspro.state — GSPro club-code mapping."""
from openflight.clubs import ClubType
from openflight.gspro.state import gspro_code_to_club


def test_gspro_code_to_club_mapping():
    assert gspro_code_to_club("DR") is ClubType.DRIVER
    assert gspro_code_to_club("W3") is ClubType.WOOD_3
    assert gspro_code_to_club("H5") is ClubType.HYBRID_5
    assert gspro_code_to_club("I7") is ClubType.IRON_7
    assert gspro_code_to_club("PW") is ClubType.PW
    assert gspro_code_to_club("LW") is ClubType.LW


def test_unknown_code_maps_to_unknown():
    assert gspro_code_to_club("XX") is ClubType.UNKNOWN


def test_putter_out_of_scope_maps_to_unknown():
    assert gspro_code_to_club("PT") is ClubType.UNKNOWN


def test_all_openconnect_codes_from_ogs_clubsync_map_to_real_clubs():
    """Contract: every code the OGS club-sync patch can emit maps to a club.

    The Developer API patch (scripts/setup/opengolfsim) converts OGS club
    ids to these OpenConnect codes; each must resolve to a non-UNKNOWN ClubType
    here, or club sync would silently produce UNKNOWN.
    """
    codes = [
        "DR", "W3", "W5", "W7", "H3", "H5", "H7", "H9",
        "I2", "I3", "I4", "I5", "I6", "I7", "I8", "I9",
        "PW", "GW", "SW", "LW",
    ]
    for code in codes:
        assert gspro_code_to_club(code) is not ClubType.UNKNOWN, code
