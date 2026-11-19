#!/usr/bin/env python3
"""
Sound trigger hardware tester for SEN-14262 + OPS243-A.

Tests the hardware sound trigger in rolling buffer mode. The SEN-14262
GATE output is wired to OPS243-A J3 pin 3 (HOST_INT). When a loud sound
(club impact) is detected, the radar dumps its rolling buffer.

Per AN-027 Figure 3:
    SEN-14262 GATE → OPS243-A J3 Pin 3 (HOST_INT)
    Sound detected → GATE goes HIGH → radar dumps buffer → we read I/Q data

This script:
  1. Configures the radar for rolling buffer mode (GC, S#n, 30ksps)
  2. Waits for the hardware trigger (no S! sent — the SEN-14262 does it)
  3. Reads and processes the I/Q buffer dump
  4. Displays speed data and re-arms for the next trigger

Usage:
    uv run python scripts/hardware-test/test_sound_trigger.py
    uv run python scripts/hardware-test/test_sound_trigger.py --pre-trigger 16
    uv run python scripts/hardware-test/test_sound_trigger.py --port /dev/ttyACM0 --swings-only
"""

import argparse
import sys
import time

# Add src to path so we can import openflight
sys.path.insert(0, "src")

from openflight.ops243 import OPS243Radar  # noqa: E402
from openflight.rolling_buffer.processor import RollingBufferProcessor  # noqa: E402

# Radar constants (AN-027)
SAMPLES_PER_SEGMENT = 128
TOTAL_SEGMENTS = 32
SEGMENT_DURATION_MS = SAMPLES_PER_SEGMENT / 30000 * 1000  # ~4.27ms at 30ksps


def process_capture(processor: RollingBufferProcessor, response: str,
                    trigger_count: int, transfer_ms: float,
                    pre_trigger_segments: int, swings_only: bool = False):
    """
    Process the I/Q buffer dump and display results.
    """
    pre_ms = pre_trigger_segments * SEGMENT_DURATION_MS
    post_ms = (TOTAL_SEGMENTS - pre_trigger_segments) * SEGMENT_DURATION_MS

    print()
    print("=" * 60)
    print(f"  TRIGGER #{trigger_count}")
    print("=" * 60)
    print(f"  Capture window: {pre_ms:.1f}ms pre / {post_ms:.1f}ms post (S#{pre_trigger_segments})")
    print(f"  Buffer received: {len(response)} bytes in {transfer_ms:.0f}ms")

    # Parse the I/Q response
    capture = processor.parse_capture(response)

    if capture is None:
        print("  WARNING: Failed to parse I/Q data from buffer dump")
        print("=" * 60)
        print()
        return False

    print(f"  I/Q samples: {len(capture.i_samples)} I, {len(capture.q_samples)} Q")

    # Run FFT processing
    timeline = processor.process_standard(capture)

    print()
    print(f"  Processed {len(timeline.readings)} speed readings across 32 segments:")
    print()

    # Show readings grouped by segment like AN-027 Appendix A output
    outbound_readings = []
    for reading in timeline.readings:
        direction = "OUT" if reading.is_outbound else " IN"
        marker = " <--" if reading.is_outbound and reading.speed_mph >= 15.0 else ""
        print(f"    Seg {reading.segment_index:>2}: {reading.speed_mph:>6.1f} mph "
              f"{direction} (mag: {reading.magnitude:>6.1f}){marker}")
        if reading.is_outbound and reading.speed_mph >= 15.0:
            outbound_readings.append(reading)

    print()
    if not outbound_readings:
        if swings_only:
            print("  No swing detected — discarding (false trigger / nearby player)")
        else:
            print("  No swing detected (false trigger / nearby player)")
        print("=" * 60)
        print()
        return False

    peak_reading = max(outbound_readings, key=lambda r: r.speed_mph)
    print(f"  Peak outbound:  {peak_reading.speed_mph:.1f} mph "
          f"(segment {peak_reading.segment_index}, mag {peak_reading.magnitude:.1f})")
    print(f"  Outbound count: {len(outbound_readings)} readings >= 15 mph")
    print("=" * 60)
    print()
    return True


