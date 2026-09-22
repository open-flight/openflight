# OV9281 High-Speed Camera

OpenFlight can capture a short, synchronized camera movie around impact. The
camera does not replace the OPS243 or IWR6843: OPS anchors speed and trigger
timing, the IWR6843 measures radar angles, and the camera provides a visual
record of the clubhead and early ball flight.

Camera capture is experimental. Camera-assisted horizontal launch, club path,
and attack angle must remain quality-gated until they are validated against a
launch-monitor source of truth.

## Supported Hardware

The tested camera is an InnoMaker OV9281 monochrome global-shutter module on a
Raspberry Pi 5. Other OV9281 modules may expose different register programming
or lens geometry even when they use the same sensor.

Required hardware:

- Raspberry Pi 5 running Raspberry Pi OS.
- InnoMaker OV9281 monochrome global-shutter camera.
- Correct Pi 5 camera ribbon cable for the selected CAM/DISP connector.
- Rigid, focusable camera mount.
- Shared sound-trigger wiring on BCM17 when using OPS and IWR6843 capture.

Power the Pi off before connecting or disconnecting the ribbon cable. Confirm
that the cable contacts face the correct direction for both the Pi connector
and camera board before applying power.

## Mounting And Sensor Offset

Mount the camera rigidly and point it down the target line. The tested original
assembly placed the camera directly above the IWR6843 antenna center, about
`8.25 in` (`0.20955 m`) above the hitting surface. A laterally offset camera is
also supported when its position is measured and passed to OpenFlight.

Measure horizontally from the radar antenna center used for radar height to the
camera lens optical center. Pass that signed distance in meters:

```bash
scripts/start-kiosk.sh \
  --camera-capture \
  --camera-capture-lateral-offset-m 0.0762
```

The sign is defined while standing behind the unit and looking down the target
line:

- Positive: camera is to target-right of the radar center.
- Negative: camera is to target-left of the radar center.
- Zero: camera and radar optical centers share the same vertical centerline.

For reference, `3 in` is `0.0762 m`. Measure center-to-center rather than using
enclosure edges. OpenFlight records this value with the session and uses it in
camera-only ball depth, camera/IWR horizontal launch, Club Path, and Attack
Angle geometry.

This physical distance is different from
`--camera-capture-horizontal-offset-deg`. The meter value describes where the
camera is mounted; the degree value corrects residual camera yaw or target-line
alignment. Do not convert the physical separation into degrees, and do not use
the degree setting as a substitute for the measured distance.

The useful image does not need to contain the golfer or the full shaft. It must
contain:

- The stationary ball and impact point.
- The clubhead for the final frames before impact.
- The clubhead through impact.
- The first several milliseconds of ball flight.

TrackMan-aligned 7-iron and 9-iron captures showed that a `200`-row view was
needed to retain both the final clubhead approach and early ball flight. The
tested high-speed `320x200` mode defaults to a vertically centered `(480,150)` crop.
A `150`-row crop was too tight for robust combined clubhead and ball-path
tracking. A raised `200`-row crop remains a future experiment, not part of the
checked-in driver.

This result has not yet been validated for wedges or driver. Use the alignment
preview before each new physical mounting configuration.

## Raspberry Pi Packages

Install the Raspberry Pi camera stack and preview dependency:

```bash
sudo apt update
sudo apt install -y rpicam-apps python3-picamera2 ffmpeg
```

The main `scripts/setup/setup.sh` installer also installs FFmpeg automatically
on Raspberry Pi systems when it is missing. Picamera2 remains an operating-
system package because it must match Raspberry Pi OS and its libcamera stack.

Install OpenFlight's optional camera image-processing dependency from the
repository root:

```bash
uv sync --extra camera
```

OpenCV is intentionally not installed for radar-only OpenFlight systems.

Reboot after enabling or changing camera hardware:

```bash
sudo reboot
```

Verify that the camera is detected:

```bash
rpicam-hello --list-cameras
```

The standard Raspberry Pi driver should list the stock OV9281 modes. The
OpenFlight high-speed driver additionally lists `640x200`, `640x100`, and
`320x200` raw modes.

## High-Speed Driver

The Raspberry Pi kernel's stock OV9281/OV9282 driver does not expose the
cropped high-speed modes used by OpenFlight. The repository includes a narrow
kernel patch and installer under:

```text
drivers/ov9281/
scripts/setup/install_ov9281_high_speed_driver.sh
```

