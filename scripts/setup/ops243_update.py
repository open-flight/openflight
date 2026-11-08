#!/usr/bin/env python3
"""Update the tested XMC4500 AC / 1 MiB OPS243-A, one explicit step at a time."""

import argparse
import hashlib
import json
import re
import struct
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen

SIZE = 0x100000
BACKUP = "ops243-before-update.bin"
FIRMWARE_URL = (
    "https://raw.githubusercontent.com/omnipresense/OPS243-A_Firmware/"
    "efaceefb8fa547071cb925c0d20d2e38363ace67/OPS243_CW_v1.3.3_20261016_1230.hex"
)
FIRMWARE_SHA256 = "169a6c6c7ae14bcc7da3e7201e31c623149da65e9f3df7598da70e0ae3e9d81c"
PROBE_URL = "https://github.com/raspberrypi/debugprobe/releases/latest/download/debugprobe.uf2"
OPENOCD = [
    "sudo",
    "openocd",
    "-f",
    "interface/cmsis-dap.cfg",
    "-c",
    "transport select swd",
    "-f",
    "target/xmc4xxx.cfg",
    "-c",
    "adapter speed 1000",
]
BACKUP_COMMANDS = [
    "init",
    "poll",
    "halt 0",
    "wait_halt 5000",
    "flash probe 0",
    "flash info 0",
    f"flash read_bank 0 {BACKUP}",
    f"flash verify_bank 0 {BACKUP}",
    "resume",
    "shutdown",
]
FLASH_COMMANDS = [
    "init",
    "poll",
    "reset run",
    "sleep 1000",
    "poll",
    "halt 0",
    "wait_halt 5000",
    "flash probe 0",
    f"flash verify_bank 0 {BACKUP}",
    "flash write_image erase ops243-app.bin 0x0C020000 bin",
    "flash write_image erase ops243-first64k.bin 0x0C000000 bin",
    "flash verify_bank 0 ops243-expected.bin",
    "shutdown",
]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def run_openocd(folder, commands, log_name):
    command = OPENOCD + [arg for item in commands for arg in ("-c", item)]
    lines = []
    with (folder / log_name).open("w", encoding="utf-8") as log:
        with subprocess.Popen(
            command, cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        ) as process:
            for line in process.stdout:
                print(line, end="", flush=True)
                log.write(line)
                log.flush()
                lines.append(line)
            status = process.wait()
    require(status == 0, f"OpenOCD failed. Keep the files and {log_name}; do not continue.")
    return "".join(lines)


def backup(folder):
    folder.mkdir(parents=True, exist_ok=True)
    require(not (folder / BACKUP).exists(), "Original backup already exists; do not replace it.")
    output = run_openocd(folder, BACKUP_COMMANDS, "ops243-backup.log")
    require("XMC4500 - Rev: AC" in output, "This guide requires XMC4500 revision AC.")
    require("size 0x00100000" in output, "This guide requires 1 MiB flash.")
    sectors = re.findall(r"#\s*\d+:\s+0x[0-9a-fA-F]+\s+\([^\n]+\)\s+([^\n]+)", output)
    require(
        len(sectors) == 12 and all(s.strip() == "not protected" for s in sectors),
        "Expected 12 unprotected flash sectors. Stop and keep the output.",
    )
    original = (folder / BACKUP).read_bytes()
    require(len(original) == SIZE and "contents match" in output, "Backup did not verify.")
    digest = hashlib.sha256(original).hexdigest()
    (folder / "backup-sha256.txt").write_text(f"{digest}  {BACKUP}\n", encoding="ascii")
    print(
        "Backup complete: 1 MiB read and verified. Copy it to another computer before continuing."
    )


def read_backup(folder):
    original = (folder / BACKUP).read_bytes()
    require(len(original) == SIZE, "Backup must be exactly 1 MiB.")
    digest = (folder / "backup-sha256.txt").read_text(encoding="ascii").split()[0]
    require(hashlib.sha256(original).hexdigest() == digest, "Original backup changed; stop.")
    return original


def images(original, raw):
    require(
        hashlib.sha256(raw).hexdigest() == FIRMWARE_SHA256, "Wrong HEX file (SHA-256 mismatch)."
    )
    image = bytearray(original)
    base = written = 0
    eof = False
    for line in raw.decode("ascii").splitlines():
        require(line.startswith(":"), "Invalid HEX record.")
        record = bytes.fromhex(line[1:])
        require(len(record) >= 5, "Invalid HEX record.")
        length, kind = record[0], record[3]
        require(len(record) == length + 5 and sum(record) % 256 == 0, "Invalid HEX record.")
        if kind == 4:
            base = int.from_bytes(record[4 : 4 + length], "big") << 16
        elif kind == 0:
            offset = base + int.from_bytes(record[1:3], "big") - 0x0C000000
            require(0 <= offset <= len(image) - length, "Address outside flash.")
            image[offset : offset + length] = record[4 : 4 + length]
            written += length
        elif kind == 1:
            eof = True
            break
        elif kind != 5:
            raise ValueError("Unexpected HEX record.")
    require(eof and written == 286768, "Unexpected firmware size or missing HEX end record.")
    require(image[0x2B4:0x20000] == original[0x2B4:0x20000], "Unexpected changes outside firmware.")
    require(image[0x65D7C:] == original[0x65D7C:], "Unexpected changes outside firmware.")
    return {
        "OPS243-v1.3.3.hex": raw,
        "ops243-first64k.bin": bytes(image[:0x10000]),
        "ops243-app.bin": bytes(image[0x20000:0x80000]),
        "ops243-expected.bin": bytes(image),
    }


