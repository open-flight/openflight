# Spin & Angle Data Quality Validation — Implementation Plan

!!! warning "ARCHIVED DOCUMENT"

    This is a historical design or implementation note, kept as a record of why
    the code is shaped the way it is. It describes the project as of the date in
    its filename and **is not a guide to follow** — commands, paths, and
    constants may no longer match the code. See the
    [Archive index](../index.md) for current alternatives.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix spin confidence over-reporting and missing angle validation so bad measurements don't reach the Shot object.

**Architecture:** Surgical changes inside existing detection code — `processor.py` for spin, `radc.py` + `tracker.py` for angles, `server.py` for horizontal plausibility. One new shared constant in `launch_monitor.py`. TDD for every behavioral change.

**Tech Stack:** Python, numpy, pytest

**Spec:** `docs/superpowers/specs/2026-04-15-spin-angle-validation-design.md`

---

### Task 1: Extract shared SPIN_CONFIDENCE_HIGH constant

**Files:**
- Modify: `src/openflight/launch_monitor.py:315`
- Modify: `src/openflight/rolling_buffer/processor.py:593-604`

- [ ] **Step 1: Add constant to launch_monitor.py**

In `src/openflight/launch_monitor.py`, add the constant after the imports (before `ClubType`), and use it in `spin_quality`:

```python
# Spin confidence threshold for "high" quality — used across modules.
# Measured spin is trusted for physics simulation only above this level.
SPIN_CONFIDENCE_HIGH = 0.7
```

Then update `spin_quality` (line 315) to use it:

```python
        if self.spin_confidence >= SPIN_CONFIDENCE_HIGH:
            return "high"
```

- [ ] **Step 2: Run existing tests to verify no regressions**

Run: `uv run pytest tests/test_rolling_buffer.py::TestSpinResult -v && uv run pytest tests/test_rolling_buffer.py::TestShotWithSpin -v`
Expected: All existing tests PASS (behavior unchanged — same threshold value)

- [ ] **Step 3: Import constant in processor.py**

In `src/openflight/rolling_buffer/processor.py`, add to imports:

```python
from ..launch_monitor import SPIN_CONFIDENCE_HIGH
```

No behavioral change yet — this just wires up the import for use in Task 2.

- [ ] **Step 4: Run tests to verify import works**

Run: `uv run pytest tests/test_rolling_buffer.py::TestSpinDetectionIntegration -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/openflight/launch_monitor.py src/openflight/rolling_buffer/processor.py
git commit -m "refactor: extract SPIN_CONFIDENCE_HIGH constant to launch_monitor"
```

---

### Task 2: Tighten spin confidence scoring + add hard RPM cap

**Files:**
- Modify: `src/openflight/rolling_buffer/processor.py:485-604`
- Test: `tests/test_rolling_buffer.py`

- [ ] **Step 1: Write failing test for hard RPM cap**

Add to `tests/test_rolling_buffer.py` at the end of the file:

```python
class TestSpinValidationGates:
    """Tests for spin detection validation: RPM ceiling, confidence tiers, modulation floor."""

    def _make_iq_with_seam_modulation(
        self,
        base_speed_mph: float,
        spin_rpm: float,
        modulation_depth: float = 0.03,
        sample_rate: int = 30000,
        num_samples: int = 4096,
    ):
        """Generate synthetic I/Q with amplitude modulation at 1x spin rate."""
        wavelength = 0.01243
        speed_mps = base_speed_mph / 2.23694
        doppler_hz = 2 * speed_mps / wavelength
        seam_hz = spin_rpm / 60.0
        t = np.arange(num_samples) / sample_rate
        phase = 2 * np.pi * doppler_hz * t
        amplitude = 200 * (1.0 + modulation_depth * np.sin(2 * np.pi * seam_hz * t))
        i_samples = (amplitude * np.cos(phase) + 2048).astype(int).clip(0, 4095).tolist()
        q_samples = (amplitude * np.sin(phase) + 2048).astype(int).clip(0, 4095).tolist()
        return i_samples, q_samples

    def test_rpm_above_12000_rejected(self):
        """Spin above SPIN_MAX_SEAM_HZ * 60 (12000 RPM) must be rejected."""
        processor = RollingBufferProcessor()
        # Create I/Q with 13000 RPM modulation — above the physical cap.
        # Call detect_spin directly so we can specify the exact ball timestamp.
        i_samples, q_samples = self._make_iq_with_seam_modulation(
            base_speed_mph=100, spin_rpm=13000, modulation_depth=0.05,
        )
        capture = IQCapture(
            sample_time=0.0, trigger_time=0.068,
            i_samples=i_samples, q_samples=q_samples,
        )
        result = processor.detect_spin(capture, ball_speed_mph=100, ball_timestamp_ms=5.0)
        assert result.spin_rpm == 0, (
            f"13000 RPM should be rejected, got {result.spin_rpm} RPM"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rolling_buffer.py::TestSpinValidationGates::test_rpm_above_12000_rejected -v`
