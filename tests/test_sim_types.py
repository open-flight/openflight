"""Tests for sim.types — ConnectionState, PlayerState, inbound events."""
import time

from openflight.clubs import ClubType
from openflight.sim.types import (
    ConnectionState, PlayerState, PlayerUpdate,
    SHOT_NUMBER_MAX, initial_shot_counter,
)


def test_initial_shot_counter_within_gspro_int32():
    # GSPro parses ShotNumber as a signed 32-bit int; the seed must fit, or the
    # shot is rejected 501 "Bad format". Epoch seconds fit; epoch millis do not.
    n = initial_shot_counter()
    assert 0 < n <= SHOT_NUMBER_MAX


def test_epoch_millis_seed_would_overflow_int32():
    # Regression: the old seed (epoch *milliseconds*) overflows GSPro's 32-bit
    # ShotNumber field (~1.78e12 > 2.15e9) and every shot came back "Bad format".
    assert int(time.time() * 1000) > SHOT_NUMBER_MAX


def test_shot_numbers_stay_int32_over_a_long_session():
    p = PlayerState(shot_counter=initial_shot_counter())
    for _ in range(10_000):
        assert p.next_shot_number() <= SHOT_NUMBER_MAX


def test_connection_state_values():
    assert ConnectionState.DISABLED.value == "disabled"
    assert ConnectionState.CONNECTING.value == "connecting"
    assert ConnectionState.CONNECTED.value == "connected"
    assert ConnectionState.RECONNECT_BACKOFF.value == "reconnecting"
    assert ConnectionState.STOPPED.value == "stopped"


def test_player_state_defaults():
    p = PlayerState()
    assert p.handed == "RH"
    assert p.club == ClubType.DRIVER
    assert p.shot_counter == 0


def test_next_shot_number_increments():
    p = PlayerState()
    assert p.next_shot_number() == 1
    assert p.next_shot_number() == 2
    assert p.shot_counter == 2


def test_apply_updates_handed_and_club():
    p = PlayerState()
    p.apply(PlayerUpdate(handed="LH", club=ClubType.IRON_7))
    assert p.handed == "LH"
    assert p.club == ClubType.IRON_7


def test_apply_ignores_none_fields():
    p = PlayerState(handed="LH", club=ClubType.IRON_7)
    p.apply(PlayerUpdate(handed=None, club=None))
    assert p.handed == "LH"
    assert p.club == ClubType.IRON_7


def test_apply_partial_update():
    p = PlayerState(handed="RH", club=ClubType.DRIVER)
    p.apply(PlayerUpdate(club=ClubType.PW))
    assert p.handed == "RH"
    assert p.club == ClubType.PW
