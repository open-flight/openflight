---
icon: lucide/stethoscope
---

# Hardware Diagnostic

One command that checks the whole signal path and tells you which link is
broken, rather than leaving you to guess from a silent UI.

```bash
uv run python scripts/hardware-test/diagnose.py
```

Run it after any wiring change, and first whenever shots stop registering.

## The seven checks

Each check depends on the ones above it. A failure cascades — later checks are
skipped rather than reported as spurious failures.

| # | Check | Time | What it proves |
| --- | --- | --- | --- |
| 1 | OPS243 UART preflight | ~1 s | Device present, no serial console holding the port, OPS USB not enumerated |
| 2 | OPS243 connectivity | ~2 s | Radar answers; reports port, firmware, and negotiated baud |
| 3 | OPS243 rolling buffer persisted | ~1 s | Radar **boots** into rolling-buffer mode without being told — proves the flash-persist took |
| 4 | OPS243 software trigger | ~3 s | A capture returns 4,096 I/Q samples and parses |
| 5 | K-LD7 vertical | ~3 s | *Deprecated hardware.* Frames streaming |
| 6 | K-LD7 horizontal | ~3 s | *Deprecated hardware.* Skipped if only one unit is present |
| 7 | Sound trigger end-to-end | ~20 s | Interactive — clap, and the hardware trigger fires a valid capture |

## Reading the output

```
[2/7] OPS243 connectivity ................... ✓ PASS
        /dev/ttyAMA0 • firmware 1.5.2 • 230400 baud • dump ~2.0s
[4/7] OPS243 software trigger ............... ✓ PASS
        Capture received: 4096 I/Q samples • 40556 bytes in 1.83s (22.2 KB/s)
```

Failures carry a hint rather than just a status:

| Failure | Usual cause |
| --- | --- |
| Check 1 — console holds the port | Serial login shell still enabled; see [UART setup](../build/ops243-uart.md#3-prepare-the-pi-uart) |
| Check 2 — no response | USB permissions (`dialout` group), or the wrong port |
| Check 2 — 19,200 baud | The `I5` command never arrived. Check Pi pin 8 → J3 pin 6. |
| Check 3 — "streaming data, CW mode" | Rolling-buffer mode was lost. Re-run [rolling buffer setup](rolling-buffer.md) and power-cycle. |
| Check 4 — no I/Q response | Radar is in the wrong mode, or the dump was truncated by a slow baud |
| Check 7 — timeout | SEN-14262 wiring to `HOST_INT`, or R17 too high a value |

## Flags

| Flag | Effect |
| --- | --- |
| `--ops-port <path>` | Skip auto-detection and use this port |
| `--ops-baud <n>` | Force a baud rate instead of negotiating |
| `--no-interactive` | Skip check 7, which needs you to clap |
| `--require-all` | Treat skips as failures — including the optional horizontal K-LD7 |

`--no-interactive` is what you want in a script or over SSH; the exit code is 0
only if every non-skipped check passed.

!!! tip "Check 3 is the one that catches the classic failure"

    The most common "it worked yesterday" fault is the OPS243 losing its
    persisted rolling-buffer mode. Check 3 tests this specifically — it verifies
    the radar *boots* into the right mode rather than checking that it can be
    put there.

## Related

- [Rolling buffer setup](rolling-buffer.md) — what check 3 verifies
- [Troubleshooting index](../troubleshooting/index.md) — routing by symptom
- [OPS243 → GPIO UART](../build/ops243-uart.md) — what checks 1 and 2 cover
