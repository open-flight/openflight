import math
from dataclasses import FrozenInstanceError, fields

import pytest

from openflight.clubs import ClubType
from openflight.clubs.physics import (
    CLUB_PHYSICS,
    CLUB_SIMULATION_PROFILES,
    SHOT_SIMULATION_DEFAULTS,
    get_club_physics,
    get_club_simulation_profile,
)


def test_physics_registry_covers_every_club_type():
    assert set(CLUB_PHYSICS) == set(ClubType)


def test_physics_values_are_finite_and_positive():
    for physics in CLUB_PHYSICS.values():
        for field in fields(physics):
            value = getattr(physics, field.name)
            assert math.isfinite(value)
            assert value > 0


def test_unknown_physics_input_uses_unknown_defaults():
    assert get_club_physics(object()) is CLUB_PHYSICS[ClubType.UNKNOWN]


def test_physics_records_are_immutable():
    physics = get_club_physics(ClubType.DRIVER)

    with pytest.raises(FrozenInstanceError):
        physics.nominal_loft_deg = 12.0


def test_simulation_registry_covers_every_club_type():
    assert set(CLUB_SIMULATION_PROFILES) == set(ClubType)


def test_simulation_profile_values_are_finite_and_positive():
    for profile in CLUB_SIMULATION_PROFILES.values():
        for field in fields(profile):
            value = getattr(profile, field.name)
            assert math.isfinite(value)
            assert value > 0


def test_unknown_simulation_input_uses_unknown_profile():
    assert get_club_simulation_profile(object()) is CLUB_SIMULATION_PROFILES[ClubType.UNKNOWN]


def test_simulation_profiles_are_immutable():
    profile = get_club_simulation_profile(ClubType.DRIVER)

    with pytest.raises(FrozenInstanceError):
        profile.average_smash = 1.5


def test_shot_simulation_defaults_are_immutable():
    with pytest.raises(FrozenInstanceError):
        SHOT_SIMULATION_DEFAULTS.min_ball_speed_mph = 40


@pytest.mark.parametrize("registry", [CLUB_PHYSICS, CLUB_SIMULATION_PROFILES])
def test_registries_are_immutable(registry):
    with pytest.raises(TypeError):
        registry[ClubType.DRIVER] = registry[ClubType.UNKNOWN]