def run(port: str, pre_trigger_segments: int, swings_only: bool, timeout: float):
    """Connect to radar, configure rolling buffer, wait for hardware triggers."""

    # --- Connect to radar ---
    print("Connecting to radar...")
    radar = OPS243Radar(port=port if port else None)
    radar.connect()
    print(f"Connected on {radar.port}")

    # Get firmware info
    info = radar.get_info()
    version = info.get("Version", "unknown")
    print(f"Firmware: {version}")

    # --- Configure rolling buffer mode ---
    print(f"Configuring rolling buffer mode (S#{pre_trigger_segments})...")
    radar.configure_for_rolling_buffer(pre_trigger_segments=pre_trigger_segments)

    # --- Set up FFT processor ---
    processor = RollingBufferProcessor()

    print()
    print(f"Pre-trigger: S#{pre_trigger_segments} "
          f"({pre_trigger_segments * SEGMENT_DURATION_MS:.1f}ms pre, "
          f"{(TOTAL_SEGMENTS - pre_trigger_segments) * SEGMENT_DURATION_MS:.1f}ms post)")
    print(f"Mode: {'swings only' if swings_only else 'all captures'}")
    print(f"Timeout: {timeout:.0f}s per trigger wait")
    print()
    print("Waiting for SEN-14262 hardware trigger... (Ctrl+C to quit)")
    print()

    trigger_count = 0
    swing_count = 0

    try:
        while True:
            # Wait for hardware trigger (SEN-14262 GATE → HOST_INT)
            start_time = time.time()
            response = radar.wait_for_hardware_trigger(timeout=timeout)
            transfer_ms = (time.time() - start_time) * 1000

            if not response:
                # Timeout — no trigger received, just keep waiting
                print(".", end="", flush=True)
                continue

            trigger_count += 1

            # Re-arm for next capture
            radar.rearm_rolling_buffer()

            # Process and display
            is_swing = process_capture(
                processor, response, trigger_count, transfer_ms,
                pre_trigger_segments, swings_only
            )

            if is_swing:
                swing_count += 1

            print(f"Waiting for next trigger... ({trigger_count} triggers, {swing_count} swings)")
            print()

    except KeyboardInterrupt:
        print()
        print()
        print(f"Done. {trigger_count} triggers, {swing_count} swings detected.")

    finally:
        print("Disconnecting radar...")
        radar.disable_rolling_buffer()
        radar.disconnect()
        print("Done.")


def main():
    parser = argparse.ArgumentParser(
        description="Test SEN-14262 hardware sound trigger with OPS243-A in rolling buffer mode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Wiring (per AN-027 Figure 3):
  SEN-14262 VCC  → 3.3V
  SEN-14262 GND  → GND
  SEN-14262 GATE → OPS243-A J3 Pin 3 (HOST_INT)

How it works:
  The SEN-14262 GATE output goes HIGH when a loud sound is detected.
  The OPS243-A detects the rising edge on HOST_INT and dumps its
  rolling buffer. This script reads the I/Q data and processes it.

Examples:
  # Default settings on the Pi GPIO UART
  uv run python scripts/hardware-test/test_sound_trigger.py

  # Use an OPS connected over USB
  uv run python scripts/hardware-test/test_sound_trigger.py --port /dev/ttyACM0

  # More pre-trigger history (16 segments = ~68ms before impact)
  uv run python scripts/hardware-test/test_sound_trigger.py --pre-trigger 16

  # Only show real swings, discard false triggers silently
  uv run python scripts/hardware-test/test_sound_trigger.py --swings-only

Tips:
  - Adjust the SEN-14262 onboard pot to set trigger sensitivity
  - Use --pre-trigger to tune how much pre-impact data is captured
  - At a driving range, use --swings-only to filter out nearby players
        """,
    )
    parser.add_argument(
        "--port", default="/dev/ttyAMA0",
        help="OPS243 serial port (default: /dev/ttyAMA0 for Pi GPIO UART; "
             "use --port /dev/ttyACM0 for USB)",
    )
    parser.add_argument(
        "--pre-trigger", "-p", type=int, default=12,
        help="Pre-trigger segments S#n, 0-32 (default: 12, ~51ms pre-trigger)"
    )
    parser.add_argument(
        "--swings-only", "-s", action="store_true",
        help="Only show captures with real swing data (>= 15 mph outbound)"
    )
    parser.add_argument(
        "--timeout", "-t", type=float, default=30.0,
        help="Timeout in seconds waiting for each trigger (default: 30)"
    )

    args = parser.parse_args()

    if not 0 <= args.pre_trigger <= 32:
        print("Error: pre-trigger must be between 0 and 32")
        sys.exit(1)

    print()
    print("=" * 60)
    print("  OpenFlight Sound Trigger Hardware Tester")
    print("  (SEN-14262 GATE → OPS243-A J3 Pin 3 HOST_INT)")
    print("=" * 60)
    print()

    run(args.port, args.pre_trigger, args.swings_only, args.timeout)


if __name__ == "__main__":
    main()
