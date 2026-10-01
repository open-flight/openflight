"""Tests for the BLE/SSE schema v2 payloads, negotiation and size budget."""

import json
from pathlib import Path

import pytest

from openflight.ble.protocol import (
    CONTROL_CHARACTERISTIC_UUID,
    CONTROL_V2_CHARACTERISTIC_UUID,
    MAX_MESSAGE_SIZE,
    SERVICE_UUID,
    SHOT_CHARACTERISTIC_UUID,
    SHOT_V2_CHARACTERISTIC_UUID,
    V2_FEATURES,
    build_event_v2,
    build_hello_result,
    build_power_status_event,
    build_profiles_event,
    build_shot_deleted_event,
    build_shot_event,
    build_shot_event_v2,
    encode_message_v2,
    encode_shot_event,
    encode_shot_event_v2,
    fragment_payload,
    stable_shot_event_id,
)
from openflight.profiles import MAX_NAME_LENGTH, MAX_PROFILES

FIXTURES = Path(__file__).parent / "fixtures"
SHOT_V2_FIXTURE = FIXTURES / "shot_v2.json"

V1_KEYS = {
    "schema_version",
    "event_id",
    "timestamp",
    "club",
    "ball_speed_mph",
    "estimated_carry_yards",
    "club_speed_mph",
    "smash_factor",
    "launch_angle_vertical",
    "launch_angle_horizontal",
    "spin_rpm",
    "club_path_deg",
    "spin_axis_deg",
}
V2_EXTRA_KEYS = {
    "type",
    "final",
    "shot_number",
    "profile_id",
    "profile_name",
    "carry_range",
    "spin_source",
    "launch_angle_confidence",
    "enrichment",
}


def _shot_data(**overrides):
    data = {
        "timestamp": "2026-09-25T14:03:07.412345",
        "club": "7-iron",
        "ball_speed_mph": 106.1,
        "club_speed_mph": 83.5,
        "smash_factor": 1.27,
        "estimated_carry_yards": 152,
        "launch_angle_vertical": 21.2,
        "launch_angle_horizontal": -0.7,
        "spin_rpm": 6482,
        "club_path_deg": 2.5,
        "spin_axis_deg": -1.6,
        "shot_number": 7,
        "profile_id": "0f8e4b2a9c7d4e1f8a6b3c5d7e9f1a2b",
        "profile_name": "Zoë",
        "carry_range": [144, 160],
        "spin_source": "measured",
        "launch_angle_confidence": 0.6,
        "readings": [1, 2, 3],
    }
    data.update(overrides)
    return data


def test_v2_characteristics_are_new_and_distinct():
    uuids = {
        SERVICE_UUID,
        SHOT_CHARACTERISTIC_UUID,
        CONTROL_CHARACTERISTIC_UUID,
        SHOT_V2_CHARACTERISTIC_UUID,
        CONTROL_V2_CHARACTERISTIC_UUID,
    }
    assert len(uuids) == 5


def test_v2_shot_is_v1_fields_plus_v2_fields():
    event = build_shot_event_v2(_shot_data(), final=True)

    assert set(event) == V1_KEYS | V2_EXTRA_KEYS
    assert event["schema_version"] == 2
    assert event["type"] == "shot"
    assert event["final"] is True
    assert event["enrichment"] is None
    assert event["shot_number"] == 7
    assert event["carry_range"] == [144, 160]
    assert "readings" not in event


def test_v1_shot_encoding_is_unchanged_by_v2():
    """The version-one builder must not grow v2 fields."""
    event = build_shot_event(_shot_data(), event_id="B0D91F0A-7950-4D7E-9DD5-AF9777C190E1")

    assert set(event) == V1_KEYS
    assert event["schema_version"] == 1
    assert encode_shot_event(_shot_data(), event_id="x").isascii()


def test_missing_v2_fields_are_explicit_nulls_and_blanks_become_null():
    data = _shot_data(profile_id="", profile_name="", spin_source="")
    for field in ("shot_number", "carry_range", "launch_angle_confidence"):
        data.pop(field)

    event = build_shot_event_v2(data, final=False, enrichment={"status": "pending"})

    for field in (
        "shot_number",
        "profile_id",
        "profile_name",
        "carry_range",
        "spin_source",
        "launch_angle_confidence",
    ):
        assert event[field] is None
    assert event["enrichment"] == {"status": "pending"}


def test_provisional_and_final_share_a_stable_event_id():
    provisional = build_shot_event_v2(_shot_data(), final=False, enrichment={"status": "pending"})
    final = build_shot_event_v2(
        _shot_data(ball_speed_mph=107.0), final=True, enrichment={"status": "complete"}
    )
    other_shot = build_shot_event_v2(_shot_data(shot_number=8), final=True)
    other_session = build_shot_event_v2(
        _shot_data(timestamp="2026-09-26T09:00:00.000001"), final=True
    )

    assert provisional["event_id"] == final["event_id"] == stable_shot_event_id(_shot_data())
    assert other_shot["event_id"] != final["event_id"]
    assert other_session["event_id"] != final["event_id"]


def test_enrichment_status_is_validated():
    with pytest.raises(ValueError):
        build_shot_event_v2(_shot_data(), final=True, enrichment={"status": "done"})

    skipped = build_shot_event_v2(
        _shot_data(), final=True, enrichment={"status": "skipped", "reason": "deadline"}
    )
    assert skipped["enrichment"] == {"status": "skipped", "reason": "deadline"}


