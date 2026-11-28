"""Tests for opengolfsim.codec and club mapping."""

import json

from openflight.launch_monitor import ClubType
from openflight.opengolfsim.clubs import ogs_club_to_club
from openflight.opengolfsim.codec import OpenGolfSimCodec
from openflight.sim.types import PlayerUpdate, ResolvedShot, ShotAck, SimError


def _resolved(**kw) -> ResolvedShot:
    base = dict(
        shot_number=1,
        ball_speed_mph=135.0,
        vla=11.1,
        hla=1.2,
        total_spin_rpm=4800.0,
        spin_axis_deg=-2.5,
        back_spin_rpm=4795.0,
        side_spin_rpm=-209.0,
        carry_yards=240.0,
        club_path_deg=0.0,
        club=ClubType.DRIVER,
        club_speed_mph=None,
        provenance={},
    )
    base.update(kw)
    return ResolvedShot(**base)


def _shot_obj(codec, resolved):
    return json.loads(codec.build_shot(resolved).decode("utf-8"))


def test_build_shot_envelope_and_fields():
    obj = _shot_obj(OpenGolfSimCodec(), _resolved())
    assert obj["type"] == "shot"
    # Imperial is the default and is sent without a "unit" field (per docs).
    assert "unit" not in obj
    shot = obj["shot"]
    assert shot["ballSpeed"] == 135.0
    assert shot["verticalLaunchAngle"] == 11.1
    assert shot["horizontalLaunchAngle"] == 1.2
    assert shot["spinAxis"] == -2.5
    assert shot["spinSpeed"] == 4800
    assert isinstance(shot["spinSpeed"], int)


def test_build_shot_omits_club_and_carry():
    shot = _shot_obj(OpenGolfSimCodec(), _resolved())["shot"]
    assert "carryDistance" not in shot
    assert "clubSpeed" not in shot
    assert "backSpin" not in shot


def test_speed_always_sent_imperial():
    # ResolvedShot speeds are mph. OGS 1.21.2 multiplies ballSpeed by 2.237 when
    # "unit" is "metric", so tagging mph as metric would more than double it.
    # The codec must never send a "unit" field, whatever the config says.
    obj = _shot_obj(OpenGolfSimCodec(units="metric"), _resolved())
    assert "unit" not in obj
    assert obj["shot"]["ballSpeed"] == 135.0


def test_no_heartbeat():
    assert OpenGolfSimCodec().heartbeat_bytes() is None


def test_on_connect_sends_device_ready():
    obj = json.loads(OpenGolfSimCodec().on_connect_bytes().decode("utf-8"))
    assert obj == {"type": "device", "status": "ready"}


def test_fields_for_target_excludes_club_and_carry():
    fields = OpenGolfSimCodec().fields_for_target()
    assert "ball_speed" in fields and "total_spin" in fields
    assert "carry" not in fields and "club_speed" not in fields


def test_parse_player_update_documented_shape():
    # Exact shape from the OpenGolfSim docs: club nested under "data".
    raw = json.dumps(
        {
            "type": "player",
            "data": {
                "playerId": "b79b745b-e643-4b28-be4e-f4ad4690907a",
                "currentPosition": {"x": 0, "y": 0, "z": 0},
                "club": {"name": "3W", "id": "3W", "distance": 205},
            },
        }
    ).encode()
    events = OpenGolfSimCodec().parse_inbound(raw)
    assert len(events) == 1
    assert isinstance(events[0], PlayerUpdate)
    assert events[0].club is ClubType.WOOD_3


def test_parse_player_update_iron_by_id():
    raw = json.dumps({"type": "player", "data": {"club": {"name": "7 Iron", "id": "7I"}}}).encode()
    events = OpenGolfSimCodec().parse_inbound(raw)
    assert events[0].club is ClubType.IRON_7


def test_parse_player_update_flat_fallback():
    # Defensive: a flat object (no "data" wrapper) still parses.
    raw = json.dumps({"type": "player", "club": {"id": "DR"}}).encode()
    events = OpenGolfSimCodec().parse_inbound(raw)
    assert events[0].club is ClubType.DRIVER


def test_parse_shot_echo_is_ack():
    # OGS 1.21.2 (lib/developerAPI.js) echoes every accepted shot to all clients
    # as {"type":"shot","shot":{...}}, newline-terminated.
    raw = b'{"type":"shot","shot":{"ballSpeed":120,"verticalLaunchAngle":16.5}}\n'
    events = OpenGolfSimCodec().parse_inbound(raw)
    assert len(events) == 1 and isinstance(events[0], ShotAck) and events[0].ok


def test_parse_result_is_not_a_second_ack():
    # The later {"type":"result"} event (carry/total in meters) must not ack twice.
    raw = json.dumps(
        {"type": "result", "data": {"result": {"carry": 150.2}, "sessionId": "x"}}
    ).encode()
    assert OpenGolfSimCodec().parse_inbound(raw) == []


def test_parse_rejection_is_sim_error():
    # What OGS 1.21.2 sends for any payload without a known "type" (e.g. OpenConnect V1).
    raw = b'{"status":400,"error":"Unable to parse payload"}\n'
    events = OpenGolfSimCodec().parse_inbound(raw)
    assert len(events) == 1 and isinstance(events[0], SimError)
    assert "Unable to parse payload" in events[0].message


def test_parse_unknown_type_ignored():
    assert OpenGolfSimCodec().parse_inbound(json.dumps({"type": "ping"}).encode()) == []


def test_parse_malformed_raises_valueerror():
    import pytest

    with pytest.raises(ValueError):
        OpenGolfSimCodec().parse_inbound(b"{not json")


# --- club mapping ------------------------------------------------------------


def test_club_mapping_names():
    assert ogs_club_to_club("Driver") is ClubType.DRIVER
    assert ogs_club_to_club("Pitching Wedge") is ClubType.PW
    assert ogs_club_to_club("7 Iron") is ClubType.IRON_7
    assert ogs_club_to_club("7i") is ClubType.IRON_7
    assert ogs_club_to_club("i7") is ClubType.IRON_7
    assert ogs_club_to_club("3 Wood") is ClubType.WOOD_3


def test_club_mapping_dict_form():
    assert ogs_club_to_club({"name": "Sand Wedge", "id": "SW"}) is ClubType.SW
    assert ogs_club_to_club({"id": "DR"}) is ClubType.DRIVER


def test_club_mapping_putter_and_unknown():
    assert ogs_club_to_club("Putter") is ClubType.UNKNOWN
    assert ogs_club_to_club("frisbee") is ClubType.UNKNOWN


def test_club_mapping_none_is_none():
    assert ogs_club_to_club(None) is None
    assert ogs_club_to_club({}) is None


# --- registry ------------------------------------------------------------------


def test_registry_maps_opengolfsim_to_native_codec():
    from openflight.sim.codec import _codec_for
    from openflight.sim.config import ConnectorConfig

    cfg = ConnectorConfig(
        type="opengolfsim",
        enabled=True,
        host="127.0.0.1",
        port=3111,
        device_id="OpenFlight",
        units="Yards",
        heartbeat_interval_s=5.0,
    )
    codec = _codec_for(cfg)
    assert isinstance(codec, OpenGolfSimCodec)
    assert json.loads(codec.build_shot(_resolved()))["type"] == "shot"
