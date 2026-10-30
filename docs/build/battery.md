# Geekworm X1202/X1206 Operator Guide

This guide covers installation, Raspberry Pi configuration, verification, and
OpenFlight battery monitoring for the Geekworm X1202 and X1206 UPS boards.
See the [battery monitoring overview](https://github.com/open-flight/openflight/blob/main/README.md) for the shared provider
architecture, UI behavior, and session logging contract.
Both boards use the same telemetry interface:

- A MAX17040/MAX17043-compatible fuel gauge at I2C address `0x36`
- GPIO6 high when external input power is available
- GPIO16 for charging control, which OpenFlight intentionally does not use

OpenFlight only monitors the UPS. It does not change charging behavior and
does not automatically shut down Linux at low battery levels.

## Choose A Board

Use Geekworm's current product documentation to confirm battery and input-power
requirements before buying cells or an adapter:

- [Geekworm X1202 product page](https://geekworm.com/products/x1202)
- [Geekworm X1202 wiki and battery requirements](https://wiki.geekworm.com/X1202)
- [Geekworm X1206 product page](https://geekworm.com/products/x1206)
- [Geekworm X1206 wiki and revision details](https://wiki.geekworm.com/X1206)

| | X1202 | X1206 |
|---|---|---|
| Battery format | Four 3.7V 18650 cells | Four 3.7V 21700 cells |
| Battery callout | Flat-top, unprotected cells required by Geekworm | Larger 21700 cells; Geekworm advertises up to 20,000mAh total |
| UPS output | 5.1V, up to 5A | 5.1V, up to 6A |
| Board size | 97.4 x 85mm | 108 x 85mm |
| Best fit | Smaller and lighter | Longer runtime and more output headroom |

The four LEDs are coarse voltage bands, not four equal fuel-gauge readings.
OpenFlight uses the MAX17040 state-of-charge model instead, so the LEDs and UI
percentage will not always change together.

> [!WARNING]
> Do not substitute protected cells where Geekworm requires unprotected cells.
> Use four matching, healthy cells of the required size and chemistry, observe
> polarity, and follow Geekworm's handling instructions. Do not mix cell models,
> capacities, ages, or charge states.
>
> **Do not charge the cells below 0 °C (32 °F).** Lithium-ion cells charged
> below freezing plate metallic lithium onto the anode, which permanently
> reduces capacity and can make the cell unsafe. A launch monitor that has been
> outdoors in the cold must be brought inside, or allowed to warm up, before
> external power is connected to the UPS.

### X1206 Input Revisions

Check the revision printed on the X1206 board before selecting a DC adapter:

| Revision | USB-C input | DC5521/XH2.54 input |
|---|---|---|
| X1206 V1.1 | 5V/5A | 5-6V, at least 3A |
| X1206 V2.0 | 5V/5A | 9-18V, at least 3A; Geekworm recommends 12V/5A |

Do not infer the DC voltage from the product name. Supplying a V1.1 board with
the V2.0 adapter voltage can damage it.

## Install The Hardware

1. Shut down the Pi and remove all power.
2. Install all four matching cells with the polarity shown on the UPS holder.
3. Mount the Pi squarely on the UPS and verify that every pogo pin is centered
   and compressed against its Pi pad.
4. Connect the external adapter to either the UPS USB-C input or the supported
   UPS DC input for that board revision.
5. Connect powered peripherals through the UPS or a separately powered hub.
6. Apply power and start the Pi with the UPS power button.

> [!WARNING]
> Do not power the Pi through the Pi's own USB-C socket while it is installed on
> the UPS. Connect input power to the Geekworm board. Do not connect the UPS
> USB-C and DC inputs at the same time.

Poor pogo-pin contact can produce missing I2C data, incorrect charger state,
undervoltage warnings, or a shutdown a few seconds after power-on. Power down
before reseating the boards.

## Configure Raspberry Pi OS

Run the checked-in setup script from the OpenFlight repository root:

```bash
sudo scripts/battery/geekworm/setup.sh
sudo reboot
scripts/battery/geekworm/setup.sh --verify
```

The script is safe to rerun. It performs these changes:

1. Installs `i2c-tools` and `upower` when missing.
2. Loads `i2c-dev` at boot for I2C diagnostics.
3. Enables I2C in `/boot/firmware/config.txt`.
4. Adds the native MAX17040 battery and active-high GPIO6 charger overlays.
5. Sets `PSU_MAX_CURRENT=5000` in the Pi 5 bootloader EEPROM so Raspberry Pi OS
   recognizes the UPS as a 5A-capable supply.
6. Sets `POWER_OFF_ON_HALT=1` so the UPS can remove power after Linux halts.
7. On 64-bit Raspberry Pi OS Trixie, installs OpenFlight's patched `wfplug-batt`
   package so the desktop panel reads the kernel's percentage.

Before changing boot or EEPROM configuration, the script writes timestamped
backups. It reports every change and tells you when a reboot is required.

The script does **not** install the community `x120x-dkms` driver, Geekworm's
automatic shutdown service, or any low-battery poweroff job. OpenFlight's 20%
and 10% warnings remain informational and dismissible. To power off cleanly
when the battery runs low, install the separate, optional
[low-battery shutdown](#low-battery-shutdown) service.

### Desktop Panel Compatibility

The upstream Raspberry Pi battery panel calculates percentage from
`charge_now/charge_full` or `energy_now/energy_full`. The standard MAX17040
kernel driver exposes an already-calculated `capacity` percentage instead, so
the unpatched panel displays 0% even though UPower and OpenFlight have valid
data.

The setup script installs the ARM64 package in
`scripts/battery/packages/`. It is built from Raspberry Pi's
[`pplug-batt`](https://github.com/raspberrypi-ui/pplug-batt) 1.11 source at commit
`c622de2e95bf8ee5d4a0e28f3fdd5e3191bbdbbd`, with the checked-in patch under
`scripts/battery/patches/`, and versioned `1.11+openflight1` so it installs as an
upgrade over Raspberry Pi's `1.11`. The patch adds support for the standard
`/sys/class/power_supply/.../capacity` property, uses the charger's `online`
property to distinguish plugged-in and unplugged states, and clamps full-charge
overshoot to 100%. This avoids a false charging icon when the MAX17040 battery
status is `Unknown` but the GPIO charger reports that external power is offline.

This package affects only Raspberry Pi's desktop taskbar. OpenFlight's own
battery display works without it. Use `--no-panel` if the Pi does not run the
standard 64-bit Raspberry Pi desktop:

```bash
sudo scripts/battery/geekworm/setup.sh --no-panel
```

#### Rebuild The Panel Package

The plugin must match the installed `wf-panel-pi`, so a newer upstream
`wfplug-batt` needs a rebuild rather than a downgrade. On an arm64 Raspberry Pi
OS Trixie Pi, bump `UPSTREAM_COMMIT` and `UPSTREAM_VERSION` in the build script
if upstream moved, then run:

```bash
scripts/battery/build_wfplug_batt.sh --install-deps
```

The script fetches the pinned upstream commit, applies the patch, builds
`wfplug-batt_<version>+openflight<n>_arm64.deb` into `scripts/battery/packages/`,
and prints the version and SHA-256 to copy into `PANEL_PACKAGE`,
`PANEL_PACKAGE_VERSION` and `PANEL_PACKAGE_SHA256` in
`scripts/battery/geekworm/setup.sh`. `--install-deps` installs the Debian build
dependencies with `apt-get`.

### Optional Pi USB Current Setting

Geekworm also recommends `usb_max_current_enable=1` when high-current USB
peripherals are powered directly from the Pi's USB ports. OpenFlight does not
set it automatically because increasing the USB current limit should be a
deliberate power-budget decision. Prefer a separately powered hub for multiple
radars.

## What The Boot Configuration Contains

The setup script adds these settings under an `[all]` section when absent:

```ini
dtparam=i2c_arm=on
dtoverlay=i2c-sensor,max17040
dtoverlay=gpio-charger,gpio=6,active_low=0,gpio_pull=down,type=mains
```

The first overlay binds Linux's `max17040_battery` driver to I2C address
`0x36`. The second binds `gpio_charger` to GPIO6, where low means adapter
failure and high means external power is good.

After reboot, the expected native devices are:

```text
/sys/class/power_supply/battery
/sys/class/power_supply/charger
```

## Verify The Installation

Run the automated checks:

```bash
scripts/battery/geekworm/setup.sh --verify
```

The command verifies the EEPROM settings, overlays, kernel modules, battery
percentage and voltage, charger state, UPower data, and desktop panel package.

Manual checks are also useful:

```bash
# The bound kernel driver appears as UU at address 36.
sudo i2cdetect -y 1

cat /sys/class/power_supply/battery/capacity
cat /sys/class/power_supply/battery/voltage_now
cat /sys/class/power_supply/charger/online
upower -i /org/freedesktop/UPower/devices/battery_battery
vcgencmd get_throttled
```

Expected results:

- I2C address `36` is present before the overlay binds it, or `UU` after it is
  owned by the kernel driver.
- `capacity` is an integer near 0-100. Some MAX17040 readings briefly report
  101 or 102 at full charge; OpenFlight and the patched panel display that as
  100%.
- `voltage_now` is in microvolts, typically about 3,400,000-4,230,000.
- `charger/online` changes from `1` to `0` when UPS input power is removed.
- `get_throttled=0x0` means the Pi has no current or historical undervoltage or
  throttling flags since boot.

## Start OpenFlight

Enable UPS monitoring explicitly:

```bash
scripts/start-kiosk.sh --battery geekworm
```

With the flag enabled, OpenFlight shows:

- Battery percentage from the MAX17040 fuel gauge
- A charging/plug icon while GPIO6 reports external power
- A normal battery state after the adapter is unplugged
- Dismissible warnings at 20% and 10% while discharging
- A red unavailable state if telemetry cannot be read

The monitor retries every five seconds. A temporary hardware read failure does
not stop shot capture or shut down the Pi.

## Low-Battery Shutdown

The X1202/X1206 take unprotected cells, so nothing else stops the Pi from
draining them. An optional systemd service powers the Pi off before that
happens. It runs independently of OpenFlight and works whether or not
OpenFlight is running or started with `--battery`.

```bash
scripts/battery/install_battery_guard.sh
```

Every 20 seconds the service reads the kernel's battery and charger devices.
When external power is offline and the battery is at or below **10%** or
**3.40 V** for **three readings in a row**, it stops OpenFlight (the
`openflight` service and any running server, waiting up to 15 seconds), then
runs `systemctl poweroff`. With `POWER_OFF_ON_HALT=1` from the setup script, the
Pi then removes power. The voltage limit catches a fuel gauge that has not
learned the cells yet; three readings ignore a brief dip under load.

It does nothing while external power is connected, and it does not act when
the battery or charger state cannot be read: a missing driver or poor pogo-pin
contact therefore also disables the protection. OpenFlight's red `--` battery
state is the warning for that.

Change the limits in `/etc/default/openflight-battery-guard`, then restart:

```bash
sudo nano /etc/default/openflight-battery-guard
sudo systemctl restart openflight-battery-guard
```

Check what it has done, and remove it:

```bash
journalctl -u openflight-battery-guard
scripts/battery/install_battery_guard.sh --uninstall
```

To test the shutdown on the real hardware, temporarily set
`OPENFLIGHT_BATTERY_GUARD_MIN_PERCENT` above the current charge, restart the
service, follow the journal, and disconnect the UPS input. Restore the limit
afterwards.

After a low-battery shutdown the UPS still draws a small current from the
cells. Recharge them soon, and remove them for long-term storage.

## Session Logs

Session JSONL files include `power_status` entries on startup, power-state
changes, warning-threshold changes, telemetry failure or recovery, and at least
once per minute while unchanged. Each entry includes:

- `state`
- `provider`
- `battery_percent`
- `battery_voltage_v`
- `external_power`
- `available`
- `error`
- `updated_at`

When `--battery geekworm` is absent, OpenFlight does not access the UPS hardware
or show a battery indicator.

## Troubleshooting

### OpenFlight Shows A Red `--`

Check the native files first:

```bash
ls -l /sys/class/power_supply
cat /sys/class/power_supply/battery/capacity
cat /sys/class/power_supply/charger/online
```

If the files are missing, confirm the overlays and reboot. If only the battery
is missing, power down and reseat the pogo pins carrying GPIO2/GPIO3 I2C. If
only charger state is wrong, inspect GPIO6/physical pin 31 contact and confirm
the adapter is connected to the UPS input rather than the Pi USB-C socket.

### Raspberry Pi's Taskbar Says 0%

OpenFlight and UPower may still be correct. Check the compatibility package:

```bash
dpkg-query -W wfplug-batt
dpkg -V wfplug-batt
sudo scripts/battery/geekworm/setup.sh
```

An OS update may replace the panel plugin with an upstream version. If that
version is not newer than the bundled build, rerunning the setup script
restores the patched package. If it is newer, the setup script warns that a
rebuild is needed instead of downgrading; follow
[Rebuild The Panel Package](#rebuild-the-panel-package).

### LEDs And Percentage Disagree

The LEDs are voltage thresholds; OpenFlight displays fuel-gauge SOC. Voltage
changes immediately with charging and load, while modeled SOC can move more
slowly and may need several full charge/discharge cycles to settle.

### External Power State Is Backwards Or Stuck

Geekworm documents GPIO6 as active-high: high means power good and low means
power loss. Keep `active_low=0`. Do not invert the software to conceal a low
signal while an adapter is connected; verify the UPS input and pogo contact.

### Roll Back Pi Changes

The setup script prints the boot-config and EEPROM backup paths it creates.
Restore the appropriate backup, then reboot. To return to Raspberry Pi's
unpatched repository package, first identify the repository version and then
request that version explicitly:

```bash
apt-cache policy wfplug-batt
sudo apt install --allow-downgrades wfplug-batt=<repository-version>
```

Removing the overlays disables native OS telemetry. OpenFlight can fall back to
direct I2C/GPIO reads, but native devices are the supported Pi configuration.
