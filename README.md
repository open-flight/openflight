<p align="center">
<img src="./ui/public/openflightlogo.svg">
  DIY Golf Launch Monitor using the OPS243-A Doppler Radar.
</p>

<p align="center">
  <a href="https://discord.gg/w8hhG4WVMN">
    <img src="https://img.shields.io/badge/Discord-join%20the%20community-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Join the OpenFlight Discord" />
  </a>
  <a href="https://buymeacoffee.com/colemangolfs">
    <img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-yellow?style=for-the-badge&logo=buy-me-a-coffee&logoColor=white" alt="Buy Me a Coffee" />
  </a>
</p>

<p align="center">
  Related projects:
  <a href="https://github.com/open-flight/openflight-enclosure">Enclosure</a>
  &middot;
  <a href="https://github.com/open-flight/openflight-mobile">Mobile App</a>
</p>

> [!WARNING]
> **This project is in active development.** Features may be incomplete, unstable, or change without notice. Contributions and bug reports are welcome!

## Overview

OpenFlight is an open-source golf launch monitor built around the OPS243-A
Doppler radar, with an optional TI IWR6843 angle radar.

### What It Measures

- **Ball Speed**: 15-200 mph range with ±0.5% accuracy (OPS243-A)
- **Club Speed**: Detected from pre-impact readings (OPS243-A)
- **Smash Factor**: Ball speed / club speed ratio
- **Launch Angle**: Measured by the IWR6843; estimated when no trusted radar angle is available
- **Club Path**: Experimental pre-impact estimate from the IWR6843
- **Spin Rate**: Experimental candidate from rolling-buffer I/Q; not used for carry by default
- **Carry Distance**: Ballistic model with explicit fallbacks for missing measurements

### Hardware at a Glance

| Component | What it does | ~Cost |
|-----------|-------------|-------|
| OPS243-A Radar | Ball speed, club speed, experimental spin | $249 |
| Raspberry Pi 5 | Runs everything | $130 |
| 7" Touchscreen | Shows shot data | $46 |
| SparkFun SEN-14262 | Impact sound trigger for shot capture | $18 |
| Power supply + accessories | | $27 |
| **Subtotal, no angle radar** | | **~$400** |
| TI IWR6843LEVM + cable | Launch angle, experimental club path | $156 |
| **Total with angle radar** | | **~$556** |
| K-LD7 (×2) + FTDI adapters | Launch angle + club path (**deprecated**) | $140 |

Without an angle radar you still get ball speed, club speed, smash factor,
experimental spin, and estimated carry. The angle radar adds measured launch
angle and experimental club path.

> **⚠️ The K-LD7 angle radars are deprecated.** The supported angle radar is now the **TI IWR6843**. Don't buy K-LD7s for a new build; their software support remains for existing builds only. See the [full parts list](docs/get-started/parts.md) for details and links.

> **The IWR6843 needs custom firmware** — the stock TI demo doesn't expose the raw radar cube OpenFlight needs. A validated prebuilt image ships in `firmware/releases/`, so flashing it doesn't require the TI toolchain. See the [IWR6843 Operator Guide](docs/iwr6843/index.md).

## Getting Started

### 1. Get the parts

See the **[Parts List](docs/get-started/parts.md)** for everything you need with purchase links.

### 2. Wire it up

Follow the **[Sound Trigger Wiring Guide](docs/build/sound-trigger.md)** to connect the SEN-14262 to the OPS243-A. The (deprecated) K-LD7 modules connect via USB — no wiring needed.

**Adding the IWR6843 angle radar?** The Pi cannot power both radars over USB, so
the OPS243 moves to the Pi's GPIO UART header while the TI board takes the USB
port. Do it in this order, validating each step before the next — doing both at
once makes any failure ambiguous:

1. **[Move the OPS243 from USB to the Pi GPIO UART](docs/build/ops243-uart.md)** — rewire and confirm the OPS still triggers on its own.
2. **[IWR6843 Operator Guide](docs/iwr6843/index.md)** — wire, flash the firmware, mount, aim, and measure geometry.

If your OPS243-A has **WiFi**, you cannot use the GPIO UART — its WiFi module
already drives the radar's UART receive line. Use a separately powered USB hub
instead; see the operator guide's Option B.

