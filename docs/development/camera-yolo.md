# Camera and YOLO Experiments

YOLO camera detection is experimental and is not part of the production kiosk.
The kiosk's optional `--camera-capture` path records high-speed video without
running this object-detection experiment.

Use this guide only to evaluate a CSI camera such as the optional InnoMaker
OV9281 global-shutter module. OpenFlight shot measurements still come from the
radars.

## Prerequisites

The experiment script requires these modules in the Pi's Python environment:

- `picamera2` for CSI camera capture;
- `ultralytics` for YOLO inference; and
- `opencv-python` for image processing and display.

They are intentionally absent from OpenFlight's normal install. Install them in
a separate experimental environment appropriate for your Raspberry Pi OS image;
do not add them to the production kiosk unless camera support is being restored.

## Check the camera

Confirm Raspberry Pi OS sees the module before debugging OpenFlight:

```bash
rpicam-hello --list-cameras
```

Then run a short headless capture with an existing YOLO model:

```bash
uv run python scripts/vision/test_yolo_detection.py \
  --model models/golf_ball_yolo11n_new_256.onnx \
  --headless --num-frames 10
```

For a desktop preview on the Pi:

```bash
DISPLAY=:0 uv run python scripts/vision/test_yolo_detection.py \
  --model models/golf_ball_yolo11n_new_256.onnx \
  --imgsz 256 --threaded
```

## Useful options

| Option | Purpose | Default |
|---|---|---:|
| `--imgsz` | YOLO inference size; smaller is faster | 256 |
| `--width` / `--height` | Camera capture resolution | 640 × 480 |
| `--fps` | Requested camera frame rate | 60 |
| `--confidence` | Minimum detection confidence | 0.3 |
| `--threaded` | Separate capture and inference threads | off |
| `--no-display` | Skip overlay display while benchmarking | off |
| `--buffer-count` | Camera buffers; fewer reduces latency | 2 |
| `--image PATH` | Test one saved image instead of the camera | unset |

Start at `--imgsz 256`. Reduce it if inference is too slow, or increase it when
the ball is too small to detect reliably. Measure performance on the actual Pi;
frame rate depends on the model, runtime, resolution, and thermal state.

## Model export

Export a PyTorch model to ONNX:

```bash
uv run python scripts/vision/test_yolo_detection.py \
  --model models/golf_ball_yolo11n.pt \
  --imgsz 256 --export-onnx
```

OpenVINO export is also supported with `--export-openvino`; add `--int8` only
after checking the accuracy loss on representative ball images.

## Production status

The server does not integrate this YOLO tracker. Adding camera-assisted
measurement would require dependency packaging, startup integration, hardware
validation, and tests; this benchmark script alone does not enable it.
