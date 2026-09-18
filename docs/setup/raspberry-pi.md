# Raspberry Pi Setup Guide

Complete guide for setting up OpenFlight on a Raspberry Pi 5 with the 7" touchscreen display.

## Prerequisites

Make sure you have all the hardware. See the **[Parts List](../get-started/parts.md)** for what to buy.

**Required:**
- Raspberry Pi 5 (4GB+ recommended)
- 7" Touchscreen Display
- MicroSD Card (32GB+)
- 27W USB-C Power Supply (official Pi 5 PSU recommended)
- OPS243-A Doppler Radar + USB cable
- SparkFun SEN-14262 sound detector (wired per the [Sound Trigger Wiring Guide](../build/sound-trigger.md))

**Optional:**
- TI IWR6843LEVM + data cable — measured launch angle and experimental club path; see the [IWR6843 Operator Guide](../iwr6843/index.md)
- Geekworm X1202 or X1206 UPS HAT — portable Pi 5 power using four separately purchased 18650 or 21700 cells; see the [battery monitoring overview](../using/battery.md) and [Geekworm operator guide](../build/battery.md)
- InnoMaker OV9281 global-shutter camera (~$30) — experimental vision work; see [Camera and YOLO Experiments](../development/camera-yolo.md)

**Optional (deprecated):**
- K-LD7 + FTDI adapter (×2) — for launch angle and club path (see [Parts List](../get-started/parts.md)). **Deprecated** — superseded by a more capable radar chip; don't buy for a new build. Supported for existing builds only.

## Setup

### 1. Install Raspberry Pi OS and Dependencies

Use Raspberry Pi Imager to flash **Raspberry Pi OS (64-bit)** to your SD card.

On the first boot, before cloning OpenFlight, you'll likely need a few dependencies to run the setup.sh script.

Run the following command:

```bash
sudo apt update && sudo apt install -y swig liblgpio-dev python3-dev
```

The UI/Electron kiosk shell needs **Node.js 22.12 or newer** to *install*
Electron. Raspberry Pi OS `apt` Node is often 18 or 20 and will print
`EBADENGINE` (or fail) for `electron@44`. A Pi that already has `ui/dist` can
still start: `start-kiosk.sh` falls back to system Chromium until Node is
upgraded and `npm install` in `ui/` succeeds. Install Node 22 LTS before a
first UI build or to actually run the Electron shell:

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs
node -v   # should report v22.12.0 or later
```

If `./scripts/setup/setup.sh` updates `~/.bashrc`, you may need to run `source ~/.bashrc` (or open a new terminal) so your current shell picks up the new environment variables immediately without needing to reboot or re-login.

### 2. Run the setup script

Plug in the OPS243-A (and the K-LD7 adapters if you have them), then:

```bash
cd ~
git clone https://github.com/jewbetcha/openflight.git
cd openflight
./scripts/setup/setup.sh
```

The script installs everything, then walks you through the one-time hardware
configuration with prompts:

1. **Dependencies** — Python venv, packages, UI build, test run
2. **OPS243-A radar** — saves rolling buffer mode to the radar's flash
   (you'll be asked to unplug/replug the radar once)
3. **K-LD7 radars** (deprecated; if you have them) — identifies each radar by
   plugging them in one at a time, so OpenFlight always knows which is which
4. **Geekworm UPS** (optional) — enables native X1202/X1206 battery and
   external-power telemetry, Pi power settings, and desktop battery support
5. **Auto-start on boot** — optional systemd service
6. **Desktop shortcut** — optional
7. **FlightWeb cloud sync** — optional uploader and device linking

Every step can be skipped and the script is **safe to re-run** any time —
it picks up where you left off.

### 3. Start hitting balls

```bash
./scripts/start-kiosk.sh        # Default: rolling buffer + sound trigger
./scripts/start-kiosk.sh --mock # Mock mode (no hardware)
```

Then open `http://localhost:8080` or use the touchscreen.

