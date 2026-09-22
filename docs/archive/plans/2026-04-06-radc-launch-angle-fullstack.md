# RADC Launch Angle Full-Stack Integration Plan

!!! warning "ARCHIVED DOCUMENT"

    This is a historical design or implementation note, kept as a record of why
    the code is shaped the way it is. It describes the project as of the date in
    its filename and **is not a guide to follow** — commands, paths, and
    constants may no longer match the code. See the
    [Archive index](../index.md) for current alternatives.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship RADC-based phase-interferometry launch angle extraction into the production K-LD7 tracker, replacing the PDAT distance-based method as primary, with PDAT as fallback.

**Architecture:** `KLD7Tracker` streams RADC+TDAT+PDAT at 3Mbaud (up from 115200 with TDAT+PDAT only). Raw RADC payloads are buffered alongside existing PDAT/TDAT frames. When `get_angle_for_shot(ball_speed_mph=X)` is called, it runs the RADC phase-interferometry pipeline (FFT → ball-speed-filtered CFAR → per-bin angle) and falls back to the existing PDAT distance-based method if RADC extraction fails. The `server.py` call site passes `shot.ball_speed_mph` to `get_angle_for_shot()`. No UI changes needed — the existing `KLD7Angle` type and WebSocket fields already carry the data.

