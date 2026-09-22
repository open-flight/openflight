---
icon: lucide/cpu
---

# Flash the Firmware

The stock TI demo does not expose the raw radar cube OpenFlight needs. A
validated prebuilt image ships in `firmware/releases/`, so flashing does not
require the TI toolchain — see the
[firmware developer guide](../development/firmware.md) only if you want to
build from source.

## Flash The IWR6843 Firmware

The recommended method flashes directly from the Pi using the checked-in ROM
bootloader client. TI UniFlash is a fallback, not a requirement.

### 1. Stop Serial Users

Stop OpenFlight, calibration, `shot_test.py`, and any other process using the TI
port. Use `Ctrl+C` in the terminal that launched OpenFlight, then check for
remaining owners:

```bash
pgrep -af 'openflight|calibrate|shot_test'
sudo fuser -v /dev/ttyUSB0
```

Do not flash until the Enhanced UART is free.

### 2. Enter Flash Mode

Set the IWR6843LEVM boot switches to:

```text
S1.1 ON, S1.2 OFF, S1.3 ON, S1.4 ON, S1.5 OFF
```

Do not press RESET yet. The flashing script opens the UART first and tells you
when to reset the board.

### 3. Probe The ROM Bootloader

Run the non-destructive probe:

```bash
uv run python firmware/flash_iwr6843.py \
  --probe \
  --port /dev/ttyUSB0
```

Follow the prompts exactly:

1. Type `READY` so the script opens the UART and settles its control lines.
2. Press and release RESET only when the script asks.
3. Wait one second.
4. Type `PROBE`.

The expected result is:

```text
IWR6843 ROM bootloader handshake: PASS
```

If you are flashing immediately, leave the board in flash mode. The flash
command will ask for another RESET after it opens the UART.

### 4. Flash The Configurable Image

```bash
uv run python firmware/flash_iwr6843.py \
  firmware/releases/l3_dump_configurable_capture_20260818.bin \
  --port /dev/ttyUSB0
```

Follow the `READY` -> RESET -> one-second wait -> `FLASH` sequence shown by the
script. The default operation erases the existing serial flash, writes the
image in acknowledged chunks, closes it, and asks the ROM bootloader to verify
the result.

A successful flash ends with:

```text
Erasing existing SFLASH...
Opening firmware image...
Writing firmware...
Writing: 100% (.../... bytes)
Closing and verifying firmware...

Flash verified by the IWR6843 ROM bootloader.
```

Do not reset, disconnect, or remove power while the erase or write is active.
An erase can take longer than ten seconds.

### 5. Return To Functional Mode

Set the switches to:

```text
S1.1 OFF, S1.2 OFF, S1.3 ON, S1.4 ON, S1.5 OFF
```

Press and release RESET. The custom firmware is now ready for OpenFlight.

