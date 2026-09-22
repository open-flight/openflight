# K-LD7 Raw ADC Processing — Design Spec

!!! warning "ARCHIVED DOCUMENT"

    This is a historical design or implementation note, kept as a record of why
    the code is shaped the way it is. It describes the project as of the date in
    its filename and **is not a guide to follow** — commands, paths, and
    constants may no longer match the code. See the
    [Archive index](../index.md) for current alternatives.

**Date:** 2026-04-05
**Status:** Draft

## Goal

Replace dependence on the K-LD7's internal detector (PDAT) with custom FFT + CFAR processing on raw ADC samples (RADC). This gives us control over detection threshold, FFT resolution, and angle estimation — the same signal-processing chain that commercial launch monitors use.

## Background

The K-LD7 currently outputs processed detections (PDAT) at ~34 fps. A golf ball appears in only 1-2 PDAT frames because the module's internal detector is conservative. With raw I/Q we can run longer FFTs, lower thresholds, and potentially recover ball returns the module discards.

### RADC Frame Structure

3072 bytes per frame at ~34 fps:

| Segment | Content | Size |
|---------|---------|------|
| F1 Freq A | 256 I samples (uint16) + 256 Q samples (uint16) | 1024 bytes |
| F2 Freq A | 256 I samples (uint16) + 256 Q samples (uint16) | 1024 bytes |
| F1 Freq B | 256 I samples (uint16) + 256 Q samples (uint16) | 1024 bytes |

Two frequencies (F1, F2) enable phase-difference angle estimation between Rx channels.

### Bandwidth Requirement

RADC at 34 fps = ~102 KB/s. Current baud rate of 115200 (~11.5 KB/s) is insufficient. Must use 3 Mbaud for reliable RADC streaming.

## Deliverables

### 1. Capture Script — `scripts/capture_kld7_radc.py`

**Purpose:** Stream and record raw ADC data from a single K-LD7 for offline analysis.

**Behavior:**
- Connect to K-LD7 at specified port and baud rate (default 3 Mbaud)
- Stream `RADC | PDAT | TDAT` simultaneously
- RADC for custom processing, PDAT/TDAT for comparison against module's detector
- Record continuously for a specified duration
- Save to `.pkl` with metadata

**CLI:**
```bash
./scripts/capture_kld7_radc.py --port /dev/ttyUSB0 --orientation vertical --duration 60
./scripts/capture_kld7_radc.py --port /dev/ttyUSB0 --baud 3000000 --duration 30
```

**Output `.pkl` structure:**
```python
{
    "metadata": {
        "module": "K-LD7",
        "port": "/dev/ttyUSB0",
        "baud_rate": 3000000,
        "orientation": "vertical",
        "capture_start": "2026-04-05T...",
        "capture_end": "2026-04-05T...",
        "total_frames": N,
        "params": { ... },  # K-LD7 config params
    },
    "frames": [
        {
            "timestamp": float,
            "radc": {
                "f1a_i": np.ndarray,  # (256,) uint16
                "f1a_q": np.ndarray,  # (256,) uint16
                "f2a_i": np.ndarray,  # (256,) uint16
                "f2a_q": np.ndarray,  # (256,) uint16
                "f1b_i": np.ndarray,  # (256,) uint16
                "f1b_q": np.ndarray,  # (256,) uint16
            },
            "tdat": dict | None,     # module's tracked target
            "pdat": list[dict],      # module's raw detections
        },
        ...
    ]
}
```

### 2. Analysis Library — `scripts/kld7_radc_lib.py`

**Purpose:** Standalone helpers for processing RADC captures. No dependency on the main `openflight` package.

**Dependencies:** `numpy` only. Plotting lives in the analysis script, not here.

#### FFT Processing

- `parse_radc_frame(frame) -> dict` — Extract I/Q arrays from raw RADC bytes
- `compute_range_doppler(iq_complex, fft_size=2048) -> np.ndarray` — Hann window, zero-pad, FFT, magnitude spectrum
- `cfar_detect(spectrum, guard_cells=4, training_cells=16, threshold_factor=8.0) -> list[Detection]` — OS-CFAR on the range-Doppler spectrum, returns peaks above adaptive noise floor

#### Spatial Filtering

- `estimate_angle_from_phase(f1_complex, f2_complex) -> float` — Phase-difference angle estimation between the two frequency channels
- `filter_by_distance(detections, min_m, max_m) -> list` — Distance gate
- `filter_by_velocity(detections, min_kmh, max_kmh) -> list` — Velocity gate

#### Ball Isolation

- `find_ball_candidates(frames, club_event_time) -> list` — Distance gate 2-6m, fast outbound, short burst, appearing after club event
- `compare_radc_vs_pdat(frame) -> dict` — Side-by-side comparison of what our FFT finds vs what the module's PDAT reported

#### Data Types

```python
@dataclass
class RADCDetection:
    frame_index: int
    timestamp: float
    distance_m: float
    velocity_kmh: float
    angle_deg: float        # from phase difference
    magnitude: float        # FFT bin magnitude
    snr_db: float          # signal-to-noise from CFAR
    bin_index: int          # FFT bin number
```

### 3. Analysis Script — `scripts/analyze_kld7_radc.py`

**Purpose:** Visualize and explore RADC captures.

**Dependencies:** `numpy`, `matplotlib`

**CLI:**
```bash
# Full analysis
./scripts/analyze_kld7_radc.py capture.pkl

# Zoom to detected swing windows
./scripts/analyze_kld7_radc.py capture.pkl --shot-windows

# Export detections to CSV
./scripts/analyze_kld7_radc.py capture.pkl --csv
```

**Generated outputs:**
- Range-Doppler heatmap per frame (around swing events)
- Detection timeline: our FFT detections vs PDAT detections on same time axis
- Distance vs time scatter for ball candidates
- Per-shot comparison table (CSV): our detection count, angles, magnitudes vs PDAT

## FFT Parameters

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Window | Hann | Standard for spectral analysis, good sidelobe suppression |
| Input samples | 256 (from RADC) | Fixed by hardware |
| Zero-pad to | 2048 | 8× interpolation, ~0.15 m/s velocity resolution |
| CFAR guard cells | 4 | Prevent target self-masking |
| CFAR training cells | 16 | Enough for noise estimate |
| CFAR threshold | 8.0× (start) | Tunable — lower = more sensitive, more false alarms |

## What This Is Not

- **Not a live replacement for the PDAT tracker** — purely offline analysis to validate the approach
- **Not coherent integration across frames** — single-frame FFT first, multi-frame stacking is a future step if this shows promise
- **Not dual-K-LD7** — single vertical unit for now, horizontal comes later

## Success Criteria

1. Capture script reliably records RADC at 3 Mbaud without dropped frames
2. Our FFT + CFAR finds at least as many ball detections as PDAT on the same capture
3. We can see ball returns in the range-Doppler map that PDAT missed
4. Phase-based angle estimation produces reasonable values (within ±10° of PDAT angles)
