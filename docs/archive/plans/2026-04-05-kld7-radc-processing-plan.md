# K-LD7 Raw ADC Processing — Implementation Plan

!!! warning "ARCHIVED DOCUMENT"

    This is a historical design or implementation note, kept as a record of why
    the code is shaped the way it is. It describes the project as of the date in
    its filename and **is not a guide to follow** — commands, paths, and
    constants may no longer match the code. See the
    [Archive index](../index.md) for current alternatives.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build capture and analysis tools for K-LD7 raw I/Q ADC data, enabling custom FFT + CFAR signal processing that can detect ball returns the module's built-in detector misses.

**Architecture:** A standalone capture script records RADC + PDAT frames to `.pkl`. A processing library (`kld7_radc_lib.py`) handles FFT, CFAR, angle estimation, and ball isolation — numpy only, no plotting. A separate analysis script generates visualizations and CSV exports. All tools are offline and independent of the live tracker.

**Tech Stack:** Python 3.10+, numpy, matplotlib (analysis only), kld7 package

---

## File Structure

| File | Responsibility |
|------|---------------|
| `scripts/capture_kld7_radc.py` | CLI capture tool — stream RADC+PDAT at 3 Mbaud, save to `.pkl` |
| `scripts/kld7_radc_lib.py` | Processing library — RADC parsing, FFT, CFAR, angle estimation, ball isolation |
| `scripts/analyze_kld7_radc.py` | Analysis CLI — load captures, generate plots and CSV |
| `tests/test_kld7_radc_lib.py` | Tests for the processing library |

---

### Task 1: RADC Parsing Library

**Files:**
- Create: `scripts/kld7_radc_lib.py`
- Create: `tests/test_kld7_radc_lib.py`

- [ ] **Step 1: Write failing test for RADC frame parsing**

```python
# tests/test_kld7_radc_lib.py
"""Tests for K-LD7 raw ADC processing library."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from kld7_radc_lib import parse_radc_payload


class TestParseRadcPayload:
    def test_parses_3072_bytes_into_six_channels(self):
        """RADC payload should split into 6 arrays of 256 uint16 samples."""
        # Create synthetic 3072-byte payload: 6 segments of 256 uint16 values
        payload = b""
        for seg in range(6):
            payload += np.arange(seg * 256, (seg + 1) * 256, dtype=np.uint16).tobytes()

        result = parse_radc_payload(payload)

        assert result["f1a_i"].shape == (256,)
        assert result["f1a_q"].shape == (256,)
        assert result["f2a_i"].shape == (256,)
        assert result["f2a_q"].shape == (256,)
        assert result["f1b_i"].shape == (256,)
        assert result["f1b_q"].shape == (256,)
        assert result["f1a_i"].dtype == np.uint16
        # Verify first segment starts at 0
        assert result["f1a_i"][0] == 0
        assert result["f1a_i"][255] == 255
        # Verify second segment starts at 256
        assert result["f1a_q"][0] == 256

    def test_rejects_wrong_payload_size(self):
        """Payloads that aren't 3072 bytes should raise ValueError."""
        with pytest.raises(ValueError, match="3072"):
            parse_radc_payload(b"\x00" * 1024)

    def test_to_complex_iq(self):
        """Should convert uint16 I/Q pairs to complex float centered at zero."""
        payload = b"\x00" * 3072
        # Put known values in F1A I and Q
        i_vals = np.full(256, 32768, dtype=np.uint16)  # midpoint
        q_vals = np.full(256, 32768 + 1000, dtype=np.uint16)  # slightly above mid
        payload_arr = bytearray(payload)
        payload_arr[0:512] = i_vals.tobytes()
        payload_arr[512:1024] = q_vals.tobytes()

        result = parse_radc_payload(bytes(payload_arr))
        f1a = to_complex_iq(result["f1a_i"], result["f1a_q"])

        assert f1a.dtype == np.complex128
        assert f1a.shape == (256,)
        # I centered near 0, Q centered near +1000
        assert np.abs(f1a[0].real) < 100
        assert np.abs(f1a[0].imag - 1000) < 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src uv run --no-project python -m pytest tests/test_kld7_radc_lib.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kld7_radc_lib'`

- [ ] **Step 3: Implement RADC parsing**

