"""Tests for the OV9281 camera connector auto-configuration script."""

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="camera setup script tests need bash",
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = PROJECT_ROOT / "scripts" / "setup" / "configure_ov9281_camera.sh"
MAIN_SETUP_SCRIPT = PROJECT_ROOT / "scripts" / "setup" / "setup.sh"
CAMERA_GUIDE = PROJECT_ROOT / "docs" / "camera" / "README.md"

BLOCK_BEGIN = (
    "# BEGIN OpenFlight OV9281 camera (managed by scripts/setup/configure_ov9281_camera.sh)"
)
BLOCK_END = "# END OpenFlight OV9281 camera"
STOCK_CONFIG = """\
# For more options and information see
# http://rptl.io/configtxt
dtparam=audio=on

# Automatically load overlays for detected cameras
camera_auto_detect=1

# Automatically load overlays for detected DSI displays
display_auto_detect=1

[cm5]
dtoverlay=dwc2,dr_mode=host

[all]
"""


def _fake_board(
    tmp_path: Path,
    *,
    model: str = "Raspberry Pi 5 Model B Rev 1.0",
    displays: tuple[int, ...] = (),
    bound_cameras: tuple[int, ...] = (),
) -> dict[str, str]:
    """Build a fake device tree and I2C sysfs; return the env that points at it."""
    dt_root = tmp_path / "device-tree"
    symbols = dt_root / "__symbols__"
    symbols.mkdir(parents=True)
    (dt_root / "model").write_bytes(model.encode() + b"\0")
    for connector in (0, 1):
        node = f"/axi/pcie@1000120000/rp1/dsi@{110000 + connector * 10000}"
        (symbols / f"dsi{connector}").write_bytes(node.encode() + b"\0")
        node_dir = dt_root / node.lstrip("/")
        node_dir.mkdir(parents=True)
        status = b"okay" if connector in displays else b"disabled"
        (node_dir / "status").write_bytes(status + b"\0")

    i2c_root = tmp_path / "i2c-devices"
    i2c_root.mkdir()
    for connector in bound_cameras:
        (i2c_root / f"{10 + connector}-0060" / "driver").mkdir(parents=True)

    return {
        "OPENFLIGHT_CAMERA_DT_ROOT": str(dt_root),
        "OPENFLIGHT_CAMERA_I2C_DEVICES_ROOT": str(i2c_root),
    }


