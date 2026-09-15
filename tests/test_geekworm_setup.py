"""Tests for the Raspberry Pi Geekworm provisioning script."""

import hashlib
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = PROJECT_ROOT / "scripts" / "battery" / "geekworm" / "setup.sh"
PANEL_PACKAGE = (
    PROJECT_ROOT / "scripts" / "battery" / "packages" / "wfplug-batt_1.3+openflight4_arm64.deb"
)
PANEL_PATCH = (
    PROJECT_ROOT / "scripts" / "battery" / "patches" / "wfplug-batt-capacity-and-power.patch"
)
BATTERY_GUIDE = PROJECT_ROOT / "docs" / "using" / "battery.md"
OPERATOR_GUIDE = PROJECT_ROOT / "docs" / "build" / "battery.md"
MAIN_SETUP_SCRIPT = PROJECT_ROOT / "scripts" / "setup" / "setup.sh"


def _run_function(function: str, config_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; "$2" "$3"',
            "geekworm-test",
            str(SETUP_SCRIPT),
            function,
            str(config_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_setup_script_has_valid_bash_syntax():
    subprocess.run(["bash", "-n", SETUP_SCRIPT], check=True)


def test_boot_configuration_update_is_idempotent(tmp_path):
    config = tmp_path / "config.txt"
    config.write_text("[cm5]\nfoo=bar\n", encoding="ascii")

    first = _run_function("update_boot_config", config)
    first_content = config.read_text(encoding="ascii")
    second = _run_function("update_boot_config", config)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert config.read_text(encoding="ascii") == first_content
    assert first_content.count("dtparam=i2c_arm=on") == 1
    assert first_content.count("dtoverlay=i2c-sensor,max17040") == 1
    assert first_content.count("dtoverlay=gpio-charger,gpio=6") == 1
    assert "\n[all]\n# OpenFlight Geekworm" in first_content


def test_boot_configuration_accepts_existing_openflight_settings(tmp_path):
    config = tmp_path / "config.txt"
    original = """\
dtparam=i2c_arm=on
[all]
dtoverlay=i2c-sensor,max17040
dtoverlay=gpio-charger,gpio=6,active_low=0,gpio_pull=down,type=mains
"""
    config.write_text(original, encoding="ascii")

    result = _run_function("update_boot_config", config)

    assert result.returncode == 0, result.stderr
    assert config.read_text(encoding="ascii") == original


def test_boot_configuration_rejects_conflicting_charger_overlay(tmp_path):
    config = tmp_path / "config.txt"
    config.write_text(
        "dtoverlay=gpio-charger,gpio=7,active_low=1\n",
        encoding="ascii",
    )

    result = _run_function("update_boot_config", config)

    assert result.returncode != 0
    assert "different gpio-charger overlay" in result.stderr


def test_eeprom_configuration_replaces_and_deduplicates_power_settings(tmp_path):
    config = tmp_path / "eeprom.conf"
    config.write_text(
        "BOOT_UART=1\nPSU_MAX_CURRENT=3000\nPOWER_OFF_ON_HALT=0\nPSU_MAX_CURRENT=2000\n",
        encoding="ascii",
    )

    result = _run_function("update_eeprom_config", config)
    content = config.read_text(encoding="ascii")

    assert result.returncode == 0, result.stderr
    assert "BOOT_UART=1" in content
    assert content.count("PSU_MAX_CURRENT=5000") == 1
    assert content.count("POWER_OFF_ON_HALT=1") == 1
    assert "PSU_MAX_CURRENT=3000" not in content
    assert "POWER_OFF_ON_HALT=0" not in content


def test_bundled_panel_package_matches_setup_checksum():
    digest = hashlib.sha256(PANEL_PACKAGE.read_bytes()).hexdigest()

    assert digest == "55db8a460f99758f2dac9d509b29907e0e268ee88bbff4edb6adcee312046d5c"


def test_panel_patch_uses_external_power_for_charge_icon():
    patch = PANEL_PATCH.read_text(encoding="utf-8")

    assert "power_supply_on_external_power" in patch
    assert '"online"' in patch
    assert '"type"' in patch
    assert 'g_ascii_strcasecmp(type, "Battery")' in patch
    assert "external_power == 0" in patch
    assert "STAT_DISCHARGING" in patch


def test_operator_guide_links_models_and_distinguishes_batteries():
    guide = OPERATOR_GUIDE.read_text(encoding="utf-8")

    assert "https://geekworm.com/products/x1202" in guide
    assert "https://geekworm.com/products/x1206" in guide
    assert "Four 3.7V 18650 cells" in guide
    assert "Four 3.7V 21700 cells" in guide
    assert "X1206 V1.1" in guide
    assert "X1206 V2.0" in guide


def test_battery_guide_documents_provider_interface_and_cli():
    guide = BATTERY_GUIDE.read_text(encoding="utf-8")

    assert "--battery geekworm" in guide
    assert "PowerReader" in guide
    # The operator guide lives at docs/build/battery.md; this asserts the
    # provider overview still links to it.
    assert "../build/battery.md" in guide


def test_main_setup_offers_geekworm_provisioning():
    setup = MAIN_SETUP_SCRIPT.read_text(encoding="utf-8")

    assert 'confirm "Do you have a Geekworm X1202 or X1206 UPS to set up?"' in setup
    assert '"$PROJECT_DIR/scripts/battery/geekworm/setup.sh"' in setup
