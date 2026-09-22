#!/usr/bin/env python3
"""Capture an OV9281 frame ring around sound-trigger claps on GPIO17."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from openflight.camera.triggered_buffer import (  # noqa: E402
    CameraFrame,
    TriggeredCapture,
    TriggeredFrameBuffer,
    timing_summary,
    unpack_r8_frame,
    unpack_yuv420_y_plane,
)
from openflight.gpio_factory import ensure_lgpio_pin_factory  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trigger-pin", type=int, default=17, help="BCM trigger pin (default: 17)")
    parser.add_argument("--fps", type=float, default=240.0, help="Requested FPS (default: 240)")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=400)
    parser.add_argument("--pre-ms", type=float, default=150.0)
    parser.add_argument("--post-ms", type=float, default=50.0)
    parser.add_argument("--exposure-us", type=int, default=1000)
    parser.add_argument("--gain", type=float, default=4.0)
    parser.add_argument("--rotate-180", action="store_true")
    parser.add_argument(
        "--stream",
        choices=("raw", "main-y"),
        default="raw",
        help="Frame source: raw R8 or cropped/scaled main Y plane (default: raw)",
    )
    parser.add_argument(
        "--scaler-crop",
        metavar="X,Y,W,H",
        help="Optional Picamera2 ScalerCrop in sensor coordinates, e.g. 256,160,768,480",
    )
    parser.add_argument(
        "--captures", type=int, default=0, help="Stop after N captures; 0 runs until Ctrl-C"
    )
    parser.add_argument(
        "--auto-interval-s",
        type=float,
        default=0.0,
        help="Automatically trigger every N seconds instead of waiting for GPIO",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path.home() / "openflight_sessions" / "camera_clap_buffer",
    )
    return parser.parse_args()


def parse_scaler_crop(value: str | None) -> tuple[int, int, int, int] | None:
    """Parse a Picamera2 ScalerCrop tuple."""
    if value is None:
        return None
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 4:
        raise ValueError("--scaler-crop must be X,Y,W,H")
    try:
        x, y, width, height = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError("--scaler-crop must contain integers") from exc
    if width <= 0 or height <= 0:
        raise ValueError("--scaler-crop width and height must be positive")
    return x, y, width, height


def save_pgm(path: Path, image: np.ndarray) -> None:
    """Save a dependency-free grayscale preview."""
    with path.open("wb") as handle:
        handle.write(f"P5\n{image.shape[1]} {image.shape[0]}\n255\n".encode("ascii"))
        handle.write(image.tobytes())


def save_capture(directory: Path, number: int, capture: TriggeredCapture) -> dict:
    """Persist one compact capture and its timing report."""
    shot_dir = directory / f"capture_{number:03d}"
    shot_dir.mkdir(parents=True, exist_ok=False)

    images = np.stack([frame.image for frame in capture.frames])
    sensor_ns = np.asarray([frame.sensor_timestamp_ns for frame in capture.frames], dtype=np.int64)
    host_ns = np.asarray([frame.host_timestamp_ns for frame in capture.frames], dtype=np.int64)
    exposure_us = np.asarray([frame.exposure_us for frame in capture.frames], dtype=np.int32)
    gain = np.asarray([frame.analogue_gain for frame in capture.frames], dtype=np.float32)

    np.savez_compressed(
        shot_dir / "frames.npz",
        frames=images,
        sensor_timestamp_ns=sensor_ns,
        host_timestamp_ns=host_ns,
        exposure_us=exposure_us,
        analogue_gain=gain,
        pre_trigger_count=np.int32(capture.pre_trigger_count),
        trigger_host_timestamp_ns=np.int64(capture.trigger_host_timestamp_ns),
    )

    for label, index in (
        ("first", 0),
        ("trigger", max(0, capture.pre_trigger_count - 1)),
        ("last", len(images) - 1),
    ):
        save_pgm(shot_dir / f"{label}.pgm", images[index])

    summary = timing_summary(capture.frames)
    summary.update(
        {
            "capture": number,
            "pre_trigger_frames": capture.pre_trigger_count,
            "post_trigger_frames": capture.post_trigger_count,
            "trigger_host_timestamp_ns": capture.trigger_host_timestamp_ns,
            "mean_brightness": float(images.mean()),
            "p99_brightness": float(np.percentile(images, 99)),
            "npz_bytes": (shot_dir / "frames.npz").stat().st_size,
        }
    )
    (shot_dir / "metadata.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main() -> int:
    """Run the camera clap capture loop."""
    args = parse_args()
    try:
        from gpiozero import Button
        from picamera2 import Picamera2
    except ImportError as exc:
        print(f"Missing Pi camera/GPIO dependency: {exc}", file=sys.stderr)
        print("Run with uv and the Pi system Python; see the command below.", file=sys.stderr)
        return 2

    pre_frames = math.ceil(args.pre_ms * args.fps / 1000.0)
    post_frames = math.ceil(args.post_ms * args.fps / 1000.0)
    try:
        scaler_crop = parse_scaler_crop(args.scaler_crop)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    ring = TriggeredFrameBuffer(pre_frames, post_frames)
    session_dir = args.outdir / datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir.mkdir(parents=True, exist_ok=False)

    camera = Picamera2()
    frame_duration_us = round(1_000_000 / args.fps)
    config = camera.create_video_configuration(
        main={"size": (args.width, args.height), "format": "YUV420"},
        raw={"size": (args.width, args.height), "format": "R8"},
        controls={
            "AeEnable": False,
            "ExposureTime": args.exposure_us,
            "AnalogueGain": args.gain,
            "FrameDurationLimits": (frame_duration_us, frame_duration_us),
        },
        buffer_count=8,
        display=None,
        encode=None,
    )
    camera.configure(config)
    if scaler_crop is not None:
        camera.set_controls({"ScalerCrop": scaler_crop})

    callback_errors = {"count": 0, "last": ""}

    def on_frame(request) -> None:
        try:
            metadata = request.get_metadata()
            if args.stream == "main-y":
                image = unpack_yuv420_y_plane(
                    request.make_array("main"), args.width, args.height, args.rotate_180
                )
            else:
                image = unpack_r8_frame(
                    request.make_array("raw"), args.width, args.height, args.rotate_180
                )
            ring.add_frame(
                CameraFrame(
                    image=image,
                    sensor_timestamp_ns=int(metadata["SensorTimestamp"]),
                    host_timestamp_ns=time.monotonic_ns(),
                    exposure_us=int(metadata.get("ExposureTime", 0)),
                    analogue_gain=float(metadata.get("AnalogueGain", 0.0)),
                )
            )
        except Exception as exc:  # pylint: disable=broad-except
            callback_errors["count"] += 1
            callback_errors["last"] = str(exc)

    camera.post_callback = on_frame
    button = None
    capture_count = 0
    log_path = session_dir / "session.jsonl"

    try:
        camera.start()
        deadline = time.monotonic() + 5.0
        while ring.buffered_frames < pre_frames and time.monotonic() < deadline:
            time.sleep(0.01)
        if ring.buffered_frames < pre_frames:
            raise RuntimeError(
                f"camera produced only {ring.buffered_frames}/{pre_frames} pre-trigger frames"
            )

        if args.auto_interval_s <= 0:
            ensure_lgpio_pin_factory()
            button = Button(args.trigger_pin, pull_up=False, bounce_time=None)

            def on_trigger() -> None:
                accepted = ring.trigger(time.monotonic_ns())
                print(
                    "\nTRIGGER accepted" if accepted else "\nTRIGGER ignored (capture busy)",
                    flush=True,
                )

            button.when_pressed = on_trigger
        print(
            f"OV9281 armed at {args.width}x{args.height}, requested {args.fps:.1f} fps "
            f"({args.stream})"
        )
        if scaler_crop is not None:
            print(f"ScalerCrop: {scaler_crop}")
        print(
            f"Ring: {pre_frames} pre ({args.pre_ms:.0f} ms requested) + "
            f"{post_frames} post ({args.post_ms:.0f} ms requested)"
        )
        if args.auto_interval_s > 0:
            print(f"Auto trigger: every {args.auto_interval_s:.1f}s; output: {session_dir}")
        else:
            print(f"Sound trigger: BCM{args.trigger_pin}; output: {session_dir}")
            print("Clap to capture. Ctrl-C to stop.")

        next_auto = time.monotonic() + args.auto_interval_s if args.auto_interval_s > 0 else 0.0
        while args.captures == 0 or capture_count < args.captures:
            if args.auto_interval_s > 0 and time.monotonic() >= next_auto:
                accepted = ring.trigger(time.monotonic_ns())
                print(
                    "\nAUTO TRIGGER accepted"
                    if accepted
                    else "\nAUTO TRIGGER ignored (capture busy)",
                    flush=True,
                )
                next_auto += args.auto_interval_s
            capture = ring.wait_for_capture(timeout_s=0.5)
            if capture is None:
                continue
            capture_count += 1
            started = time.monotonic()
            summary = save_capture(session_dir, capture_count, capture)
            summary["save_time_ms"] = (time.monotonic() - started) * 1000.0
            summary["callback_errors"] = callback_errors["count"]
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(summary) + "\n")
            print(
                f"[{capture_count}] {summary['frame_count']} frames "
                f"({summary['pre_trigger_frames']} pre/{summary['post_trigger_frames']} post), "
                f"{summary['delivered_fps']:.1f} fps, gaps={summary['gap_count']}, "
                f"brightness={summary['mean_brightness']:.1f}, "
                f"saved={summary['npz_bytes'] / 1_000_000:.1f} MB"
            )
            print("ARMED - clap again")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if button is not None:
            button.close()
        try:
            camera.stop()
            camera.close()
        except KeyboardInterrupt:
            # A second Ctrl-C can arrive while Picamera2 joins its preview thread.
            pass

    if callback_errors["count"]:
        print(
            f"WARNING: {callback_errors['count']} camera callback errors; "
            f"last={callback_errors['last']}"
        )
    print(f"Captured {capture_count} clap(s). Session: {session_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