def prepare(folder):
    require(
        not (folder / "flash-attempted.txt").exists(),
        "Flash already attempted; stop and keep the files.",
    )
    original = read_backup(folder)
    with urlopen(FIRMWARE_URL, timeout=60) as response:
        prepared = images(original, response.read())
    for name, data in prepared.items():
        (folder / name).write_bytes(data)
    print("Prepared v1.3.3; original backup retained. Settings area: 65,536 bytes unchanged.")


def flash(folder):
    require(
        not (folder / "flash-attempted.txt").exists(), "Flash already attempted; do not rerun it."
    )
    original = read_backup(folder)
    prepared = images(original, (folder / "OPS243-v1.3.3.hex").read_bytes())
    for name, data in prepared.items():
        require((folder / name).read_bytes() == data, f"Prepared file changed: {name}; stop.")
    with (folder / "flash-attempted.txt").open("x", encoding="ascii") as marker:
        marker.write("Keep this file and the backup. Do not blindly retry a flash attempt.\n")
    print("Flashing takes about four minutes. Keep power and cables connected.", flush=True)
    output = run_openocd(folder, FLASH_COMMANDS, "ops243-flash.log")
    require(output.count("contents match") == 2, "Full-flash verification missing; stop.")
    require(
        "wrote 393216 bytes" in output and "wrote 65536 bytes" in output,
        "Both completed writes were not reported; stop.",
    )
    print("Flash complete: full 1 MiB verified. Power off before disconnecting the probe.")


def update_probe(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with urlopen(PROBE_URL, timeout=60) as response:
        firmware = response.read()
    require(len(firmware) > 0 and len(firmware) % 512 == 0, "Invalid probe UF2 download.")
    for offset in range(0, len(firmware), 512):
        header = struct.unpack_from("<8I", firmware, offset)
        require(
            header[:2] == (0x0A324655, 0x9E5D5157)
            and header[2] & 0x2000
            and header[7] == 0xE48BFF56
            and struct.unpack_from("<I", firmware, offset + 508)[0] == 0x0AB16F30,
            "Expected official RP2040 Debug Probe UF2 firmware.",
        )
    uf2 = folder / "debugprobe.uf2"
    uf2.write_bytes(firmware)
    print("Leave the probe's D cable disconnected. Unplug its USB cable.")
    print("Hold BOOTSEL while reconnecting USB to the Pi, then release BOOTSEL.")
    input("Press Enter when the probe is connected in BOOTSEL mode: ")
    result = subprocess.run(
        ["lsblk", "--json", "--output", "PATH,LABEL,MOUNTPOINTS"],
        check=True,
        capture_output=True,
        text=True,
    )
    pending = json.loads(result.stdout)["blockdevices"]
    candidates = []
    while pending:
        device = pending.pop()
        pending.extend(device.get("children", []))
        if device.get("label") == "RPI-RP2":
            candidates.append(device)
    require(
        len(candidates) == 1,
        "Expected exactly one RPI-RP2 drive. Check BOOTSEL and unplug other Pico boards.",
    )
    device = candidates[0]
    mounted = next((p for p in device.get("mountpoints", []) if p), None)
    mount = Path(mounted) if mounted else folder / "probe-mount"
    if not mounted:
        mount.mkdir(exist_ok=True)
        subprocess.run(["sudo", "mount", device["path"], str(mount)], check=True)
    try:
        require(
            "Board-ID: RPI-RP2" in (mount / "INFO_UF2.TXT").read_text(),
            "Drive is not an RP2040 BOOTSEL volume; nothing copied.",
        )
        subprocess.run(["sudo", "cp", str(uf2), str(mount / "debugprobe.uf2")], check=True)
        subprocess.run(["sync"], check=True)
    finally:
        if not mounted:
            subprocess.run(["sudo", "umount", str(mount)], check=False, capture_output=True)
    print("Firmware copied. The probe should restart and the RPI-RP2 drive disappear.")
    print("Check its firmware version with: lsusb -v -d 2e8a:000c")


def version(_folder):
    sys.path.insert(0, str(Path.cwd() / "src"))
    from openflight.ops243 import OPS243Radar  # pylint: disable=import-outside-toplevel

    radar = OPS243Radar(port="/dev/ttyAMA0")
    try:
        radar.connect()
        firmware = radar.get_firmware_version()
        print("OPS243 firmware:", firmware)
        require(firmware == "1.3.3", "Expected firmware 1.3.3.")
    finally:
        radar.disconnect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=("probe", "backup", "prepare", "flash", "version"))
    parser.add_argument("--directory", type=Path, default=Path.home() / "ops243-backup")
    args = parser.parse_args()
    actions = {
        "probe": update_probe,
        "backup": backup,
        "prepare": prepare,
        "flash": flash,
        "version": version,
    }
    try:
        actions[args.step](args.directory.expanduser().resolve())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"STOP: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
