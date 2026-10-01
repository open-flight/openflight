"""Tests for the OpenFlight phone protocol: payloads, negotiation, size budget and framing."""

import json
from pathlib import Path

import pytest

from openflight.ble.protocol import (
    ACCEPTED_REQUEST_SCHEMAS,
    CONTROL_CHARACTERISTIC_UUID,
    FEATURES,
    FRAGMENT_PAYLOAD_SIZE,
    FRAME_SIZE,
    MAX_MESSAGE_SIZE,
    SCHEMA_VERSION,
    SERVICE_UUID,
    SHOT_CHARACTERISTIC_UUID,
    FragmentReassembler,
    build_event,
    build_hello_result,
    build_power_status_event,
    build_profiles_event,
    build_shot_deleted_event,
    build_shot_event,
    encode_message,
    encode_shot_event,
    fragment_payload,
    parse_fragment,
    reassemble_fragments,
    stable_shot_event_id,
)
from openflight.profiles import MAX_NAME_LENGTH, MAX_PROFILES

FIXTURES = Path(__file__).parent / "fixtures"
SHOT_FIXTURE = FIXTURES / "shot_v2.json"

SHOT_KEYS = {
    "schema_version",
    "type",
    "event_id",
    "timestamp",
    "club",
    "ball_speed_mph",
    "estimated_carry_yards",
    "final",
    "club_speed_mph",
    "smash_factor",
    "launch_angle_vertical",
    "launch_angle_horizontal",
    "spin_rpm",
    "club_path_deg",
    "spin_axis_deg",
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


def test_service_and_characteristics_are_distinct():
    assert len({SERVICE_UUID, SHOT_CHARACTERISTIC_UUID, CONTROL_CHARACTERISTIC_UUID}) == 3


def test_shot_carries_every_documented_field_and_nothing_else():
    event = build_shot_event(_shot_data(), final=True)

    assert set(event) == SHOT_KEYS
    assert event["schema_version"] == SCHEMA_VERSION == 2
    assert event["type"] == "shot"
    assert event["final"] is True
    assert event["enrichment"] is None
    assert event["shot_number"] == 7
    assert event["carry_range"] == [144, 160]
    assert "readings" not in event


def test_missing_measurements_are_explicit_nulls():
    data = _shot_data()
    for key in (
        "club_speed_mph",
        "smash_factor",
        "launch_angle_vertical",
        "launch_angle_horizontal",
        "spin_rpm",
        "club_path_deg",
        "spin_axis_deg",
    ):
        data.pop(key)

    event = build_shot_event(data, final=True)

    for key in ("club_speed_mph", "launch_angle_vertical", "spin_rpm", "spin_axis_deg"):
        assert event[key] is None


def test_missing_session_fields_are_null_and_blanks_become_null():
    data = _shot_data(profile_id="", profile_name="", spin_source="")
    for field in ("shot_number", "carry_range", "launch_angle_confidence"):
        data.pop(field)

    event = build_shot_event(data, final=False, enrichment={"status": "pending"})

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
    provisional = build_shot_event(_shot_data(), final=False, enrichment={"status": "pending"})
    final = build_shot_event(
        _shot_data(ball_speed_mph=107.0), final=True, enrichment={"status": "complete"}
    )
    other_shot = build_shot_event(_shot_data(shot_number=8), final=True)
    other_session = build_shot_event(_shot_data(timestamp="2026-09-26T09:00:00.000001"), final=True)

    assert provisional["event_id"] == final["event_id"] == stable_shot_event_id(_shot_data())
    assert other_shot["event_id"] != final["event_id"]
    assert other_session["event_id"] != final["event_id"]


def test_explicit_event_id_wins():
    assert build_shot_event(_shot_data(), final=True, event_id="given")["event_id"] == "given"


def test_enrichment_status_is_validated():
    with pytest.raises(ValueError):
        build_shot_event(_shot_data(), final=True, enrichment={"status": "done"})

    skipped = build_shot_event(
        _shot_data(), final=True, enrichment={"status": "skipped", "reason": "deadline"}
    )
    assert skipped["enrichment"] == {"status": "skipped", "reason": "deadline"}


def test_encoding_is_compact_sorted_utf8_and_deterministic():
    payload = encode_shot_event(_shot_data(), final=True)

    assert b" " not in payload.replace(b"Zo\xc3\xab", b"")
    assert "Zoë".encode() in payload
    decoded = json.loads(payload.decode("utf-8"))
    assert list(decoded) == sorted(decoded)
    assert payload == encode_shot_event(_shot_data(), final=True)


def test_non_finite_numbers_are_rejected():
    with pytest.raises(ValueError, match="Out of range float values"):
        encode_shot_event(_shot_data(ball_speed_mph=float("nan")), final=True)


def test_shot_deleted_event_carries_the_delete_key():
    assert build_shot_deleted_event("2026-09-25T12:00:00.000001") == {
        "schema_version": 2,
        "type": "shot_deleted",
        "timestamp": "2026-09-25T12:00:00.000001",
    }
    with pytest.raises(ValueError):
        build_shot_deleted_event("")


def test_events_cannot_override_envelope_fields():
    with pytest.raises(ValueError):
        build_event("profiles", {"type": "shot"})
    with pytest.raises(ValueError):
        build_event("not_an_event", {})


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


# -- hello ------------------------------------------------------------------------------


@pytest.mark.parametrize("client_max", [2, 9])
def test_hello_answers_schema_features_and_characteristics(client_max):
    assert build_hello_result(client_max) == {
        "schema_version": 2,
        "features": list(FEATURES),
        "characteristics": {
            "shot": SHOT_CHARACTERISTIC_UUID,
            "control": CONTROL_CHARACTERISTIC_UUID,
        },
    }


def test_hello_features_are_read_and_select_only():
    features = build_hello_result(2)["features"]

    assert "shot_deleted" in features
    assert "delete_shot" not in features
    assert "session_clear" not in features


@pytest.mark.parametrize("bad", [None, "2", 2.0, True, 0, -1, 1])
def test_hello_rejects_clients_that_cannot_speak_schema_2(bad):
    with pytest.raises(ValueError):
        build_hello_result(bad)


def test_requests_may_use_a_version_one_envelope():
    assert ACCEPTED_REQUEST_SCHEMAS == (1, 2)


# -- size budget: every message must fit in 255 fragments of 15 bytes -------------------


def _worst_float():
    # The longest repr a finite double produces.
    return -1.2345678901234567e-300


def test_worst_case_shot_fits_in_one_ble_message():
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
    payload = encode_shot_event(
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

    payload = encode_message(build_profiles_event(snapshot))

    assert len(payload) <= MAX_MESSAGE_SIZE
    fragment_payload(payload, sequence=0)


# -- contract fixture -------------------------------------------------------------------


def test_shot_fixture_is_a_final_shot():
    fixture = json.loads(SHOT_FIXTURE.read_text(encoding="utf-8"))

    assert set(fixture) == SHOT_KEYS
    assert fixture["schema_version"] == 2
    assert fixture["type"] == "shot"
    assert fixture["final"] is True
    assert fixture["event_id"] == stable_shot_event_id(fixture)


def test_shot_fixture_round_trips_through_the_encoder():
    fixture = json.loads(SHOT_FIXTURE.read_text(encoding="utf-8"))

    rebuilt = build_shot_event(fixture, final=fixture["final"], enrichment=fixture["enrichment"])

    assert rebuilt == fixture


# -- framing ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload_size", "expected_frames"),
    [
        (1, 1),
        (FRAGMENT_PAYLOAD_SIZE, 1),
        (FRAGMENT_PAYLOAD_SIZE + 1, 2),
        (FRAGMENT_PAYLOAD_SIZE * 3, 3),
    ],
)
def test_fragment_boundaries(payload_size, expected_frames):
    frames = fragment_payload(b"x" * payload_size, sequence=42)

    assert len(frames) == expected_frames
    assert all(len(frame) <= FRAME_SIZE for frame in frames)
    assert reassemble_fragments(reversed(frames)) == b"x" * payload_size


def test_duplicate_fragment_is_harmless():
    frames = fragment_payload(b"a complete payload", sequence=7)

    assert reassemble_fragments([frames[0], frames[0], *frames[1:]]) == b"a complete payload"


def test_incomplete_message_is_rejected():
    frames = fragment_payload(b"x" * (FRAGMENT_PAYLOAD_SIZE + 1), sequence=8)

    with pytest.raises(ValueError, match="incomplete"):
        reassemble_fragments(frames[:-1])


def test_mixed_sequences_are_rejected():
    first = fragment_payload(b"first message that has chunks", sequence=1)
    second = fragment_payload(b"second message", sequence=2)

    with pytest.raises(ValueError, match="different messages"):
        reassemble_fragments([first[0], second[0]])


def test_invalid_frame_metadata_is_rejected():
    frame = bytearray(fragment_payload(b"payload", sequence=3)[0])
    frame[4] = 0

    with pytest.raises(ValueError, match="metadata"):
        parse_fragment(bytes(frame))


def test_oversized_payload_is_rejected():
    with pytest.raises(ValueError, match="maximum"):
        fragment_payload(b"x" * (MAX_MESSAGE_SIZE + 1), sequence=0)


def test_incremental_reassembler_completes_control_payload():
    payload = json.dumps(
        {
            "schema_version": 2,
            "type": "iwr6843_orientation_calibration",
            "request_id": "request-1",
            "payload": {"mount_tilt_deg": 12.25},
        }
    ).encode()
    reassembler = FragmentReassembler()

    result = None
    for frame in fragment_payload(payload, sequence=42):
        result = reassembler.append(frame) or result

    assert result == payload


def test_incremental_reassembler_recovers_when_a_new_sequence_arrives():
    old_frames = fragment_payload(b"old incomplete payload", sequence=1)
    new_payload = b"new complete payload"
    new_frames = fragment_payload(new_payload, sequence=2)
    reassembler = FragmentReassembler()

    assert reassembler.append(old_frames[0]) is None
    result = None
    for frame in new_frames:
        result = reassembler.append(frame) or result

    assert result == new_payload
