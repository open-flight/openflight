# K-LD7 Angle Radar Integration — Design

!!! warning "ARCHIVED DOCUMENT"

    This is a historical design or implementation note, kept as a record of why
    the code is shaped the way it is. It describes the project as of the date in
    its filename and **is not a guide to follow** — commands, paths, and
    constants may no longer match the code. See the
    [Archive index](../index.md) for current alternatives.

**Date:** 2026-03-23
**Status:** Exploration phase

## Goal

Add an RFbeam K-LD7 24GHz FMCW radar module to supplement the OPS243-A Doppler radar. The OPS243 handles speed and spin detection. The K-LD7 provides per-target **angle**, **distance**, and **magnitude** data that the OPS243 cannot.

Long-term, the K-LD7 may replace the camera for horizontal angle measurement and provide distance-to-target data. This phase is data gathering only.

## K-LD7 Module Overview

- **Frequency:** 24 GHz ISM band (same as OPS243)
- **Modulation:** FMCW (vs OPS243's CW Doppler)
- **Beam:** H80° × V34° (datasheet: ±40° H, ±17° V at -3dB)
- **Measurements per target:** distance (m), speed (km/h), horizontal angle (°), magnitude
- **Multi-target:** PDAT returns all detected targets; TDAT returns the strongest
- **Interface:** UART, 115200 baud default (even parity), up to 3 Mbaud
- **Power:** 5V, 200mA
- **Range settings:** 5m / 10m / 30m / 100m
- **Speed settings:** 12.5 / 25 / 50 / 100 km/h (max 62 mph — below golf ball speed)
- **Angle range:** -90° to +90° horizontal
- **Driver:** `nickovs/kld7` Python library (pip install kld7)

## Known Limitations

1. **Speed aliasing (not just saturation):** Max 100 km/h (62 mph). The datasheet warns that speeds above the max setting produce *wrong measurements* due to aliasing — not just clipped values. Golf balls at 150+ mph will report incorrect speeds. This is acceptable since OPS243 handles speed. Angle and distance should still be valid.
2. **Very few frames per shot:** At 100 km/h speed setting, frame duration is ~29ms (~34 fps). A golf ball transits the 5m detection zone in ~30ms, giving only 1-2 frames per shot. PDAT (raw detections) is more reliable than TDAT (tracked target) since the tracking filter likely won't lock on in time.
3. **Horizontal angle only:** No vertical angle measurement. Vertical launch angle still requires camera or estimation. A second K-LD7 mounted at 90° could provide vertical angle (future consideration).
4. **Interference potential:** Both radars operate at 24 GHz. May need frequency offset via K-LD7's RBFR parameter (low/mid/high base frequency).
5. **Tracking filter limitations:** Tracks only 1 target with 30m max range. For multi-target scenarios or fast transients, rely on PDAT raw data instead.

## Phase 1: Test Script

### `scripts/test_kld7.py`

Standalone script for data gathering. No integration with the server or OPS243 yet.

**Behavior:**
1. Connect to K-LD7 via USB serial (EVAL board)
2. Configure for golf use case (short range, max speed, both directions)
3. Stream TDAT + PDAT + RFFT frames continuously
4. Print live target data to terminal
5. Save all frames to `.pkl` on Ctrl+C

**Configuration for golf:**
- `RSPI = 3` — 100 km/h max speed
- `RRAI = 0` — 5m range (ball is close to radar)
- `DEDI = 2` — both approaching and receding
- `THOF = 10` — low threshold for sensitivity
- `TRFT = 1` — fast detection (ball is transient)

**Output format (.pkl):**
```python
{
    "metadata": {
        "module": "K-LD7",
        "capture_start": "<iso timestamp>",
        "capture_end": "<iso timestamp>",
        "total_frames": N,
        "params": {"RSPI": 3, "RRAI": 0, ...},
    },
    "frames": [
        {
            "timestamp": <float>,
            "tdat": {"distance": 1.23, "speed": 45.2, "angle": 3.1, "magnitude": 500},
            "pdat": [{"distance": ..., "speed": ..., "angle": ..., "magnitude": ...}, ...],
            "rfft": [[256 bins], [256 bins]],
        },
        ...
    ]
}
```

**CLI args:**
- `--port` — serial port (auto-detect if omitted)
- `--range` — range setting: 5, 10, 30, 100 (default: 5)
- `--speed` — max speed: 12, 25, 50, 100 km/h (default: 100)
- `--baud` — baud rate (default: 115200)
- `--no-fft` — skip RFFT frames (smaller output, faster)
- `-o, --output` — output .pkl path (default: auto-generated in ~/openflight_sessions/)
- `-n, --max-frames` — stop after N frames (default: unlimited)

**Connection:** USB via EVAL board → `/dev/ttyUSB*` on Pi.

### Datasheets

- `docs/K-LD7_Datasheet.pdf` — Module datasheet
- `docs/K-LD7-EVAL_Datasheet.pdf` — EVAL board datasheet

## Future Phases (not in scope now)

- **Phase 2:** Side-by-side capture with OPS243 — correlate K-LD7 angle data with OPS243 speed/spin data on same shots
- **Phase 3:** Integration module (`src/openflight/kld7/`) with real-time angle injection into shots
- **Phase 4:** Evaluate replacing camera with K-LD7 for horizontal angle; consider second K-LD7 for vertical