### 3. Set up the Pi

Flash Raspberry Pi OS (64-bit), plug in the radars, then run the interactive setup:

```bash
git clone https://github.com/jewbetcha/openflight.git
cd openflight
./scripts/setup/setup.sh
```

The script installs everything and walks you through the one-time hardware
configuration (radar flash setup, legacy K-LD7 device naming, auto-start, and
optional cloud sync) with prompts. It's safe to re-run any time.
See the **[Raspberry Pi Setup Guide](docs/setup/raspberry-pi.md)** for details and troubleshooting. Touchscreen installations can use the **[Startup Splash Screen](docs/setup/splash-screen.md)** guide to install a terminal-free desktop launcher with immediate startup progress.

### 4. Hit balls

```bash
# Default: rolling buffer mode with sound trigger
scripts/start-kiosk.sh

# With the IWR6843 angle radar (OPS243 on the Pi GPIO UART).
# Geometry values are examples — measure your own; see the operator guide.
scripts/start-kiosk.sh --iwr6843 \
  --ops-port /dev/ttyAMA0 \
  --iwr6843-tee-m 1.372 --iwr6843-net-m 4.064 \
  --iwr6843-tilt-deg 5.5 --iwr6843-radar-height-m 0.229 \
  --iwr6843-ball-height-m 0.021

# With legacy K-LD7 hardware (measure the mount tilt first)
scripts/start-kiosk.sh --kld7 --kld7-mount-tilt <measured-degrees>

# Swing speed training (air swings / speed sticks, no sound trigger)
scripts/start-kiosk.sh --swing-speed

# Development mode (no hardware)
scripts/start-kiosk.sh --mock

# With Geekworm X1202/X1206 battery monitoring
scripts/start-kiosk.sh --battery geekworm
```

