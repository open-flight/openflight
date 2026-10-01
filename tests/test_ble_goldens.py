"""Cross-language BLE goldens in tests/fixtures/ble_goldens.

Server-to-client files must match what the encoder produces today (regenerate
with ``uv run python scripts/ble/generate_goldens.py``). Client-to-server files
are committed by client implementations; they must reassemble to their JSON
and be answered as their ``expect`` block says.
"""

import importlib.util
import json
from pathlib import Path

import pytest
from ble_harness import PHONE_UUIDS, server_loopback

from openflight.ble.protocol import (
    CONTROL_CHARACTERISTIC_UUID,
    FRAME_SIZE,
    MAX_MESSAGE_SIZE,
    SHOT_CHARACTERISTIC_UUID,
    parse_fragment,
    reassemble_fragments,
)

ROOT = Path(__file__).resolve().parents[1]
GOLDENS_DIR = ROOT / "tests" / "fixtures" / "ble_goldens"


def _load_generator():
    path = ROOT / "scripts" / "ble" / "generate_goldens.py"
    spec = importlib.util.spec_from_file_location("generate_ble_goldens", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GENERATOR = _load_generator()


def _files(direction):
    for path in sorted(GOLDENS_DIR.glob("*.json")):
        golden = json.loads(path.read_text(encoding="utf-8"))
        if golden["direction"] == direction:
            yield pytest.param(golden, id=path.stem)


def test_committed_server_goldens_match_the_encoder():
    expected = GENERATOR.build_goldens()
    committed = {
        path.stem: path.read_text(encoding="utf-8")
        for path in GOLDENS_DIR.glob("*.json")
        if json.loads(path.read_text(encoding="utf-8"))["direction"] == "server_to_client"
    }

    assert set(committed) == set(expected)
    for name, golden in expected.items():
        assert committed[name] == GENERATOR.render(golden), (
            f"{name}.json is stale; run scripts/ble/generate_goldens.py"
        )


@pytest.mark.parametrize("golden", list(_files("server_to_client")))
def test_server_golden_frames_decode_to_their_message(golden):
    frames = [bytes.fromhex(item) for item in golden["frames_hex"]]
    payload = bytes.fromhex(golden["payload_hex"])

    assert all(len(frame) <= FRAME_SIZE for frame in frames)
    assert {parse_fragment(frame)[0] for frame in frames} == {golden["sequence"]}
    assert reassemble_fragments(reversed(frames)) == payload
    assert len(payload) <= MAX_MESSAGE_SIZE
    assert json.loads(payload.decode("utf-8")) == golden["message"]
    assert golden["message"]["schema_version"] == golden["schema_version"]


def test_goldens_are_schema_2_utf8_on_the_phone_characteristics():
    for golden in GENERATOR.build_goldens().values():
        assert golden["schema_version"] == 2
        assert golden["characteristic"] in (SHOT_CHARACTERISTIC_UUID, CONTROL_CHARACTERISTIC_UUID)
    profiles = GENERATOR.build_goldens()["v2_event_profiles"]
    assert "Zoë ⛳".encode() in bytes.fromhex(profiles["payload_hex"])


def test_shot_goldens_share_one_event_id():
    goldens = GENERATOR.build_goldens()
    provisional = goldens["v2_shot_provisional"]["message"]
    final = goldens["v2_shot_final"]["message"]

    assert provisional["event_id"] == final["event_id"]
    assert (provisional["final"], final["final"]) == (False, True)


@pytest.mark.parametrize("golden", list(_files("client_to_server")))
def test_client_golden_reassembles_and_is_dispatched(golden, monkeypatch, tmp_path):
    frames = [bytes.fromhex(item) for item in golden["frames_hex"]]
    payload = reassemble_fragments(frames)
    assert payload == bytes.fromhex(golden["payload_hex"])
    assert json.loads(payload) == golden["message"]

    with server_loopback(monkeypatch, tmp_path) as loopback:
        phone = loopback.central("golden-client")
        phone.subscribe(*PHONE_UUIDS)
        for frame in frames:
            loopback.write(golden["characteristic"], frame)
        response = phone.wait_for(
            golden["characteristic"],
            lambda message: message.get("request_id") == golden["message"]["request_id"],
        )

    expect = golden["expect"]
    assert response["ok"] is expect["ok"]
    assert response["schema_version"] == expect["schema_version"]
    for key, value in expect.get("result", {}).items():
        assert response["result"][key] == value
