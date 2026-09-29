"""Tests for USB serial latency timer diagnostics."""

import os
from pathlib import Path

import pytest

from openflight.serial_latency import read_usb_serial_latency_timer


@pytest.mark.skipif(
    os.name == "nt", reason="symlink creation requires elevation on Windows"
)
def test_read_usb_serial_latency_timer_resolves_udev_alias(tmp_path: Path):
    dev_root = tmp_path / "dev"
    sysfs_root = tmp_path / "sys" / "bus" / "usb-serial" / "devices"
    dev_root.mkdir()
    tty = dev_root / "ttyUSB4"
    tty.touch()
    alias = dev_root / "kld7_vertical"
    alias.symlink_to(tty)

    latency_file = sysfs_root / "ttyUSB4" / "latency_timer"
    latency_file.parent.mkdir(parents=True)
    latency_file.write_text("16\n", encoding="ascii")

    info = read_usb_serial_latency_timer(str(alias), sysfs_root=sysfs_root)

    assert info.port == str(alias)
    assert info.resolved_port == str(tty)
    assert info.device_name == "ttyUSB4"
    assert info.latency_ms == 16
    assert info.sysfs_path == latency_file
    assert info.unavailable_reason is None


def test_read_usb_serial_latency_timer_handles_missing_sysfs(tmp_path: Path):
    port = tmp_path / "dev" / "ttyACM0"

    info = read_usb_serial_latency_timer(str(port), sysfs_root=tmp_path / "missing")

    assert info.latency_ms is None
    assert info.device_name == "ttyACM0"
    assert info.unavailable_reason == "latency_timer not exposed"


def test_read_usb_serial_latency_timer_handles_invalid_value(tmp_path: Path):
    sysfs_root = tmp_path / "sys"
    latency_file = sysfs_root / "ttyUSB0" / "latency_timer"
    latency_file.parent.mkdir(parents=True)
    latency_file.write_text("fast\n", encoding="ascii")

    info = read_usb_serial_latency_timer("/dev/ttyUSB0", sysfs_root=sysfs_root)

    assert info.latency_ms is None
    assert info.unavailable_reason == "invalid latency_timer value: 'fast'"
