#!/usr/bin/env python3
"""Capture K-LD7 raw ADC (RADC) data alongside OPS243 speed readings.

Runs both radars simultaneously:
- K-LD7: streams RADC at 3 Mbaud (main thread), matching production
- OPS243: rolling buffer mode with hardware sound trigger (background thread),
  captures I/Q data on each trigger and re-arms for the next shot

The OPS243 ball speed anchors the K-LD7 velocity search for offline analysis.

Usage:
    # K-LD7 horizontal, picked via udev symlink (preferred on the Pi).
    # Same for --orientation vertical.
    ./scripts/analysis/capture_kld7_radc.py --orientation horizontal --duration 60

    # K-LD7 only, explicit port
    ./scripts/analysis/capture_kld7_radc.py --port /dev/ttyUSB0 --duration 60

    # Both radars, OPS243 auto-detected
    ./scripts/analysis/capture_kld7_radc.py --orientation horizontal --ops243 --duration 60

    # Both radars, OPS243 port specified explicitly
    ./scripts/analysis/capture_kld7_radc.py --port /dev/ttyUSB0 --ops243-port /dev/ttyACM0 --duration 60

K-LD7 port selection:
    1. --port <path>        explicit override
    2. /dev/kld7_<orient>   udev symlink (deterministic — set up by
                            docs/setup/raspberry-pi.md)
    3. FTDI/CP210x scan     non-deterministic with two radars plugged in;
                            a warning is printed in this case.

Output:
    .pkl file with RADC frames, OPS243 shots, and metadata.
"""

from __future__ import annotations

import argparse
import pickle
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

try:
    from kld7 import KLD7, FrameCode, KLD7Exception
except ImportError:
    print("kld7 package not installed. Reinstall the project: uv pip install -e '.[ui]'")
    sys.exit(1)

# Add src to path for OPS243 import. This script lives in scripts/analysis.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def target_to_dict(target):
    if target is None:
        return None
    return {
        "distance": target.distance,
        "speed": target.speed,
        "angle": target.angle,
        "magnitude": target.magnitude,
    }


def read_all_params(radar):
    """Read all configurable parameters from the K-LD7."""
    param_names = [
        "RBFR", "RSPI", "RRAI", "THOF", "TRFT", "VISU",
        "MIRA", "MARA", "MIAN", "MAAN", "MISP", "MASP", "DEDI",
        "RATH", "ANTH", "SPTH", "DIG1", "DIG2", "DIG3", "HOLD", "MIDE", "MIDS",
    ]
    params = {}
    for name in param_names:
        try:
            params[name] = getattr(radar.params, name)
        except Exception:
            pass
    return params


def configure_for_golf(radar, range_m=5, speed_kmh=100):
    """Configure K-LD7 for golf ball detection."""
    range_settings = {5: 0, 10: 1, 30: 2, 100: 3}
    speed_settings = {12: 0, 25: 1, 50: 2, 100: 3}

    params = radar.params
    params.RRAI = range_settings.get(range_m, 0)
    params.RSPI = speed_settings.get(speed_kmh, 3)
    params.DEDI = 2    # Both directions
    params.THOF = 10   # Max sensitivity
    params.TRFT = 1    # Fast tracking
    params.MIAN = -90
    params.MAAN = 90
    params.MIRA = 0
    params.MARA = 100
    params.MISP = 0
    params.MASP = 100
    params.VISU = 0    # No vibration suppression