**Tech Stack:** Python, numpy, kld7 library (3Mbaud RADC streaming), phase interferometry

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/openflight/kld7/radc.py` | RADC signal processing (moved from `scripts/kld7_radc_lib.py`) |
| Modify | `src/openflight/kld7/types.py:7-14` | Add `radc` field to `KLD7Frame` |
| Modify | `src/openflight/kld7/tracker.py` | Stream RADC at 3Mbaud, RADC angle extraction, fallback to PDAT |
| Modify | `src/openflight/kld7/__init__.py` | Export `radc` module |
| Modify | `src/openflight/server.py:857` | Pass `ball_speed_mph` to `get_angle_for_shot()` |
| Modify | `tests/test_kld7.py` | Add RADC extraction tests |
| Modify | `tests/test_kld7_radc_lib.py` | Update imports to new location |

---

### Task 1: Move RADC processing into the kld7 package

The signal processing code currently lives in `scripts/kld7_radc_lib.py` which isn't importable from the production code. Move the core functions into `src/openflight/kld7/radc.py`.

**Files:**
- Create: `src/openflight/kld7/radc.py`
- Modify: `src/openflight/kld7/__init__.py`
- Modify: `tests/test_kld7_radc_lib.py` (update imports)

- [ ] **Step 1: Create `src/openflight/kld7/radc.py`**

Copy these functions and types from `scripts/kld7_radc_lib.py` — these are the ones needed by the tracker:

```python
"""RADC signal processing for K-LD7 phase-interferometry angle extraction."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# --- Constants ---
RADC_PAYLOAD_BYTES = 3072
SAMPLES_PER_CHANNEL = 256
DC_MASK_BINS = 8

# K-LD7 antenna parameters (24 GHz)
WAVELENGTH_M = 3e8 / 24.125e9  # ~12.43 mm
ANTENNA_SPACING_M = 8.0e-3  # ~0.64λ, calibrated against PDAT reference data
```

Then copy these functions verbatim from `scripts/kld7_radc_lib.py`:
- `parse_radc_payload`
- `to_complex_iq`
- `compute_spectrum`
- `compute_fft_complex`
- `per_bin_angle_deg`
- `bin_to_velocity_kmh`
- `_velocity_to_bin`
- `ball_bin_range_from_speed`
- `cfar_detect` and `CFARDetection`
- `find_impact_frames`
- `extract_launch_angle` (with the fixes from this session: single-frame detection, ball bin range in impact detection)

Do NOT copy: `analyze_capture`, `process_radc_frame`, `process_radc_frame_spatial`, `compare_radc_vs_pdat`, `RADCDetection`, `SpatialDetection`, `ball_bin_range`, `club_bin_range`, `estimate_angle_from_phase`, `compute_angle_velocity_map` — those are offline analysis only.

- [ ] **Step 2: Update `src/openflight/kld7/__init__.py`**

```python
from .types import KLD7Angle, KLD7Frame
from .tracker import KLD7Tracker

__all__ = ["KLD7Angle", "KLD7Frame", "KLD7Tracker"]
```

No change needed — `radc` is an internal module imported by `tracker.py`, not a public export.

- [ ] **Step 3: Update `tests/test_kld7_radc_lib.py` imports**

Add a fallback import so the tests work with either location. At the top of `tests/test_kld7_radc_lib.py`, change the import block:

```python
try:
    from openflight.kld7.radc import (
        CFARDetection,
        bin_to_velocity_kmh,
        cfar_detect,
        compute_spectrum,
        parse_radc_payload,
        to_complex_iq,
    )
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    from kld7_radc_lib import (
        CFARDetection,
        bin_to_velocity_kmh,
        cfar_detect,
        compute_spectrum,
        parse_radc_payload,
        to_complex_iq,
    )
```

Remove the old `RADCDetection`, `compare_radc_vs_pdat`, `estimate_angle_from_phase`, `process_radc_frame` imports — those functions stay in the scripts module only.

- [ ] **Step 4: Run existing RADC tests**

Run: `uv run pytest tests/test_kld7_radc_lib.py -v`
Expected: All existing tests PASS (imports resolve to new location).

- [ ] **Step 5: Commit**

```bash
git add src/openflight/kld7/radc.py tests/test_kld7_radc_lib.py
git commit -m "refactor: move RADC processing into kld7 package"
```

---

### Task 2: Add `radc` field to `KLD7Frame`

**Files:**
- Modify: `src/openflight/kld7/types.py:7-14`

- [ ] **Step 1: Write the failing test**

In `tests/test_kld7.py`, add to `TestKLD7Types`:

```python
def test_kld7_frame_radc_field(self):
    frame = KLD7Frame(timestamp=1000.0)
    assert frame.radc is None
    frame_with_radc = KLD7Frame(timestamp=1000.0, radc=b"\x00" * 3072)
    assert len(frame_with_radc.radc) == 3072
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_kld7.py::TestKLD7Types::test_kld7_frame_radc_field -v`
Expected: FAIL — `KLD7Frame.__init__() got an unexpected keyword argument 'radc'`

- [ ] **Step 3: Add `radc` field to `KLD7Frame`**

In `src/openflight/kld7/types.py`, add the field:

```python
@dataclass
class KLD7Frame:
    """A single frame from the K-LD7 radar stream."""

    timestamp: float
    tdat: Optional[dict] = None  # {"distance", "speed", "angle", "magnitude"}
    pdat: list = field(default_factory=list)  # list of target dicts
    radc: Optional[bytes] = None  # Raw 3072-byte RADC payload
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_kld7.py::TestKLD7Types -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/openflight/kld7/types.py tests/test_kld7.py
git commit -m "feat: add radc field to KLD7Frame"
```

---

### Task 3: Add RADC angle extraction method to `KLD7Tracker`

**Files:**
- Modify: `src/openflight/kld7/tracker.py`
- Modify: `tests/test_kld7.py`

- [ ] **Step 1: Write the failing test for RADC angle extraction**

Add a new test class to `tests/test_kld7.py`:

```python
import numpy as np

class TestRADCAngleExtraction:
    """Tests for RADC-based phase-interferometry launch angle extraction."""

    def _make_tracker(self, orientation="vertical"):
        tracker = KLD7Tracker.__new__(KLD7Tracker)
        tracker.orientation = orientation
        tracker.buffer_seconds = 2.0
        tracker.max_buffer_frames = 70
        tracker.angle_offset_deg = 0.0
        tracker._init_ring_buffer()
        return tracker

    def _make_radc_payload_with_tone(self, velocity_kmh, angle_deg=10.0, amplitude=5000):
        """Create a synthetic RADC payload with a tone at the given velocity.

        Generates I/Q samples for F1A and F2A channels with a phase offset
        corresponding to the target angle, so phase interferometry recovers it.
        """
        from openflight.kld7.radc import (
            ANTENNA_SPACING_M, WAVELENGTH_M, SAMPLES_PER_CHANNEL,
        )

        n = SAMPLES_PER_CHANNEL  # 256
        max_speed_kmh = 100.0
        fft_size = 2048

        # Velocity to normalized frequency
        if velocity_kmh >= 0:
            norm_freq = velocity_kmh / (2 * max_speed_kmh)
        else:
            norm_freq = 1.0 + velocity_kmh / (2 * max_speed_kmh)

        t = np.arange(n)
        phase_per_sample = 2 * np.pi * norm_freq

        # F1A channel: reference
        f1a_i = (amplitude * np.cos(phase_per_sample * t)).astype(np.int16) + 32768
        f1a_q = (amplitude * np.sin(phase_per_sample * t)).astype(np.int16) + 32768

        # F2A channel: same tone shifted by angle-dependent phase
        angle_rad = np.radians(angle_deg)
        steering_phase = 2 * np.pi * ANTENNA_SPACING_M * np.sin(angle_rad) / WAVELENGTH_M
        f2a_i = (amplitude * np.cos(phase_per_sample * t + steering_phase)).astype(np.int16) + 32768
        f2a_q = (amplitude * np.sin(phase_per_sample * t + steering_phase)).astype(np.int16) + 32768

        # F1B channel: zeros (not used for angle)
        zeros = np.full(n, 32768, dtype=np.uint16)

        # Pack into 3072-byte payload
        payload = b""
        for ch in [f1a_i, f1a_q, f2a_i, f2a_q, zeros, zeros]:
            payload += ch.astype(np.uint16).tobytes()
        return payload

    def test_extracts_angle_from_radc_with_ball_speed(self):
        """RADC extraction should find the angle at the OPS-anchored velocity bin."""
        tracker = self._make_tracker()
        now = time.time()

        # Ball at 72 mph = 115.9 km/h, aliases to -84.1 km/h at 200 km/h range
        ball_speed_mph = 72.0
        ball_kmh = ball_speed_mph * 1.609
        aliased_kmh = ball_kmh % 200.0
        if aliased_kmh > 100.0:
            aliased_kmh -= 200.0
        target_angle = 12.0

        radc = self._make_radc_payload_with_tone(aliased_kmh, angle_deg=target_angle)

        # Quiet frames, then impact frame, then quiet frames
        for i in range(10):
            tracker._add_frame(KLD7Frame(timestamp=now + i * 0.056, radc=None))
        tracker._add_frame(KLD7Frame(timestamp=now + 0.56, radc=radc))
        for i in range(10):
            tracker._add_frame(KLD7Frame(timestamp=now + 0.62 + i * 0.056, radc=None))

        result = tracker.get_angle_for_shot(ball_speed_mph=ball_speed_mph)
        assert result is not None
        assert result.detection_class == "ball"
        assert result.vertical_deg == pytest.approx(target_angle, abs=3.0)
        assert result.confidence > 0.0

    def test_falls_back_to_pdat_without_radc(self):
        """When no RADC frames are present, should fall back to PDAT-based detection."""
        tracker = self._make_tracker()
        now = time.time()

        # PDAT-only frames with ball signature
        for i in range(3):
            tracker._add_frame(KLD7Frame(
                timestamp=now + i * 0.033,
                pdat=[{"distance": 4.2, "speed": 25.0, "angle": 15.0, "magnitude": 2500}],
            ))

        result = tracker.get_angle_for_shot(ball_speed_mph=72.0)
        assert result is not None
        assert result.detection_class == "ball"
        assert 14.0 < result.vertical_deg < 16.0

    def test_falls_back_to_pdat_without_ball_speed(self):
        """When ball_speed_mph is None, should use PDAT-based detection."""
        tracker = self._make_tracker()
        now = time.time()

        for i in range(3):
            tracker._add_frame(KLD7Frame(
                timestamp=now + i * 0.033,
                pdat=[{"distance": 4.2, "speed": 25.0, "angle": 15.0, "magnitude": 2500}],
            ))

        result = tracker.get_angle_for_shot(ball_speed_mph=None)
        assert result is not None

    def test_angle_offset_applied_to_radc(self):
        """Angle offset should be applied to RADC-extracted angle."""
        tracker = self._make_tracker()
        tracker.angle_offset_deg = 5.0
        now = time.time()

        ball_speed_mph = 72.0
        ball_kmh = ball_speed_mph * 1.609
        aliased_kmh = ball_kmh % 200.0
        if aliased_kmh > 100.0:
            aliased_kmh -= 200.0

        radc = self._make_radc_payload_with_tone(aliased_kmh, angle_deg=10.0)

        for i in range(10):
            tracker._add_frame(KLD7Frame(timestamp=now + i * 0.056, radc=None))
        tracker._add_frame(KLD7Frame(timestamp=now + 0.56, radc=radc))
        for i in range(10):
            tracker._add_frame(KLD7Frame(timestamp=now + 0.62 + i * 0.056, radc=None))

        result = tracker.get_angle_for_shot(ball_speed_mph=ball_speed_mph)
        assert result is not None
        # Should be ~10° raw + 5° offset = ~15°
        assert result.vertical_deg == pytest.approx(15.0, abs=3.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_kld7.py::TestRADCAngleExtraction -v`
Expected: FAIL — `get_angle_for_shot() got an unexpected keyword argument 'ball_speed_mph'`

- [ ] **Step 3: Add `_extract_ball_radc` method and update `get_angle_for_shot`**

In `src/openflight/kld7/tracker.py`, add the RADC extraction method and update the public API:

At the top of the file, add the import:
```python
from typing import List, Optional
```

Add this method to `KLD7Tracker`, before `_extract_ball`:

```python
    def _extract_ball_radc(self, ball_speed_mph: float) -> Optional[KLD7Angle]:
        """Extract ball launch angle from RADC frames using phase interferometry.

        Uses OPS243 ball speed to narrow the FFT velocity search to the
        exact aliased bin, then extracts angle from the F1A/F2A phase
        difference at that bin.

        Args:
            ball_speed_mph: Ball speed from OPS243, used to compute the
                expected aliased velocity bin in the K-LD7 spectrum.

        Returns:
            KLD7Angle or None if no RADC detection found.
        """
        from .radc import extract_launch_angle

        # Collect frames that have RADC data
        radc_frames = []
        for frame in self._ring_buffer:
            radc_frames.append({
                "timestamp": frame.timestamp,
                "radc": frame.radc,
            })

        if not any(f["radc"] for f in radc_frames):
            return None

        results = extract_launch_angle(
            radc_frames,
            ops243_ball_speed_mph=ball_speed_mph,
            angle_offset_deg=self.angle_offset_deg,
            speed_tolerance_mph=10.0,
        )

        if not results:
            logger.debug("K-LD7 RADC: no ball detected at %.1f mph", ball_speed_mph)
            return None

        # Use the best (first) result
        best = results[0]

        logger.info(
            "K-LD7 RADC: angle=%.1f° speed=%.1f mph snr=%.1f conf=%.2f frames=%d",
            best["launch_angle_deg"], best["ball_speed_mph"],
            best["avg_snr_db"], best["confidence"], best["frame_count"],
        )

        if self.orientation == "vertical":
            return KLD7Angle(
                vertical_deg=best["launch_angle_deg"],
                horizontal_deg=None,
                distance_m=0.0,
                magnitude=best["avg_snr_db"],
                confidence=best["confidence"],
                num_frames=best["frame_count"],
                detection_class="ball",
            )
        return KLD7Angle(
            vertical_deg=None,
            horizontal_deg=best["launch_angle_deg"],
            distance_m=0.0,
            magnitude=best["avg_snr_db"],
            confidence=best["confidence"],
            num_frames=best["frame_count"],
            detection_class="ball",
        )
```

Update `get_angle_for_shot` to accept `ball_speed_mph` and try RADC first:

```python
    def get_angle_for_shot(
        self,
        shot_timestamp: Optional[float] = None,
        ball_speed_mph: Optional[float] = None,
    ) -> Optional[KLD7Angle]:
        """Search the ring buffer for the ball launch angle.

        If ball_speed_mph is provided and RADC frames are available, uses
        phase-interferometry for higher accuracy. Falls back to PDAT
        distance-based detection otherwise.
        """
        # Try RADC phase-interferometry first (requires ball speed anchor)
        if ball_speed_mph is not None:
            try:
                radc_result = self._extract_ball_radc(ball_speed_mph)
                if radc_result is not None:
                    return radc_result
                logger.debug("K-LD7 RADC extraction failed, falling back to PDAT")
            except Exception as e:
                logger.warning("K-LD7 RADC error, falling back to PDAT: %s", e)

        # Fallback: PDAT distance-based detection
        return self._extract_ball(shot_timestamp)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_kld7.py -v`
Expected: All PASS — both new RADC tests and existing PDAT tests.

- [ ] **Step 5: Commit**

```bash
git add src/openflight/kld7/tracker.py tests/test_kld7.py
git commit -m "feat: RADC phase-interferometry angle extraction with PDAT fallback"
```

---

### Task 4: Stream RADC at 3Mbaud in the tracker

**Files:**
- Modify: `src/openflight/kld7/tracker.py`

- [ ] **Step 1: Update `connect()` to use 3Mbaud**

Change the baud rate from 115200 to 3000000:

```python
    def connect(self) -> bool:
        """Connect to K-LD7 and configure for golf."""
        try:
            from kld7 import KLD7
        except ImportError:
            logger.error("kld7 package not installed. Run: pip install kld7")
            return False

        port = self.port or _find_port()
        if not port:
            logger.error("No K-LD7 EVAL board detected")
            return False

        try:
            self._radar = KLD7(port, baudrate=3000000)
            logger.info("K-LD7 connected on %s at 3Mbaud", port)
        except Exception as e:
            logger.error("K-LD7 connection failed: %s", e)
            return False

        self._configure_for_golf()
        return True
```

- [ ] **Step 2: Update `_stream_loop` to capture RADC frames**

Change frame_codes to include RADC, and store the RADC payload:

```python
    def _stream_loop(self):
        """Background thread: stream RADC+TDAT+PDAT into ring buffer."""
        from kld7 import FrameCode

        frame_codes = FrameCode.RADC | FrameCode.TDAT | FrameCode.PDAT
        current_frame = KLD7Frame(timestamp=time.time())
        seen_in_frame = set()

        try:
            for code, payload in self._radar.stream_frames(frame_codes, max_count=-1):
                if not self._running:
                    break

                if code in seen_in_frame:
                    self._add_frame(current_frame)
                    current_frame = KLD7Frame(timestamp=time.time())
                    seen_in_frame = set()

                seen_in_frame.add(code)

                if code == "RADC":
                    current_frame.radc = payload
                elif code == "TDAT":
                    current_frame.tdat = _target_to_dict(payload)
                elif code == "PDAT":
                    current_frame.pdat = [_target_to_dict(t) for t in payload] if payload else []

            if seen_in_frame:
                self._add_frame(current_frame)

        except Exception as e:
            if self._running:
                logger.error("K-LD7 stream error: %s", e)
```

- [ ] **Step 3: Run all K-LD7 tests**

Run: `uv run pytest tests/test_kld7.py -v`
Expected: All PASS (no hardware needed — tests construct frames directly)

- [ ] **Step 4: Commit**

```bash
git add src/openflight/kld7/tracker.py
git commit -m "feat: stream RADC at 3Mbaud for phase-interferometry data"
```

---

### Task 5: Pass ball_speed_mph from server to tracker

**Files:**
- Modify: `src/openflight/server.py:857`

- [ ] **Step 1: Update the `get_angle_for_shot` call in `on_shot_detected`**

In `src/openflight/server.py`, find the call at line ~857:

```python
            kld7_angle = kld7_tracker.get_angle_for_shot(
                shot_timestamp=shot_ts
            )
```

Change it to pass the ball speed:

```python
            kld7_angle = kld7_tracker.get_angle_for_shot(
                shot_timestamp=shot_ts,
                ball_speed_mph=shot.ball_speed_mph,
            )
```

- [ ] **Step 2: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add src/openflight/server.py
git commit -m "feat: pass ball speed to K-LD7 for RADC velocity filtering"
```

---

### Task 6: Keep `scripts/kld7_radc_lib.py` in sync

The scripts module is used for offline analysis and needs to keep working. Rather than maintaining two copies, make it import from the package.

**Files:**
- Modify: `scripts/kld7_radc_lib.py`

- [ ] **Step 1: Replace duplicated code with re-exports**

Replace the top of `scripts/kld7_radc_lib.py` so it re-exports from the package for shared functions, and keeps offline-only functions in place:

```python
"""Standalone helpers for K-LD7 raw ADC (RADC) signal processing.

