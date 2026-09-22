#!/usr/bin/env python3
"""Sweep OV9281 exposure/gain settings and recommend a capture preset.

This is intended for field setup before a hitting session. Put the enclosure in
its normal position with a ball at impact, then run this script to capture the
same static scene at several exposure/gain settings.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np


def parse_csv_ints(value: str) -> list[int]:
    """Parse a comma-separated integer list."""
    try:
        values = [int(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a comma-separated integer list") from exc
    if not values or any(item <= 0 for item in values):
        raise argparse.ArgumentTypeError("values must be positive")
    return values


def parse_csv_floats(value: str) -> list[float]:
    """Parse a comma-separated float list."""
    try:
        values = [float(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a comma-separated number list") from exc
    if not values or any(item <= 0 for item in values):
        raise argparse.ArgumentTypeError("values must be positive")
    return values


def parse_scaler_crop(value: str | None) -> tuple[int, int, int, int] | None:
    """Parse a Picamera2 ScalerCrop tuple."""
    if value is None:
        return None
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("--scaler-crop must be X,Y,W,H")
    try:
        x, y, width, height = (int(part) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--scaler-crop must contain integers") from exc
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("--scaler-crop width and height must be positive")
    return x, y, width, height


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=400)
    parser.add_argument("--fps", type=float, default=300.0)
    parser.add_argument(
        "--exposures-us",
        type=parse_csv_ints,
        default=parse_csv_ints("250,500,750,1000,1500,2000,2500"),
        help="Comma-separated exposure sweep in microseconds",
    )
    parser.add_argument(
        "--gains",
        type=parse_csv_floats,
        default=parse_csv_floats("1,2,4"),
        help="Comma-separated analogue gain sweep",
    )
    parser.add_argument("--frames", type=int, default=20, help="Frames measured per setting")
    parser.add_argument("--warmup-frames", type=int, default=8)
    parser.add_argument(
        "--settle-ms",
        type=float,
        default=100.0,
        help="Delay after changing exposure/gain before measuring",
    )
    parser.add_argument("--rotate-180", action="store_true")
    parser.add_argument(
        "--stream",
        choices=("raw", "main-y"),
        default="raw",
        help="Frame source to evaluate (default: raw)",
    )
    parser.add_argument(
        "--scaler-crop",
        type=parse_scaler_crop,
        metavar="X,Y,W,H",
        help="Optional Picamera2 ScalerCrop in sensor coordinates",
    )
    parser.add_argument("--target-mean-low", type=float, default=80.0)
    parser.add_argument("--target-mean-high", type=float, default=150.0)
    parser.add_argument("--max-clipped-pct", type=float, default=1.0)
    parser.add_argument("--max-dark-pct", type=float, default=5.0)
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path.home() / "openflight_sessions" / "camera_exposure_calibration",
    )
    return parser.parse_args()


def save_pgm(path: Path, image: np.ndarray) -> None:
    """Save a dependency-free grayscale preview."""
    with path.open("wb") as handle:
        handle.write(f"P5\n{image.shape[1]} {image.shape[0]}\n255\n".encode("ascii"))
        handle.write(image.tobytes())


def unpack_r8_frame(raw: np.ndarray, width: int, height: int, rotate_180: bool) -> np.ndarray:
    """Convert the Pi's unpacked OV9281 R8 buffer into a compact image."""
    if raw.ndim != 2 or raw.shape[0] < height:
        raise ValueError(f"unexpected raw frame shape {raw.shape}")

    rows = raw[:height]
    if rows.dtype == np.uint16:
        image = (rows[:, :width] >> 8).astype(np.uint8)
    elif rows.dtype == np.uint8 and rows.shape[1] >= width * 2:
        # PiSP exposes R8 as an R16 stream; the luminance byte is the high byte.
        image = rows[:, 1 : width * 2 : 2].copy()
    elif rows.dtype == np.uint8 and rows.shape[1] >= width:
        image = rows[:, :width].copy()
    else:
        raise ValueError(f"unexpected raw frame layout {raw.shape} {raw.dtype}")

    if rotate_180:
        image = np.ascontiguousarray(image[::-1, ::-1])
    return image


def unpack_yuv420_y_plane(
    main: np.ndarray, width: int, height: int, rotate_180: bool
) -> np.ndarray:
    """Extract the luma plane from Picamera2's YUV420 main stream."""
    if main.ndim == 2:
        if main.shape[0] < height or main.shape[1] < width:
            raise ValueError(f"unexpected YUV420 frame shape {main.shape}")
        image = main[:height, :width].copy()
    elif main.ndim == 3:
        if main.shape[0] < height or main.shape[1] < width:
            raise ValueError(f"unexpected YUV420 frame shape {main.shape}")
        image = main[:height, :width, 0].copy()
    else:
        raise ValueError(f"unexpected YUV420 frame shape {main.shape}")

    if rotate_180:
        image = np.ascontiguousarray(image[::-1, ::-1])
    return image


def score_result(result: dict, args: argparse.Namespace) -> float:
    """Rank settings by useful dynamic range and low clipping."""
    mean = result["mean"]
    clipped = result["clipped_pct"]
    dark = result["dark_pct"]
    blur_penalty = result["exposure_us"] / 3000.0

    target_mid = (args.target_mean_low + args.target_mean_high) / 2.0
    target_half_width = (args.target_mean_high - args.target_mean_low) / 2.0
    mean_error = abs(mean - target_mid) / max(1.0, target_half_width)

    # Clipping destroys edge information, so treat it as much worse than being
    # somewhat too bright/dark.
    penalty = mean_error
    penalty += max(0.0, clipped - args.max_clipped_pct) * 12.0
    penalty += max(0.0, dark - args.max_dark_pct) * 0.25
    penalty += blur_penalty * 0.15
    return penalty