def find_kld7_port(orientation: str) -> tuple[str | None, str]:
    """Locate the K-LD7 serial port for the given orientation.

    Strategy (deterministic first):
      1. /dev/kld7_<orientation> udev symlink — preferred, identifies the
         physical radar by FTDI serial number (see docs/setup/raspberry-pi.md).
      2. Fall back to FTDI/CP210x VID + description scan. This finds *a*
         K-LD7 but cannot tell vertical from horizontal when both are
         plugged in, so we return a warning the caller should surface.

    Returns:
        (port, source_description). port is None if nothing was found.
    """
    udev_path = Path(f"/dev/kld7_{orientation}")
    if udev_path.exists():
        return (str(udev_path), f"udev symlink {udev_path}")

    try:
        from serial.tools.list_ports import comports
    except ImportError:
        return (None, "pyserial missing")

    matches = []
    for p in comports():
        desc = (p.description or "").lower()
        mfg = (p.manufacturer or "").lower()
        if any(kw in desc for kw in ["ftdi", "cp210", "usb-serial", "uart"]) or \
                any(kw in mfg for kw in ["ftdi", "silicon labs"]):
            matches.append(p.device)

    if not matches:
        return (None, "no FTDI/CP210x ports found")

    note = (
        f"FTDI scan picked {matches[0]} (orientation NOT verified — both "
        f"K-LD7s look identical to USB; prefer /dev/kld7_<orientation> "
        "udev symlinks)"
    )
    if len(matches) > 1:
        note += f". Other candidates: {matches[1:]}"
    return (matches[0], note)


class OPS243RollingBufferReader:
    """Background OPS243 rolling buffer reader with hardware sound trigger.

    Mirrors the production stack: SoundTrigger + RollingBufferProcessor.
    SEN-14262 GATE → HOST_INT triggers I/Q dump, re-arms for next shot.
    """

    PRE_TRIGGER_SEGMENTS = 12  # Match SoundTrigger default

    def __init__(self, port: str):
        self.port = port
        self.radar = None
        self.processor = None
        self.trigger = None
        self._running = False
        self._thread = None
        self._lock = threading.Lock()
        self.captures = []  # Raw I/Q captures with timestamps
        self.shots = []     # Captures where processing found a valid shot

    def connect(self) -> bool:
        try:
            from openflight.ops243 import OPS243Radar
            from openflight.rolling_buffer.processor import RollingBufferProcessor
            from openflight.rolling_buffer.trigger import SoundTrigger

            self.radar = OPS243Radar(port=self.port)
            self.radar.connect()
            self.radar.configure_for_rolling_buffer(
                pre_trigger_segments=self.PRE_TRIGGER_SEGMENTS,
            )
            self.processor = RollingBufferProcessor()
            self.trigger = SoundTrigger(
                pre_trigger_segments=self.PRE_TRIGGER_SEGMENTS,
            )
            return True
        except Exception as e:
            print(f"OPS243 connection failed: {e}")
            return False

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            # A hardware-triggered OPS dump can take ~7s at 57,600 baud.
            # Give the reader enough time to finish parsing/re-arming before
            # closing the port.
            self._thread.join(timeout=12.0)
            if self._thread.is_alive():
                print("\n  [OPS243] Warning: reader did not stop cleanly; forcing close")
        if self.radar:
            try:
                self.radar.rearm_rolling_buffer(self.PRE_TRIGGER_SEGMENTS)
            except Exception as e:
                print(f"\n  [OPS243] Final re-arm skipped: {e}")
            try:
                self.radar.disconnect()
            except Exception:
                pass

    def _read_loop(self):
        """Mirror production _capture_loop: trigger → process → re-arm."""
        while self._running:
            try:
                # SoundTrigger.wait_for_trigger handles:
                #   wait_for_hardware_trigger → rearm → parse → validate
                capture = self.trigger.wait_for_trigger(
                    radar=self.radar,
                    processor=self.processor,
                    timeout=3.0,  # Short so we can check _running flag
                )
                self.trigger.reset()

                if capture is None:
                    continue

                now = time.time()

                # Store raw I/Q
                capture_entry = {
                    "timestamp": now,
                    "sample_time": capture.sample_time,
                    "trigger_time": capture.trigger_time,
                    "i_samples": capture.i_samples,
                    "q_samples": capture.q_samples,
                }

                # Full processing (FFT + speed/spin), same as monitor
                processed = self.processor.process_capture(capture)
                ball_speed = None
                club_speed = None
                spin = None
                if processed:
                    ball_speed = processed.ball_speed_mph
                    club_speed = processed.club_speed_mph
                    spin = processed.spin

                capture_entry["ball_speed_mph"] = ball_speed
                capture_entry["club_speed_mph"] = club_speed
                capture_entry["spin_rpm"] = spin.spin_rpm if spin else None
                capture_entry["spin_confidence"] = spin.confidence if spin else None
                capture_entry["spin_quality"] = spin.quality if spin else None
                capture_entry["spin_snr"] = spin.snr if spin else None
                capture_entry["spin_modulation_depth"] = (
                    spin.modulation_depth if spin else None
                )
                capture_entry["spin_peak_freq_hz"] = spin.peak_freq_hz if spin else None
                capture_entry["spin_candidate_rpm"] = (
                    round(spin.peak_freq_hz * 60)
                    if spin and spin.peak_freq_hz is not None else None
                )
                capture_entry["spin_seam_cycles"] = spin.seam_cycles if spin else None
                capture_entry["spin_at_lower_rail"] = spin.at_lower_rail if spin else None
                capture_entry["spin_at_upper_rail"] = spin.at_upper_rail if spin else None
                capture_entry["spin_rejection_reason"] = (
                    spin.rejection_reason if spin else None
                )

                with self._lock:
                    self.captures.append(capture_entry)
                    if ball_speed and ball_speed >= 15:
                        self.shots.append(capture_entry)

                speed_str = f"{ball_speed:.1f} mph" if ball_speed else "no speed"
                club_str = f", club: {club_speed:.1f} mph" if club_speed else ""
                spin_str = ""
                if spin and spin.spin_rpm > 0:
                    spin_str = (
                        f", spin: {spin.spin_rpm:.0f} rpm "
                        f"(snr={spin.snr:.1f}, {spin.quality})"
                    )
                elif spin and spin.rejection_reason:
                    spin_str = (
                        f", spin: none ({spin.rejection_reason}, "
                        f"snr={spin.snr:.1f})"
                    )
                print(f"\n  [OPS243] Trigger #{len(self.captures)}: {speed_str}{club_str}{spin_str}")

            except Exception as e:
                print(f"\n  [OPS243] Error: {e}")
                try:
                    if self.radar:
                        self.radar.rearm_rolling_buffer(self.PRE_TRIGGER_SEGMENTS)
                        print("  [OPS243] Re-armed after reader error")
                except Exception as rearm_error:
                    print(f"  [OPS243] Re-arm after error failed: {rearm_error}")
                time.sleep(0.1)

    def get_shots(self):
        with self._lock:
            return list(self.shots)

    def get_captures(self):
        with self._lock:
            return list(self.captures)