Core processing functions live in src/openflight/kld7/radc.py.
This module re-exports them and adds offline-analysis-only functions
(analyze_capture, compare_radc_vs_pdat, etc.).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow importing from src/ when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Re-export core processing functions
from openflight.kld7.radc import (  # noqa: F401, E402
    ANTENNA_SPACING_M,
    CFARDetection,
    DC_MASK_BINS,
    RADC_PAYLOAD_BYTES,
    SAMPLES_PER_CHANNEL,
    WAVELENGTH_M,
    ball_bin_range_from_speed,
    bin_to_velocity_kmh,
    cfar_detect,
    compute_fft_complex,
    compute_spectrum,
    extract_launch_angle,
    find_impact_frames,
    parse_radc_payload,
    per_bin_angle_deg,
    to_complex_iq,
)
```

Keep all the offline-only functions (`analyze_capture`, `process_radc_frame`, `process_radc_frame_spatial`, `compare_radc_vs_pdat`, `RADCDetection`, `SpatialDetection`, `ball_bin_range`, `club_bin_range`, `estimate_angle_from_phase`, `compute_angle_velocity_map`, `ADC_MIDPOINT`, `_velocity_to_bin`) in this file below the re-exports. `_velocity_to_bin` is used by `ball_bin_range` and `club_bin_range` which are offline-only, so copy it here too. Same for `ADC_MIDPOINT` which is referenced by tests/analysis.

- [ ] **Step 2: Run analysis script smoke test**

Run: `python3 -c "import sys; sys.path.insert(0, 'scripts'); from kld7_radc_lib import analyze_capture, extract_launch_angle, parse_radc_payload; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Run RADC lib tests**

