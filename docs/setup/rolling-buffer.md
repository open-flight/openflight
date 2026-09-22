---
icon: lucide/save
---

# Rolling Buffer Setup

**One-time, per radar.** The OPS243-A must have rolling-buffer mode saved to
persistent flash for hardware triggers to work. This is a firmware quirk: the
`HOST_INT` pin mode switches when the radar changes modes at runtime, so the
setting has to survive a power cycle rather than be applied at startup.

Do this once, after wiring the sound trigger and before your first session.
Every other guide links here rather than repeating the procedure.

## The procedure

The OPS243-A needs a one-time configuration to enable rolling buffer mode with
hardware sound triggering, saved to flash so it boots correctly every time.

> **Why?** The OPS243-A has a firmware bug where the HOST_INT pin mode switches
> unexpectedly when entering rolling buffer mode at runtime. Saving to flash and
> power cycling bypasses this. Confirmed by OmniPreSense engineering.

<details>
<summary>Manual steps</summary>

```bash
# 1. Configure and save to flash
uv run python scripts/hardware-test/test_rolling_buffer_persist.py --setup

# 2. Power cycle the radar, wait 3 seconds, reconnect (see note below)

# 3. Verify — make a sound near the SEN-14262, you should see I/Q trigger data
uv run python scripts/hardware-test/test_rolling_buffer_persist.py --test
```

How you power cycle depends on how the OPS243 is connected:

| Connection | Power cycle by |
|------------|----------------|
| USB | Unplugging the USB cable |
| Pi GPIO UART | Disconnecting 5V from OPS `J3` pin 9 — **not** by rebooting the Pi, which does not necessarily drop the header rail |

If the OPS243 is on the GPIO UART, add `--port /dev/ttyAMA0` to both commands
above.

</details>


## If the OPS243 is on the Pi GPIO UART

Once you have completed the [UART migration](../build/ops243-uart.md), the same
procedure needs an explicit port, and the power cycle is the 5 V jumper on J3
pin 9 rather than a USB cable. See
[Re-running the one-time rolling-buffer setup](../build/ops243-uart.md#re-running-the-one-time-rolling-buffer-setup).

## Related

- [Sound trigger wiring](../build/sound-trigger.md) — do this first; the
  trigger path is what rolling-buffer mode exists to serve.
- [Rolling buffer and spin detection](../how-it-works/rolling-buffer.md) — what
  the captured buffer is actually used for.
