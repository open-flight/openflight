#!/usr/bin/env python3

import os
import time
from pathlib import Path

try:
    from evdev import InputDevice, ecodes, list_devices
except ImportError:  # checked in main(); keeps the helpers importable for tests
    InputDevice = ecodes = list_devices = None

BACKLIGHT_ROOT = Path("/sys/class/backlight")
TOUCHSCREEN_NAME = os.environ.get(
    "OPENFLIGHT_TOUCHSCREEN_NAME",
    "Goodix Capacitive TouchScreen",
)

WAKE_BRIGHTNESS = int(os.environ.get("OPENFLIGHT_WAKE_BRIGHTNESS", "15"))
DOUBLE_TAP_WINDOW = 0.6
WAKE_COOLDOWN = 1.0


def find_backlight(root=None):
    """OPENFLIGHT_BACKLIGHT_PATH, else the panel backlight under /sys/class/backlight.

    Same rule as find_backlight() in src/openflight/server.py, so the server and
    this service agree without configuration (panel_backlight@0 on a Raspberry
    Pi Touch Display 2).
    """
    configured = os.environ.get("OPENFLIGHT_BACKLIGHT_PATH")
    if configured:
        return Path(configured)
    candidates = sorted((root or BACKLIGHT_ROOT).glob("*/brightness"))
    panels = [path for path in candidates if path.parent.name.startswith("panel_backlight")]
    return (panels or candidates or [None])[0]


def is_touchscreen(device):
    """Whether an input device reports touches (BTN_TOUCH)."""
    return ecodes.BTN_TOUCH in device.capabilities().get(ecodes.EV_KEY, [])


def find_touchscreen():
    devices = [InputDevice(path) for path in list_devices()]
    for device in devices:
        if TOUCHSCREEN_NAME in device.name:
            return device

    for device in devices:
        if is_touchscreen(device):
            return device

    raise RuntimeError(f"No touchscreen found (looked for {TOUCHSCREEN_NAME!r})")


def brightness(backlight):
    return int(backlight.read_text().strip())


def wake_level(last_brightness, max_brightness):
    """The level to wake to: the last level seen awake, else the configured one."""
    level = last_brightness if last_brightness > 0 else WAKE_BRIGHTNESS
    return max(1, min(level, max_brightness))


def wake_screen(backlight, level):
    backlight.write_text(str(level))
    print(f"Screen awake at brightness {level}", flush=True)


def main():
    if InputDevice is None:
        raise SystemExit("python3-evdev is required: sudo apt install python3-evdev")

    while True:
        device = None

        try:
            backlight = find_backlight()
            if backlight is None or not backlight.exists():
                raise RuntimeError("Display backlight not found")
            max_brightness = int((backlight.parent / "max_brightness").read_text().strip())
            device = find_touchscreen()
            print(
                f"Monitoring {device.name} at {device.path}, backlight {backlight}",
                flush=True
            )

            last_tap = 0.0
            previous_sleep_state = False
            sleep_started = 0.0
            last_brightness = 0

            for event in device.read_loop():
                current = brightness(backlight)
                asleep = current == 0
                now = time.monotonic()

                if not asleep:
                    last_brightness = current

                if asleep and not previous_sleep_state:
                    sleep_started = now
                    last_tap = 0.0

                previous_sleep_state = asleep

                if not asleep:
                    last_tap = 0.0
                    continue

                if now - sleep_started < WAKE_COOLDOWN:
                    continue

                if (
                    event.type == ecodes.EV_KEY
                    and event.code == ecodes.BTN_TOUCH
                    and event.value == 1
                ):
                    if 0 < now - last_tap <= DOUBLE_TAP_WINDOW:
                        wake_screen(backlight, wake_level(last_brightness, max_brightness))
                        last_tap = 0.0
                        previous_sleep_state = False
                    else:
                        last_tap = now

        except (OSError, RuntimeError, ValueError) as error:
            print(f"Touchscreen unavailable: {error}", flush=True)
            time.sleep(2)

        finally:
            if device is not None:
                device.close()


if __name__ == "__main__":
    main()
