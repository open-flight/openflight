"""Contracts for the boot-time systemd unit."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
UNIT = REPO_ROOT / "scripts/setup/openflight.service"
SETUP = REPO_ROOT / "scripts/setup/setup.sh"


def _unit_lines() -> list[str]:
    return [line.strip() for line in UNIT.read_text(encoding="utf-8").splitlines()]


def _section(name: str) -> list[str]:
    lines = _unit_lines()
    start = lines.index(f"[{name}]") + 1
    body = []
    for line in lines[start:]:
        if line.startswith("["):
            break
        if line:
            body.append(line)
    return body


def test_service_does_not_restart_when_another_instance_owns_the_kiosk():
    """Exit 3 means "someone else is running OpenFlight"; retrying every 5 s is noise."""
    assert "RestartPreventExitStatus=3" in _section("Service")


def test_service_stops_crash_looping_instead_of_retrying_forever():
    """1,100 restarts in 90 minutes each ran cleanup against the desktop session's kiosk."""
    unit = _section("Unit")
    burst = next(line for line in unit if line.startswith("StartLimitBurst="))
    interval = next(line for line in unit if line.startswith("StartLimitIntervalSec="))

    assert int(burst.split("=", 1)[1]) <= 5
    assert int(interval.split("=", 1)[1]) >= 60


def test_every_home_path_in_the_unit_is_rewritten_by_setup():
    """setup.sh only rewrites the project path; any other /home/coleman entry would ship stale."""
    setup = SETUP.read_text(encoding="utf-8")
    assert "s|/home/coleman/openflight|$PROJECT_DIR|g" in setup

    for line in _unit_lines():
        if "/home/coleman" in line:
            assert "/home/coleman/openflight" in line, line