The IWR6843 example values are not universal. Measure the geometry from the
antenna center and follow the [operator guide](docs/iwr6843/mounting.md#measure-the-geometry);
wrong values bias the result instead of producing an obvious startup error.

Then open http://localhost:8080 or use the touchscreen. Footer tabs switch
between Live, Stats, Shots, Camera, Players, and Debug. Tap the footer logo for
units, theme, and language; the header power icon opens shutdown confirmation.
On Live, tap a metric to pin it top-left while keeping all metrics visible. Use
the Replay action on camera-backed shots to open a touch-friendly slow-motion
impact clip. The MP4 is generated only when Replay is selected and is cached
beside the raw camera capture. For a TV or tablet, use
[TV Display Mode](#tv-display-mode).

### 5. Sync to the cloud (optional)

OpenFlight can push your sessions to the **FlightWeb** cloud so you can review
shots from any device. It's opt-in, and **raw radar data never leaves your
Pi** — only shot results and session metadata are uploaded (verify with
`openflight-cloud push --dry-run`).

`setup.sh` offers to enable this and link your Pi. To do it by hand:

```bash
openflight-cloud link       # pair this Pi (enter a short code in your browser)
openflight-cloud status     # linked? queued? parked?
```

Once linked, sessions sync automatically (on session end and via a ~10-minute
timer that heals wifi outages). See the **[Cloud Sync Guide](docs/using/cloud-sync.md)**
for details.

### TV Display Mode

OpenFlight also serves a fullscreen-friendly browser display for tablets, TV browsers, or a Chrome tab cast to Chromecast.

1. Start OpenFlight as usual with `scripts/start-kiosk.sh`.
2. Find the OpenFlight host on your LAN — its hostname (see below) or its IP address.
3. Open `http://<openflight-host>:8080/display` from another laptop, tablet, or TV browser.
4. For Chromecast, open the display page in Chrome and use Chrome's built-in **Cast** feature to cast the tab.

> **Prefer the hostname over the IP.** Raspberry Pi OS broadcasts its hostname over
> mDNS (Avahi), so `http://openflight.local:8080/display` keeps working even when the
> Pi's DHCP lease expires and it comes back on a different address — a bookmarked IP
> breaks unless you reserved it on your router. Set the name in Raspberry Pi Imager's
> **Hostname** field when you flash the card; the default is `raspberrypi`, i.e.
> `raspberrypi.local`. The viewing device has to support mDNS — macOS, iOS, Windows 10+
> and most Linux desktops do, but some smart-TV browsers don't, so use the IP there.

This is browser/tab casting only. OpenFlight does not include native Cast SDK support yet.

### Swing Speed Training

For air swings and speed-stick training, OpenFlight can use the OPS243-A fast
speed stream directly instead of waiting for impact audio. Start it with
`scripts/start-kiosk.sh --swing-speed`; the server emits `swing_speed` events
with peak club speed, rep duration, reading count, and session stats. See the
**[Swing Speed Training Guide](docs/using/swing-speed.md)** for setup and
tuning options.

## How It Works

### System Architecture

```
SEN-14262 ── trigger ──► OPS243-A ── I/Q ──► RollingBufferMonitor
                                                   │
IWR6843 (optional) ── angle + club path ───────────┤
                                                   ▼
                                           Flask + WebSocket ──► React UI
```

1. **Sound trigger fires** — SEN-14262 detects club impact, triggers OPS243-A HOST_INT
2. **OPS243-A dumps buffer** — I/Q data is analyzed for ball speed, club speed, and an experimental spin candidate
3. **IWR6843 correlates** — The optional angle radar uses the same impact edge for launch angle and experimental club path
4. **Carry computed** — The ballistic model uses trusted measurements and fills missing inputs with documented fallbacks
5. **UI updates** — Shot data emitted via WebSocket to the React frontend

### Doppler Radar Basics

The OPS243-A transmits a 24 GHz signal. When it bounces off a moving object (the golf ball), the frequency shifts proportionally to the object's speed — this is the Doppler effect. At 24.125 GHz, each 1 mph of speed creates a ~71.7 Hz Doppler shift.

### Positioning

Place the OPS243-A **3-5 feet behind the tee**, pointing down the target line:

```
                Ball Flight Direction
                ======================>

[Tee]  ←--- 3-5 ft ---→  [OPS243-A]
```

The IWR6843 has stricter mounting and measurement requirements; use the
[operator guide](docs/iwr6843/mounting.md#mount-and-aim-the-radar) rather than assuming it
shares the OPS243 position.

## Configuration

### Radar Settings for Golf

| Setting        | Value                  | Why                                          |
| -------------- | ---------------------- | -------------------------------------------- |
| Mode           | Rolling buffer         | Raw I/Q capture for precise speeds and experimental spin |
| Sample Rate    | 30 ksps                | Supports up to ~208 mph ball speed           |
| Capture        | 4096 I/Q samples       | ~136 ms around impact                        |
| Trigger        | Sound (SEN-14262)      | ~10 µs hardware latency via HOST_INT         |
| Min Ball Speed | 15 mph                 | Minimum valid ball speed threshold (35 mph for speed-triggered mode) |
| DC Mask        | ~15 mph exclusion zone | Reject body movement and environmental noise |

These are applied automatically — the one-time flash configuration is handled
by the setup script.

### Python API

```python
from openflight.rolling_buffer import RollingBufferMonitor

monitor = RollingBufferMonitor()   # auto-detects the OPS243-A
monitor.connect()
monitor.start()

print("Swing when ready...")
shot = monitor.wait_for_shot(timeout=60)
if shot:
    print(f"Ball Speed: {shot.ball_speed_mph:.1f} mph")
    print(f"Est. Carry: {shot.estimated_carry_yards:.0f} yards")

monitor.stop()
monitor.disconnect()
```

## Limitations

- **Cosine error**: If ball doesn't travel directly toward/away from radar, measured speed will be slightly lower than actual
- **Spin detection**: The live multitaper value is experimental and is not used for carry by default. Short indoor flight windows and multipath make individual readings unreliable; see [Rolling Buffer and Spin Detection](docs/how-it-works/rolling-buffer.md).
- **K-LD7 speed aliasing** (deprecated hardware): The K-LD7 max speed is 62 mph, so it's used only for angle/distance, not speed

## Hardware Diagnostic

To verify the OPS243, sound trigger, and legacy K-LD7 path:

```bash
uv run python scripts/hardware-test/diagnose.py
```

The diagnostic checks the OPS243 transport, rolling-buffer persistence,
software and hardware triggers, and any connected K-LD7 radars. IWR6843 builds
use the [operator guide's first-capture checks](docs/iwr6843/verify.md#verify-the-first-capture).

Missing optional hardware (like the horizontal K-LD7) is reported as a skip rather than a failure. Pass `--require-all` to fail on skips, or `--no-interactive` to skip the sound-trigger prompt in unattended runs.

## Project Structure

```
openflight/
├── src/openflight/
│   ├── ops243.py              # OPS243-A radar driver
│   ├── launch_monitor.py      # Shot detection & club/ball separation
│   ├── server.py              # Flask server and shot orchestration
│   ├── session_logger.py      # JSONL session logging
│   ├── rolling_buffer/        # OPS capture and signal processing
│   ├── iwr6843/               # Current angle radar
│   ├── kld7/                  # Legacy angle radar
│   ├── sim/                   # Simulator connector framework
│   └── cloud/                 # FlightWeb uploader
├── ui/                        # React frontend
├── scripts/                   # Utility & setup scripts
├── docs/                      # Documentation
└── pyproject.toml
```

## Community

Join the **[OpenFlight Discord](https://discord.gg/w8hhG4WVMN)** to ask build
questions, share results, and follow development.

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

Areas of interest:

- **Better spin detection**: A dechirped Doppler-sideband estimator is in development (`scripts/analysis/replay_spin_dechirp.py`) — help validating it against launch-monitor truth data is especially welcome
- **Mobile app**: Bluetooth connection to phone

### Running Tests

```bash
uv run pytest tests/ -v
```

## Documentation

📖 **Full documentation site: <https://openflight.dev/docs/>**

Build it locally with `make docs` (serves at `localhost:8000`).

- **[Parts List](docs/get-started/parts.md)** — What to buy
- **[Sound Trigger Wiring](docs/build/sound-trigger.md)** — How to wire the sound trigger
- **[Raspberry Pi Setup](docs/setup/raspberry-pi.md)** — Full setup guide
- **[Battery Monitoring](docs/using/battery.md)** — Provider architecture, UI states, and shared Pi support
- **[Geekworm X1202/X1206 Operator Guide](docs/build/battery.md)** — Batteries, Pi setup, native telemetry, and warnings
- **[IWR6843 Operator Guide](docs/iwr6843/index.md)** — Wire, flash, mount, aim, and calibrate the angle radar
- **[LIS3DH Inclinometer Setup](docs/build/inclinometer.md)**: Add enclosure-level compensation to IWR6843 tilt
- **[OPS243 USB → GPIO UART Migration](docs/build/ops243-uart.md)** — Required before adding the IWR6843
- **[IWR6843 Firmware Developer Guide](docs/development/firmware.md)** — Build the firmware from source (not needed to flash the prebuilt image)
- **[Enclosure & Case](docs/build/enclosure.md)** — The printed IARC v3 case
- **[Hardware Diagnostic](docs/setup/raspberry-pi.md)** — Pi setup, auto-start, and troubleshooting
- **[Simulator Connectors](docs/using/simulator/index.md)** — Stream shots to GSPro, OpenGolfSim, PAR-TEE, and others
- **[Cloud Sync](docs/using/cloud-sync.md)** — Push filtered sessions to FlightWeb
- **[Rolling Buffer & Spin Detection](docs/how-it-works/rolling-buffer.md)** — Production capture and experimental spin details
- **[Dechirped-Sideband Spin Replay](docs/development/spin-replay.md)** — Next-gen spin estimator test bench
- **[Camera and YOLO Experiments](docs/development/camera-yolo.md)** — Optional, non-production vision work
- **[Legacy K-LD7 Setup](docs/legacy/index.md)** — Existing K-LD7 builds only
- **[K-LD7 Ball Detection Theory](docs/legacy/ball-detection-theory.md)** — How angle detection works (deprecated hardware)
- **[Observability & Log Shipping](docs/using/observability.md)** — Ship logs to Grafana Cloud
- **[Contributing Guide](CONTRIBUTING.md)** — How to contribute
- **[Changelog](docs/changelog.md)** — Version history

## License

GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later) - see LICENSE file.

## Acknowledgments

- [OmniPreSense](https://omnipresense.com/) for the OPS243-A radar and documentation
- The golf hacker community for inspiration