The experimental `320x200` register sequence is derived from InnoMaker's
[CAM-OV9281RAW-V2 reference repository](https://github.com/INNO-MAKER/CAM-OV9281RAW-V2)
and adapted to Raspberry Pi's upstream `ov9282` driver.

Install the driver on the Pi with:

```bash
cd ~/openflight
scripts/setup/install_ov9281_high_speed_driver.sh
sudo reboot
```

The installer resolves the stock driver source matching the running Raspberry
Pi kernel, builds only the `ov9282` module against the installed kernel headers,
backs up the stock module, installs the patched module, and runs `depmod`. It
also grants members of the `video` group access to the live vertical sensor
position control. Kernel upgrades require rebuilding the module for the new
kernel before the custom modes are available again.

Restore the stock module with:

```bash
cd ~/openflight
scripts/setup/install_ov9281_high_speed_driver.sh --restore
sudo reboot
```

Do not unload or replace the active camera module while the camera pipeline is
running. Stop OpenFlight and reboot after installing or restoring a driver.

## Capture Modes

Measured modes on the test Pi 5:

| Mode | Purpose | Observed cadence |
|---|---|---:|
| `640x400` | Full-field setup and baseline capture | about `288 FPS` |
| `640x200` | Wide impact strip | about `536 FPS` |
| `320x200` | Dense impact and early-flight capture | about `576 FPS` |
| `640x100` | Timing experiment only; vertically fragile | not recommended |

The production experiment should use the fixed `320x200` mode requested at
`450 FPS`. Higher requests require exposures too short for reliable golf
capture in the tested lighting environments.
The delivered cadence is expected to be lower than the request and is recorded
in each capture's `metadata.json`.

## Alignment Preview

Stop OpenFlight before opening the camera preview:

```bash
cd ~/openflight
CAMERA_WIDTH=320 \
CAMERA_HEIGHT=200 \
CAMERA_FPS=120 \
CAMERA_EXPOSURE_US=500 \
CAMERA_GAIN=2 \
scripts/hardware-test/preview_camera_alignment.sh
```

Use the preview to place the ball near the horizontal center and ensure the
clubhead and expected launch corridor remain within the vertical window. The
preview is rotated for the tested inverted camera mount.

Small residual camera roll can be corrected without resampling the high-speed
capture. Pass a clockwise-positive correction measured from a stationary
full-field preview:

```bash
scripts/start-kiosk.sh \
  --camera-capture \
  --camera-capture-roll-deg 2.8
```

OpenFlight applies this value to the Camera-tab preview and to the pixel-ray
geometry used by horizontal launch, Club Path, and Attack Angle. Saved raw
frames remain unchanged so calibration can be revised during offline replay.
Physical leveling is still preferred when practical because it preserves the
entire usable area of a compact sensor crop.

## Camera Tab Controls

When `--camera-capture` is enabled, the Camera tab keeps the preview visible
beside an operator-control column. Exposure and analogue gain are selected
automatically. The tab shows the current shutter, gain, impact-area brightness,
contrast, motion-blur risk, and whether camera-assisted analysis is eligible.
There is no manual exposure override in the UI.

With the OpenFlight driver and the fixed `320x200` capture mode, **View up** and
**View down** move the real sensor window in safe 10-pixel steps. Each move
briefly restarts the camera and refills the pre-trigger ring. Wait for the tab
to report **Armed** before hitting. The centered starting position provides
70 output pixels of safe UI travel in either direction. The controls account
for the tested 180-degree camera mount, so the visible scene moves in the
direction printed on the button.

Resolution, requested frame rate, and pre/post-trigger timing are shown as
read-only capture provenance. Changing those values requires a controlled
camera restart and is intentionally not part of the live-control path.

## Automatic Exposure

Lighting varies too much for one universal exposure. OpenFlight measures the
center-lower impact region and selects from camera settings validated to fit
inside the configured frame period.

At startup, the controller begins with the last good setting saved for the same
resolution and frame rate. If that setting is not usable, it can jump several
steps and recheck after `300 ms`, rather than waiting through one five-second
step at a time. It makes at most three fast startup adjustments before shot
capture is armed. The selected manual exposure and gain are then locked for the
life of that camera runtime, so every replay in the session uses the same image
controls. There is no periodic exposure monitoring after startup.

The last good setting is stored at:

```text
~/.config/openflight/camera-exposure.json
```

If the available lighting cannot produce a usable image, OpenFlight does not
stop the camera. Preview and raw frame capture continue, but camera-assisted
horizontal launch, Club Path, and Attack Angle are withheld for that shot.
Horizontal launch falls back to the IWR6843 path. The Camera tab reports
**Lighting needed** and **Radar fallback active** so the operator can add or
redirect light. Restart OpenFlight after changing the lighting so startup
calibration can select and lock a new setting.

The standalone calibration sweep remains useful when diagnosing an unusual
lighting installation:

```bash
cd ~/openflight
uv run --no-project --python /usr/bin/python3 \
  python scripts/hardware-test/calibrate_camera_exposure.py \
  --rotate-180 \
  --fps 300 \
  --width 640 \
  --height 400 \
  --exposures-us 250,350,500,700,900,1100 \
  --gains 1,2,4 \
  --settle-ms 150
```

Prefer adding light to the hitting area over relying on long shutter times or
high gain. Both can reduce clubhead edge quality even when the preview appears
bright enough.

## Standalone Trigger Test

Use the clap-buffer test before starting OpenFlight:

```bash
cd ~/openflight
uv run --no-project --python /usr/bin/python3 \
  python scripts/hardware-test/test_camera_clap_buffer.py \
  --width 320 \
  --height 200 \
  --fps 450 \
  --pre-ms 150 \
  --post-ms 50 \
  --exposure-us 500 \
  --gain 2 \
  --rotate-180 \
  --captures 5
```

Check that every capture reports the expected pre/post frame counts, plausible
delivered FPS, and `gaps=0`.

## Running OpenFlight

Enable synchronized rolling capture with `--camera-capture`:

```bash
scripts/start-kiosk.sh \
  --debug \
  --iwr6843 \
  --camera-capture \
  --camera-capture-width 320 \
  --camera-capture-height 200 \
  --camera-capture-fps 450 \
  --camera-capture-pre-ms 150 \
  --camera-capture-post-ms 50 \
  --camera-capture-rotate-180 \
  --session-location home
```

When capture is enabled, OpenFlight keeps a rolling pre-trigger frame buffer and freezes it
from the same sound-trigger event used by the radar pipeline.

## Shot Replay

A shot with a matched high-speed camera capture exposes **Replay** in the Live
header and a play control in its Shots row. Replay opens a full-screen,
touch-friendly 60 FPS slow-motion player with play/pause, restart, scrubbing,
looping, selectable `1x`, `0.5x`, `0.25x`, and `0.1x` playback speeds, and an
impact marker derived from the recorded trigger frame. Each replay carries its
saved-frame orientation, so the player applies the operator-facing left/right
correction only when needed and never flips a capture twice. Saved frames used
by camera geometry remain unchanged.

The browser-ready H.264 MP4 is intentionally lazy. Shot processing registers
only the existing `frames.npz`; OpenFlight does not run FFmpeg until the user
selects Replay. The first request creates `replay.mp4` atomically beside the raw
capture, and later requests reuse that file. Closing the player while it is
preparing does not affect the shot or raw frames. Preparation and playback
failures are shown as retryable player states, while server-side failures are
logged without interrupting launch-monitor operation. FFmpeg preparation is
serialized across replays to avoid simultaneous CPU and memory spikes on the
Raspberry Pi.

## Saved Artifacts

Camera captures are written under:

```text
~/openflight_sessions/<location>/camera/camera_<timestamp>_<sequence>/
```

Each capture contains:

- `frames.npz`: grayscale frames and per-frame timing/control metadata.
- `metadata.json`: delivered cadence, frame gaps, brightness, timing, settings,
  and the capture-time automatic-exposure decision.
- `first.pgm`: first buffered frame.
- `trigger.pgm`: frame nearest the hardware trigger.
- `last.pgm`: final post-trigger frame.
- `replay.mp4`: optional 60 FPS slow-motion video, created only after the first
  manual Replay selection and then cached.

The session JSONL contains a `camera_capture` entry linking the shot number to
the camera directory. Keep the JSONL, OPS capture, IWR6843 dump, and camera
directory together when copying a session for offline analysis.

## Troubleshooting

### No camera detected

1. Stop OpenFlight.
2. Power the Pi off.
3. Reseat both ends of the ribbon cable.
4. Boot and run `rpicam-hello --list-cameras`.

### `picamera2` import fails under `uv`

Raspberry Pi OS installs Picamera2 for the system Python. Run hardware scripts
with:

```bash
uv run --no-project --python /usr/bin/python3 python <script>
```

For OpenFlight itself, first confirm `python3-picamera2` is installed through
`apt`, then run `uv sync --extra camera`. The camera runtime exposes Raspberry
Pi OS's package directory to the OpenFlight environment automatically.

### Requested high-speed mode is missing

The custom module was not built for the running kernel, or a kernel update
restored the stock driver. Re-run the high-speed driver installer and reboot.

### Frames are dark

Verify that the lens cap is removed and that light reaches the clubhead path,
not just the stationary ball. If the Camera tab remains on **Lighting needed**
at the brightest validated setting, add or redirect light. Preview and raw
capture continue while radar-only measurements remain active.

### Highlights are solid white

Redirect intense light away from reflective surfaces and the ball. The
automatic controller will move darker, but clipped ball and club pixels cannot
provide reliable centroids even when the image looks bright enough to a person.

### Frame gaps increase

Stop preview applications, verify that only one process owns the camera, and
check the Pi for thermal throttling. Capture to RAM first; persistence happens
after the frame window freezes.

### Camera works once and then remains busy

Stop OpenFlight cleanly and look for stale `rpicam-*`, `ffplay`, or Python
processes. Reboot rather than hot-unloading the camera kernel module.

## Current Limitations

- The `320x200` crop has only been evaluated with 7-iron and 9-iron TrackMan shots.
- Camera pose is not yet a complete metric calibration.
- Camera-assisted club path and attack angle remain experimental.
- A kernel update requires rebuilding the custom module.
- The tested down-the-line view cannot independently measure downrange speed;
  OPS remains necessary for converting image-plane motion into delivery angles.