```python
# scripts/kld7_radc_lib.py
"""Standalone helpers for K-LD7 raw ADC (RADC) signal processing."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

RADC_PAYLOAD_BYTES = 3072
SAMPLES_PER_CHANNEL = 256
ADC_MIDPOINT = 32768  # uint16 midpoint for DC offset removal


def parse_radc_payload(payload: bytes) -> dict[str, np.ndarray]:
    """Parse a 3072-byte RADC payload into six uint16 channel arrays.

    Layout (each segment = 256 × uint16 = 512 bytes):
        [0:512]     F1 Freq A — I channel
        [512:1024]  F1 Freq A — Q channel
        [1024:1536] F2 Freq A — I channel
        [1536:2048] F2 Freq A — Q channel
        [2048:2560] F1 Freq B — I channel
        [2560:3072] F1 Freq B — Q channel
    """
    if len(payload) != RADC_PAYLOAD_BYTES:
        raise ValueError(
            f"RADC payload must be {RADC_PAYLOAD_BYTES} bytes, got {len(payload)}"
        )
    seg = 512  # bytes per segment
    return {
        "f1a_i": np.frombuffer(payload[0:seg], dtype=np.uint16).copy(),
        "f1a_q": np.frombuffer(payload[seg : 2 * seg], dtype=np.uint16).copy(),
        "f2a_i": np.frombuffer(payload[2 * seg : 3 * seg], dtype=np.uint16).copy(),
        "f2a_q": np.frombuffer(payload[3 * seg : 4 * seg], dtype=np.uint16).copy(),
        "f1b_i": np.frombuffer(payload[4 * seg : 5 * seg], dtype=np.uint16).copy(),
        "f1b_q": np.frombuffer(payload[5 * seg : 6 * seg], dtype=np.uint16).copy(),
    }


def to_complex_iq(i_channel: np.ndarray, q_channel: np.ndarray) -> np.ndarray:
    """Convert uint16 I/Q arrays to complex float, removing DC offset."""
    i_float = i_channel.astype(np.float64) - ADC_MIDPOINT
    q_float = q_channel.astype(np.float64) - ADC_MIDPOINT
    return i_float + 1j * q_float
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src uv run --no-project python -m pytest tests/test_kld7_radc_lib.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```
feat: add RADC payload parsing for K-LD7 raw I/Q
```

---

### Task 2: FFT Processing

**Files:**
- Modify: `scripts/kld7_radc_lib.py`
- Modify: `tests/test_kld7_radc_lib.py`

- [ ] **Step 1: Write failing test for range-Doppler FFT**

```python
# Add to tests/test_kld7_radc_lib.py
from kld7_radc_lib import parse_radc_payload, to_complex_iq, compute_spectrum


class TestComputeSpectrum:
    def test_returns_magnitude_spectrum_of_correct_size(self):
        """FFT should produce magnitude spectrum with fft_size bins."""
        iq = np.random.randn(256) + 1j * np.random.randn(256)
        spectrum = compute_spectrum(iq, fft_size=2048)
        assert spectrum.shape == (2048,)
        assert spectrum.dtype == np.float64

    def test_detects_injected_tone(self):
        """A pure tone at a known bin should produce a clear peak."""
        n = 256
        fft_size = 2048
        bin_target = 100  # put energy at bin 100
        freq = bin_target / fft_size
        t = np.arange(n)
        iq = np.exp(2j * np.pi * freq * t)

        spectrum = compute_spectrum(iq, fft_size=fft_size)

        peak_bin = np.argmax(spectrum)
        # Peak should be at or very near the target bin
        assert abs(peak_bin - bin_target) <= 1

    def test_zero_padding_increases_resolution(self):
        """Larger FFT size should produce more bins."""
        iq = np.random.randn(256) + 1j * np.random.randn(256)
        spec_small = compute_spectrum(iq, fft_size=512)
        spec_large = compute_spectrum(iq, fft_size=4096)
        assert spec_small.shape == (512,)
        assert spec_large.shape == (4096,)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src uv run --no-project python -m pytest tests/test_kld7_radc_lib.py::TestComputeSpectrum -v`
Expected: FAIL — `cannot import name 'compute_spectrum'`

- [ ] **Step 3: Implement FFT processing**

```python
# Add to scripts/kld7_radc_lib.py

def compute_spectrum(iq: np.ndarray, fft_size: int = 2048) -> np.ndarray:
    """Compute magnitude spectrum from complex I/Q with Hann window and zero-padding.

    Args:
        iq: Complex I/Q array (256 samples from RADC)
        fft_size: FFT length (zero-padded if > len(iq))

    Returns:
        Magnitude spectrum (linear scale), length = fft_size
    """
    windowed = iq * np.hanning(len(iq))
    padded = np.zeros(fft_size, dtype=np.complex128)
    padded[: len(windowed)] = windowed
    fft_result = np.fft.fft(padded)
    return np.abs(fft_result)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src uv run --no-project python -m pytest tests/test_kld7_radc_lib.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```
feat: add FFT spectrum computation for K-LD7 RADC
```

---

### Task 3: CFAR Detection

**Files:**
- Modify: `scripts/kld7_radc_lib.py`
- Modify: `tests/test_kld7_radc_lib.py`

- [ ] **Step 1: Write failing test for CFAR detector**

```python
# Add to tests/test_kld7_radc_lib.py
from kld7_radc_lib import (
    parse_radc_payload, to_complex_iq, compute_spectrum,
    cfar_detect, CFARDetection,
)


class TestCFARDetect:
    def test_detects_tone_above_noise(self):
        """A clear tone in noise should produce exactly one detection."""
        fft_size = 2048
        # Noise floor
        spectrum = np.random.exponential(1.0, size=fft_size)
        # Inject a strong tone at bin 500
        spectrum[500] = 200.0

        detections = cfar_detect(spectrum, guard_cells=4, training_cells=16, threshold_factor=8.0)

        assert len(detections) >= 1
        bins = [d.bin_index for d in detections]
        assert 500 in bins

    def test_no_detections_in_pure_noise(self):
        """Uniform noise should produce very few or zero false detections."""
        fft_size = 2048
        np.random.seed(42)
        spectrum = np.random.exponential(1.0, size=fft_size)

        detections = cfar_detect(spectrum, guard_cells=4, training_cells=16, threshold_factor=12.0)

        # With high threshold, noise should produce very few false alarms
        assert len(detections) <= 5

    def test_detection_has_required_fields(self):
        """Each detection should carry bin index, magnitude, and SNR."""
        spectrum = np.ones(2048)
        spectrum[300] = 100.0

        detections = cfar_detect(spectrum, guard_cells=4, training_cells=16, threshold_factor=8.0)

        assert len(detections) >= 1
        d = detections[0]
        assert hasattr(d, "bin_index")
        assert hasattr(d, "magnitude")
        assert hasattr(d, "snr_db")
        assert d.snr_db > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src uv run --no-project python -m pytest tests/test_kld7_radc_lib.py::TestCFARDetect -v`
