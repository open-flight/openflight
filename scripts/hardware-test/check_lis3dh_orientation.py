#!/usr/bin/env python3
"""Check that the LIS3DH is mounted with its +Y arrow pointing at the ball.

Takes a reading with the rig level, asks you to lift the front (the side that
faces the ball), and takes another. With +Y toward the ball, pitch rises and no
sign flip is needed. Exits 0 when the mounting is correct, 1 when it is not,
and 2 when the sensor cannot be read.
"""

import argparse
import sys
import time

from openflight.inclinometer import LIS3DH, LIS3DHIdentityError
from openflight.inclinometer.orientation import check_mounting


def _collect(sensor: LIS3DH, count: int, interval: float) -> list:
    samples = []
    for _ in range(count):
        samples.append(sensor.read())
        time.sleep(interval)
    return samples


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bus", type=int, default=1, help="I2C bus number (default: 1)")
    parser.add_argument("--address", type=lambda value: int(value, 0), default=0x18)
    parser.add_argument("--samples", type=int, default=20, help="Readings per pose (default: 20)")
    parser.add_argument("--interval", type=float, default=0.05)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be at least 1")

    try:
        sensor = LIS3DH(bus_number=args.bus, address=args.address)
        sensor.initialize()
    except (OSError, LIS3DHIdentityError) as exc:
        print(f"Cannot read the LIS3DH on I2C-{args.bus} at 0x{args.address:02x}: {exc}")
        print("Check the wiring and that I2C is enabled. See docs/build/inclinometer.md.")
        return 2

    print("The LIS3DH belongs in the LEFT floor bay (looking downrange), with the")
    print("Y arrow on the board pointing at the ball: the I2 / A1-A3 / SDO / CS pin")
    print("row faces downrange.")
    print()
    try:
        input("1. Stand the rig level on its feet and keep it still. Press Enter... ")
        level = _collect(sensor, args.samples, args.interval)
        input(
            "2. Lift the FRONT of the rig (the side facing the ball) about 2-3 cm, for\n"
            "   example onto a book, and hold it still. Press Enter... "
        )
        lifted = _collect(sensor, args.samples, args.interval)
    except OSError as exc:
        print(f"Lost the LIS3DH while reading: {exc}")
        return 2
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return 1
    finally:
        sensor.close()

    result = check_mounting(level, lifted)
    print()
    print(("OK: " if result.ok else "Fix needed: ") + result.message)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
