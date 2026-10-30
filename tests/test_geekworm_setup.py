"""Tests for the Raspberry Pi Geekworm provisioning script."""

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = PROJECT_ROOT / "scripts" / "battery" / "geekworm" / "setup.sh"
PANEL_PACKAGE = (
    PROJECT_ROOT / "scripts" / "battery" / "packages" / "wfplug-batt_1.11+openflight1_arm64.deb"
)
PANEL_BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "battery" / "build_wfplug_batt.sh"
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

    assert digest == "950e985dcc83c669b6abbc8a4769fc410a5842785c1488a12e7ed8f2829fbda1"
    setup = SETUP_SCRIPT.read_text(encoding="utf-8")
    assert f'PANEL_PACKAGE_SHA256="{digest}"' in setup
    assert f"/packages/{PANEL_PACKAGE.name}" in setup


def test_panel_build_script_has_valid_bash_syntax_and_matches_bundled_version():
    subprocess.run(["bash", "-n", PANEL_BUILD_SCRIPT], check=True)
    build = PANEL_BUILD_SCRIPT.read_text(encoding="utf-8")

    assert 'UPSTREAM_VERSION="1.11"' in build
    assert 'OPENFLIGHT_REVISION="1"' in build
    assert PANEL_PATCH.name in build


@pytest.mark.skipif(shutil.which("dpkg") is None, reason="needs dpkg --compare-versions")
@pytest.mark.parametrize(
    ("installed_version", "newer"),
    [
        ("1.12", True),
        ("1.11", False),
        ("1.11+openflight1", False),
        ("1.12+openflight1", False),
        ("", False),
    ],
)
def test_panel_upstream_is_newer_only_for_newer_upstream_builds(installed_version, newer):
    result = _run_function("panel_upstream_is_newer", installed_version)

    assert (result.returncode == 0) is newer, result.stderr


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


def test_battery_guides_warn_against_charging_below_freezing():
    for guide_path in (BATTERY_GUIDE, OPERATOR_GUIDE):
        guide = guide_path.read_text(encoding="utf-8")

        assert "Do not charge" in guide
        assert "below 0 °C (32 °F)" in guide


def test_main_setup_offers_geekworm_provisioning():
    setup = MAIN_SETUP_SCRIPT.read_text(encoding="utf-8")

    assert 'confirm "Do you have a Geekworm X1202 or X1206 UPS to set up?"' in setup
    assert '"$PROJECT_DIR/scripts/battery/geekworm/setup.sh"' in setup
