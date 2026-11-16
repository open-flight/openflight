"""Tests for the optional low-battery shutdown service."""

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GUARD_SCRIPT = PROJECT_ROOT / "scripts" / "battery" / "battery_guard.sh"
INSTALL_SCRIPT = PROJECT_ROOT / "scripts" / "battery" / "install_battery_guard.sh"


def _power_supplies(root: Path, *, online="0", capacity="50", voltage_now="3900000") -> Path:
    battery = root / "battery"
    battery.mkdir(parents=True)
    (battery / "type").write_text("Battery\n", encoding="ascii")
    if capacity is not None:
        (battery / "capacity").write_text(f"{capacity}\n", encoding="ascii")
    if voltage_now is not None:
        (battery / "voltage_now").write_text(f"{voltage_now}\n", encoding="ascii")
    charger = root / "charger"
    charger.mkdir()
    (charger / "type").write_text("Mains\n", encoding="ascii")
    if online is not None:
        (charger / "online").write_text(f"{online}\n", encoding="ascii")
    return root


def _run_guard(root: Path, max_polls: int = 5) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "OPENFLIGHT_BATTERY_GUARD_POWER_SUPPLY_ROOT": str(root),
        "OPENFLIGHT_BATTERY_GUARD_INTERVAL_S": "0",
        "OPENFLIGHT_BATTERY_GUARD_DRY_RUN": "1",
        "OPENFLIGHT_BATTERY_GUARD_MAX_POLLS": str(max_polls),
    }
    return subprocess.run(
        ["bash", str(GUARD_SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_scripts_have_valid_bash_syntax():
    subprocess.run(["bash", "-n", GUARD_SCRIPT], check=True)
    subprocess.run(["bash", "-n", INSTALL_SCRIPT], check=True)


def test_low_percentage_on_battery_shuts_down_after_three_readings(tmp_path):
    result = _run_guard(_power_supplies(tmp_path, capacity="10"))

    assert result.returncode == 0, result.stderr
    assert "(3/3)" in result.stdout
    assert "(4/3)" not in result.stdout
    assert "Dry run: would stop OpenFlight and power off." in result.stdout


def test_low_voltage_on_battery_shuts_down_even_when_percentage_looks_fine(tmp_path):
    result = _run_guard(_power_supplies(tmp_path, capacity="40", voltage_now="3400000"))

    assert result.returncode == 0, result.stderr
    assert "would stop OpenFlight and power off" in result.stdout


def test_low_battery_on_external_power_does_not_shut_down(tmp_path):
    result = _run_guard(_power_supplies(tmp_path, online="1", capacity="5", voltage_now="3300000"))

    assert result.returncode == 0, result.stderr
    assert "Low battery" not in result.stdout
    assert "would stop OpenFlight" not in result.stdout


def test_healthy_battery_does_not_shut_down(tmp_path):
    result = _run_guard(_power_supplies(tmp_path, capacity="11", voltage_now="3410000"))

    assert result.returncode == 0, result.stderr
    assert "Low battery" not in result.stdout


def test_unknown_external_power_does_not_shut_down(tmp_path):
    result = _run_guard(_power_supplies(tmp_path, online=None, capacity="5"))

    assert result.returncode == 0, result.stderr
    assert "External power state is unreadable" in result.stdout
    assert "Low battery" not in result.stdout


def test_unreadable_battery_does_not_shut_down(tmp_path):
    result = _run_guard(_power_supplies(tmp_path, capacity=None, voltage_now=None))

    assert result.returncode == 0, result.stderr
    assert "Battery level is unreadable" in result.stdout
    assert "Low battery" not in result.stdout


def test_installer_unit_runs_the_repository_guard_with_the_config_file():
    result = subprocess.run(
        ["bash", "-c", 'source "$1"; render_unit', "install-test", str(INSTALL_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert f"ExecStart=/bin/bash {GUARD_SCRIPT.as_posix()}" in result.stdout
    assert "EnvironmentFile=-/etc/default/openflight-battery-guard" in result.stdout
    assert "Restart=always" in result.stdout