Expected: FAIL — `cannot import name 'cfar_detect'`

- [ ] **Step 3: Implement OS-CFAR detector**

```python
# Add to scripts/kld7_radc_lib.py

@dataclass(frozen=True)
class CFARDetection:
    bin_index: int
    magnitude: float
    snr_db: float


def cfar_detect(
    spectrum: np.ndarray,
    guard_cells: int = 4,
    training_cells: int = 16,
    threshold_factor: float = 8.0,
) -> list[CFARDetection]:
    """Ordered-statistic CFAR detection on a magnitude spectrum.

    For each bin, estimates the noise level from surrounding training cells
    (excluding guard cells) and declares a detection if the bin exceeds
    threshold_factor × noise_estimate.

    Args:
        spectrum: Magnitude spectrum (1D array)
        guard_cells: Number of guard cells on each side of the cell under test
        training_cells: Number of training cells on each side (outside guard)
        threshold_factor: Detection threshold as multiple of noise estimate

    Returns:
        List of detections sorted by magnitude (descending)
    """
    n = len(spectrum)
    margin = guard_cells + training_cells
    detections = []

    for i in range(margin, n - margin):
        left_train = spectrum[i - margin : i - guard_cells]
        right_train = spectrum[i + guard_cells + 1 : i + margin + 1]
        training = np.concatenate([left_train, right_train])
        # Use median (OS-CFAR) for robustness against interfering targets
        noise_estimate = np.median(training)

        if noise_estimate <= 0:
            continue

        if spectrum[i] > threshold_factor * noise_estimate:
            snr_db = 10.0 * np.log10(spectrum[i] / noise_estimate)
            detections.append(
                CFARDetection(
                    bin_index=i,
                    magnitude=float(spectrum[i]),
                    snr_db=float(snr_db),
                )
            )

    detections.sort(key=lambda d: d.magnitude, reverse=True)
    return detections
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src uv run --no-project python -m pytest tests/test_kld7_radc_lib.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```
feat: add OS-CFAR detector for K-LD7 RADC spectra
```

---

### Task 4: Bin-to-Physical Conversion and Angle Estimation

**Files:**
- Modify: `scripts/kld7_radc_lib.py`
- Modify: `tests/test_kld7_radc_lib.py`

- [ ] **Step 1: Write failing tests for physical unit conversion**

```python
# Add to tests/test_kld7_radc_lib.py
from kld7_radc_lib import (
    parse_radc_payload, to_complex_iq, compute_spectrum,
    cfar_detect, CFARDetection,
    bin_to_velocity_kmh, estimate_angle_from_phase, RADCDetection,
)


