#!/usr/bin/env python3
"""Write cross-language BLE golden files to ``tests/fixtures/ble_goldens``.

Each server-to-client golden holds one message exactly as OpenFlight notifies
it: the decoded JSON, the UTF-8 payload and every 20-byte frame, all as hex.
Client implementations (the Swift app, the Kotlin Multiplatform app) decode
these frames in their own tests, and ``tests/test_ble_goldens.py`` fails when
the committed files drift from what the encoder produces now.

Files whose ``direction`` is ``client_to_server`` are inputs, not outputs:
clients commit the frames their encoders produce, and the Python tests
reassemble and dispatch them. This script never rewrites them.

Usage::

    uv run python scripts/ble/generate_goldens.py            # rewrite goldens
    uv run python scripts/ble/generate_goldens.py --check    # exit 1 on drift
    uv run python scripts/ble/generate_goldens.py --refresh-shot-fixture
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime
from pathlib import Path

from openflight.ble.protocol import (
    CONTROL_CHARACTERISTIC_UUID,
    SHOT_CHARACTERISTIC_UUID,
    build_club_event,
    build_control_response,
    build_hello_result,
    build_power_status_event,
    build_profiles_event,
    build_session_cleared_event,
    build_shot_deleted_event,
    build_shot_event,
    build_shot_processing_event,
    encode_message,
    fragment_payload,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
GOLDENS_DIR = FIXTURES / "ble_goldens"
# "v2" names the wire schema (``schema_version: 2``); client test suites use these names.
SHOT_V2_FIXTURE = FIXTURES / "shot_v2.json"

REQUEST_ID = "5E0F2C4A-8B1D-4C3E-9F6A-7D2B1C0E9A84"
PROFILE_A = "0f8e4b2a9c7d4e1f8a6b3c5d7e9f1a2b"
PROFILE_B = "7c1d9e3f5a2b4c6d8e0f1a3b5c7d9e1f"


def build_shot_v2_fixture() -> dict:
    """Build the shot contract fixture from a real (seeded) mock shot."""
    from openflight import server  # pylint: disable=import-outside-toplevel
    from openflight.launch_monitor import ClubType  # pylint: disable=import-outside-toplevel

    random.seed(2026)
    monitor = server.MockLaunchMonitor()
    monitor.set_club(ClubType.IRON_7)
    shot = monitor.simulate_shot()
    shot.timestamp = datetime(2026, 9, 25, 14, 3, 7, 412345)
    shot.shot_number = 7
    shot.profile_id = PROFILE_A
    shot.profile_name = "Zoë"
    return build_shot_event(
        server.shot_to_dict(shot),
        final=True,
        enrichment={"status": "complete"},
    )


def _golden(name: str, description: str, characteristic: str, message: dict, sequence: int) -> dict:
    payload = encode_message(message)
    return {
        "name": name,
        "description": description,
        "direction": "server_to_client",
        "characteristic": characteristic,
        "schema_version": message["schema_version"],
        "sequence": sequence,
        "message": message,
        "payload_hex": payload.hex(),
        "frames_hex": [frame.hex() for frame in fragment_payload(payload, sequence=sequence)],
    }


def worst_case_profiles_snapshot() -> dict:
    """Twelve profiles with the longest-escaping 40-character names allowed."""
    return {
        "profiles": [
            {
                "id": f"{index:032x}",
                "name": "\x01" * 40,
                "created_at": "2026-09-25T12:00:00Z",
                "settings": {"ignored": "x" * 500},
            }
            for index in range(12)
        ],
        "active_profile_id": f"{0:032x}",
    }


def build_goldens() -> dict[str, dict]:
    """Every server-to-client golden, keyed by file stem."""
    shot_v2 = json.loads(SHOT_V2_FIXTURE.read_text(encoding="utf-8"))
    provisional = build_shot_event(
        shot_v2,
        final=False,
        enrichment={"status": "pending"},
        event_id=shot_v2["event_id"],
    )
    profiles = {
        "profiles": [
            {"id": PROFILE_A, "name": "Zoë ⛳", "created_at": "2026-09-01T08:00:00Z"},
            {"id": PROFILE_B, "name": "Sam", "created_at": "2026-09-02T08:00:00Z"},
        ],
        "active_profile_id": PROFILE_A,
    }
    power = {
        "available": True,
        "provider": "geekworm",
        "state": "on_battery",
        "battery_percent": 76.5,
        "battery_voltage_v": 3.98,
        "external_power": False,
        "updated_at": "2026-09-25T14:03:05.000000+00:00",
        "error": None,
    }

    goldens = [
        _golden(
            "v2_shot_provisional",
            "Provisional OPS-only v2 shot (final:false); same event_id as v2_shot_final.",
            SHOT_CHARACTERISTIC_UUID,
            provisional,
            0,
        ),
        _golden(
            "v2_shot_final",
            "Final v2 shot (tests/fixtures/shot_v2.json).",
            SHOT_CHARACTERISTIC_UUID,
            shot_v2,
            1,
        ),
        _golden(
            "v2_response_hello",
            "hello answered on the v2 control characteristic.",
            CONTROL_CHARACTERISTIC_UUID,
            build_control_response(REQUEST_ID, result=build_hello_result(2)),
            0,
        ),
        _golden(
            "v2_response_set_active_profile",
            "set_active_profile accepted.",
            CONTROL_CHARACTERISTIC_UUID,
            build_control_response(
                REQUEST_ID,
                result={"status": "applied", "active_profile_id": PROFILE_B},
            ),
            1,
        ),
        _golden(
            "v2_response_error",
            "A failed v2 command.",
            CONTROL_CHARACTERISTIC_UUID,
            build_control_response(REQUEST_ID, error="Unknown profile"),
            2,
        ),
        _golden(
            "v2_event_shot_processing",
            "shot_processing notify (capturing | calculating | failed).",
            CONTROL_CHARACTERISTIC_UUID,
            build_shot_processing_event("calculating"),
            3,
        ),
        _golden(
            "v2_event_profiles",
            "profiles notify: ids and names only, UTF-8 (not \\u-escaped) names.",
            CONTROL_CHARACTERISTIC_UUID,
            build_profiles_event(profiles),
            4,
        ),
        _golden(
            "v2_event_profiles_worst_case",
            "Twelve 40-character names that each escape to six bytes per character: "
            "the largest profiles event the server can send.",
            CONTROL_CHARACTERISTIC_UUID,
            build_profiles_event(worst_case_profiles_snapshot()),
            5,
        ),
        _golden(
            "v2_event_power_status",
            "power_status notify: the Socket.IO payload beside type.",
            CONTROL_CHARACTERISTIC_UUID,
            build_power_status_event(power),
            6,
        ),
        _golden(
            "v2_event_session_cleared",
            "session_cleared notify.",
            CONTROL_CHARACTERISTIC_UUID,
            build_session_cleared_event(PROFILE_A),
            7,
        ),
        _golden(
            "v2_event_shot_deleted",
            "shot_deleted notify: a shot was deleted (over Wi-Fi/Socket.IO); key is its timestamp.",
            CONTROL_CHARACTERISTIC_UUID,
            build_shot_deleted_event(shot_v2["timestamp"]),
            9,
        ),
        _golden(
            "v2_event_club_changed",
            "club_changed notify on the v2 control characteristic.",
            CONTROL_CHARACTERISTIC_UUID,
            build_club_event("7-iron"),
            8,
        ),
    ]
    return {golden["name"]: golden for golden in goldens}


def render(golden: dict) -> str:
    return json.dumps(golden, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if files differ")
    parser.add_argument(
        "--refresh-shot-fixture",
        action="store_true",
        help="rebuild tests/fixtures/shot_v2.json from a seeded mock shot first",
    )
    args = parser.parse_args(argv)

    if args.refresh_shot_fixture:
        SHOT_V2_FIXTURE.write_text(
            json.dumps(build_shot_v2_fixture(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )

    GOLDENS_DIR.mkdir(parents=True, exist_ok=True)
    drift = []
    for name, golden in build_goldens().items():
        path = GOLDENS_DIR / f"{name}.json"
        text = render(golden)
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                drift.append(path.name)
        else:
            path.write_text(text, encoding="utf-8")
    if drift:
        print("BLE goldens out of date: " + ", ".join(drift), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