def _run(script: str, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    """Source the setup script, run ``script`` with sudo stubbed out."""
    return subprocess.run(
        [
            "bash",
            "-c",
            f'source "$1"; shift; as_root() {{ "$@"; }}; {script}',
            "camera-test",
            str(SETUP_SCRIPT),
            *args,
        ],
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", **env},
    )


def _configure(config: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return _run("main", {**env, "OPENFLIGHT_CAMERA_BOOT_CONFIG": str(config)})


def _block(content: str) -> list[str]:
    start = content.index(BLOCK_BEGIN)
    end = content.index(BLOCK_END)
    return content[start:end].splitlines()[1:]


def _write_config(tmp_path: Path, content: str = STOCK_CONFIG) -> Path:
    config = tmp_path / "config.txt"
    config.write_text(content, encoding="ascii")
    return config


def test_setup_script_has_valid_bash_syntax():
    subprocess.run(["bash", "-n", SETUP_SCRIPT], check=True)


def test_setup_script_uses_unix_newlines():
    assert b"\r\n" not in SETUP_SCRIPT.read_bytes()


def test_pi5_without_display_loads_the_overlay_on_both_connectors(tmp_path):
    config = _write_config(tmp_path)

    result = _configure(config, _fake_board(tmp_path))
    content = config.read_text(encoding="ascii")

    assert result.returncode == 0, result.stderr
    assert _block(content) == [
        "[all]",
        "camera_auto_detect=0",
        "dtoverlay=ov9281,cam0",
        "dtoverlay=ov9281",
    ]


def test_connector_with_dsi_display_on_cam_disp_0_is_left_alone(tmp_path):
    config = _write_config(tmp_path)

    result = _configure(config, _fake_board(tmp_path, displays=(0,)))

    assert result.returncode == 0, result.stderr
    assert _block(config.read_text(encoding="ascii")) == [
        "[all]",
        "camera_auto_detect=0",
        "dtoverlay=ov9281",
    ]
    assert "CAM/DISP 0 drives a DSI display" in result.stderr


def test_connector_with_dsi_display_on_cam_disp_1_is_left_alone(tmp_path):
    config = _write_config(tmp_path)

    result = _configure(config, _fake_board(tmp_path, displays=(1,)))

    assert result.returncode == 0, result.stderr
    assert _block(config.read_text(encoding="ascii")) == [
        "[all]",
        "camera_auto_detect=0",
        "dtoverlay=ov9281,cam0",
    ]


def test_two_dsi_displays_fail_without_touching_the_boot_config(tmp_path):
    config = _write_config(tmp_path)

    result = _configure(config, _fake_board(tmp_path, displays=(0, 1)))

    assert result.returncode != 0
    assert "no free camera connector" in result.stderr
    assert config.read_text(encoding="ascii") == STOCK_CONFIG
    assert not list(tmp_path.glob("config.txt.pre-openflight-camera-*"))


def test_board_without_two_connectors_uses_the_single_default_overlay(tmp_path):
    config = _write_config(tmp_path)

    result = _configure(config, _fake_board(tmp_path, model="Raspberry Pi 4 Model B Rev 1.5"))

    assert result.returncode == 0, result.stderr
    assert _block(config.read_text(encoding="ascii")) == [
        "[all]",
        "camera_auto_detect=0",
        "dtoverlay=ov9281",
    ]


def test_existing_camera_auto_detect_is_turned_off_in_place(tmp_path):
    config = _write_config(tmp_path)

    _configure(config, _fake_board(tmp_path))
    content = config.read_text(encoding="ascii")

    assert "camera_auto_detect=1" not in content
    before_block = content[: content.index(BLOCK_BEGIN)]
    assert "# Automatically load overlays for detected cameras\ncamera_auto_detect=0\n" in (
        before_block
    )


def test_configuration_is_idempotent(tmp_path):
    config = _write_config(tmp_path)
    env = _fake_board(tmp_path)

    first = _configure(config, env)
    first_content = config.read_text(encoding="ascii")
    backups = list(tmp_path.glob("config.txt.pre-openflight-camera-*"))
    second = _configure(config, env)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "already up to date" in second.stdout
    assert config.read_text(encoding="ascii") == first_content
    assert first_content.count(BLOCK_BEGIN) == 1
    assert first_content.count("[all]") == 2
    assert list(tmp_path.glob("config.txt.pre-openflight-camera-*")) == backups


def test_configuration_backs_up_the_original_boot_config(tmp_path):
    config = _write_config(tmp_path)

    result = _configure(config, _fake_board(tmp_path))
    backups = list(tmp_path.glob("config.txt.pre-openflight-camera-*"))

    assert result.returncode == 0, result.stderr
    assert len(backups) == 1
    assert backups[0].read_text(encoding="ascii") == STOCK_CONFIG
    assert "Reboot required" in result.stdout


def test_moving_the_display_rewrites_the_block_instead_of_appending(tmp_path):
    config = _write_config(tmp_path)

    _configure(config, _fake_board(tmp_path / "before"))
    _configure(config, _fake_board(tmp_path / "after", displays=(1,)))
    content = config.read_text(encoding="ascii")

    assert content.count(BLOCK_BEGIN) == 1
    assert _block(content) == ["[all]", "camera_auto_detect=0", "dtoverlay=ov9281,cam0"]


def test_hand_written_ov9281_overlays_are_replaced_not_duplicated(tmp_path):
    config = _write_config(
        tmp_path,
        "camera_auto_detect=0\ndtoverlay=ov9281,cam1\n#dtoverlay=ov9281,cam0\n"
        "dtoverlay=ov9281x\n",
    )

    result = _configure(config, _fake_board(tmp_path))
    content = config.read_text(encoding="ascii")

    assert result.returncode == 0, result.stderr
    assert "dtoverlay=ov9281,cam1" not in content
    assert "#dtoverlay=ov9281,cam0\n" in content
    assert "dtoverlay=ov9281x\n" in content
    lines = content.splitlines()
    assert lines.count("dtoverlay=ov9281") == 1
    assert lines.count("dtoverlay=ov9281,cam0") == 1


def test_remove_restores_a_config_that_had_no_camera_settings(tmp_path):
    original = "dtparam=audio=on\n\n[all]\ndtparam=i2c_arm=on\n"
    config = _write_config(tmp_path, original)
    env = {**_fake_board(tmp_path), "OPENFLIGHT_CAMERA_BOOT_CONFIG": str(config)}

    _run("main", env)
    result = _run('main "$1"', env, "--remove")

    assert result.returncode == 0, result.stderr
    assert config.read_text(encoding="ascii") == original


def test_remove_keeps_camera_auto_detect_off_and_says_how_to_restore_it(tmp_path):
    config = _write_config(tmp_path)
    env = {**_fake_board(tmp_path), "OPENFLIGHT_CAMERA_BOOT_CONFIG": str(config)}

    _run("main", env)
    result = _run('main "$1"', env, "--remove")
    content = config.read_text(encoding="ascii")

    assert result.returncode == 0, result.stderr
    assert BLOCK_BEGIN not in content
    assert "dtoverlay=ov9281" not in content
    assert "camera_auto_detect=0" in content
    assert "set it to 1" in result.stdout


def test_detected_connectors_reports_only_bound_sensors(tmp_path):
    result = _run("detected_connectors", _fake_board(tmp_path, bound_cameras=(1,)))

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["1"]


def test_verify_reports_the_connector_with_the_camera(tmp_path):
    config = _write_config(tmp_path)
    env = {
        **_fake_board(tmp_path, bound_cameras=(0,)),
        "OPENFLIGHT_CAMERA_BOOT_CONFIG": str(config),
    }
    _run("main", env)

    result = _run('main "$1"', env, "--verify")

    assert result.returncode == 0, result.stderr
    assert "OV9281 detected on CAM/DISP 0." in result.stdout


def test_verify_fails_when_no_connector_answers(tmp_path):
    config = _write_config(tmp_path)
    env = {**_fake_board(tmp_path), "OPENFLIGHT_CAMERA_BOOT_CONFIG": str(config)}
    _run("main", env)

    result = _run('main "$1"', env, "--verify")

    assert result.returncode != 0
    assert "No OV9281 answered on either connector" in result.stderr


def test_verify_fails_before_configuration(tmp_path):
    config = _write_config(tmp_path)
    env = {
        **_fake_board(tmp_path, bound_cameras=(1,)),
        "OPENFLIGHT_CAMERA_BOOT_CONFIG": str(config),
    }

    result = _run('main "$1"', env, "--verify")

    assert result.returncode != 0
    assert "camera block is missing" in result.stderr


def test_main_setup_offers_camera_configuration_without_asking_for_a_connector():
    script = MAIN_SETUP_SCRIPT.read_text(encoding="utf-8")
    start = script.index("# --- OV9281 camera ---")
    camera_step = script[start : script.index("# --- Bluetooth (BLE phone app) ---")]

    assert 'confirm "Do you have an InnoMaker OV9281 camera to set up?" "N"' in camera_step
    assert '"$SCRIPT_DIR/configure_ov9281_camera.sh"' in camera_step
    assert "CAM/DISP" not in camera_step.split("confirm", 1)[1].split("\n", 1)[0]


def test_camera_guide_points_at_the_setup_script_instead_of_manual_overlays():
    guide = CAMERA_GUIDE.read_text(encoding="utf-8")

    assert "scripts/setup/configure_ov9281_camera.sh" in guide
    assert "--verify" in guide
    assert ",cam1" not in guide