For the current IWR6843 angle radar, use the measured startup command in the
[IWR6843 Operator Guide](../iwr6843/verify.md#start-openflight). Existing K-LD7
builds use `--kld7 --kld7-mount-tilt <measured-degrees>`; see
[Legacy K-LD7 Setup](../legacy/index.md).

### 4. (Optional) Stream to a golf simulator

To send shots to GSPro, OpenGolfSim, or another supported sim, copy the
example config and enable your simulator:

```bash
cp config/sim.example.json config/sim.json   # then edit host/port + "enabled": true
```

See **[Simulator Connectors](../using/simulator/index.md)** for the full guide.

---

## What the Script Configures (Reference)

You don't need this section unless something went wrong or you prefer to do
things by hand.

### IWR6843 Angle Radar

The setup script does not configure the IWR6843 — it needs custom firmware
flashed over the ROM bootloader, which requires physically moving a switch on
the board. That is covered end to end in the
**[IWR6843 Operator Guide](../iwr6843/index.md)**.

Do it in this order, and confirm each step works before starting the next:

1. **Move the OPS243 to the Pi GPIO UART** —
   [migration guide](../build/ops243-uart.md). The Pi cannot power both radars
   over USB, so the OPS243 has to vacate the USB port. Validate the OPS on its
   own after rewiring, before the TI board is involved at all.
2. **Flash the IWR6843** — operator guide, *Flash The IWR6843 Firmware*. A
   validated prebuilt image is in `firmware/releases/`, so the TI toolchain is
   not required. You only need the
   [firmware developer guide](../development/firmware.md) to build from source.
3. **Mount, aim, and measure geometry** — operator guide. The geometry values
   are passed on the command line and a wrong one silently biases the launch
   angle instead of erroring, so measure rather than estimate.

Re-flashing is only needed if the image in `firmware/releases/` changes. A
software update alone does not require it — compare the release filename against
what you flashed.

> [!WARNING]
> A **WiFi-equipped OPS243-A cannot use the GPIO UART.** Its WiFi module already
> drives the radar processor's UART receive line, so the Pi cannot send it
> commands, and OpenFlight must be able to reconfigure and rearm the OPS after
> every capture. Use a separately powered USB hub for both radars instead
> (operator guide, Option B).

### Geekworm X1202/X1206 UPS

The optional UPS setup is automated and safe to rerun:

```bash
sudo ./scripts/battery/geekworm/setup.sh
sudo reboot
./scripts/battery/geekworm/setup.sh --verify
```

It enables I2C, native Linux battery and charger devices, the Pi 5 EEPROM power
settings required by Geekworm, and the Raspberry Pi taskbar compatibility
package. It does not install automatic shutdown or charging-control services.

Cell type, board-revision power limits, physical installation, every system
change, and troubleshooting are documented in the
**[Geekworm X1202/X1206 Operator Guide](../build/battery.md)**.

### K-LD7 Device Names (Deprecated Hardware)

Existing K-LD7 builds need stable `/dev/kld7_vertical` and
`/dev/kld7_horizontal` names because USB adapter numbers can swap after a
reboot. Run the device wizard, then follow the legacy guide:

```bash
./scripts/setup/setup_kld7_devices.sh
./scripts/setup/setup_kld7_devices.sh --show
```

The wizard also installs the required FTDI low-latency rule. See
[Legacy K-LD7 Setup](../legacy/index.md) for mounting and startup, and
[K-LD7 Troubleshooting](../legacy/troubleshooting.md) for serial failures.

### Desktop Launcher And Startup Splash

OpenFlight can display component progress immediately after a desktop launch
without opening a terminal window. Install or refresh the user-local wrapper
and desktop entry with:

```bash
cd ~/openflight
scripts/setup/install_desktop_launcher.sh
```

The installer uses checkout-specific launcher and desktop filenames, preserving
multiple OpenFlight installations on the same Pi. Machine-specific device paths
and calibration remain outside Git. See the
[Startup Splash Screen](splash-screen.md) guide for configuration, screenshots,
error recovery, updating existing launchers, and rollback.

### Auto-Start on Boot

The setup script installs and enables a systemd service configured for your
username and install path.

<details>
<summary>Manual steps and service management</summary>

```bash
# Install (adjust User= and paths in the file if your username isn't the default)
sudo cp ~/openflight/scripts/setup/openflight.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable openflight
sudo systemctl start openflight
```

Management:

```bash
sudo systemctl status openflight --no-pager   # Check status
journalctl -u openflight -f                   # View logs
sudo systemctl stop openflight                # Stop
sudo systemctl restart openflight             # Restart
sudo systemctl disable openflight             # Disable auto-start
```

To modify the service:

```bash
sudo nano /etc/systemd/system/openflight.service
sudo systemctl daemon-reload
sudo systemctl restart openflight
```

</details>

## Running OpenFlight

### Kiosk Mode (Fullscreen — Recommended)

```bash
./scripts/start-kiosk.sh        # Default: rolling buffer + sound trigger
./scripts/start-kiosk.sh --mock # Mock mode (no hardware needed)
```

Use the [IWR6843 startup steps](../iwr6843/verify.md#start-openflight) or
[Legacy K-LD7 Setup](../legacy/index.md) for angle-radar startup commands.

### Manual Start

```bash
openflight-server                # With radar
openflight-server --mock         # No hardware
```

Then open `http://localhost:8080`.

### Running Over SSH

```bash
DISPLAY=:0 ./scripts/start-kiosk.sh
```

## Observability (Grafana Cloud)

OpenFlight can ship session logs to Grafana Cloud for long-term analysis.

```bash
sudo ./scripts/setup/setup_alloy.sh
sudo vim /etc/alloy/credentials.env
```

See [observability.md](../using/observability.md) for full setup and LogQL queries.

## Troubleshooting

### Radar Not Detected

```bash
uv run python scripts/hardware-test/diagnose.py

# OPS243 connected through the Pi GPIO UART
uv run python scripts/hardware-test/diagnose.py --ops-port /dev/ttyAMA0
```

### Sound Trigger Not Working

See the [Sound Trigger Wiring Guide — Troubleshooting](../build/sound-trigger.md#troubleshooting).

### K-LD7 Not Connecting

```bash
# Check the device mapping
./scripts/setup/setup_kld7_devices.sh --show

# Test standalone
uv run python scripts/hardware-test/test_kld7.py
```

If the mapping is missing or points at the wrong radar, re-run the wizard:
`./scripts/setup/setup_kld7_devices.sh`. Look for `[KLD7] Connected on
/dev/ttyUSB...` in the server logs. See [K-LD7 Troubleshooting](../legacy/troubleshooting.md)
for "Wrong length reply" and other connection issues.

### Kiosk Window Closes Seconds After Loading

If the UI appears and then vanishes with `GPU process launch failed`,
`Failed to send GetTerminationStatus message to zygote`, and finally
`GPU process isn't usable. Goodbye.` in the terminal, something outside the
window killed Electron's helper processes. The usual culprit is a second
copy of `start-kiosk.sh`, typically a failing boot service restarting in a
loop while you launch by hand:

```bash
sudo systemctl status openflight --no-pager   # "activating (auto-restart)" = looping
journalctl -u openflight -n 40 --no-pager     # the recovery hint is printed here
```

Fix whatever the journal reports, or `sudo systemctl disable openflight` if
you launch from the desktop instead. Current launchers refuse to start while
another instance holds `/tmp/openflight-kiosk-<port>.lock` (exit code 3) and
only ever stop the browser they started, so an old unit file is the one thing
left to update: re-copy `scripts/setup/openflight.service` as shown in
[Auto-Start on Boot](#auto-start-on-boot).

### Service Won't Start

```bash
journalctl -u openflight --no-pager -n 50

# If service is masked
sudo systemctl unmask openflight
sudo cp ~/openflight/scripts/setup/openflight.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable openflight
```

### Slow UI Updates

Check for WebSocket instability:
```bash
journalctl -u openflight -f
```

Look for "Client disconnected/connected" messages.

### Display Issues Over SSH

Use `DISPLAY=:0` before commands that need the Pi's display.

## Useful Commands

```bash
./scripts/setup/setup.sh --deps-only                              # Dependencies only
./scripts/start-kiosk.sh --port 3000                              # Custom web port
uv run python scripts/hardware-test/test_rolling_buffer_persist.py --test
uv run python scripts/hardware-test/test_sound_trigger_hardware.py
uv run pytest tests/ -v
```