def _baseline_clutter_report(frames: list, fft_size: int = 2048,
                             max_speed_kmh: float = 100.0,
                             top_n: int = 10) -> None:
    """Per-frame FFT, median magnitude per bin, print top-N persistent
    peaks. Used to spot static clutter sources (fans, mats, etc.).

    Persistent peaks (high MEDIAN magnitude) are clutter; transient
    peaks (high MAX, low median) are normal moving targets passing
    through the beam during the scan.
    """
    try:
        import numpy as np

        # Reuse the project FFT path so the same DC mask is applied.
        from openflight.kld7.radc import (
            DC_MASK_BINS,
            bin_to_velocity_kmh,
            compute_spectrum,
            parse_radc_payload,
            to_complex_iq,
        )
    except ImportError as e:
        print(f"  (baseline analysis unavailable: {e})")
        return

    radc_frames = [f for f in frames if f.get("radc") is not None]
    if not radc_frames:
        print("  No RADC frames in capture — nothing to analyze.")
        return

    spec_grid = np.zeros((len(radc_frames), fft_size), dtype=np.float32)
    for i, fr in enumerate(radc_frames):
        try:
            ch = parse_radc_payload(fr["radc"])
            iq = to_complex_iq(ch["f1a_i"], ch["f1a_q"])
            spec_grid[i] = compute_spectrum(iq, fft_size=fft_size)
        except (ValueError, KeyError):
            continue

    median_mag = np.median(spec_grid, axis=0)
    # Mask out the DC region — always loud, never useful for clutter detection.
    median_mag[:DC_MASK_BINS] = 0.0
    median_mag[fft_size - DC_MASK_BINS:] = 0.0

    nonzero = median_mag[median_mag > 0]
    noise_floor = float(np.median(nonzero)) if nonzero.size else 0.0

    # Top-N peaks by median magnitude
    top_indices = np.argsort(median_mag)[-top_n:][::-1]

    print()
    print("=" * 70)
    print("  BASELINE CLUTTER SCAN")
    print("=" * 70)
    print(f"  Frames analyzed:  {len(radc_frames)}")
    print(f"  Noise floor:      {noise_floor:.2f} (median of non-zero bins)")
    print()
    print(f"  Top {top_n} persistent peaks (by median magnitude across frames):")
    print(f"  {'rank':>4}  {'bin':>5}  {'velocity':>11}  "
          f"{'med mag':>10}  {'×floor':>7}  {'note':<30}")
    flagged = False
    for rank, b in enumerate(top_indices, 1):
        mag = float(median_mag[b])
        vel = bin_to_velocity_kmh(int(b), fft_size, max_speed_kmh)
        ratio = mag / noise_floor if noise_floor > 0 else 0.0
        note = ""
        if ratio >= 50.0:
            note = "STRONG CLUTTER — investigate"
            flagged = True
        elif ratio >= 10.0:
            note = "elevated; possible clutter"
        print(
            f"  {rank:>4}  {int(b):>5}  {vel:>+8.1f} km/h  "
            f"{mag:>10.2f}  {ratio:>6.1f}×  {note:<30}"
        )
    print()
    if flagged:
        print("  WARNING: persistent-bin magnitudes >50× the noise floor")
        print("  indicate a stationary or repetitive moving clutter source")
        print("  (fan blade, vibrating mat, fluorescent ballast, neighbor's")
        print("  car, etc.) somewhere in the radar's field of view. The")
        print("  live algorithm's OPS-bin penalty will downweight these")
        print("  detections, but heavy clutter can still drop the ball")
        print("  detection rate. Find and remove the source if possible.")
    else:
        print("  No persistent strong clutter detected — radar field of view")
        print("  appears clean. If the live algorithm still misses shots,")
        print("  the issue is more likely with mounting orientation or")
        print("  ball-detection SNR than with clutter.")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="Capture K-LD7 raw ADC data with optional OPS243 speed reference.",
    )
    # K-LD7 args
    parser.add_argument("--port", default=None, help="K-LD7 serial port (auto-detect if not set)")
    parser.add_argument("--baud", type=int, default=3000000, help="K-LD7 baud rate (default: 3000000)")
    parser.add_argument("--orientation", default="vertical", choices=["vertical", "horizontal"])
    parser.add_argument(
        "--include-targets",
        action="store_true",
        help="Also request K-LD7 PDAT/TDAT target frames. Off by default "
        "because production streams RADC only and combined frame streaming "
        "is more prone to serial timeouts.",
    )

    # OPS243 args
    parser.add_argument("--ops243", action="store_true",
                        help="Enable OPS243 capture (auto-detects port unless --ops243-port given)")
    parser.add_argument("--ops243-port", default=None,
                        help="OPS243 serial port (implies --ops243; omit for auto-detect)")

    # General
    parser.add_argument("--duration", type=int, default=60, help="Capture duration in seconds")
    parser.add_argument("--output", default=None, help="Output .pkl path")
    parser.add_argument("--club", default=None, help="Club label for metadata")
    parser.add_argument("--shots", type=int, default=None, help="Expected shot count")
    parser.add_argument("--notes", default=None, help="Freeform notes")
    parser.add_argument(
        "--baseline", action="store_true",
        help="Clutter scan mode. Stream the K-LD7 only (no OPS243, no "
        "shots) and after capture print the FFT bins with the strongest "
        "persistent (median) magnitude. Use to identify static clutter "
        "sources (fans, vibrating mats, etc.) that the live algorithm's "
        "OPS-bin penalty would flag.",
    )
    args = parser.parse_args()

    # Baseline mode: force OPS243 off, set a sensible default duration.
    if args.baseline:
        args.ops243 = False
        args.ops243_port = None
        if args.duration == 60:  # default unchanged → use a shorter scan
            args.duration = 5

    # Resolve K-LD7 port — orientation-aware (prefer udev symlink).
    port = args.port
    port_source = "explicit --port" if port else None
    if port is None:
        port, port_source = find_kld7_port(args.orientation)
        if port is None:
            print(f"No K-LD7 detected ({port_source}). Use --port to specify.")
            sys.exit(1)
        print(f"K-LD7 auto-detect ({args.orientation}): {port_source}")
    else:
        # Explicit port given. If the udev symlinks exist, verify the
        # caller didn't accidentally point at the wrong physical radar.
        expected = Path(f"/dev/kld7_{args.orientation}")
        if expected.exists():
            try:
                expected_resolved = expected.resolve()
                given_resolved = Path(port).resolve()
                if expected_resolved != given_resolved:
                    other = Path(
                        f"/dev/kld7_{'horizontal' if args.orientation == 'vertical' else 'vertical'}"
                    )
                    other_resolved = other.resolve() if other.exists() else None
                    if other_resolved == given_resolved:
                        print(
                            f"WARNING: --port {port} resolves to /dev/kld7_"
                            f"{'horizontal' if args.orientation == 'vertical' else 'vertical'} "
                            f"but --orientation is {args.orientation}. "
                            f"Use --port {expected} or change --orientation."
                        )
                    else:
                        print(
                            f"WARNING: --port {port} does not match "
                            f"{expected} for orientation={args.orientation}. "
                            "Capture will proceed but orientation tag may be wrong."
                        )
            except OSError:
                pass

    # Output path
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
    else:
        output_dir = PROJECT_ROOT / "session_logs"
        output_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = f"-{args.club}" if args.club else ""
        output_path = output_dir / f"kld7_radc_{timestamp}{suffix}.pkl"

    # Connect OPS243 if requested. --ops243-port implies --ops243.
    ops243 = None
    ops243_enabled = args.ops243 or bool(args.ops243_port)
    ops243_port = args.ops243_port
    if ops243_enabled and ops243_port is None:
        # Auto-detect using the same VID/description heuristics as OPS243Radar.
        try:
            from openflight.ops243 import OPS243Radar
            candidates = OPS243Radar.find_radar_ports()
        except Exception as e:
            print(f"OPS243 auto-detect failed: {e}")
            candidates = []
        if not candidates:
            print("OPS243 auto-detect found no radar; specify --ops243-port or omit --ops243.")
        else:
            ops243_port = candidates[0]
            if len(candidates) > 1:
                print(f"OPS243 auto-detect: multiple candidates {candidates}, using {ops243_port}")
            else:
                print(f"OPS243 auto-detect: {ops243_port}")
    if ops243_enabled and ops243_port:
        ops243 = OPS243RollingBufferReader(ops243_port)
        if not ops243.connect():
            print("Continuing without OPS243.")
            ops243 = None

    print("=" * 60)
    print("  K-LD7 Raw ADC Capture")
    print("=" * 60)
    print(f"  K-LD7 port:  {port}  ({port_source})")
    print(f"  K-LD7 baud:  {args.baud}")
    print(f"  OPS243:      {ops243_port or 'disabled'}")
    print(f"  Duration:    {args.duration}s")
    print(f"  Orientation: {args.orientation}")
    print(f"  Output:      {output_path}")
    print(f"  Targets:     {'PDAT/TDAT included' if args.include_targets else 'disabled (RADC only)'}")
    print()

    # Connect K-LD7
    print("Connecting K-LD7...")
    # Use connect_with_recovery to (a) retry past a stuck prior session
    # by sending GBYE-at-3Mbaud between attempts, and (b) install the
    # USB Full Speed short-read patch on _read_packet. Same recovery
    # path the live tracker uses.
    try:
        from openflight.kld7.serial_io import connect_with_recovery
    except ImportError as e:
        print(f"  WARN: connect_with_recovery unavailable ({e}); "
              "falling back to single-attempt connect")
        connect_with_recovery = None  # type: ignore[assignment]

    try:
        if connect_with_recovery is not None:
            kld7 = connect_with_recovery(port, baudrate=args.baud, log=print)
        else:
            kld7 = KLD7(port, baudrate=args.baud)
    except (KLD7Exception, Exception) as e:
        print(f"Error: {e}")
        sys.exit(1)
    print(f"  Connected: {kld7}")

    print("Configuring for golf...")
    configure_for_golf(kld7)
    all_params = read_all_params(kld7)
    print()

    # Start OPS243 background reader
    if ops243:
        ops243.start()
        print("  OPS243 speed reader started")

    # Stream K-LD7 RADC. Production uses RADC only; requesting PDAT/TDAT
    # concurrently has proven more fragile on the Pi/FTDI path and can
    # timeout after only a couple frames.
    frame_codes = FrameCode.RADC
    stream_label = "RADC"
    if args.include_targets:
        frame_codes = frame_codes | FrameCode.PDAT | FrameCode.TDAT
        stream_label = "RADC + PDAT + TDAT"

    metadata = {
        "module": "K-LD7",
        "mode": "RADC",
        "port": port,
        "port_source": port_source,
        "baud_rate": args.baud,
        "orientation": args.orientation,
        "ops243_port": ops243_port,
        "ops243_enabled": ops243 is not None,
        "capture_start": datetime.now().isoformat(),
        "params": all_params,
        "club": args.club,
        "expected_shots": args.shots,
        "notes": args.notes,
        "include_targets": args.include_targets,
        "frame_codes": stream_label,
    }

    frames = []
    radc_count = 0
    invalid_radc_count = 0
    pdat_detection_count = 0
    start_time = time.time()

    print("-" * 60)
    if args.baseline:
        print(f"BASELINE CLUTTER SCAN — streaming for {args.duration}s")
        print("Stand still; do not swing. We're looking for persistent peaks")
        print("from static or repetitively-moving clutter sources.")
    else:
        print(f"Streaming {stream_label} for {args.duration}s (Ctrl+C to stop)")
        if ops243:
            print("OPS243 rolling buffer armed, waiting for sound triggers")
    print("-" * 60)

    current_frame = {"timestamp": time.time()}
    seen_in_frame = set()
    last_status_time = 0.0
    stream_errors = 0
    max_stream_errors = 10

    try:
        while time.time() - start_time < args.duration and stream_errors < max_stream_errors:
            try:
                for code, payload in kld7.stream_frames(frame_codes, max_count=-1):
                    now = time.time()
                    if now - start_time >= args.duration:
                        break

                    if code == "RADC" and (
                        not isinstance(payload, bytes) or len(payload) != 3072
                    ):
                        invalid_radc_count += 1
                        continue

                    if code in seen_in_frame:
                        frames.append(current_frame)
                        current_frame = {"timestamp": now}
                        seen_in_frame = set()

                    seen_in_frame.add(code)

                    if code == "RADC":
                        current_frame["radc"] = payload
                        radc_count += 1
                        stream_errors = 0

                    elif code == "TDAT":
                        current_frame["tdat"] = target_to_dict(payload)
                        stream_errors = 0

                    elif code == "PDAT":
                        current_frame["pdat"] = [target_to_dict(t) for t in payload] if payload else []
                        pdat_detection_count += sum(1 for _ in (payload or []))
                        stream_errors = 0

                    if now - last_status_time >= 0.25:
                        frame_count = len(frames) + (1 if seen_in_frame else 0)
                        elapsed = now - start_time
                        fps = frame_count / elapsed if elapsed > 0 else 0
                        n_captures = len(ops243.get_captures()) if ops243 else 0
                        n_shots = len(ops243.get_shots()) if ops243 else 0
                        print(
                            f"\r  Frames: {frame_count}  RADC: {radc_count}  "
                            f"PDAT: {pdat_detection_count}  "
                            f"FPS: {fps:.1f}  "
                            f"{'OPS: ' + str(n_captures) + ' cap/' + str(n_shots) + ' shots  ' if ops243 else ''}"
                            f"Elapsed: {elapsed:.0f}s",
                            end="",
                            flush=True,
                        )
                        last_status_time = now

                if time.time() - start_time >= args.duration:
                    break

                stream_errors += 1
                print(
                    f"\nK-LD7 stream ended unexpectedly "
                    f"({stream_errors}/{max_stream_errors}); retrying..."
                )
                time.sleep(0.1)

            except KLD7Exception as e:
                stream_errors += 1
                print(f"\nK-LD7 stream error {stream_errors}/{max_stream_errors}: {e}")
                try:
                    kld7._drain_serial()
                except Exception:
                    pass
                time.sleep(0.1)

        if stream_errors >= max_stream_errors:
            print(f"\nK-LD7 stream gave up after {max_stream_errors} consecutive errors")

    except KeyboardInterrupt:
        pass
    finally:
        if seen_in_frame:
            frames.append(current_frame)
        try:
            kld7.close()
        except Exception:
            pass
        try:
            kld7._port = None
        except Exception:
            pass
        if ops243:
            ops243.stop()

    # Gather OPS243 data
    ops243_shots = ops243.get_shots() if ops243 else []
    ops243_captures = ops243.get_captures() if ops243 else []

    metadata["capture_end"] = datetime.now().isoformat()
    metadata["total_frames"] = len(frames)
    metadata["radc_frames"] = radc_count
    metadata["invalid_radc_frames"] = invalid_radc_count
    metadata["pdat_detection_count"] = pdat_detection_count
    metadata["ops243_shot_count"] = len(ops243_shots)
    metadata["ops243_capture_count"] = len(ops243_captures)

    print()
    print()
    print("=" * 60)
    print(f"  K-LD7: {len(frames)} frames ({radc_count} with RADC)")
    if invalid_radc_count:
        print(f"  Invalid RADC payloads skipped: {invalid_radc_count}")
    print(f"  PDAT detections: {pdat_detection_count}")
    if ops243:
        print(f"  OPS243: {len(ops243_captures)} captures, {len(ops243_shots)} shots")
        for i, shot in enumerate(ops243_shots):
            club = f", club: {shot['club_speed_mph']:.1f} mph" if shot['club_speed_mph'] else ""
            ball = f"{shot['ball_speed_mph']:.1f} mph" if shot['ball_speed_mph'] else "no speed"
            print(f"    Shot {i+1}: {ball}{club}")
    print(f"  Saving to {output_path}")

    data = {
        "metadata": metadata,
        "frames": frames,
        "ops243_shots": ops243_shots,
        "ops243_captures": ops243_captures,
    }
    with open(output_path, "wb") as f:
        pickle.dump(data, f)

    print(f"  Done ({output_path.stat().st_size / 1024:.0f} KB)")
    print("=" * 60)

    if args.baseline:
        _baseline_clutter_report(frames)


if __name__ == "__main__":
    main()
