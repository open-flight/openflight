# K-LD7 Troubleshooting Guide

!!! warning "DEPRECATED HARDWARE"

    The K-LD7 angle radars are deprecated. The supported angle radar is the
    **TI IWR6843** — see the [IWR6843 operator guide](../iwr6843/index.md).
    Do not buy K-LD7s for a new build; this page is kept for existing builds
    and for reading historical session logs.

> **⚠️ DEPRECATED:** The K-LD7 angle radars are deprecated — OpenFlight has moved to a more capable radar chip for angle measurement. This guide is kept for existing K-LD7 builds only.

Common issues with the K-LD7 angle radar and how to resolve them.

## Dual Radar Setup

Two K-LD7 radars measure independent angle planes:
- **Vertical** — launch angle (ball flight elevation)
- **Horizontal** — aim direction (ball flight left/right of target)

### Requirements

| Requirement | Details |
|-------------|---------|
| **Separate FTDI adapters** | Each K-LD7 needs its own 3.3V FTDI USB-to-serial adapter |
| **Free USB controller path** | Each FTDI adapter is USB Full Speed (12 Mbps), so a USB 2.0 port can work. The important part is avoiding two K-LD7 streams on the same saturated controller or hub. |
| **Different USB controllers** | Spread the two K-LD7s across different USB *controllers* on the Pi, not just different ports. Two K-LD7s sharing one xHCI controller can starve each other at 3 Mbaud. See [USB bus arrangement](#usb-bus-arrangement) below. |
| **FTDI latency_timer=1ms** | The FTDI Linux default can be `16ms`; install `sudo scripts/setup/setup_kld7_latency.sh` and verify both K-LD7 startup logs show `latency_timer=1ms`. |
| **Different base frequencies** | Vertical: RBFR=0 (24.05 GHz), Horizontal: RBFR=2 (24.25 GHz). Set automatically by the server. |
| **Stable device names** | Use udev rules to prevent port swaps on reboot (see [setup guide](../setup/raspberry-pi.md#k-ld7-device-names-deprecated-hardware)) |

### USB bus arrangement

The Raspberry Pi 5 exposes two independent xHCI controllers (one per pair of physical ports — the two USB 3.0 sockets share one controller, the two USB 2.0 sockets share the other). When both K-LD7s end up on the same controller, the controller's scheduling can't keep up with two simultaneous 3 Mbaud streams and you'll see one or more of:

- `KLD7Exception: Wrong length reply` on connect
- `KLD7Exception: Timeout waiting for reply` on connect
- `Failed to read all of reply` mid-stream
- One radar's `Stream health (...) Hz` log dropping below 25 Hz
- One radar's `kld7_buffer` snapshots showing significantly fewer frames than the other

**Verify your bus arrangement:**

```bash
ls -l /dev/serial/by-path/
```

Look at the `platform-xhci-hcd.<N>` prefix — the `<N>` is the controller number. Both K-LD7s on `xhci-hcd.0` (or both on `xhci-hcd.1`) is the failure mode. You want one on `xhci-hcd.0` and the other on `xhci-hcd.1`:

```
# Bad — both K-LD7s on controller 0:
platform-xhci-hcd.0-usb-0:1:1.0-port0 -> ../../ttyUSB0    # K-LD7 vertical
platform-xhci-hcd.0-usb-0:2:1.0-port0 -> ../../ttyUSB1    # K-LD7 horizontal
platform-xhci-hcd.1-usb-0:2:1.0       -> ../../ttyACM0    # OPS243

# Good — K-LD7s split across both controllers:
platform-xhci-hcd.0-usb-0:1:1.0-port0 -> ../../ttyUSB0    # K-LD7 vertical (USB 3.0)
platform-xhci-hcd.1-usb-0:1:1.0-port0 -> ../../ttyUSB1    # K-LD7 horizontal (USB 2.0 still works)
platform-xhci-hcd.1-usb-0:2:1.0       -> ../../ttyACM0    # OPS243
```

**Fix:** physically move one of the K-LD7 FTDI adapters to a port served by the *other* controller (typically the USB 2.0 ports on Pi 5). At 3 Mbaud the FTDI runs at USB Full Speed (12 Mbps) which a USB 2.0 port handles fine — the issue is bus contention, not raw bandwidth.

### FTDI latency timer

Linux FTDI adapters often default to a `16ms` USB serial latency timer. That is
fine for interactive serial consoles, but it is too much buffering for K-LD7
RADC timing work and can make stream health and per-frame timestamps worse.

Install the persistent udev rule:

```bash
sudo scripts/setup/setup_kld7_latency.sh
```

The script targets `/dev/kld7_vertical` and `/dev/kld7_horizontal` by FTDI
serial number, writes a persistent rule, applies the value to connected devices,
and reloads udev. If the stable symlinks are not available yet, use:

```bash
sudo scripts/setup/setup_kld7_latency.sh --all-ftdi
```

Verify on the next kiosk start:

```text
[KLD7:vertical] USB serial latency_timer=1ms ...
[KLD7:horizontal] USB serial latency_timer=1ms ...
```

### Starting with dual radars

```bash
scripts/start-kiosk.sh --kld7 --kld7-mount-tilt <measured-degrees>
```

Measure the vertical radar face with a phone inclinometer; a guessed tilt
directly biases the launch angle. The script uses `/dev/kld7_vertical` and
auto-enables `/dev/kld7_horizontal` when that symlink exists. Use the explicit
port and geometry flags in [K-LD7 setup](index.md) only when your rig differs
from the defaults.

### Horizontal radar mounting

Mount the horizontal K-LD7 directly behind the ball, same position as the vertical. The antenna face should point toward the ball/target.

**Sign convention** (looking from behind the ball toward the target):
- **Positive angle** = ball went right of center
- **Negative angle** = ball went left of center

This matches the K-LD7 datasheet convention when the antenna face is oriented with Tx on the right side.

### Horizontal detection rate

The horizontal radar detects the ball less consistently than the vertical (~60-80% vs ~95%). This is because the K-LD7 beam is asymmetric (80° × 34°) — when mounted horizontally, the narrow 34° dimension is in the horizontal plane. The ball may not always produce a strong enough return for detection.

Full shots (100+ mph) have much higher detection rates than half shots. If horizontal angles are missing on some shots, this is normal.

## Connection Issues

### "Wrong length reply" on startup

**Symptom:** Server logs show:
```
[KLD7] Connect attempt 1/5 failed: Wrong length reply
```

**Most likely cause #1: stuck prior session.** The K-LD7 was left at 3Mbaud from a prior session that crashed without sending GBYE. The kld7 library tries to INIT at 115200 baud, but the K-LD7 is still at 3Mbaud and can't parse the packet.

> **Automatic recovery:** The server (and `scripts/analysis/capture_kld7_radc.py`) sends a binary GBYE packet at 3Mbaud between retry attempts to reset the K-LD7 to its idle state. This usually succeeds on attempt 2. If it doesn't recover after 5 attempts, the server exits.
>
> **Manual recovery:** Power cycle the K-LD7 (unplug USB, wait 3 seconds, replug).

**Most likely cause #2: USB bus contention.** Both K-LD7s are plugged into ports served by the same USB controller on the Pi, and the controller can't keep up at 3 Mbaud. See [USB bus arrangement](#usb-bus-arrangement). The retry/GBYE logic can't recover from this because it's a hardware-level scheduling problem, not a stuck-session problem.

### "Timeout waiting for reply" on startup

**Symptom:** Server logs show:
```
[KLD7] Connect attempt 1/5 failed: Timeout waiting for reply
[KLD7] Connect attempt 2/5 failed: Timeout waiting for reply
... (continues to attempt 5/5)
```

Unlike "Wrong length reply", this means the K-LD7 isn't responding at all — even after the GBYE-at-3Mbaud reset between attempts.

**Most likely cause: USB bus contention.** Both K-LD7s on the same USB controller can leave one of them silently unable to transmit. See [USB bus arrangement](#usb-bus-arrangement) — verify with `ls -l /dev/serial/by-path/` that the two K-LD7s are on different `xhci-hcd.<N>` controllers.

Other possibilities:
- Wrong port specified (`--kld7-port` / `--kld7-horizontal-port`).
- Bad USB cable.
- Hardware failure — try a different FTDI adapter.

### "No K-LD7 EVAL board detected"

**Symptom:** Auto-detection can't find the K-LD7 serial port.

**Cause:** The K-LD7 uses an FTDI USB-to-serial adapter which shows up as `/dev/ttyUSB*`. If multiple USB-serial devices are connected, auto-detection may pick the wrong one.

**Fix:** Run the device naming wizard so each radar gets a fixed name, or
specify the port explicitly:
```bash
# Recommended: map the radars to /dev/kld7_vertical / /dev/kld7_horizontal
./scripts/setup/setup_kld7_devices.sh

# Or: explicit port
scripts/start-kiosk.sh --kld7 --kld7-mount-tilt <measured-degrees> \
  --kld7-port /dev/ttyUSB0

# Find available ports
ls /dev/ttyUSB*
uv run python -m serial.tools.list_ports -v
```

### Server exits on K-LD7 failure

If `--kld7` or `--kld7-horizontal` is passed but the radar fails to connect after 5 attempts, the server exits with an error. This is intentional — running without a requested radar would produce incomplete data.

## RADC Streaming Issues

### Measuring real K-LD7 RADC cadence

Use the guarded timing probe when launch-angle extraction is missing frames or
when one K-LD7 orientation appears slower than the other:

```bash
uv run python scripts/hardware-test/probe_kld7_timing.py \
  --port /dev/kld7_vertical \
  --duration 10 \
  --frame-mask RADC,DONE \
  --output /tmp/kld7_vertical_timing.jsonl
```

At the production `RSPI=3` setting, expect roughly 34 RADC frames per second
with low `done_frame_gaps`. If cadence is much lower or gaps are high,
investigate USB scheduling, serial read duration, or requested packet volume
before changing launch-angle selection logic.

Undocumented command probing is available only through `--unsafe-probe` and
requires `--output`. Do not use it in production sessions.

### No RADC frames in buffer

**Symptom:** K-LD7 connects but shots show `angle_source: estimated` instead of `radar`.

**Check the startup logs for:**
```
[KLD7] Stream started: RADC only (3Mbaud, vertical)
[KLD7] First RADC frame received (3072 bytes, vertical)    ← must see this
[KLD7] Stream health: 50 RADC frames (vertical)            ← confirms sustained streaming
```

**If "First RADC frame" never appears:**
- The K-LD7 isn't sending RADC data. Check that the connection is at 3Mbaud.
- RADC frames are 3072 bytes each at ~18 FPS = ~55 KB/s. This requires 3Mbaud.

### Occasional stream errors (horizontal)

**Symptom:** Debug logs show occasional `[KLD7] Stream error` messages on the horizontal radar.

**Cause:** FTDI USB Full Speed (12 Mbps) adapters occasionally deliver short packets at 3Mbaud. The stream has built-in retry logic and recovers automatically. The error counter resets on each successful frame.

**This is normal** with two K-LD7s running simultaneously. The stream continues working. If errors become persistent (10 consecutive failures), the stream stops and you'll need to restart the server.

### Truncated RADC packets

**Symptom:** RADC payload is less than 3072 bytes, or `Stream health (...): N Hz` log lines show one orientation running below 25 Hz.

**Causes:**

1. **USB Full Speed short reads** are normal at 3 Mbaud and now handled in software — `serial_io.install_robust_read_packet()` loops over the read until the full packet arrives, so individual short reads no longer drop frames.
2. **USB controller contention** — if persistent, both K-LD7s are likely on the same xHCI controller. See [USB bus arrangement](#usb-bus-arrangement).
3. **USB 2.0 port without a free controller** — USB 2.0 is fine *if* the port is served by a different controller from the other K-LD7. Two K-LD7s on the same USB 2.0 hub will contend.

## Angle Accuracy

### Launch angle calibration (vertical)

The estimator needs the physical mount, ball distance, and boresight offset to
match real launch angles. Start with the measured mount tilt:

```bash
scripts/start-kiosk.sh --kld7 --kld7-mount-tilt <measured-degrees>
```

To recalibrate:

1. Hit 5–10 shots beside a trusted reference monitor.
2. Compare reported launch angles.
3. Adjust mount/distance only if the physical setup changes; adjust
   `--kld7-angle-offset` for a stable boresight bias.

```bash
scripts/start-kiosk.sh --kld7 --kld7-mount-tilt <measured-degrees> \
  --kld7-angle-offset <calibrated-degrees>
```

### Aim direction calibration (horizontal)

1. Start with `--kld7-horizontal-offset 0`
2. Hit several shots straight at the target
3. The horizontal angle should read near 0° (±2°)
4. If it reads consistently offset, adjust `--kld7-horizontal-offset`

### "RADC extraction returned None"

**Possible causes:**
1. **Ball speed too low** — Below ~35 mph, the ball signal may be too weak to detect.
2. **Ball outside velocity search window** — The search window is ±10 mph around the OPS speed.
3. **Low SNR** — Single-frame detections require SNR ≥ 5.0.

This is normal for some shots — the ball doesn't always produce a strong enough return.

## Velocity Aliasing

The K-LD7 operates at RSPI=3 (100 km/h max speed). Ball speeds above 62 mph alias:

| Ball Speed | Aliased Velocity | FFT Region |
|-----------|-----------------|------------|
| < 62 mph | Positive (no alias) | Bins 0-1024 |
| 62-124 mph | Negative (-100 to 0 km/h) | Bins 1024-2048 |
| 124-186 mph | Positive again (wraps) | Bins 0-1024 |

The RADC extraction handles aliasing automatically using the OPS243 ball speed.

## Hardware Reference

### Antenna spacing

Rx1/Rx2 spacing: 6.223 mm (datasheet). Code uses calibrated value of 8.0 mm for effective electrical spacing.

### Serial port bandwidth

| Baud Rate | Throughput | RADC Capable? |
|-----------|-----------|---------------|
| 115200 | ~11.5 KB/s | No |
| 3000000 | ~300 KB/s | Yes |

### USB requirements

| Device | USB Speed | Notes |
|--------|-----------|-------|
| K-LD7 FTDI adapters | USB 3.0 required | FTDI runs at USB Full Speed (12M) but needs the 3.0 controller's better scheduling |
| OPS243-A | USB 2.0 OK | CDC-ACM, bursty transfers only on trigger |

### Base frequency (RBFR)

| RBFR | Frequency | Use |
|------|-----------|-----|
| 0 | 24.05 GHz (Low) | Vertical radar (default) |
| 1 | 24.15 GHz (Mid) | Available |
| 2 | 24.25 GHz (High) | Horizontal radar |

200 MHz separation prevents RF interference between dual radars.

## Log Lines Reference

### Healthy dual-radar startup
```
[KLD7] Connected on /dev/kld7_vertical at 3000000 baud (attempt 1/5)
[KLD7] Configured: range=5m, speed=100km/h, orientation=vertical, RBFR=0 (Low/24.05GHz)
[KLD7] Ready: port=/dev/kld7_vertical, baud=3000000, range=5m, speed=100km/h, orientation=vertical
[KLD7] Stream started: RADC only (3Mbaud, vertical)
[KLD7] First RADC frame received (3072 bytes, vertical)
[KLD7] Connected on /dev/kld7_horizontal at 3000000 baud (attempt 1/5)
[KLD7] Configured: range=5m, speed=100km/h, orientation=horizontal, RBFR=2 (High/24.25GHz)
[KLD7] Ready: port=/dev/kld7_horizontal, baud=3000000, range=5m, speed=100km/h, orientation=horizontal
[KLD7] Stream started: RADC only (3Mbaud, horizontal)
[KLD7] First RADC frame received (3072 bytes, horizontal)
```

### Per-shot angle extraction
```
[KLD7] Angle extraction: ball_speed=66.1 mph, buffer=68 frames
[KLD7] RADC: examining 68 frames, ball_speed=66.1 mph
[KLD7] RADC: angle=8.3° speed=65.8 mph snr=28.2 conf=0.88 frames=2
[SERVER] Vertical angle: 8.3° (conf=88%, 2 frames)
[SERVER] Horizontal angle: 4.1° (conf=93%, 2 frames)
```

### Recovery from prior crash
```
[KLD7] Connect attempt 1/5 failed: Wrong length reply
[KLD7] Sent GBYE at 3Mbaud to reset prior session
[KLD7] Connected on /dev/kld7_vertical at 3000000 baud (attempt 2/5)
```
