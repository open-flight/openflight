---
icon: lucide/terminal
---

# Running & Modes

`scripts/start-kiosk.sh` is the supported entry point. It handles virtualenv
activation, the UI build, and the server launch — assume it unless a guide says
otherwise.

```bash
scripts/start-kiosk.sh
```

For the complete flag list, see the [CLI reference](../reference/cli.md).

## Run modes

| Mode | Command | What it needs |
| --- | --- | --- |
| **Standard** | `scripts/start-kiosk.sh` | OPS243 + sound trigger |
| **With angle radar** | `… --iwr6843 --radar-port /dev/ttyAMA0` | + IWR6843 on USB, OPS243 on UART |
| **Swing speed** | `… --swing-speed` | OPS243 only — no ball, no trigger |
| **Mock** | `… --mock` | Nothing |
| **Mock swing speed** | `… --mock-swing-speed` | Nothing |
| **Legacy K-LD7** | `… --kld7 --kld7-mount-tilt <deg>` | Deprecated hardware |

### Standard

Rolling buffer plus hardware sound trigger. Ball speed, club speed, an
experimental spin candidate, and carry.

```bash
scripts/start-kiosk.sh
```

### With the angle radar

Adds launch angle, launch direction, and club path. The OPS243 must already be
on the Pi GPIO UART — the IWR6843 needs the USB bus.

```bash
scripts/start-kiosk.sh \
  --iwr6843 \
  --radar-port /dev/ttyAMA0 \
  --iwr6843-tee-m 1.575 \
  --iwr6843-net-m 4.6 \
  --iwr6843-ball-height-m 0.040
```

!!! warning "Measure your own geometry"

    The distances above are examples from one rig. Wrong geometry silently
    corrupts the launch angle rather than failing loudly. See
    [mounting and geometry](../iwr6843/mounting.md#measure-the-geometry).

Add `--inclinometer` if you have fitted the LIS3DH, so enclosure tilt is
compensated rather than assumed.

### Swing speed training

Uses the OPS243 fast speed stream directly — no impact audio, no ball.

```bash
scripts/start-kiosk.sh --swing-speed
```

Tuning flags are documented in
[swing speed training](swing-speed.md) and the
[CLI reference](../reference/cli.md#swing-speed).

### Mock

No hardware at all. Simulated shots for UI work and software checks.

```bash
scripts/start-kiosk.sh --mock
```

## Frequently used flags

| Flag | Effect |
| --- | --- |
| `--web-port <n>` | Web server port (default `8080`) |
| `--host <addr>` | Bind address (default `0.0.0.0`) |
| `--session-location <name>` | Tags the session log — `range`, `home`, … |
| `--log-dir <path>` | Session log directory (default `~/openflight_sessions`) |
| `--no-logging` | Disable session logging entirely |
| `--sim` | Enable simulator connectors from `config/sim.json` |
| `--battery <provider>` | Show battery and external-power status |
| `--debug` | Verbose FFT/CFAR output |
| `--no-ballistics` | Use the legacy carry table instead of the RK4 simulator |
| `--dry-run` | Print the command the script would run, then exit |

`--dry-run` is the quickest way to see exactly what `start-kiosk.sh` will
execute with your flags — useful when a wrapper flag and a server flag share a
name.

## Trigger strategies

`--trigger` selects how a capture is initiated.

| Strategy | Latency | Notes |
| --- | --- | --- |
| `sound` | ~10 µs | Hardware: SEN-14262 `GATE` → `HOST_INT`. What production uses. |
| `speed` | ~5–6 ms | Radar speed detection initiates the capture. Min ball speed rises to 35 mph. |
| `threshold` | — | Magnitude threshold |
| `polling` | — | Default in the raw server; the kiosk script selects `sound` |

## Running as a service

For auto-start on boot, kiosk fullscreen, and the over-SSH variant, see
**[auto-start & kiosk mode](../setup/auto-start.md)**.

## Stopping

`Ctrl+C` in the foreground. Under systemd:

```bash
sudo systemctl stop openflight
```