def test_v2_encoding_is_compact_sorted_utf8():
    payload = encode_shot_event_v2(_shot_data(), final=True)

    assert b" " not in payload.replace(b"Zo\xc3\xab", b"")
    assert "Zoë".encode() in payload
    decoded = json.loads(payload.decode("utf-8"))
    assert list(decoded) == sorted(decoded)


def test_shot_deleted_event_carries_the_delete_key():
    assert build_shot_deleted_event("2026-09-25T12:00:00.000001") == {
        "schema_version": 2,
        "type": "shot_deleted",
        "timestamp": "2026-09-25T12:00:00.000001",
    }
    with pytest.raises(ValueError):
        build_shot_deleted_event("")


def test_hello_features_are_read_and_select_only():
    features = build_hello_result(2)["features"]

    assert "shot_deleted" in features
    assert "delete_shot" not in features
    assert "session_clear" not in features


def test_v2_events_cannot_override_envelope_fields():
    with pytest.raises(ValueError):
        build_event_v2("profiles", {"type": "shot"})
    with pytest.raises(ValueError):
        build_event_v2("not_an_event", {})


def test_power_status_event_flattens_the_socketio_payload():
    status = {"available": False, "provider": "geekworm", "state": "unavailable"}

    assert build_power_status_event(status) == {
        "schema_version": 2,
        "type": "power_status",
        **status,
    }


def test_profiles_event_keeps_ids_and_names_only():
    event = build_profiles_event(
        {
            "profiles": [
                {"id": "a", "name": "A", "created_at": "2026", "settings": {"x": 1}},
            ],
            "active_profile_id": "a",
        }
    )

    assert event == {
        "schema_version": 2,
        "type": "profiles",
        "profiles": [{"id": "a", "name": "A"}],
        "active_profile_id": "a",
    }


@pytest.mark.parametrize(
    ("client_max", "expected_schema"),
    [(1, 1), (2, 2), (9, 2)],
)
def test_hello_negotiates_the_highest_common_schema(client_max, expected_schema):
    result = build_hello_result(client_max)

    assert result["schema_version"] == expected_schema
    if expected_schema == 2:
        assert result["features"] == list(V2_FEATURES)
        assert result["characteristics"] == {
            "shot": SHOT_V2_CHARACTERISTIC_UUID,
            "control": CONTROL_V2_CHARACTERISTIC_UUID,
        }
    else:
        assert result == {"schema_version": 1, "features": []}


@pytest.mark.parametrize("bad", [None, "2", 2.0, True, 0, -1])
def test_hello_rejects_invalid_client_schema(bad):
    with pytest.raises(ValueError):
        build_hello_result(bad)


# -- size budget: every v2 message must fit in 255 fragments of 15 bytes ----------


def _worst_float():
    # The longest repr a finite double produces.
    return -1.2345678901234567e-300


def test_worst_case_v2_shot_fits_in_one_ble_message():
    worst_name = "\x01" * MAX_NAME_LENGTH  # six bytes per character once escaped
    data = {
        "timestamp": "2026-09-25T14:03:07.412345",
        "club": "3-hybrid",
        "ball_speed_mph": _worst_float(),
        "club_speed_mph": _worst_float(),
        "smash_factor": _worst_float(),
        "estimated_carry_yards": _worst_float(),
        "launch_angle_vertical": _worst_float(),
        "launch_angle_horizontal": _worst_float(),
        "spin_rpm": _worst_float(),
        "club_path_deg": _worst_float(),
        "spin_axis_deg": _worst_float(),
        "shot_number": 2**63,
        "profile_id": "f" * 64,
        "profile_name": worst_name,
        "carry_range": [_worst_float(), _worst_float()],
        "spin_source": "calculated_from_launch_angle",
        "launch_angle_confidence": _worst_float(),
    }
    payload = encode_shot_event_v2(
        data,
        final=False,
        enrichment={"status": "skipped", "reason": "worker_unavailable"},
    )

    assert len(payload) <= MAX_MESSAGE_SIZE
    assert len(fragment_payload(payload, sequence=0)) <= 255


@pytest.mark.parametrize("worst_char", ["\x01", "\U0001f3cc", '"', "\\"])
def test_twelve_worst_case_profiles_fit_in_one_ble_message(worst_char):
    snapshot = {
        "profiles": [
            {
                "id": f"{index:032x}",  # ProfileStore ids are uuid4().hex
                "name": worst_char * MAX_NAME_LENGTH,
                "created_at": "2026-09-25T12:00:00Z",
                "settings": {"not_sent": "x" * 10_000},
            }
            for index in range(MAX_PROFILES)
        ],
        "active_profile_id": f"{0:032x}",
    }

    payload = encode_message_v2(build_profiles_event(snapshot))

    assert len(payload) <= MAX_MESSAGE_SIZE
    fragment_payload(payload, sequence=0)


# -- contract fixture ---------------------------------------------------------------


def test_shot_v2_fixture_is_a_final_v2_shot():
    fixture = json.loads(SHOT_V2_FIXTURE.read_text(encoding="utf-8"))

    assert set(fixture) == V1_KEYS | V2_EXTRA_KEYS
    assert fixture["schema_version"] == 2
    assert fixture["type"] == "shot"
    assert fixture["final"] is True
    assert fixture["event_id"] == stable_shot_event_id(fixture)


def test_shot_v2_fixture_round_trips_through_the_encoder():
    fixture = json.loads(SHOT_V2_FIXTURE.read_text(encoding="utf-8"))

    rebuilt = build_shot_event_v2(fixture, final=fixture["final"], enrichment=fixture["enrichment"])

    assert rebuilt == fixture