def summarize_images(images: np.ndarray, exposure_us: int, gain: float) -> dict:
    """Calculate image exposure statistics for one setting."""
    return {
        "exposure_us": exposure_us,
        "gain": gain,
        "frames": int(images.shape[0]),
        "mean": float(images.mean()),
        "p50": float(np.percentile(images, 50)),
        "p95": float(np.percentile(images, 95)),
        "p99": float(np.percentile(images, 99)),
        "clipped_pct": float(np.mean(images >= 250) * 100.0),
        "dark_pct": float(np.mean(images <= 5) * 100.0),
    }


def main() -> int:
    """Run the exposure/gain sweep."""
    args = parse_args()
    if args.frames < 1 or args.warmup_frames < 0:
        print("--frames must be positive and --warmup-frames cannot be negative", file=sys.stderr)
        return 2
    if args.fps <= 0 or args.width <= 0 or args.height <= 0:
        print("--width, --height, and --fps must be positive", file=sys.stderr)
        return 2

    try:
        from picamera2 import Picamera2  # pylint: disable=import-error,import-outside-toplevel
    except ImportError as exc:
        print(f"Missing Pi camera dependency: {exc}", file=sys.stderr)
        print(
            "Run on the Pi with: uv run --no-project --python /usr/bin/python3 ...", file=sys.stderr
        )
        return 2

    session_dir = args.outdir / datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir.mkdir(parents=True, exist_ok=False)

    frame_duration_us = round(1_000_000 / args.fps)
    camera = Picamera2()
    config = camera.create_video_configuration(
        main={"size": (args.width, args.height), "format": "YUV420"},
        raw={"size": (args.width, args.height), "format": "R8"},
        controls={
            "AeEnable": False,
            "ExposureTime": min(max(args.exposures_us), frame_duration_us),
            "AnalogueGain": max(args.gains),
            "FrameDurationLimits": (frame_duration_us, frame_duration_us),
        },
        buffer_count=8,
        display=None,
        encode=None,
    )
    camera.configure(config)
    if args.scaler_crop is not None:
        camera.set_controls({"ScalerCrop": args.scaler_crop})

    results: list[dict] = []
    try:
        camera.start()
        time.sleep(0.3)
        print(
            f"OV9281 exposure calibration: {args.width}x{args.height} @ {args.fps:.1f}fps "
            f"({args.stream})"
        )
        print(f"Output: {session_dir}")
        print("Place the enclosure/ball in normal setup. Press Enter to start...")
        with open("/dev/tty", encoding="utf-8") as terminal:
            terminal.readline()

        for exposure_us in args.exposures_us:
            if exposure_us >= frame_duration_us:
                print(f"Skipping {exposure_us}us exposure; frame period is {frame_duration_us}us")
                continue
            for gain in args.gains:
                camera.set_controls({"ExposureTime": exposure_us, "AnalogueGain": gain})
                if args.settle_ms > 0:
                    time.sleep(args.settle_ms / 1000.0)
                for _ in range(args.warmup_frames):
                    request = camera.capture_request()
                    request.release()

                images = []
                metadata = []
                for _ in range(args.frames):
                    request = camera.capture_request()
                    try:
                        if args.stream == "main-y":
                            image = unpack_yuv420_y_plane(
                                request.make_array("main"),
                                args.width,
                                args.height,
                                args.rotate_180,
                            )
                        else:
                            image = unpack_r8_frame(
                                request.make_array("raw"),
                                args.width,
                                args.height,
                                args.rotate_180,
                            )
                        images.append(image)
                        metadata.append(request.get_metadata())
                    finally:
                        request.release()

                stack = np.stack(images)
                result = summarize_images(stack, exposure_us, gain)
                result["score"] = score_result(result, args)
                result["metadata_exposure_us"] = int(metadata[-1].get("ExposureTime", exposure_us))
                result["metadata_gain"] = float(metadata[-1].get("AnalogueGain", gain))
                results.append(result)

                stem = f"exp{exposure_us:04d}_gain{gain:g}".replace(".", "p")
                save_pgm(
                    session_dir / f"{stem}_median.pgm", np.median(stack, axis=0).astype(np.uint8)
                )
                print(
                    f"exp={exposure_us:4d}us gain={gain:>4g} "
                    f"mean={result['mean']:6.1f} p99={result['p99']:6.1f} "
                    f"clip={result['clipped_pct']:5.2f}% dark={result['dark_pct']:5.2f}% "
                    f"score={result['score']:.2f}"
                )
    finally:
        camera.stop()
        camera.close()

    if not results:
        print("No valid settings tested.", file=sys.stderr)
        return 1

    results.sort(key=lambda item: item["score"])
    acceptable = [
        result
        for result in results
        if (
            args.target_mean_low <= result["mean"] <= args.target_mean_high
            and result["clipped_pct"] <= args.max_clipped_pct
            and result["dark_pct"] <= args.max_dark_pct
        )
    ]
    best = acceptable[0] if acceptable else results[0]
    (session_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print("\nRecommended setting:" if acceptable else "\nBest tested setting, but NOT acceptable:")
    print(f"  --camera-capture-exposure-us {best['exposure_us']}")
    print(f"  --camera-capture-gain {best['gain']:g}")
    print(
        f"  mean={best['mean']:.1f}, p99={best['p99']:.1f}, "
        f"clipped={best['clipped_pct']:.2f}%, dark={best['dark_pct']:.2f}%"
    )
    if not acceptable:
        print(
            "  Reason: every tested setting missed the target window. "
            "Run a lower exposure sweep or reduce scene brightness."
        )
    print(f"Saved previews and results: {session_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
