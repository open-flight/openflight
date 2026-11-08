"""Offline checks for image preservation, flash ordering and SSH probe updates."""

import hashlib
import importlib.util
import io
import json
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/setup/ops243_update.py"


@pytest.fixture
def updater():
    spec = importlib.util.spec_from_file_location("ops243_update", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hex_record(kind, address, payload):
    record = bytes([len(payload)]) + address.to_bytes(2, "big") + bytes([kind]) + payload
    return ":" + (record + bytes([-sum(record) & 255])).hex()


def firmware(gap=False):
    lines = []
    upper = None
    for start, size in ((0x0C000000, 0x2B4), (0x0C010000 if gap else 0x0C020000, 0x45D7C)):
        for offset in range(0, size, 128):
            address = start + offset
            if address >> 16 != upper:
                upper = address >> 16
                lines.append(hex_record(4, 0, upper.to_bytes(2, "big")))
            payload = b"\xa5" * min(128, size - offset)
            lines.append(hex_record(0, address & 65535, payload))
    lines.append(hex_record(1, 0, b""))
    return ("\n".join(lines) + "\n").encode("ascii")


@pytest.fixture
def prepared(updater, tmp_path, monkeypatch):
    original = bytes(range(256)) * 4096
    (tmp_path / updater.BACKUP).write_bytes(original)
    (tmp_path / "backup-sha256.txt").write_text(hashlib.sha256(original).hexdigest())
    raw = firmware()
    monkeypatch.setattr(updater, "FIRMWARE_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(updater, "urlopen", lambda *args, **kwargs: io.BytesIO(raw))
    updater.prepare(tmp_path)
    return original, raw


def test_prepare_preserves_settings_and_all_bytes_outside_firmware(updater, prepared, tmp_path):
    original, raw = prepared
    expected = (tmp_path / "ops243-expected.bin").read_bytes()
    assert len(expected) == 1048576
    assert expected[:0x2B4] == b"\xa5" * 0x2B4
    assert expected[0x20000:0x65D7C] == b"\xa5" * 0x45D7C
    assert expected[0x2B4:0x20000] == original[0x2B4:0x20000]
    assert expected[0x65D7C:] == original[0x65D7C:]
    first = (tmp_path / "ops243-first64k.bin").read_bytes()
    app = (tmp_path / "ops243-app.bin").read_bytes()
    assert len(first) == 65536 and len(app) == 393216
    reconstructed = bytearray(original)
    reconstructed[:0x10000] = first
    reconstructed[0x20000:0x80000] = app
    assert reconstructed == expected
    assert (tmp_path / updater.BACKUP).read_bytes() == original
    assert (tmp_path / "OPS243-v1.3.3.hex").read_bytes() == raw


@pytest.mark.parametrize("problem", ["truncated_backup", "changed_backup", "wrong_download"])
def test_prepare_rejects_bad_inputs_without_outputs(updater, tmp_path, monkeypatch, problem):
    original = bytes(1048576)
    (tmp_path / updater.BACKUP).write_bytes(original)
    (tmp_path / "backup-sha256.txt").write_text(hashlib.sha256(original).hexdigest())
    if problem == "truncated_backup":
        (tmp_path / updater.BACKUP).write_bytes(original[:-1])
    elif problem == "changed_backup":
        (tmp_path / updater.BACKUP).write_bytes(b"X" + original[1:])
    monkeypatch.setattr(updater, "urlopen", lambda *args, **kwargs: io.BytesIO(b"wrong HEX"))
    with pytest.raises(ValueError):
        updater.prepare(tmp_path)
    assert not (tmp_path / "ops243-expected.bin").exists()


def test_images_rejects_data_in_settings_gap(updater, monkeypatch):
    raw = firmware(gap=True)
    monkeypatch.setattr(updater, "FIRMWARE_SHA256", hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError, match="outside firmware"):
        updater.images(bytes(1048576), raw)


def test_backup_reads_verifies_and_refuses_overwrite(updater, tmp_path, monkeypatch):
    output = "XMC4500 - Rev: AC\nsize 0x00100000\ncontents match\n"
    output += "".join(f"# {i}: 0x00000000 (0x4000 16kB) not protected\n" for i in range(12))

    def run(folder, commands, _log):
        assert not any("write_image" in command or "erase" in command for command in commands)
        assert commands.index(f"flash read_bank 0 {updater.BACKUP}") < commands.index(
            f"flash verify_bank 0 {updater.BACKUP}"
        )
        (folder / updater.BACKUP).write_bytes(bytes(1048576))
        return output

    mock_run = Mock(side_effect=run)
    monkeypatch.setattr(updater, "run_openocd", mock_run)
    updater.backup(tmp_path)
    assert updater.read_backup(tmp_path) == bytes(1048576)
    with pytest.raises(ValueError, match="already exists"):
        updater.backup(tmp_path)
    assert mock_run.call_count == 1


@pytest.mark.parametrize("problem", ["wrong_chip", "wrong_size", "protected", "unverified"])
def test_backup_requires_supported_verified_target(updater, tmp_path, monkeypatch, problem):
    output = "XMC4500 - Rev: AC\nsize 0x00100000\ncontents match\n"
    output += "".join(f"# {i}: 0x00000000 (0x4000 16kB) not protected\n" for i in range(12))
    substitutions = {
        "wrong_chip": ("Rev: AC", "Rev: AB"),
        "wrong_size": ("0x00100000", "0x00080000"),
        "protected": ("not protected", "protected"),
        "unverified": ("contents match", "verification failed"),
    }
    output = output.replace(*substitutions[problem])

    def run(*args):
        (tmp_path / updater.BACKUP).write_bytes(bytes(1048576))
        return output

    monkeypatch.setattr(updater, "run_openocd", run)
    with pytest.raises(ValueError):
        updater.backup(tmp_path)
    assert not (tmp_path / "backup-sha256.txt").exists()


def test_flash_keeps_tested_sequence_and_blocks_second_attempt(
    updater, prepared, tmp_path, monkeypatch
):
    output = "contents match\nwrote 393216 bytes\nwrote 65536 bytes\ncontents match\n"
    run = Mock(return_value=output)
    monkeypatch.setattr(updater, "run_openocd", run)
    updater.flash(tmp_path)
    assert run.call_args.args[1] == [
        "init",
        "poll",
        "reset run",
        "sleep 1000",
        "poll",
        "halt 0",
        "wait_halt 5000",
        "flash probe 0",
        "flash verify_bank 0 ops243-before-update.bin",
        "flash write_image erase ops243-app.bin 0x0C020000 bin",
        "flash write_image erase ops243-first64k.bin 0x0C000000 bin",
        "flash verify_bank 0 ops243-expected.bin",
        "shutdown",
    ]
    with pytest.raises(ValueError, match="do not rerun"):
        updater.flash(tmp_path)
    assert run.call_count == 1


@pytest.mark.parametrize("name", ["ops243-app.bin", "ops243-first64k.bin", "ops243-expected.bin"])
def test_flash_rejects_changed_images_before_hardware(
    updater, prepared, tmp_path, monkeypatch, name
):
    path = tmp_path / name
    path.write_bytes(b"X" + path.read_bytes()[1:])
    run = Mock()
    monkeypatch.setattr(updater, "run_openocd", run)
    with pytest.raises(ValueError, match="Prepared file changed"):
        updater.flash(tmp_path)
    run.assert_not_called()
    assert not (tmp_path / "flash-attempted.txt").exists()


def test_failed_flash_keeps_attempt_marker(updater, prepared, tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "run_openocd", Mock(side_effect=ValueError("OpenOCD failed")))
    with pytest.raises(ValueError, match="OpenOCD failed"):
        updater.flash(tmp_path)
    assert (tmp_path / "flash-attempted.txt").exists()
    with pytest.raises(ValueError, match="already attempted"):
        updater.prepare(tmp_path)


def uf2():
    data = bytearray(512)
    struct.pack_into(
        "<8I", data, 0, 0x0A324655, 0x9E5D5157, 0x2000, 0x10000100, 256, 0, 1, 0xE48BFF56
    )
    struct.pack_into("<I", data, 508, 0x0AB16F30)
    return bytes(data)


@pytest.mark.parametrize("automounted", [True, False])
def test_probe_update_over_ssh_handles_mounted_and_unmounted_drives(
    updater, tmp_path, monkeypatch, automounted
):
    mount = tmp_path / ("desktop-mount" if automounted else "probe-mount")
    mount.mkdir()
    (mount / "INFO_UF2.TXT").write_text("UF2 Bootloader\nBoard-ID: RPI-RP2\n")
    device = {
        "path": "/dev/test-probe1",
        "label": "RPI-RP2",
        "mountpoints": [str(mount)] if automounted else [None],
    }
    inventory = {"blockdevices": [{"path": "/dev/test-probe", "children": [device]}]}

    def command(args, **kwargs):
        return SimpleNamespace(stdout=json.dumps(inventory) if args[0] == "lsblk" else "")

    run = Mock(side_effect=command)
    monkeypatch.setattr(updater.subprocess, "run", run)
    monkeypatch.setattr(updater, "urlopen", lambda *args, **kwargs: io.BytesIO(uf2()))
    monkeypatch.setattr("builtins.input", lambda *args: "")
    updater.update_probe(tmp_path)
    commands = [call.args[0] for call in run.call_args_list]
    assert [
        "sudo",
        "cp",
        str(tmp_path / "debugprobe.uf2"),
        str(mount / "debugprobe.uf2"),
    ] in commands
    assert ["sync"] in commands
    if automounted:
        assert not any("mount" in command or "umount" in command for command in commands)
    else:
        assert ["sudo", "mount", "/dev/test-probe1", str(mount)] in commands
        assert ["sudo", "umount", str(mount)] in commands


@pytest.mark.parametrize("count", [0, 2])
def test_probe_refuses_missing_or_ambiguous_drives(updater, tmp_path, monkeypatch, count):
    devices = [
        {"path": f"/dev/test{i}", "label": "RPI-RP2", "mountpoints": []} for i in range(count)
    ]
    run = Mock(return_value=SimpleNamespace(stdout=json.dumps({"blockdevices": devices})))
    monkeypatch.setattr(updater.subprocess, "run", run)
    monkeypatch.setattr(updater, "urlopen", lambda *args, **kwargs: io.BytesIO(uf2()))
    monkeypatch.setattr("builtins.input", lambda *args: "")
    with pytest.raises(ValueError, match="exactly one"):
        updater.update_probe(tmp_path)
    assert run.call_count == 1


def test_probe_rejects_invalid_download_before_touching_usb(updater, tmp_path, monkeypatch):
    run = Mock()
    monkeypatch.setattr(updater.subprocess, "run", run)
    monkeypatch.setattr(updater, "urlopen", lambda *args, **kwargs: io.BytesIO(b"wrong firmware"))
    with pytest.raises(ValueError, match="Invalid probe UF2"):
        updater.update_probe(tmp_path)
    run.assert_not_called()


def test_openocd_failure_is_reported_and_logged(updater, tmp_path, monkeypatch):
    process = Mock()
    process.stdout = io.StringIO("Error: target not halted\n")
    process.wait.return_value = 1
    context = Mock()
    context.__enter__ = Mock(return_value=process)
    context.__exit__ = Mock(return_value=False)
    popen = Mock(return_value=context)
    monkeypatch.setattr(updater.subprocess, "Popen", popen)
    with pytest.raises(ValueError, match="OpenOCD failed"):
        updater.run_openocd(tmp_path, updater.BACKUP_COMMANDS, "test.log")
    assert "target not halted" in (tmp_path / "test.log").read_text()
    assert popen.call_args.kwargs["stderr"] == subprocess.STDOUT
