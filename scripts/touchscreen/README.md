# OpenFlight Touchscreen Sleep and Double-Tap Wake

This optional feature allows OpenFlight to turn off a supported
Raspberry Pi touchscreen backlight without stopping the application.

A separate systemd service monitors touchscreen events and restores
the backlight after a double tap.

## Requirements

- Raspberry Pi running Linux
- Touchscreen that continues generating touch events when its backlight is off
- Writable backlight brightness control under /sys/class/backlight/
- Python 3 and python3-evdev
- Sufficient permissions to read input events and write backlight brightness

## Identify your hardware

Find the available backlight controls:

    ls /sys/class/backlight/

Find your touchscreen input device:

    python3 -c "from evdev import InputDevice, list_devices; [print(InputDevice(p).name, p) for p in list_devices()]"

With no configuration, the server and the wake service both use the
panel backlight they find under /sys/class/backlight/ (any
`panel_backlight*`, else the first backlight), and the wake service
watches the Goodix touchscreen, else the first device that reports touches.

This works unconfigured on the Raspberry Pi Touch Display 2: its touch
controller reports itself as `Goodix Capacitive TouchScreen`, and the
backlight's name varies between setups (`panel_backlight@0` on a Pi 5 with
the `vc4-kms-dsi-ili9881-7inch` overlay; this guide previously assumed
`panel_backlight@1`). Double tap wakes the screen at the brightness it had
before sleeping.

Other hardware may require different settings.

## Configuration

The wake script supports these environment variables:

- OPENFLIGHT_BACKLIGHT_PATH: the backlight's `brightness` file, if
  detection picks the wrong one
- OPENFLIGHT_TOUCHSCREEN_NAME: part of the touchscreen's input name
- OPENFLIGHT_WAKE_BRIGHTNESS: wake level when the previous one is unknown
  (default 15, capped at the display's `max_brightness`)

The OpenFlight server also supports OPENFLIGHT_BACKLIGHT_PATH. If you set
it, set the same path for both the server and the wake service.

## Install

Install the evdev dependency if necessary:

    sudo apt install python3-evdev

Copy the example service:

    sudo cp scripts/touchscreen/openflight-screen-wake.service.example /etc/systemd/system/openflight-screen-wake.service

Edit the service:

    sudo nano /etc/systemd/system/openflight-screen-wake.service

Replace /REPLACE_WITH_OPENFLIGHT_PATH with the absolute path to
your OpenFlight repository.

For a custom touchscreen, add an Environment= line for each setting
under [Service].

Enable and start:

    sudo systemctl daemon-reload
    sudo systemctl enable --now openflight-screen-wake.service

Check status:

    systemctl status openflight-screen-wake.service

## Usage

Use the Sleep Screen option in OpenFlight's shutdown dialog.

The application and radar remain running while the backlight is off.

Wait one second, then double tap the touchscreen to restore the backlight.

## Limitations

- Not all touchscreens continue reporting events with the backlight off.
- The default backlight path is Raspberry Pi hardware-specific.
- The example service runs as root for input-device and backlight access.
- A touchscreen without a functioning wake service may require manual
  backlight restoration or a system restart.
- This feature does not suspend the Raspberry Pi or stop OpenFlight.