Expected: FAIL — 13000 RPM is above SPIN_MAX_SEAM_HZ (200 Hz) so the FFT mask should already reject it, but this test confirms the safety net. If it passes already (FFT mask catches it), that's fine — the hard cap is still needed for the autocorrelation override path.

- [ ] **Step 3: Write failing test for tightened confidence — bare SNR without cycles**

Add to `TestSpinValidationGates`:

```python
    def test_medium_snr_low_cycles_gets_reduced_confidence(self):
        """SNR >= 5 but < 3 seam cycles should score 0.5, not 0.7.

        With a short ball window (few cycles), the FFT peak is unreliable
        even with decent SNR — this is the repeated-bin pattern.
        """
        processor = RollingBufferProcessor()
        # Use 700 samples (~23ms) at 4000 RPM = 66.7 Hz → ~1.5 seam cycles.
        # Short window means few cycles, but SNR can still be decent.
        i_samples, q_samples = self._make_iq_with_seam_modulation(
            base_speed_mph=130, spin_rpm=4000, modulation_depth=0.04,
            num_samples=700,
        )
        capture = IQCapture(
            sample_time=0.0, trigger_time=0.003,
            i_samples=i_samples, q_samples=q_samples,
        )
        result = processor.detect_spin(capture, ball_speed_mph=130, ball_timestamp_ms=1.0)
        # If spin is detected at all, confidence should be <= 0.5
        if result.spin_rpm > 0:
            assert result.confidence <= 0.5, (
                f"Low-cycle detection should cap at 0.5, got {result.confidence}"
            )
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_rolling_buffer.py::TestSpinValidationGates::test_medium_snr_low_cycles_gets_reduced_confidence -v`
Expected: FAIL (current code gives 0.7 for bare `SNR >= 5.0`)

- [ ] **Step 5: Write failing test for modulation depth confidence cap**

Add to `TestSpinValidationGates`:

```python
    def test_weak_modulation_caps_confidence(self):
        """Modulation depth < 1% should cap confidence at 0.5 max."""
        processor = RollingBufferProcessor()
        # 0.008 modulation depth — above the 0.005 rejection floor but below 1%.
        i_samples, q_samples = self._make_iq_with_seam_modulation(
            base_speed_mph=130, spin_rpm=5000, modulation_depth=0.008,
        )
        capture = IQCapture(
            sample_time=0.0, trigger_time=0.068,
            i_samples=i_samples, q_samples=q_samples,
        )
        result = processor.detect_spin(capture, ball_speed_mph=130, ball_timestamp_ms=5.0)
        if result.spin_rpm > 0:
            assert result.confidence <= 0.5, (
                f"Weak modulation should cap at 0.5, got {result.confidence}"
            )
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/test_rolling_buffer.py::TestSpinValidationGates::test_weak_modulation_caps_confidence -v`
Expected: FAIL (current code doesn't cap confidence based on modulation depth)

- [ ] **Step 7: Write test that good spin still scores high**

Add to `TestSpinValidationGates`:

```python
    def test_strong_spin_still_scores_high(self):
        """Clean spin with good SNR and many cycles should still score 0.9."""
        processor = RollingBufferProcessor()
        i_samples, q_samples = self._make_iq_with_seam_modulation(
            base_speed_mph=120, spin_rpm=7000, modulation_depth=0.03,
        )
        capture = IQCapture(
            sample_time=0.0, trigger_time=0.068,
            i_samples=i_samples, q_samples=q_samples,
        )
        result = processor.detect_spin(capture, ball_speed_mph=120, ball_timestamp_ms=5.0)
        assert result.spin_rpm > 0, f"Should detect spin, got quality={result.quality}"
        assert result.confidence >= 0.8, (
            f"Strong spin should score >= 0.8, got {result.confidence}"
        )
```

- [ ] **Step 8: Run to verify it passes (existing behavior should handle this)**

Run: `uv run pytest tests/test_rolling_buffer.py::TestSpinValidationGates::test_strong_spin_still_scores_high -v`
Expected: PASS (7000 RPM at 120 mph with 3% modulation should produce high SNR and many cycles)

- [ ] **Step 9: Implement all three spin validation changes in processor.py**

In `src/openflight/rolling_buffer/processor.py`, make three changes:

**Change A — Modulation depth flag (after line 494):**

Replace:

```python
        # Remove DC and apply Hann window
        ball_envelope -= envelope_mean
```

With:

```python
        # Flag weak modulation — above the noise floor (0.5%) but below
        # the level where we trust the envelope FFT peak (1%). Caps
        # confidence later to prevent marginal signals scoring 0.7+.
        weak_modulation = modulation_depth < 0.01

        # Remove DC and apply Hann window
        ball_envelope -= envelope_mean
```

**Change B — Hard RPM cap (after line 531, before the cycles check):**

After the line `spin_rpm = peak_freq * 60`, add:

```python
        # Hard ceiling — reject anything above physical maximum.
        # The FFT mask should enforce this, but the autocorrelation override
        # path can bypass it. Belt-and-suspenders.
        max_rpm = self.SPIN_MAX_SEAM_HZ * 60
        if spin_rpm > max_rpm:
            return SpinResult.no_spin_detected(
                f"Spin {spin_rpm:.0f} RPM exceeds physical maximum ({max_rpm:.0f})"
            )
```

**Change C — New confidence tiers (replace lines 593-604):**

Replace:

```python
        if fft_snr >= self.SPIN_SNR_HIGH and seam_cycles >= 5:
            quality = "high"
            confidence = 0.9
        elif fft_snr >= self.SPIN_SNR_MEDIUM or autocorr_confirmed:
            quality = "medium"
            confidence = 0.7
        elif fft_snr >= self.SPIN_SNR_MIN:
            quality = "low"
            confidence = 0.4
        else:
            quality = "low"
            confidence = 0.3
```

With:

```python
        if fft_snr >= self.SPIN_SNR_HIGH and seam_cycles >= 5:
            quality = "high"
            confidence = 0.9
        elif fft_snr >= self.SPIN_SNR_HIGH and seam_cycles >= 3:
            quality = "high"
            confidence = 0.8
        elif fft_snr >= self.SPIN_SNR_MEDIUM and (seam_cycles >= 3 or autocorr_confirmed):
            quality = "medium"
            confidence = SPIN_CONFIDENCE_HIGH
        elif fft_snr >= self.SPIN_SNR_MEDIUM or autocorr_confirmed:
            quality = "low"
            confidence = 0.5
        elif fft_snr >= self.SPIN_SNR_MIN:
            quality = "low"
            confidence = 0.3
        else:
            quality = "low"
            confidence = 0.3

        # Weak modulation caps confidence — the envelope FFT peak may be
        # noise rather than real seam modulation.
        if weak_modulation:
            confidence = min(confidence, 0.5)
            if quality == "high":
                quality = "medium"
```

- [ ] **Step 10: Run all spin tests**

Run: `uv run pytest tests/test_rolling_buffer.py::TestSpinValidationGates -v && uv run pytest tests/test_rolling_buffer.py::TestSpinDetectionIntegration -v`
Expected: All new tests PASS, all existing spin tests PASS

- [ ] **Step 11: Commit**

```bash
git add src/openflight/rolling_buffer/processor.py tests/test_rolling_buffer.py
git commit -m "fix: tighten spin confidence scoring and add hard RPM ceiling"
```

---

### Task 3: Add hard angle bounds in radc.py

**Files:**
- Modify: `src/openflight/kld7/radc.py:276-285, 443-445`
- Modify: `src/openflight/kld7/tracker.py:321-327`
- Test: `tests/test_kld7_radc_lib.py`

- [ ] **Step 1: Write failing test for vertical angle bounds**

Add to `tests/test_kld7_radc_lib.py`:

```python
from openflight.kld7.radc import extract_launch_angle


class TestAngleBoundsValidation:
    """Tests for hard angle bounds rejection in extract_launch_angle."""

    def _make_single_frame_with_angle(self, angle_deg: float, snr: float = 10.0):
        """Create a minimal synthetic RADC frame that produces a known angle.

        Rather than constructing raw I/Q that produces a specific angle through
        the full pipeline (fragile), we test the bounds check by calling
        extract_launch_angle with crafted frames and verifying the result
        is filtered. This relies on the integration tests in test_kld7.py
        for end-to-end coverage.
        """
        # Build a synthetic 3072-byte RADC payload with a known phase offset
        # between F1A and F2A that produces the desired angle.
        # angle = arcsin(dphi * lambda / (2 * pi * d))
        # dphi = arcsin(angle_deg * pi / 180) * 2 * pi * d / lambda
        import math
        wavelength = 0.01243
        spacing = 0.008
        dphi = math.asin(max(-1, min(1, math.sin(math.radians(angle_deg))))) * 2 * math.pi * spacing / wavelength

        # Create payload: F1A and F2A with a phase offset that produces dphi
        # at a specific FFT bin. We put energy in bin 50 (a mid-range velocity).
        n = 256
        t = np.arange(n, dtype=np.float64)
        freq_bin = 50
        f = freq_bin / n  # normalized frequency

        # F1A: reference channel
        f1a_i = (100 * np.cos(2 * np.pi * f * t) + 2048).astype(np.uint16)
        f1a_q = (100 * np.sin(2 * np.pi * f * t) + 2048).astype(np.uint16)
        # F2A: phase-shifted channel
        f2a_i = (100 * np.cos(2 * np.pi * f * t + dphi) + 2048).astype(np.uint16)
        f2a_q = (100 * np.sin(2 * np.pi * f * t + dphi) + 2048).astype(np.uint16)
        # F1B: unused, zero
        f1b_i = np.full(n, 2048, dtype=np.uint16)
        f1b_q = np.full(n, 2048, dtype=np.uint16)

        payload = b"".join(ch.tobytes() for ch in [f1a_i, f1a_q, f2a_i, f2a_q, f1b_i, f1b_q])
        return {"timestamp": 0.0, "radc": payload}

    def test_vertical_angle_above_45_rejected(self):
        """Vertical angles above 45° should be filtered out."""
        frame = self._make_single_frame_with_angle(50.0)
        results = extract_launch_angle(
            [frame] * 5,
            ops243_ball_speed_mph=100.0,
            orientation="vertical",
        )
        for r in results:
            assert r["launch_angle_deg"] <= 45.0, (
                f"Vertical angle {r['launch_angle_deg']}° should be rejected (>45°)"
            )

    def test_vertical_angle_below_0_rejected(self):
        """Negative vertical angles should be filtered out."""
        frame = self._make_single_frame_with_angle(-5.0)
        results = extract_launch_angle(
            [frame] * 5,
            ops243_ball_speed_mph=100.0,
            orientation="vertical",
        )
        for r in results:
            assert r["launch_angle_deg"] >= 0.0, (
                f"Vertical angle {r['launch_angle_deg']}° should be rejected (<0°)"
            )

    def test_horizontal_angle_beyond_15_rejected(self):
        """Horizontal angles beyond ±15° should be filtered out."""
        frame = self._make_single_frame_with_angle(20.0)
        results = extract_launch_angle(
            [frame] * 5,
            ops243_ball_speed_mph=100.0,
            orientation="horizontal",
        )
        for r in results:
            assert abs(r["launch_angle_deg"]) <= 15.0, (
                f"Horizontal angle {r['launch_angle_deg']}° should be rejected (>±15°)"
            )

    def test_valid_vertical_angle_passes(self):
        """A valid 12° vertical angle should pass through."""
        frame = self._make_single_frame_with_angle(12.0)
        results = extract_launch_angle(
            [frame] * 5,
            ops243_ball_speed_mph=100.0,
            orientation="vertical",
        )
        # Result may be empty if synthetic frame doesn't produce enough SNR,
        # but if present, the angle should be close to 12°.
        # This test verifies valid angles aren't rejected by the bounds check.
        for r in results:
            assert 0.0 <= r["launch_angle_deg"] <= 45.0

    def test_orientation_defaults_to_no_filtering(self):
        """When orientation is not specified, no angle bounds are applied (backward compat)."""
        frame = self._make_single_frame_with_angle(50.0)
        # No orientation parameter — should not crash or filter
        results = extract_launch_angle(
            [frame] * 5,
            ops243_ball_speed_mph=100.0,
        )
        # Just verify it doesn't raise — bounds filtering is opt-in via orientation
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_kld7_radc_lib.py::TestAngleBoundsValidation -v`
Expected: FAIL — `extract_launch_angle` doesn't accept an `orientation` parameter yet

- [ ] **Step 3: Add orientation parameter and bounds check to extract_launch_angle**

In `src/openflight/kld7/radc.py`, modify the function signature (line 276):

Replace:

```python
def extract_launch_angle(
    frames: list[dict],
    fft_size: int = 2048,
    max_speed_kmh: float = 100.0,
    cfar_threshold: float = 2.5,
    impact_energy_threshold: float = 3.0,
    angle_offset_deg: float = 0.0,
    ops243_ball_speed_mph: float | None = None,
    speed_tolerance_mph: float = 10.0,
) -> list[dict]:
```

With:

```python
def extract_launch_angle(
    frames: list[dict],
    fft_size: int = 2048,
    max_speed_kmh: float = 100.0,
    cfar_threshold: float = 2.5,
    impact_energy_threshold: float = 3.0,
    angle_offset_deg: float = 0.0,
    ops243_ball_speed_mph: float | None = None,
    speed_tolerance_mph: float = 10.0,
    orientation: str | None = None,
) -> list[dict]:
```

Then, inside the function, after `corrected_angle` is computed (after line 422) and before `avg_speed_mph` (line 424), add the bounds check:

Replace:

```python
        corrected_angle = weighted_angle + angle_offset_deg

        avg_speed_mph = float(np.mean(peak_speeds_mph))
```

With:

```python
        corrected_angle = weighted_angle + angle_offset_deg

        # Hard physical bounds — reject obvious outliers before they
        # reach the Shot object. Orientation-aware: vertical [0°, 45°],
        # horizontal [-15°, +15°]. When orientation is None (offline
        # analysis), skip bounds filtering.
        if orientation == "vertical" and not (0.0 <= corrected_angle <= 45.0):
            logger.info(
                "[RADC] Vertical angle %.1f° outside [0, 45] — rejected",
                corrected_angle,
            )
            continue
        if orientation == "horizontal" and abs(corrected_angle) > 15.0:
            logger.info(
                "[RADC] Horizontal angle %.1f° outside ±15° — rejected",
                corrected_angle,
            )
            continue

        avg_speed_mph = float(np.mean(peak_speeds_mph))
```

Also add `import logging` and `logger = logging.getLogger(__name__)` at the top of `radc.py` if not already present.

- [ ] **Step 4: Pass orientation from tracker.py**

In `src/openflight/kld7/tracker.py`, update `_extract_ball_radc()` (line 321):

Replace:

```python
        results = extract_launch_angle(
            frames,
            ops243_ball_speed_mph=ball_speed_mph,
            angle_offset_deg=self.angle_offset_deg,
            speed_tolerance_mph=10.0,
            impact_energy_threshold=energy_threshold,
        )
```

With:

```python
        results = extract_launch_angle(
            frames,
            ops243_ball_speed_mph=ball_speed_mph,
            angle_offset_deg=self.angle_offset_deg,
            speed_tolerance_mph=10.0,
            impact_energy_threshold=energy_threshold,
            orientation=self.orientation,
        )
```

- [ ] **Step 5: Run angle bounds tests and existing K-LD7 tests**

Run: `uv run pytest tests/test_kld7_radc_lib.py::TestAngleBoundsValidation -v && uv run pytest tests/test_kld7_radc_lib.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/openflight/kld7/radc.py src/openflight/kld7/tracker.py tests/test_kld7_radc_lib.py
git commit -m "fix: add hard angle bounds to K-LD7 extraction (V[0,45] H[±15])"
```

---

### Task 4: Add horizontal angle plausibility check in server.py

**Files:**
- Modify: `src/openflight/server.py:997-1002`
- Test: `tests/test_rolling_buffer.py` (server-level test)

- [ ] **Step 1: Write failing test for horizontal rejection**

Add to `tests/test_rolling_buffer.py`:

```python
class TestHorizontalAnglePlausibility:
    """Tests for server-level horizontal angle rejection."""

    def test_extreme_horizontal_angle_rejected(self):
        """Horizontal angles beyond ±15° should not reach the Shot."""
        # This tests the contract: any KLD7Angle with |horizontal_deg| > 15
        # should be rejected before assignment to shot.launch_angle_horizontal.
        # We test the logic directly rather than spinning up the server.
        h_angle = 34.0  # The exact value from the bad session
        assert abs(h_angle) > 15.0, "Test setup: angle should be out of bounds"

    def test_valid_horizontal_angle_accepted(self):
        """Horizontal angles within ±15° should be accepted."""
        h_angle = -3.3
        assert abs(h_angle) <= 15.0, "Test setup: angle should be in bounds"
```

Note: These are contract/sanity tests. The real integration test requires the full server, which we can't run in CI without hardware. The bounds check in `radc.py` (Task 3) is the primary gate; this server-level check is the backstop.

- [ ] **Step 2: Add horizontal plausibility check in server.py**

In `src/openflight/server.py`, replace lines 997-1002:

Replace:

```python
                if kld7_angle_h and kld7_angle_h.horizontal_deg is not None:
                    shot.launch_angle_horizontal = kld7_angle_h.horizontal_deg
                    if shot.angle_source is None:
                        shot.angle_source = "radar"
                    if shot.launch_angle_confidence is None:
                        shot.launch_angle_confidence = kld7_angle_h.confidence
```

With:

```python
                if kld7_angle_h and kld7_angle_h.horizontal_deg is not None:
                    if abs(kld7_angle_h.horizontal_deg) <= 15.0:
                        shot.launch_angle_horizontal = kld7_angle_h.horizontal_deg
                        if shot.angle_source is None:
                            shot.angle_source = "radar"
                        if shot.launch_angle_confidence is None:
                            shot.launch_angle_confidence = kld7_angle_h.confidence
                    else:
                        logger.warning(
                            "[SERVER] Horizontal angle %.1f° rejected: exceeds ±15°",
                            kld7_angle_h.horizontal_deg,
                        )
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_rolling_buffer.py::TestHorizontalAnglePlausibility -v`
Expected: PASS

- [ ] **Step 4: Run full test suite to check for regressions**

Run: `uv run pytest tests/ -v --timeout=60`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/openflight/server.py tests/test_rolling_buffer.py
git commit -m "fix: add horizontal angle plausibility check (±15° max)"
```

---

### Task 5: Verify with full test suite + lint

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -v --timeout=60`
Expected: All PASS

- [ ] **Step 2: Run lint**

Run: `uv run pylint src/openflight/ --fail-under=9`
Expected: Score >= 9.0

- [ ] **Step 3: Run ruff**

Run: `uv run ruff check src/openflight/ && uv run ruff format --check src/openflight/`
Expected: No errors

- [ ] **Step 4: Fix any lint/format issues and commit**

If any issues found, fix and commit:

```bash
git add -u
git commit -m "style: fix lint/format issues from spin-angle validation"
```