class TestBinToPhysical:
    def test_zero_bin_is_zero_velocity(self):
        """DC bin should map to zero velocity."""
        v = bin_to_velocity_kmh(0, fft_size=2048, max_speed_kmh=100.0)
        assert v == pytest.approx(0.0, abs=0.1)

    def test_velocity_scales_linearly(self):
        """Bins should map linearly to velocity up to max_speed."""
        fft_size = 2048
        max_speed = 100.0
        v_quarter = bin_to_velocity_kmh(fft_size // 4, fft_size=fft_size, max_speed_kmh=max_speed)
        v_half = bin_to_velocity_kmh(fft_size // 2, fft_size=fft_size, max_speed_kmh=max_speed)
        assert v_quarter == pytest.approx(max_speed / 4, abs=1.0)
        assert v_half == pytest.approx(max_speed / 2, abs=1.0)

    def test_negative_velocity_for_upper_bins(self):
        """Upper half of FFT bins represent negative (inbound) velocity."""
        fft_size = 2048
        max_speed = 100.0
        v = bin_to_velocity_kmh(fft_size - 10, fft_size=fft_size, max_speed_kmh=max_speed)
        assert v < 0


class TestAngleEstimation:
    def test_zero_phase_difference_gives_zero_angle(self):
        """Identical signals on both channels should give ~0 degrees."""
        n = 256
        signal = np.exp(2j * np.pi * 0.1 * np.arange(n))
        angle = estimate_angle_from_phase(signal, signal)
        assert abs(angle) < 2.0

    def test_known_phase_offset_gives_nonzero_angle(self):
        """A deliberate phase shift between channels should produce a measurable angle."""
        n = 256
        signal = np.exp(2j * np.pi * 0.1 * np.arange(n))
        shifted = signal * np.exp(1j * np.pi / 6)  # 30 degree phase shift
        angle = estimate_angle_from_phase(signal, shifted)
        assert abs(angle) > 5.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src uv run --no-project python -m pytest tests/test_kld7_radc_lib.py::TestBinToPhysical tests/test_kld7_radc_lib.py::TestAngleEstimation -v`
Expected: FAIL — import error

- [ ] **Step 3: Implement conversions**

```python
# Add to scripts/kld7_radc_lib.py

@dataclass(frozen=True)
class RADCDetection:
    frame_index: int
    timestamp: float
    distance_m: float
    velocity_kmh: float
    angle_deg: float
    magnitude: float
    snr_db: float
    bin_index: int


def bin_to_velocity_kmh(bin_index: int, fft_size: int, max_speed_kmh: float) -> float:
    """Convert FFT bin index to velocity in km/h.

    Bins 0..N/2 = 0..+max_speed (outbound).
    Bins N/2..N = -max_speed..0 (inbound, aliased).
    """
    if bin_index <= fft_size // 2:
        return bin_index * max_speed_kmh / (fft_size // 2)
    else:
        return (bin_index - fft_size) * max_speed_kmh / (fft_size // 2)


def estimate_angle_from_phase(
    f1_complex: np.ndarray,
    f2_complex: np.ndarray,
) -> float:
    """Estimate angle from phase difference between two frequency channels.

    Uses cross-correlation phase to estimate the angle of arrival.
    The exact angle-to-phase mapping depends on K-LD7 antenna geometry
    (spacing, wavelength). This returns a proportional estimate that
    needs empirical calibration against known angles.

    Returns:
        Angle estimate in degrees (uncalibrated — proportional to phase diff)
    """
    # Cross-spectral phase
    cross = np.sum(f1_complex * np.conj(f2_complex))
    phase_rad = np.angle(cross)
    # Convert to degrees — scale factor TBD from calibration
    # For K-LD7 at 24 GHz with ~6mm antenna spacing, rough estimate:
    # angle ≈ arcsin(phase / pi) * (180/pi)
    # For now return raw phase in degrees as a proportional estimate
    return float(np.degrees(phase_rad))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src uv run --no-project python -m pytest tests/test_kld7_radc_lib.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```
feat: add velocity conversion and phase-based angle estimation
```

---

### Task 5: Frame Processing Pipeline

**Files:**
- Modify: `scripts/kld7_radc_lib.py`
- Modify: `tests/test_kld7_radc_lib.py`

- [ ] **Step 1: Write failing test for full frame processing**

```python
# Add to tests/test_kld7_radc_lib.py
from kld7_radc_lib import (
    parse_radc_payload, to_complex_iq, compute_spectrum,
    cfar_detect, CFARDetection,
    bin_to_velocity_kmh, estimate_angle_from_phase, RADCDetection,
    process_radc_frame,
)


class TestProcessRadcFrame:
    def _make_frame(self, tone_bin=100):
        """Create a synthetic RADC frame with a tone at a known bin."""
        n = 256
        fft_size = 2048
        freq = tone_bin / fft_size
        t = np.arange(n)
        signal = 5000.0 * np.exp(2j * np.pi * freq * t)
        noise = np.random.randn(n) + 1j * np.random.randn(n)
        iq = signal + noise

        i_vals = (iq.real + ADC_MIDPOINT).astype(np.uint16)
        q_vals = (iq.imag + ADC_MIDPOINT).astype(np.uint16)
        # Pack into 3072-byte payload (put signal in F1A, zeros elsewhere)
        payload = bytearray(3072)
        payload[0:512] = i_vals.tobytes()
        payload[512:1024] = q_vals.tobytes()

        return {
            "timestamp": 1000.0,
            "radc": bytes(payload),
            "tdat": None,
            "pdat": [],
        }

    def test_returns_detections_for_frame_with_tone(self):
        """A frame with an injected tone should produce at least one detection."""
        frame = self._make_frame(tone_bin=100)
        detections = process_radc_frame(
            frame, frame_index=0, fft_size=2048, max_speed_kmh=100.0,
        )
        assert len(detections) >= 1
        assert all(isinstance(d, RADCDetection) for d in detections)

    def test_returns_empty_for_noise_only_frame(self):
        """A frame with only noise should produce few or no detections."""
        np.random.seed(42)
        noise_i = np.random.randint(30000, 35000, size=256, dtype=np.uint16)
        noise_q = np.random.randint(30000, 35000, size=256, dtype=np.uint16)
        payload = bytearray(3072)
        payload[0:512] = noise_i.tobytes()
        payload[512:1024] = noise_q.tobytes()
        frame = {"timestamp": 1000.0, "radc": bytes(payload), "tdat": None, "pdat": []}
        detections = process_radc_frame(
            frame, frame_index=0, fft_size=2048, max_speed_kmh=100.0,
            cfar_threshold=12.0,
        )
        assert len(detections) <= 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src uv run --no-project python -m pytest tests/test_kld7_radc_lib.py::TestProcessRadcFrame -v`
Expected: FAIL — import error

- [ ] **Step 3: Implement frame processing pipeline**

```python
# Add to scripts/kld7_radc_lib.py

def process_radc_frame(
    frame: dict,
    frame_index: int,
    fft_size: int = 2048,
    max_speed_kmh: float = 100.0,
    cfar_threshold: float = 8.0,
    cfar_guard: int = 4,
    cfar_training: int = 16,
) -> list[RADCDetection]:
    """Process one RADC frame: parse → FFT → CFAR → physical units.

    Uses F1A channel as primary, F2A for angle estimation.
    """
    radc_raw = frame.get("radc")
    if radc_raw is None:
        return []

    if isinstance(radc_raw, bytes):
        channels = parse_radc_payload(radc_raw)
    else:
        channels = radc_raw

    f1a = to_complex_iq(channels["f1a_i"], channels["f1a_q"])
    f2a = to_complex_iq(channels["f2a_i"], channels["f2a_q"])

    spectrum = compute_spectrum(f1a, fft_size=fft_size)
    cfar_hits = cfar_detect(
        spectrum,
        guard_cells=cfar_guard,
        training_cells=cfar_training,
        threshold_factor=cfar_threshold,
    )

    angle_deg = estimate_angle_from_phase(f1a, f2a)
    timestamp = float(frame["timestamp"])

    detections = []
    for hit in cfar_hits:
        velocity = bin_to_velocity_kmh(hit.bin_index, fft_size, max_speed_kmh)
        detections.append(
            RADCDetection(
                frame_index=frame_index,
                timestamp=timestamp,
                distance_m=0.0,  # RADC gives velocity, not range — set from FMCW chirp later
                velocity_kmh=velocity,
                angle_deg=angle_deg,
                magnitude=hit.magnitude,
                snr_db=hit.snr_db,
                bin_index=hit.bin_index,
            )
        )

    return detections
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src uv run --no-project python -m pytest tests/test_kld7_radc_lib.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```
feat: add RADC frame processing pipeline (FFT + CFAR + angle)
```

---

### Task 6: RADC vs PDAT Comparison

**Files:**
- Modify: `scripts/kld7_radc_lib.py`
- Modify: `tests/test_kld7_radc_lib.py`

- [ ] **Step 1: Write failing test for comparison function**

```python
# Add to tests/test_kld7_radc_lib.py
from kld7_radc_lib import (
    parse_radc_payload, to_complex_iq, compute_spectrum,
    cfar_detect, CFARDetection,
    bin_to_velocity_kmh, estimate_angle_from_phase, RADCDetection,
    process_radc_frame, compare_radc_vs_pdat,
)


class TestCompareRadcVsPdat:
    def test_counts_radc_and_pdat_detections(self):
        """Comparison should report counts from both sources."""
        radc_detections = [
            RADCDetection(0, 1.0, 0.0, 50.0, 10.0, 100.0, 15.0, 500),
            RADCDetection(0, 1.0, 0.0, 30.0, 8.0, 80.0, 12.0, 300),
        ]
        pdat = [
            {"distance": 4.2, "speed": 25.0, "angle": 10.0, "magnitude": 2500},
        ]

        result = compare_radc_vs_pdat(radc_detections, pdat)

        assert result["radc_count"] == 2
        assert result["pdat_count"] == 1

    def test_handles_empty_inputs(self):
        """Should handle cases where one or both sources have no detections."""
        result = compare_radc_vs_pdat([], [])
        assert result["radc_count"] == 0
        assert result["pdat_count"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src uv run --no-project python -m pytest tests/test_kld7_radc_lib.py::TestCompareRadcVsPdat -v`
Expected: FAIL — import error

- [ ] **Step 3: Implement comparison**

```python
# Add to scripts/kld7_radc_lib.py

def compare_radc_vs_pdat(
    radc_detections: list[RADCDetection],
    pdat: list[dict],
) -> dict:
    """Compare our RADC FFT detections against the module's PDAT output.

    Returns a summary dict for logging / CSV export.
    """
    pdat_speeds = [abs(p.get("speed", 0)) for p in pdat if p]
    pdat_mags = [p.get("magnitude", 0) for p in pdat if p]
    radc_velocities = [abs(d.velocity_kmh) for d in radc_detections]
    radc_mags = [d.magnitude for d in radc_detections]

    return {
        "radc_count": len(radc_detections),
        "pdat_count": len(pdat),
        "radc_max_velocity_kmh": max(radc_velocities) if radc_velocities else 0.0,
        "pdat_max_speed_kmh": max(pdat_speeds) if pdat_speeds else 0.0,
        "radc_max_magnitude": max(radc_mags) if radc_mags else 0.0,
        "pdat_max_magnitude": max(pdat_mags) if pdat_mags else 0.0,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src uv run --no-project python -m pytest tests/test_kld7_radc_lib.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```
feat: add RADC vs PDAT comparison utility
```

---

### Task 7: Capture Script

**Files:**
- Create: `scripts/capture_kld7_radc.py`

- [ ] **Step 1: Create the capture script**

```python
#!/usr/bin/env python3
"""Capture K-LD7 raw ADC (RADC) data alongside PDAT/TDAT for offline analysis.

Usage:
    ./scripts/capture_kld7_radc.py --port /dev/ttyUSB0 --duration 60
    ./scripts/capture_kld7_radc.py --port /dev/ttyUSB0 --baud 3000000 --duration 30

Output:
    .pkl file with RADC + PDAT + TDAT per frame, plus metadata.
"""

from __future__ import annotations

import argparse
import pickle
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from kld7 import KLD7, FrameCode, KLD7Exception
except ImportError:
    print("kld7 package not installed. Run: pip install kld7")
    sys.exit(1)


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


def main():
    parser = argparse.ArgumentParser(
        description="Capture K-LD7 raw ADC data for offline signal processing.",
    )
    parser.add_argument("--port", default=None, help="Serial port (auto-detect if not set)")
    parser.add_argument("--baud", type=int, default=3000000, help="Baud rate (default: 3000000)")
    parser.add_argument("--duration", type=int, default=60, help="Capture duration in seconds")
    parser.add_argument("--orientation", default="vertical", choices=["vertical", "horizontal"])
    parser.add_argument("--output", default=None, help="Output .pkl path")
    parser.add_argument("--club", default=None, help="Club label for metadata")
    parser.add_argument("--shots", type=int, default=None, help="Expected shot count")
    parser.add_argument("--notes", default=None, help="Freeform notes")
    args = parser.parse_args()

    # Auto-detect port
    port = args.port
    if port is None:
        from serial.tools.list_ports import comports
        for p in comports():
            desc = (p.description or "").lower()
            mfg = (p.manufacturer or "").lower()
            if any(kw in desc for kw in ["ftdi", "cp210", "usb-serial", "uart"]):
                port = p.device
                break
            if any(kw in mfg for kw in ["ftdi", "silicon labs"]):
                port = p.device
                break
        if port is None:
            print("No K-LD7 detected. Use --port to specify.")
            sys.exit(1)

    # Output path
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
    else:
        output_dir = Path(__file__).resolve().parent.parent / "session_logs"
        output_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = f"-{args.club}" if args.club else ""
        output_path = output_dir / f"kld7_radc_{timestamp}{suffix}.pkl"

    print("=" * 60)
    print("  K-LD7 Raw ADC Capture")
    print("=" * 60)
    print(f"  Port:        {port}")
    print(f"  Baud:        {args.baud}")
    print(f"  Duration:    {args.duration}s")
    print(f"  Orientation: {args.orientation}")
    print(f"  Output:      {output_path}")
    print()

    # Connect
    print("Connecting...")
    try:
        radar = KLD7(port, baudrate=args.baud)
    except (KLD7Exception, Exception) as e:
        print(f"Error: {e}")
        sys.exit(1)
    print(f"  Connected: {radar}")

    # Configure
    print("Configuring for golf...")
    configure_for_golf(radar)
    all_params = read_all_params(radar)
    print()

    # Stream RADC + PDAT + TDAT
    frame_codes = FrameCode.RADC | FrameCode.PDAT | FrameCode.TDAT

    metadata = {
        "module": "K-LD7",
        "mode": "RADC",
        "port": port,
        "baud_rate": args.baud,
        "orientation": args.orientation,
        "capture_start": datetime.now().isoformat(),
        "params": all_params,
        "club": args.club,
        "expected_shots": args.shots,
        "notes": args.notes,
    }

    frames = []
    frame_count = 0
    radc_count = 0
    pdat_detection_count = 0
    start_time = time.time()

    print("-" * 60)
    print(f"Streaming RADC + PDAT + TDAT for {args.duration}s (Ctrl+C to stop)")
    print("-" * 60)

    try:
        current_frame = {"timestamp": time.time()}
        seen_in_frame = set()

        for code, payload in radar.stream_frames(frame_codes, max_count=-1):
            if time.time() - start_time >= args.duration:
                break

            if code in seen_in_frame:
                frames.append(current_frame)
                current_frame = {"timestamp": time.time()}
                seen_in_frame = set()

            seen_in_frame.add(code)

            if code == "RADC":
                current_frame["radc"] = payload  # raw bytes, parse offline
                radc_count += 1

            elif code == "TDAT":
                current_frame["tdat"] = target_to_dict(payload)
                frame_count += 1
                elapsed = time.time() - start_time
                fps = frame_count / elapsed if elapsed > 0 else 0
                n_pdat = len(current_frame.get("pdat", []))
                has_radc = "Y" if "radc" in current_frame else "N"
                print(
                    f"\r  Frames: {frame_count}  RADC: {radc_count}  "
                    f"PDAT targets: {pdat_detection_count}  "
                    f"FPS: {fps:.1f}  Elapsed: {elapsed:.0f}s",
                    end="",
                    flush=True,
                )

            elif code == "PDAT":
                current_frame["pdat"] = [target_to_dict(t) for t in payload] if payload else []
                pdat_detection_count += sum(1 for _ in (payload or []))

        if seen_in_frame:
            frames.append(current_frame)

    except KeyboardInterrupt:
        pass
    except KLD7Exception as e:
        print(f"\nK-LD7 error: {e}")
    finally:
        try:
            radar.close()
        except Exception:
            pass

    metadata["capture_end"] = datetime.now().isoformat()
    metadata["total_frames"] = len(frames)
    metadata["radc_frames"] = radc_count
    metadata["pdat_detection_count"] = pdat_detection_count

    print()
    print()
    print("=" * 60)
    print(f"  Captured {len(frames)} frames ({radc_count} with RADC)")
    print(f"  PDAT detections: {pdat_detection_count}")
    print(f"  Saving to {output_path}")

    with open(output_path, "wb") as f:
        pickle.dump({"metadata": metadata, "frames": frames}, f)

    print(f"  Done ({output_path.stat().st_size / 1024:.0f} KB)")
    print("=" * 60)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Make it executable and verify it compiles**

Run:
```bash
chmod +x scripts/capture_kld7_radc.py
python -m py_compile scripts/capture_kld7_radc.py
```
Expected: no output (clean compile)

- [ ] **Step 3: Commit**

```
feat: add K-LD7 RADC capture script (3 Mbaud)
```

---

### Task 8: Analysis Script

**Files:**
- Create: `scripts/analyze_kld7_radc.py`

- [ ] **Step 1: Create the analysis script**

```python
#!/usr/bin/env python3
"""Analyze K-LD7 RADC captures — FFT, CFAR detection, comparison with PDAT.

Usage:
    uv run --no-project --with numpy --with matplotlib python scripts/analyze_kld7_radc.py capture.pkl
    uv run --no-project --with numpy --with matplotlib python scripts/analyze_kld7_radc.py capture.pkl --shot-windows
    uv run --no-project --with numpy --with matplotlib python scripts/analyze_kld7_radc.py capture.pkl --csv
"""

from __future__ import annotations

import argparse
import csv
import pickle
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from kld7_radc_lib import (
    ADC_MIDPOINT,
    process_radc_frame,
    compare_radc_vs_pdat,
    compute_spectrum,
    to_complex_iq,
    parse_radc_payload,
)


def load_capture(path: Path) -> dict:
    with open(path, "rb") as f:
        return pickle.load(f)


def find_active_windows(frames: list[dict], min_pdat_targets: int = 3, context_frames: int = 10):
    """Find frame ranges with activity (likely swings)."""
    windows = []
    in_window = False
    start = 0

    for i, frame in enumerate(frames):
        n_pdat = len(frame.get("pdat") or [])
        if n_pdat >= min_pdat_targets:
            if not in_window:
                start = max(0, i - context_frames)
                in_window = True
        else:
            if in_window:
                end = min(len(frames), i + context_frames)
                windows.append((start, end))
                in_window = False

    if in_window:
        windows.append((start, len(frames)))

    # Merge overlapping windows
    merged = []
    for window in windows:
        if merged and window[0] <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], window[1]))
        else:
            merged.append(window)

    return merged


def plot_spectrogram(frames: list[dict], title: str, output_path: Path, fft_size: int = 2048):
    """Plot a time-frequency spectrogram from RADC frames."""
    spectra = []
    timestamps = []

    for frame in frames:
        radc = frame.get("radc")
        if radc is None:
            continue
        if isinstance(radc, bytes):
            channels = parse_radc_payload(radc)
        else:
            channels = radc
        iq = to_complex_iq(channels["f1a_i"], channels["f1a_q"])
        spec = compute_spectrum(iq, fft_size=fft_size)
        spectra.append(10.0 * np.log10(spec + 1e-10))
        timestamps.append(frame["timestamp"])

    if not spectra:
        print(f"  No RADC frames for spectrogram: {title}")
        return

    spectrogram = np.array(spectra).T  # (fft_size, n_frames)
    t0 = timestamps[0]
    time_axis = np.array(timestamps) - t0

    fig, ax = plt.subplots(figsize=(14, 6))
    ax.imshow(
        spectrogram[: fft_size // 2, :],
        aspect="auto",
        origin="lower",
        extent=[time_axis[0], time_axis[-1], 0, fft_size // 2],
        cmap="viridis",
        vmin=np.percentile(spectrogram, 20),
        vmax=np.percentile(spectrogram, 99),
    )
    ax.set_title(title)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("FFT bin (0 = DC, higher = faster)")
    fig.colorbar(ax.images[0], label="Power (dB)")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def plot_detection_timeline(
    frames: list[dict],
    title: str,
    output_path: Path,
    fft_size: int = 2048,
    max_speed_kmh: float = 100.0,
    cfar_threshold: float = 8.0,
):
    """Plot RADC detections vs PDAT detections over time."""
    radc_times = []
    radc_velocities = []
    radc_snrs = []
    pdat_times = []
    pdat_speeds = []
    pdat_mags = []

    t0 = frames[0]["timestamp"] if frames else 0

    for i, frame in enumerate(frames):
        ts = frame["timestamp"] - t0
        detections = process_radc_frame(
            frame, frame_index=i, fft_size=fft_size, max_speed_kmh=max_speed_kmh,
            cfar_threshold=cfar_threshold,
        )
        for d in detections:
            radc_times.append(ts)
            radc_velocities.append(d.velocity_kmh)
            radc_snrs.append(d.snr_db)

        for p in frame.get("pdat") or []:
            if p:
                pdat_times.append(ts)
                pdat_speeds.append(p.get("speed", 0))
                pdat_mags.append(p.get("magnitude", 0))

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True)

    if radc_times:
        sc = ax1.scatter(radc_times, radc_velocities, c=radc_snrs, s=10, cmap="hot", alpha=0.7)
        fig.colorbar(sc, ax=ax1, label="SNR (dB)")
    ax1.set_ylabel("RADC velocity (km/h)")
    ax1.set_title(f"{title} — RADC detections (our FFT + CFAR)")
    ax1.grid(True, alpha=0.25)

    if pdat_times:
        sc2 = ax2.scatter(pdat_times, pdat_speeds, c=pdat_mags, s=10, cmap="viridis", alpha=0.7)
        fig.colorbar(sc2, ax=ax2, label="Magnitude")
    ax2.set_ylabel("PDAT speed (km/h)")
    ax2.set_xlabel("Time (s)")
    ax2.set_title("Module PDAT detections (built-in detector)")
    ax2.grid(True, alpha=0.25)

    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def write_comparison_csv(
    frames: list[dict],
    output_path: Path,
    fft_size: int = 2048,
    max_speed_kmh: float = 100.0,
    cfar_threshold: float = 8.0,
):
    """Write per-frame RADC vs PDAT comparison to CSV."""
    t0 = frames[0]["timestamp"] if frames else 0

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "frame", "time_s", "radc_count", "pdat_count",
            "radc_max_velocity_kmh", "pdat_max_speed_kmh",
            "radc_max_magnitude", "pdat_max_magnitude",
        ])
        writer.writeheader()

        for i, frame in enumerate(frames):
            detections = process_radc_frame(
                frame, frame_index=i, fft_size=fft_size, max_speed_kmh=max_speed_kmh,
                cfar_threshold=cfar_threshold,
            )
            comparison = compare_radc_vs_pdat(detections, frame.get("pdat") or [])
            comparison["frame"] = i
            comparison["time_s"] = round(frame["timestamp"] - t0, 3)
            writer.writerow(comparison)


def main():
    parser = argparse.ArgumentParser(description="Analyze K-LD7 RADC captures.")
    parser.add_argument("capture", type=Path, help="Path to RADC .pkl capture file")
    parser.add_argument("--shot-windows", action="store_true", help="Only plot active windows")
    parser.add_argument("--csv", action="store_true", help="Export per-frame comparison CSV")
    parser.add_argument("--fft-size", type=int, default=2048, help="FFT size (default: 2048)")
    parser.add_argument("--cfar-threshold", type=float, default=8.0, help="CFAR threshold factor")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory for plots")
    args = parser.parse_args()

    data = load_capture(args.capture)
    meta = data["metadata"]
    frames = data["frames"]

    output_dir = args.output_dir or args.capture.parent / f"radc_analysis_{args.capture.stem}"
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(f"  K-LD7 RADC Analysis: {args.capture.name}")
    print("=" * 60)
    print(f"  Frames:     {len(frames)}")
    radc_frames = sum(1 for f in frames if f.get("radc"))
    print(f"  RADC:       {radc_frames} frames")
    print(f"  FFT size:   {args.fft_size}")
    print(f"  CFAR:       {args.cfar_threshold}x threshold")
    print(f"  Output:     {output_dir}")
    print()

    if args.shot_windows:
        windows = find_active_windows(frames)
        print(f"  Active windows: {len(windows)}")
        for i, (start, end) in enumerate(windows):
            window_frames = frames[start:end]
            t0 = window_frames[0]["timestamp"]
            duration = window_frames[-1]["timestamp"] - t0
            print(f"    Window {i+1}: frames {start}-{end} ({duration:.1f}s)")

            plot_spectrogram(
                window_frames,
                f"Window {i+1} (frames {start}-{end})",
                output_dir / f"spectrogram_window_{i+1:02d}.png",
                fft_size=args.fft_size,
            )
            plot_detection_timeline(
                window_frames,
                f"Window {i+1}",
                output_dir / f"detections_window_{i+1:02d}.png",
                fft_size=args.fft_size,
                cfar_threshold=args.cfar_threshold,
            )
    else:
        plot_spectrogram(
            frames,
            f"Full capture: {args.capture.name}",
            output_dir / "spectrogram_full.png",
            fft_size=args.fft_size,
        )
        plot_detection_timeline(
            frames,
            args.capture.name,
            output_dir / "detections_full.png",
            fft_size=args.fft_size,
            cfar_threshold=args.cfar_threshold,
        )

    if args.csv:
        csv_path = output_dir / "radc_vs_pdat.csv"
        write_comparison_csv(
            frames, csv_path,
            fft_size=args.fft_size,
            cfar_threshold=args.cfar_threshold,
        )
        print(f"  CSV: {csv_path}")

    # Summary stats
    total_radc_detections = 0
    total_pdat_detections = 0
    for i, frame in enumerate(frames):
        dets = process_radc_frame(
            frame, frame_index=i, fft_size=args.fft_size,
            cfar_threshold=args.cfar_threshold,
        )
        total_radc_detections += len(dets)
        total_pdat_detections += len(frame.get("pdat") or [])

    print()
    print(f"  RADC detections (our FFT+CFAR): {total_radc_detections}")
    print(f"  PDAT detections (module):       {total_pdat_detections}")
    print(f"  Ratio: {total_radc_detections / max(total_pdat_detections, 1):.1f}x")
    print()
    print(f"  Plots saved to {output_dir}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Make executable and verify compile**

Run:
```bash
chmod +x scripts/analyze_kld7_radc.py
python -m py_compile scripts/analyze_kld7_radc.py
```
Expected: no output

- [ ] **Step 3: Create wrapper script**

```bash
# scripts/analyze-radc.sh
#!/usr/bin/env bash
# Analyze a K-LD7 RADC capture.
# Usage: ./scripts/analyze-radc.sh capture.pkl [--shot-windows] [--csv]

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <capture.pkl> [--shot-windows] [--csv]"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

uv run --no-project --with numpy --with matplotlib \
    python "$SCRIPT_DIR/analyze_kld7_radc.py" "$@"
```

Run: `chmod +x scripts/analyze-radc.sh`

- [ ] **Step 4: Commit**

```
feat: add K-LD7 RADC analysis script with spectrogram and detection plots
```

---

### Task 9: Run All Tests

- [ ] **Step 1: Run the full RADC test suite**

Run: `PYTHONPATH=src uv run --no-project --with numpy python -m pytest tests/test_kld7_radc_lib.py -v`
Expected: all passed

- [ ] **Step 2: Run the existing test suite to verify no regressions**

Run: `PYTHONPATH=src uv run --no-project python -m pytest tests/ -v`
Expected: all existing tests still pass

- [ ] **Step 3: Commit**

```
chore: verify RADC processing tests pass alongside existing suite
```

---

Plan complete and saved to `docs/plans/2026-04-05-kld7-radc-processing-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session, batch execution with checkpoints

Which approach?
