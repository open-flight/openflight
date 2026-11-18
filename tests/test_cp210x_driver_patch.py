"""The TI's USB serial driver must keep reading while the tty is throttled.

The TI UART has no flow control. When the 4 KB tty read buffer nears full, the
stock driver stops resubmitting its read URBs and the CP2105 loses whole 256-byte
USB blocks. With throttling a no-op the backlog waits in the tty flip buffers.
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
PATCH = ROOT / "drivers/cp210x/cp210x-keep-reading.patch"
INSTALLER = ROOT / "scripts/setup/install_cp210x_keep_reading_driver.sh"


def test_patch_replaces_only_the_throttle_callback():
    patch = PATCH.read_text()
    assert "-\t.throttle\t\t= usb_serial_generic_throttle," in patch
    assert "+\t.throttle\t\t= cp210x_keep_reading," in patch
    assert "+static void cp210x_keep_reading(struct tty_struct *tty)" in patch
    removed = [
        line for line in patch.splitlines() if line.startswith("-") and not line.startswith("---")
    ]
    assert len(removed) == 1  # unthrottle and everything else stay stock


def test_installer_is_valid_keeps_a_stock_backup_and_can_restore():
    subprocess.run(["bash", "-n", str(INSTALLER)], check=True)
    text = INSTALLER.read_text()
    assert "openflight-stock-" in text and "--restore" in text
    assert "depmod -a" in text
    assert "rmmod" not in text.replace("then: sudo rmmod cp210x", "")  # never unloads a live port
