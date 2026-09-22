#!/usr/bin/env bash
# Live OV9281 alignment preview using the raw monochrome stream.

set -euo pipefail

WIDTH="${CAMERA_WIDTH:-640}"
HEIGHT="${CAMERA_HEIGHT:-400}"
FPS="${CAMERA_FPS:-60}"
EXPOSURE_US="${CAMERA_EXPOSURE_US:-1000}"
GAIN="${CAMERA_GAIN:-8}"

export DISPLAY="${DISPLAY:-:0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"

if pgrep -f "openflight.server" >/dev/null; then
    echo "OpenFlight is running and may own the camera. Stop it before alignment." >&2
    exit 1
fi

if ! command -v rpicam-raw >/dev/null; then
    echo "rpicam-raw is not installed." >&2
    exit 1
fi

if ! command -v ffplay >/dev/null; then
    echo "ffplay is not installed." >&2
    exit 1
fi

echo "OV9281 alignment preview: ${WIDTH}x${HEIGHT} @ ${FPS} fps"
echo "Exposure: ${EXPOSURE_US} us, gain: ${GAIN}"
echo "The bright crosshair is the image center. Press Ctrl+C to close."

# PiSP exposes OV9281 R8 samples in the high byte of an unpacked R16 stream.
# ffplay reads that stream directly, rotates the mounted camera image, and adds
# a center crosshair without involving the black monochrome ISP/JPEG path.
rpicam-raw \
    --camera 0 \
    --mode "${WIDTH}:${HEIGHT}:8:P" \
    --width "${WIDTH}" \
    --height "${HEIGHT}" \
    --framerate "${FPS}" \
    --shutter "${EXPOSURE_US}" \
    --gain "${GAIN}" \
    --denoise off \
    --nopreview \
    --timeout 0 \
    --output - \
    2> >(sed 's/^/[camera] /' >&2) \
| ffplay \
    -hide_banner \
    -loglevel warning \
    -fflags nobuffer \
    -flags low_delay \
    -f rawvideo \
    -pixel_format gray16le \
    -video_size "${WIDTH}x${HEIGHT}" \
    -framerate "${FPS}" \
    -vf "hflip,vflip,drawbox=x=iw/2-1:y=0:w=3:h=ih:color=white@0.75:t=fill,drawbox=x=0:y=ih/2-1:w=iw:h=3:color=white@0.75:t=fill" \
    -window_title "OpenFlight OV9281 Alignment" \
    -