Run: `uv run pytest tests/test_kld7_radc_lib.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add scripts/kld7_radc_lib.py
git commit -m "refactor: scripts/kld7_radc_lib re-exports from kld7 package"
```

---

### Task 7: End-to-end verification

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All PASS

- [ ] **Step 2: Run lint**

Run: `uv run ruff check src/openflight/kld7/`
Expected: No errors

- [ ] **Step 3: Verify the capture analysis still works**

Run: `python3 -c "
import pickle, sys
sys.path.insert(0, 'scripts')
from kld7_radc_lib import analyze_capture

with open('session_logs/kld7_radc_20260406_161627-7i.pkl', 'rb') as f:
    data = pickle.load(f)

for s in data.get('ops243_shots', []):
    if 'impact_timestamp' not in s:
        s['impact_timestamp'] = s['timestamp']

results = analyze_capture(data)
assert len(results) >= 1
print(f'Shot 0: angle={results[0][\"launch_angle_deg\"]}°, speed={results[0][\"ball_speed_mph\"]} mph')
print('OK')
"`
Expected: `Shot 0: angle=8.0°, speed=71.7 mph` and `OK`

- [ ] **Step 4: Commit all remaining changes**

```bash
git add -A
git commit -m "feat: RADC launch angle extraction in full stack"
```
