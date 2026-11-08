"""Tests for the Raspberry Pi 5 RTC backup-battery charging script."""

import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RTC_SCRIPT = PROJECT_ROOT / "scripts" / "setup" / "enable_rtc_battery_charging.sh"
MAIN_SETUP_SCRIPT = PROJECT_ROOT / "scripts" / "setup" / "setup.sh"
SETUP_GUIDE = PROJECT_ROOT / "docs" / "setup" / "raspberry-pi.md"
CHARGE_LINE = "dtparam=rtc_bbat_vchg=3000000"


def _run_function(function: str, config_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; "$2" "$3"',
            "rtc-test",
            str(RTC_SCRIPT),
            function,
            str(config_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def _run_script(
    *args: str, config_path: Path, sysfs: Path, stdin: str = ""
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "OPENFLIGHT_RTC_ALLOW_NON_PI": "1",
        "OPENFLIGHT_RTC_BOOT_CONFIG": str(config_path),
        "OPENFLIGHT_RTC_SYSFS": str(sysfs),
    }
    return subprocess.run(
        ["bash", str(RTC_SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
        input=stdin,
        env=env,
    )


def test_script_has_valid_bash_syntax():
    subprocess.run(["bash", "-n", RTC_SCRIPT], check=True)


def test_enable_adds_charging_line_once_and_is_idempotent(tmp_path):
    config = tmp_path / "config.txt"
    config.write_text("[cm5]\nfoo=bar\n", encoding="ascii")

    first = _run_function("update_boot_config", config)
    first_content = config.read_text(encoding="ascii")
    second = _run_function("update_boot_config", config)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert config.read_text(encoding="ascii") == first_content
    assert first_content.count(CHARGE_LINE) == 1
    assert first_content.startswith("[cm5]\nfoo=bar\n")
    # Appended under [all] so a preceding conditional section cannot hide it.
    assert first_content.endswith(
        f"\n[all]\n# OpenFlight: charge the rechargeable RTC backup battery\n{CHARGE_LINE}\n"
    )


def test_enable_replaces_a_different_charging_voltage(tmp_path):
    config = tmp_path / "config.txt"
    config.write_text(
        "dtparam=audio=on\ndtparam=rtc_bbat_vchg=4400000\n",
        encoding="ascii",
    )

    result = _run_function("update_boot_config", config)
    content = config.read_text(encoding="ascii")

    assert result.returncode == 0, result.stderr
    assert "rtc_bbat_vchg=4400000" not in content
    assert content.count("rtc_bbat_vchg") == 1
    assert CHARGE_LINE in content
    assert "dtparam=audio=on" in content


def test_enable_deduplicates_repeated_charging_lines(tmp_path):
    config = tmp_path / "config.txt"
    config.write_text(f"{CHARGE_LINE}\n{CHARGE_LINE}\n", encoding="ascii")

    result = _run_function("update_boot_config", config)

    assert result.returncode == 0, result.stderr
    assert config.read_text(encoding="ascii").count("rtc_bbat_vchg") == 1


def test_enable_fails_when_config_is_missing(tmp_path):
    result = _run_function("update_boot_config", tmp_path / "missing.txt")

    assert result.returncode != 0
    assert "not found" in result.stderr


def test_disable_removes_every_charging_line_and_comment(tmp_path):
    config = tmp_path / "config.txt"
    config.write_text("dtparam=audio=on\n", encoding="ascii")
    assert _run_function("update_boot_config", config).returncode == 0
    with config.open("a", encoding="ascii") as handle:
        handle.write("#dtparam=rtc_bbat_vchg=2000000\n")

    result = _run_function("disable_boot_config", config)
    content = config.read_text(encoding="ascii")

    assert result.returncode == 0, result.stderr
    assert "rtc_bbat_vchg" not in content
    assert "OpenFlight: charge" not in content
    assert content == "dtparam=audio=on\n"


def test_enable_disable_cycles_do_not_accumulate_sections(tmp_path):
    config = tmp_path / "config.txt"
    original = "dtparam=audio=on\n[pi5]\ndtoverlay=foo\n"
    config.write_text(original, encoding="ascii")

    for _ in range(3):
        assert _run_function("update_boot_config", config).returncode == 0
        assert _run_function("disable_boot_config", config).returncode == 0

    assert config.read_text(encoding="ascii") == original


def test_disable_keeps_a_user_owned_all_section(tmp_path):
    config = tmp_path / "config.txt"
    config.write_text("[all]\ndtparam=rtc_bbat_vchg=3000000\narm_boost=1\n", encoding="ascii")

    result = _run_function("disable_boot_config", config)

    assert result.returncode == 0, result.stderr
    assert config.read_text(encoding="ascii") == "[all]\narm_boost=1\n"


def test_disable_leaves_unrelated_config_untouched(tmp_path):
    config = tmp_path / "config.txt"
    original = "dtparam=audio=on\n[all]\ndtoverlay=vc4-kms-v3d\n"
    config.write_text(original, encoding="ascii")

    result = _run_function("disable_boot_config", config)

    assert result.returncode == 0, result.stderr
    assert config.read_text(encoding="ascii") == original


def test_enable_requires_typed_rechargeable_confirmation(tmp_path):
    """Without a TTY or --yes the script must refuse rather than guess."""
    config = tmp_path / "config.txt"
    config.write_text("dtparam=audio=on\n", encoding="ascii")

    result = _run_script(config_path=config, sysfs=tmp_path, stdin="rechargeable\n")

    assert result.returncode != 0
    assert "rtc_bbat_vchg" not in config.read_text(encoding="ascii")
    assert "RECHARGEABLE RTC BATTERY ONLY" in result.stdout


def test_enable_with_yes_still_prints_the_battery_warning(tmp_path, monkeypatch):
    config = tmp_path / "config.txt"
    config.write_text("dtparam=audio=on\n", encoding="ascii")
    # as_root uses sudo when not root; stub it so the test never escalates.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "sudo").write_text('#!/bin/sh\nexec "$@"\n', encoding="ascii")
    (bin_dir / "sudo").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    result = _run_script("--yes", config_path=config, sysfs=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "RECHARGEABLE RTC BATTERY ONLY" in result.stdout
    assert "CR2032" in result.stdout
    assert config.read_text(encoding="ascii").count(CHARGE_LINE) == 1
    assert list(tmp_path.glob("config.txt.pre-openflight-rtc-*")), "backup not written"


def test_verify_reports_charging_state(tmp_path):
    config = tmp_path / "config.txt"
    config.write_text(f"{CHARGE_LINE}\n", encoding="ascii")
    (tmp_path / "charging_voltage").write_text("3000000\n", encoding="ascii")

    on = _run_script("--verify", config_path=config, sysfs=tmp_path)
    (tmp_path / "charging_voltage").write_text("0\n", encoding="ascii")
    off = _run_script("--verify", config_path=config, sysfs=tmp_path)

    assert on.returncode == 0, on.stderr
    assert "charging is ON" in on.stdout
    assert off.returncode == 0, off.stderr
    assert "charging is OFF" in off.stdout


def test_verify_fails_without_rtc_sysfs(tmp_path):
    config = tmp_path / "config.txt"
    config.write_text("", encoding="ascii")

    result = _run_script("--verify", config_path=config, sysfs=tmp_path / "absent")

    assert result.returncode != 0
    assert "Cannot read" in result.stderr


def test_rejects_non_pi_5(tmp_path):
    model = tmp_path / "model"
    model.write_text("Raspberry Pi 4 Model B Rev 1.5\0", encoding="ascii")
    env = {
        **os.environ,
        "OPENFLIGHT_RTC_PI_MODEL_PATH": str(model),
        "OPENFLIGHT_RTC_BOOT_CONFIG": str(tmp_path / "config.txt"),
    }
    env.pop("OPENFLIGHT_RTC_ALLOW_NON_PI", None)

    result = subprocess.run(
        ["bash", str(RTC_SCRIPT), "--yes"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "Raspberry Pi 5" in result.stderr


def test_help_warns_rechargeable_only():
    result = subprocess.run(
        ["bash", str(RTC_SCRIPT), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "RECHARGEABLE" in result.stdout
    assert "CR2032" in result.stdout


def test_main_setup_asks_about_rechargeable_rtc_battery():
    setup = MAIN_SETUP_SCRIPT.read_text(encoding="utf-8")

    assert "RECHARGEABLE RTC battery" in setup
    assert '"$SCRIPT_DIR/enable_rtc_battery_charging.sh"' in setup
    # Default answer must be No: enabling charging is opt-in.
    assert 'RTC battery (e.g. the official one) fitted?" "N"' in setup


def test_setup_guide_documents_rechargeable_only_warning():
    guide = SETUP_GUIDE.read_text(encoding="utf-8")

    assert "### RTC Backup Battery" in guide
    assert "**Rechargeable batteries only**" in guide
    assert "CR2032" in guide
    assert "--disable" in guide
