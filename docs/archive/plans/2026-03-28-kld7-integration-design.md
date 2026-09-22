# K-LD7 Full Stack Integration — Design

!!! warning "ARCHIVED DOCUMENT"

    This is a historical design or implementation note, kept as a record of why
    the code is shaped the way it is. It describes the project as of the date in
    its filename and **is not a guide to follow** — commands, paths, and
    constants may no longer match the code. See the
    [Archive index](../index.md) for current alternatives.

**Date:** 2026-03-28
**Status:** Approved

## Goal

Integrate the RFbeam K-LD7 24 GHz FMCW radar into the openflight stack for real-time angle measurement. The K-LD7 handles angle (vertical or horizontal, depending on mount orientation) and distance. The OPS243-A handles speed and spin. Long-term, K-LD7 replaces the camera for angle measurement.

## Architecture

### K-LD7 Module (`src/openflight/kld7/`)

**`kld7_tracker.py`** — Main integration class, mirrors CameraTracker pattern:

```python
KLD7Tracker(
    port=None,              # Auto-detect FTDI serial
    range_m=5,              # Detection range
    speed_kmh=100,          # Hardware max; gives fastest frame rate (~34 fps)
    orientation="vertical", # "vertical" or "horizontal" — which angle plane this module measures
)
```

- Background thread streams TDAT+PDAT continuously (no FFT — maximizes frame rate to ~34 fps)
- Ring buffer (`collections.deque`) holds last ~2 seconds of frames (~70 frames)
- `get_angle_for_shot() -> KLD7Angle | None`:
  - Searches ring buffer for highest-magnitude detection event
  - Prefers PDAT over TDAT (more reliable for transient targets — validated in testing)
  - Returns angle, distance, magnitude, confidence, frame count
  - Confidence based on: magnitude strength, number of detection frames, angle consistency
- `reset()` — clears ring buffer after shot processed
- Supports two instances on different ports for dual-module (vertical + horizontal)

**`types.py`** — Data types:

```python
@dataclass
class KLD7Angle:
    vertical_deg: float | None    # Populated when orientation="vertical"
    horizontal_deg: float | None  # Populated when orientation="horizontal"
    distance_m: float
    magnitude: float
    confidence: float             # 0.0 - 1.0
    num_frames: int               # Frames with detection in the event

@dataclass
class KLD7Frame:
    timestamp: float
    tdat: dict | None             # Single tracked target
    pdat: list[dict]              # All raw detections
```

### Server Integration

**CLI flags** (server.py + start-kiosk.sh):

| Flag | Default | Description |
|------|---------|-------------|
| `--kld7` | off | Enable K-LD7 module |
| `--kld7-port` | auto-detect | Serial port |
| `--kld7-orientation` | `vertical` | `vertical` or `horizontal` |

Future (second module): `--kld7-port-2`, `--kld7-orientation-2`

**Startup:**
1. If `--kld7` present, create `KLD7Tracker`, connect, start background thread
2. Non-fatal on failure — warn and continue without angle radar (like camera)

**Shot attachment** (in `on_shot_detected()`):
1. OPS243 creates shot with speed + spin
2. K-LD7 step: `kld7_tracker.get_angle_for_shot()` → `KLD7Angle`
3. Attach to shot: `shot.launch_angle_vertical` or `shot.launch_angle_horizontal` per orientation
4. Reset ring buffer

**Angle priority chain (vertical):**
1. K-LD7 radar (highest — direct measurement)
2. Camera (medium — visual tracking)
3. Estimation from club/speed (lowest)

**Angle priority chain (horizontal):**
1. K-LD7 radar (when horizontal module connected)
2. Camera (existing)
3. None (no estimation)

K-LD7 data overrides camera data when both are present.

### Carry Distance

**Vertical angle:** No formula changes. `adjust_carry_for_launch_angle()` already penalizes deviation from optimal launch angle per club type. K-LD7 provides better input data with higher confidence.

**Horizontal angle** (when available): `carry_along_target = carry * cos(horizontal_angle_rad)`. Applied after all other adjustments (spin, smash factor). A 10° offline shot loses ~1.5% carry.

### UI Changes

**Current:** Ball speed gauge + 4 metric cards (carry, club speed, launch angle, spin)

**Changes:**
- Rename "Launch Angle" → **"Vertical"** — vertical launch angle in degrees
- Add **"Horizontal"** card — horizontal angle in degrees (positive = right of target)
- Subtext shows source: "radar" or "estimated"
- Horizontal card hidden when no horizontal data available
- Same confidence indicator pattern (high/medium/low)

## Data Flow

```
K-LD7 (USB serial)              OPS243-A (USB serial)
    │                                │
    ▼                                ▼
KLD7Tracker                    RollingBufferMonitor
(background thread)            (capture thread)
    │                                │
    │ ring buffer                    │ trigger → process → Shot
    │ (~2s of TDAT/PDAT)             │ (speed + spin)
    │                                │
    └──────────┐    ┌────────────────┘
               ▼    ▼
         on_shot_detected()
               │
               ├── kld7_tracker.get_angle_for_shot()
               ├── camera_tracker.calculate_launch_angle() [fallback]
               ├── estimate_launch_angle() [last resort]
               ├── adjust carry for angles
               ├── session_logger.log_shot(...)
               └── socketio.emit("shot", ...)
                        │
                        ▼
                    React UI
                (vertical + horizontal cards)
```

## Hardware

**Single module (tomorrow's test):**
- K-LD7 on EVAL board, mounted vertically (90° rotation)
- Provides vertical launch angle
- `/dev/ttyUSB*` via FTDI

**Dual module (future):**
- Module 1: mounted vertically → vertical angle
- Module 2: bare K-LD7 + FTDI cable (~$95) → horizontal angle
- Two `/dev/ttyUSB*` ports, two `KLD7Tracker` instances

## Known Limitations

1. **Speed aliasing:** K-LD7 max 100 km/h; golf ball speeds alias. Speed data ignored — OPS handles speed.
2. **~34 fps without FFT:** 1-2 frames per ball pass at golf speed. May limit angle accuracy.
3. **Golf ball RCS:** Detection at 5m validated indoors with tossed balls. Full-speed struck balls unverified.
4. **Single-plane per module:** Each K-LD7 measures one angle plane only.

## Module Responsibilities (Clean Separation)

| Measurement | Module | Notes |
|-------------|--------|-------|
| Ball speed | OPS243-A | Primary measurement |
| Club speed | OPS243-A | From pre-impact readings |
| Spin rate | OPS243-A | Rolling buffer I/Q analysis |
| Vertical angle | K-LD7 (vertical mount) | Replaces camera when available |
| Horizontal angle | K-LD7 (horizontal mount) | Future second module |
| Distance | K-LD7 | Bonus data, not used in carry calc yet |
