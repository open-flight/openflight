"""Tests for the BlueZ configuration script used by the BLE phone app."""

import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "setup" / "configure_bluetooth.sh"
MAIN_SETUP_SCRIPT = PROJECT_ROOT / "scripts" / "setup" / "setup.sh"
BLE_GUIDE = PROJECT_ROOT / "docs" / "ios-ble.md"

# Trimmed from Raspberry Pi OS trixie (BlueZ 5.82). The stock file documents
# `#Client = true` under [CSIS] even though bluetoothd reads it from [GATT].
STOCK_MAIN_CONF = """\
[General]
#ReverseServiceDiscovery = true

[GATT]
#Cache = always

# Export claimed services by plugins
#ExportClaimedServices = read-only

[CSIS]
#Rank = 0

# This enables the GATT client functionally, so it can be disabled in system
# which can only operate as a peripheral.
# Defaults to 'true'.
#Client = true

[AVDTP]
#SessionMode = basic
"""


def _stub_bin(tmp_path: Path) -> Path:
    """Stub sudo (runs the command) and systemctl (records calls, never touches the host)."""
    bin_dir = tmp_path / "bin"
    if bin_dir.exists():
        return bin_dir
    bin_dir.mkdir()
    (bin_dir / "sudo").write_text('#!/bin/sh\nexec "$@"\n', encoding="ascii")
    (bin_dir / "systemctl").write_text(
        f'#!/bin/sh\necho "$*" >> "{tmp_path / "systemctl.log"}"\n'
        '[ "$1" = "is-active" ] && exit 3\nexit 0\n',
        encoding="ascii",
    )
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)
    return bin_dir


def _run(tmp_path: Path, conf: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PATH": f"{_stub_bin(tmp_path)}{os.pathsep}{os.environ['PATH']}",
        "BLUEZ_MAIN_CONF": str(conf),
    }
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def _section(text: str, name: str) -> list[str]:
    """Lines belonging to one INI section, header excluded."""
    lines, inside = [], False
    for line in text.splitlines():
        if line.startswith("["):
            inside = line.strip() == f"[{name}]"
            continue
        if inside:
            lines.append(line)
    return lines


def _systemctl_calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "systemctl.log"
    return log.read_text(encoding="ascii").splitlines() if log.exists() else []


def _backups(conf: Path) -> list[Path]:
    return sorted(conf.parent.glob(f"{conf.name}.openflight-*.bak"))


def test_script_has_valid_bash_syntax():
    subprocess.run(["bash", "-n", SCRIPT], check=True)


def test_check_reports_stock_config_as_unconfigured(tmp_path):
    conf = tmp_path / "main.conf"
    conf.write_text(STOCK_MAIN_CONF, encoding="ascii")

    result = _run(tmp_path, conf, "--check")

    assert result.returncode == 1
    assert conf.read_text(encoding="ascii") == STOCK_MAIN_CONF
    assert not _backups(conf)
    assert not _systemctl_calls(tmp_path)


def test_apply_disables_gatt_client_under_gatt_not_csis(tmp_path):
    conf = tmp_path / "main.conf"
    conf.write_text(STOCK_MAIN_CONF, encoding="ascii")

    result = _run(tmp_path, conf)

    assert result.returncode == 0, result.stderr
    text = conf.read_text(encoding="ascii")
    assert "Client = false" in _section(text, "GATT")
    assert "Client = false" not in _section(text, "CSIS")
    assert "#Client = true" in _section(text, "CSIS")
    assert text.count("Client = false") == 1
    # Everything else is preserved line for line.
    assert [line for line in text.splitlines() if line not in ("Client = false", "")] == [
        line for line in STOCK_MAIN_CONF.splitlines() if line != ""
    ]


def test_apply_backs_up_original_and_restarts_bluetooth(tmp_path):
    conf = tmp_path / "main.conf"
    conf.write_text(STOCK_MAIN_CONF, encoding="ascii")

    result = _run(tmp_path, conf)

    assert result.returncode == 0, result.stderr
    backups = _backups(conf)
    assert len(backups) == 1
    assert backups[0].read_text(encoding="ascii") == STOCK_MAIN_CONF
    assert "restart bluetooth" in _systemctl_calls(tmp_path)
    assert "start-kiosk.sh --ble" in result.stdout


def test_apply_is_idempotent(tmp_path):
    conf = tmp_path / "main.conf"
    conf.write_text(STOCK_MAIN_CONF, encoding="ascii")

    first = _run(tmp_path, conf)
    configured = conf.read_text(encoding="ascii")
    second = _run(tmp_path, conf)
    check = _run(tmp_path, conf, "--check")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert check.returncode == 0, check.stderr
    assert conf.read_text(encoding="ascii") == configured
    assert len(_backups(conf)) == 1
    assert _systemctl_calls(tmp_path).count("restart bluetooth") == 1


def test_apply_replaces_existing_gatt_client_setting(tmp_path):
    conf = tmp_path / "main.conf"
    conf.write_text("[GATT]\nCache = always\nClient=true\n#Client = true\n", encoding="ascii")

    result = _run(tmp_path, conf)

    assert result.returncode == 0, result.stderr
    assert conf.read_text(encoding="ascii") == "[GATT]\nCache = always\nClient = false\n"


def test_apply_appends_gatt_section_when_missing(tmp_path):
    conf = tmp_path / "main.conf"
    conf.write_text("[General]\nName = OpenFlight\n", encoding="ascii")

    result = _run(tmp_path, conf)

    assert result.returncode == 0, result.stderr
    assert conf.read_text(encoding="ascii") == (
        "[General]\nName = OpenFlight\n\n[GATT]\nClient = false\n"
    )


def test_missing_config_fails_without_changes(tmp_path):
    conf = tmp_path / "absent.conf"

    result = _run(tmp_path, conf)

    assert result.returncode != 0
    assert not conf.exists()
    assert not _systemctl_calls(tmp_path)


def test_unknown_option_is_rejected(tmp_path):
    conf = tmp_path / "main.conf"
    conf.write_text(STOCK_MAIN_CONF, encoding="ascii")

    result = _run(tmp_path, conf, "--bogus")

    assert result.returncode != 0
    assert conf.read_text(encoding="ascii") == STOCK_MAIN_CONF


def test_main_setup_offers_bluetooth_configuration():
    setup = MAIN_SETUP_SCRIPT.read_text(encoding="utf-8")

    assert '"$SCRIPT_DIR/configure_bluetooth.sh" --check' in setup
    assert 'confirm "Configure Bluetooth for the iPhone app?' in setup


def test_ble_guide_documents_pairing_prompt_fix():
    guide = BLE_GUIDE.read_text(encoding="utf-8")

    assert "configure_bluetooth.sh" in guide
    assert "Client = false" in guide
