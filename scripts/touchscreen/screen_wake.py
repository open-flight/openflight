
#!/usr/bin/env python3

import os
import time
from pathlib import Path
from evdev import InputDevice, ecodes, list_devices

BACKLIGHT = Path(os.environ.get(
    "OPENFLIGHT_BACKLIGHT_PATH",
    "/sys/class/backlight/panel_backlight@1/brightness",
))
TOUCHSCREEN_NAME = os.environ.get(
    "OPENFLIGHT_TOUCHSCREEN_NAME",
    "Goodix Capacitive TouchScreen",
)

WAKE_BRIGHTNESS = int(os.environ.get("OPENFLIGHT_WAKE_BRIGHTNESS", "15"))
DOUBLE_TAP_WINDOW = 0.6
WAKE_COOLDOWN = 1.0


def find_touchscreen():
    for path in list_devices():
        device = InputDevice(path)
        if TOUCHSCREEN_NAME in device.name:
            return device

    raise RuntimeError("Goodix touchscreen not found")


def brightness():
    return int(BACKLIGHT.read_text().strip())


def wake_screen():
    BACKLIGHT.write_text(str(WAKE_BRIGHTNESS))
    print("Screen awake", flush=True)


def main():
    while True:
        device = None

        try:
            device = find_touchscreen()
            print(
                f"Monitoring {device.name} at {device.path}",
                flush=True
            )

            last_tap = 0.0
            previous_sleep_state = False
            sleep_started = 0.0

            for event in device.read_loop():
                asleep = brightness() == 0
                now = time.monotonic()

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
                        wake_screen()
                        last_tap = 0.0
                        previous_sleep_state = False
                    else:
                        last_tap = now

        except (OSError, RuntimeError) as error:
            print(f"Touchscreen unavailable: {error}", flush=True)
            time.sleep(2)

        finally:
            if device is not None:
                device.close()


if __name__ == "__main__":
    main()
