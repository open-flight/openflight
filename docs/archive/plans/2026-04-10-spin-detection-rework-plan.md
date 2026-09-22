# Spin Detection Rework Implementation Plan

!!! warning "ARCHIVED DOCUMENT"

    This is a historical design or implementation note, kept as a record of why
    the code is shaped the way it is. It describes the project as of the date in
    its filename and **is not a guide to follow** — commands, paths, and
    constants may no longer match the code. See the
    [Archive index](../index.md) for current alternatives.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken secondary-FFT spin detection with amplitude envelope demodulation that works directly on the raw I/Q capture, detecting the golf ball seam modulation at 2x spin rate.

**Architecture:** Bandpass filter raw I/Q around the ball's Doppler frequency (from OPS243 speed), extract amplitude envelope, find spin frequency via FFT (primary) or autocorrelation (fallback). Returns the same `SpinResult` type so no downstream changes needed.

**Tech Stack:** Python, numpy, scipy.signal (Butterworth bandpass filter)

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `src/openflight/rolling_buffer/processor.py:408-523` | Replace `detect_spin()` with envelope demodulation |
| Modify | `src/openflight/rolling_buffer/processor.py:678-691` | Update `process_capture()` to pass `IQCapture` to new `detect_spin` |
| Modify | `src/openflight/rolling_buffer/processor.py:72-75` | Update spin constants |
| Modify | `tests/test_rolling_buffer.py:1373-1500` | Replace spin tests with AM-based tests |

---

### Task 1: Update spin constants and add new ones

**Files:**
- Modify: `src/openflight/rolling_buffer/processor.py:72-75`

- [ ] **Step 1: Replace the spin constants**

In `src/openflight/rolling_buffer/processor.py`, replace the existing spin constants (lines 72-75):

```python
    # Spin detection
    MIN_SPIN_RPM = 1000
    MAX_SPIN_RPM = 10000
    MIN_SPIN_SNR = 4.0
```

With:

```python
    # Spin detection via amplitude envelope demodulation.
    # The ball seam modulates the radar return at 2x spin rate.
    SPIN_BANDPASS_BW_HZ = 200       # ±200 Hz around ball Doppler
    SPIN_BANDPASS_ORDER = 4          # Butterworth filter order
    SPIN_ENVELOPE_FFT_SIZE = 8192   # Zero-padded FFT for envelope
    SPIN_MIN_SEAM_HZ = 80.0         # 2400 RPM min (seam = 2x spin)
    SPIN_MAX_SEAM_HZ = 670.0        # 20100 RPM max
    SPIN_MIN_SAMPLES = 600           # ~20ms minimum ball signal
    SPIN_SNR_HIGH = 8.0              # High confidence threshold
    SPIN_SNR_MEDIUM = 5.0            # Medium confidence threshold
    SPIN_SNR_MIN = 3.0               # Minimum to report
    SPIN_AUTOCORR_MIN = 0.3          # Minimum normalized correlation
    SPIN_MIN_CYCLES = 2              # Minimum seam cycles to report
```

- [ ] **Step 2: Add scipy import at top of file**

Add after the existing numpy import (around line 7):

```python
from scipy.signal import butter, sosfiltfilt
```

- [ ] **Step 3: Verify file still parses**

Run: `python3 -c "import ast; ast.parse(open('src/openflight/rolling_buffer/processor.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add src/openflight/rolling_buffer/processor.py
git commit -m "refactor: update spin detection constants for envelope demodulation"
```

---

### Task 2: Write tests for new spin detection

**Files:**
- Modify: `tests/test_rolling_buffer.py`

- [ ] **Step 1: Replace the spin test helper and tests**

In `tests/test_rolling_buffer.py`, find the `_make_iq_with_oscillating_speed` method (line ~1373) and the three spin tests that follow it (`test_spin_detected_with_oscillating_signal`, `test_process_capture_spin_field_populated`, `test_no_spin_with_constant_speed`). Replace all of them with:

```python
    def _make_iq_with_seam_modulation(
        self,
        base_speed_mph: float,
        spin_rpm: float,
        modulation_depth: float = 0.03,
        sample_rate: int = 30000,
        num_samples: int = 4096,
    ):
        """Generate synthetic I/Q with amplitude modulation at 2x spin rate.

        Simulates the golf ball seam crossing the radar beam, which modulates
        the return amplitude at twice the spin frequency.
        """
        wavelength = 0.01243
        speed_mps = base_speed_mph / 2.23694
        doppler_hz = 2 * speed_mps / wavelength
        seam_hz = (spin_rpm / 60.0) * 2  # seam modulates at 2x spin

        t = np.arange(num_samples) / sample_rate
        phase = 2 * np.pi * doppler_hz * t

        # Amplitude modulated by seam rotation
        amplitude = 200 * (1.0 + modulation_depth * np.sin(2 * np.pi * seam_hz * t))

        i_samples = (amplitude * np.cos(phase) + 2048).astype(int).clip(0, 4095).tolist()
        q_samples = (amplitude * np.sin(phase) + 2048).astype(int).clip(0, 4095).tolist()

        return i_samples, q_samples

    def test_spin_detected_7iron(self):
        """7-iron at 7000 RPM should be reliably detected from seam modulation."""
        i_samples, q_samples = self._make_iq_with_seam_modulation(
            base_speed_mph=120, spin_rpm=7000, modulation_depth=0.03,
        )
        capture = IQCapture(
            sample_time=0.0, trigger_time=0.068,
            i_samples=i_samples, q_samples=q_samples,
        )
        processor = RollingBufferProcessor()
        result = processor.process_capture(capture)

        assert result is not None
        assert result.spin is not None
        assert result.spin.spin_rpm > 0, f"Should detect spin, got quality={result.spin.quality}"
        assert abs(result.spin.spin_rpm - 7000) < 500, (
            f"Expected ~7000 RPM, got {result.spin.spin_rpm:.0f}"
        )

    def test_spin_detected_driver(self):
        """Driver at 3000 RPM (fewer cycles) should still be detectable."""
        i_samples, q_samples = self._make_iq_with_seam_modulation(
            base_speed_mph=160, spin_rpm=3000, modulation_depth=0.03,
        )
        capture = IQCapture(
            sample_time=0.0, trigger_time=0.068,
            i_samples=i_samples, q_samples=q_samples,
        )
        processor = RollingBufferProcessor()
        result = processor.process_capture(capture)

        assert result is not None
        assert result.spin is not None
        assert result.spin.spin_rpm > 0, f"Should detect spin, got quality={result.spin.quality}"
        assert abs(result.spin.spin_rpm - 3000) < 500, (
            f"Expected ~3000 RPM, got {result.spin.spin_rpm:.0f}"
        )

    def test_spin_detected_wedge(self):
        """Wedge at 10000 RPM (many cycles, strong signal)."""
        i_samples, q_samples = self._make_iq_with_seam_modulation(
            base_speed_mph=90, spin_rpm=10000, modulation_depth=0.05,
        )
        capture = IQCapture(
            sample_time=0.0, trigger_time=0.068,
            i_samples=i_samples, q_samples=q_samples,
        )
        processor = RollingBufferProcessor()
        result = processor.process_capture(capture)

        assert result is not None
        assert result.spin is not None
        assert result.spin.spin_rpm > 0
        assert abs(result.spin.spin_rpm - 10000) < 500

    def test_no_spin_with_constant_amplitude(self):
        """Constant amplitude (no seam modulation) should yield no spin."""
        sample_rate = 30000
        num_samples = 4096
        speed_mph = 150
        wavelength = 0.01243
        speed_mps = speed_mph / 2.23694
        freq = 2 * speed_mps / wavelength

        t = np.arange(num_samples) / sample_rate
        phase = 2 * np.pi * freq * t

        i_samples = (200 * np.cos(phase) + 2048).astype(int).clip(0, 4095).tolist()
        q_samples = (200 * np.sin(phase) + 2048).astype(int).clip(0, 4095).tolist()

        capture = IQCapture(
            sample_time=0.0, trigger_time=0.068,
            i_samples=i_samples, q_samples=q_samples,
        )

        processor = RollingBufferProcessor()
        result = processor.process_capture(capture)

        assert result is not None
        assert result.spin is not None
        # Should NOT detect spin (no modulation)
        assert result.spin.spin_rpm == 0 or result.spin.quality in (
            "low", "No clear spin signal", "Envelope variation too low",
        ), f"Unexpected spin: {result.spin.spin_rpm} RPM, quality={result.spin.quality}"

    def test_spin_result_is_populated(self):
        """process_capture should always populate the spin field."""
        i_samples, q_samples = self._make_iq_with_seam_modulation(
            base_speed_mph=130, spin_rpm=5000,
        )
        capture = IQCapture(
            sample_time=0.0, trigger_time=0.068,
            i_samples=i_samples, q_samples=q_samples,
        )

        processor = RollingBufferProcessor()
        result = processor.process_capture(capture)

        assert result is not None
        assert result.spin is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rolling_buffer.py -k "spin" -v`
Expected: New tests FAIL because `detect_spin` still has the old signature and doesn't accept `IQCapture`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_rolling_buffer.py
git commit -m "test: add envelope-based spin detection tests"
```

---

### Task 3: Implement new detect_spin with envelope demodulation

**Files:**
- Modify: `src/openflight/rolling_buffer/processor.py:408-523`

- [ ] **Step 1: Replace detect_spin entirely**

Replace the entire `detect_spin` method (lines 408-523) with:

```python
    def detect_spin(
        self,
        capture: IQCapture,
        ball_speed_mph: float,
        ball_timestamp_ms: float,
    ) -> SpinResult:
        """
        Detect spin rate from amplitude envelope of the ball's Doppler signal.

        The golf ball seam creates amplitude modulation at 2x spin rate as it
        crosses the radar beam twice per revolution. We isolate the ball's
        Doppler signal with a bandpass filter, extract the amplitude envelope,
        then find the modulation frequency.

        Primary: FFT on the envelope (good for irons/wedges with many cycles).
        Fallback: Autocorrelation (more robust for drivers with few cycles).

        Args:
            capture: Raw I/Q capture (4096 samples at 30 kHz)
            ball_speed_mph: Ball speed from OPS243 (for Doppler frequency)
            ball_timestamp_ms: When ball was detected in the capture

        Returns:
            SpinResult with detected spin or failure reason
        """
        i_data = np.array(capture.i_samples, dtype=np.float64)
        q_data = np.array(capture.q_samples, dtype=np.float64)

        # Remove DC offset
        i_data -= np.mean(i_data)
        q_data -= np.mean(q_data)

        # Complex I/Q signal
        iq = i_data + 1j * q_data

        # Ball Doppler frequency
        ball_speed_mps = ball_speed_mph / self.MPS_TO_MPH
        ball_doppler_hz = 2 * ball_speed_mps / self.WAVELENGTH_M

        # Bandpass filter around ball Doppler frequency
        nyquist = self.SAMPLE_RATE / 2
        low = (ball_doppler_hz - self.SPIN_BANDPASS_BW_HZ) / nyquist
        high = (ball_doppler_hz + self.SPIN_BANDPASS_BW_HZ) / nyquist

        # Clamp to valid range
        low = max(low, 0.001)
        high = min(high, 0.999)
        if low >= high:
            return SpinResult.no_spin_detected("Ball Doppler outside filter range")

        try:
            sos = butter(self.SPIN_BANDPASS_ORDER, [low, high], btype="band", output="sos")
            filtered = sosfiltfilt(sos, iq)
        except Exception as e:
            return SpinResult.no_spin_detected(f"Bandpass filter failed: {e}")

        # Amplitude envelope
        envelope = np.abs(filtered)

        # Trim to ball-present window (from ball onset to end of capture)
        start_sample = max(0, int(ball_timestamp_ms * self.SAMPLE_RATE / 1000))
        ball_envelope = envelope[start_sample:]

        if len(ball_envelope) < self.SPIN_MIN_SAMPLES:
            return SpinResult.no_spin_detected(
                f"Ball signal too short ({len(ball_envelope)} samples, need {self.SPIN_MIN_SAMPLES})"
            )

        # Remove DC and apply Hann window
        ball_envelope -= np.mean(ball_envelope)
        if np.std(ball_envelope) < 1e-6:
            return SpinResult.no_spin_detected("Envelope variation too low")
        windowed = ball_envelope * np.hanning(len(ball_envelope))

        # --- Primary: FFT on envelope ---
        fft_result = np.fft.fft(windowed, self.SPIN_ENVELOPE_FFT_SIZE)
        freqs = np.fft.fftfreq(self.SPIN_ENVELOPE_FFT_SIZE, d=1 / self.SAMPLE_RATE)
        half = self.SPIN_ENVELOPE_FFT_SIZE // 2
        magnitude = np.abs(fft_result[1:half])
        freqs = freqs[1:half]

        # Restrict to seam frequency range
        valid_mask = (freqs >= self.SPIN_MIN_SEAM_HZ) & (freqs <= self.SPIN_MAX_SEAM_HZ)
        if not np.any(valid_mask):
            return SpinResult.no_spin_detected("No valid seam frequencies in range")

        valid_mag = magnitude[valid_mask]
        valid_freqs = freqs[valid_mask]

        # Reject first 2 bins in the valid range (DC leakage into envelope)
        if len(valid_mag) > 2:
            valid_mag[:2] = 0

        peak_idx = np.argmax(valid_mag)
        peak_freq = valid_freqs[peak_idx]
        peak_mag = valid_mag[peak_idx]

        # SNR: peak vs median noise floor in valid range
        noise_floor = np.median(valid_mag[valid_mag > 0]) if np.any(valid_mag > 0) else 1.0
        fft_snr = peak_mag / noise_floor if noise_floor > 0 else 0

        # Seam frequency to spin RPM (seam = 2x spin)
        spin_rpm = (peak_freq / 2) * 60

        # Check minimum cycles in window
        window_seconds = len(ball_envelope) / self.SAMPLE_RATE
        seam_cycles = peak_freq * window_seconds

        logger.info(
            "[PROCESSOR] Spin envelope: peak=%.1f Hz (%.0f RPM), SNR=%.1f, "
            "cycles=%.1f, window=%.0fms, samples=%d",
            peak_freq, spin_rpm, fft_snr, seam_cycles,
            window_seconds * 1000, len(ball_envelope),
        )

        # --- Fallback: Autocorrelation for marginal FFT ---
        autocorr_confirmed = False
        if fft_snr < self.SPIN_SNR_MEDIUM and fft_snr >= self.SPIN_SNR_MIN:
            # Normalized autocorrelation
            norm = np.correlate(windowed, windowed, mode="full")
            norm = norm[len(norm) // 2:]  # positive lags only
            if norm[0] > 0:
                norm = norm / norm[0]

            # Search for peak at lag corresponding to seam frequency range
            min_lag = int(self.SAMPLE_RATE / self.SPIN_MAX_SEAM_HZ)
            max_lag = int(self.SAMPLE_RATE / self.SPIN_MIN_SEAM_HZ)
            max_lag = min(max_lag, len(norm) - 1)

            if min_lag < max_lag:
                search_region = norm[min_lag:max_lag]
                if len(search_region) > 0:
                    acorr_peak_idx = np.argmax(search_region)
                    acorr_peak_val = search_region[acorr_peak_idx]
                    acorr_lag = min_lag + acorr_peak_idx

                    if acorr_peak_val >= self.SPIN_AUTOCORR_MIN and acorr_lag > 0:
                        acorr_freq = self.SAMPLE_RATE / acorr_lag
                        acorr_rpm = (acorr_freq / 2) * 60

                        # Confirm if autocorrelation agrees with FFT (within 10%)
                        if abs(acorr_rpm - spin_rpm) / max(spin_rpm, 1) < 0.10:
                            autocorr_confirmed = True
                            logger.info(
                                "[PROCESSOR] Spin autocorrelation confirms: %.0f RPM (corr=%.2f)",
                                acorr_rpm, acorr_peak_val,
                            )
                        else:
                            # Autocorrelation found a different frequency — use it if stronger
                            logger.info(
                                "[PROCESSOR] Spin autocorrelation disagrees: FFT=%.0f, autocorr=%.0f RPM (corr=%.2f)",
                                spin_rpm, acorr_rpm, acorr_peak_val,
                            )
                            if acorr_peak_val >= 0.4:
                                spin_rpm = acorr_rpm
                                peak_freq = acorr_freq
                                autocorr_confirmed = True

        # --- Quality assessment ---
        if seam_cycles < self.SPIN_MIN_CYCLES:
            return SpinResult.no_spin_detected(
                f"Too few seam cycles ({seam_cycles:.1f}, need {self.SPIN_MIN_CYCLES})"
            )

        if fft_snr < self.SPIN_SNR_MIN and not autocorr_confirmed:
            return SpinResult.no_spin_detected(
                f"SNR too low ({fft_snr:.1f}, need {self.SPIN_SNR_MIN})"
            )

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

        return SpinResult(
            spin_rpm=round(spin_rpm),
            confidence=confidence,
            snr=round(fft_snr, 2),
            quality=quality,
        )
```

- [ ] **Step 2: Verify file parses**

Run: `python3 -c "import ast; ast.parse(open('src/openflight/rolling_buffer/processor.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/openflight/rolling_buffer/processor.py
git commit -m "feat: replace spin detection with envelope demodulation"
```

---

### Task 4: Update process_capture to pass IQCapture to detect_spin

**Files:**
- Modify: `src/openflight/rolling_buffer/processor.py:678-691`

- [ ] **Step 1: Update the spin detection call in process_capture**

Find the current spin detection block (around line 678):

```python
        # Try spin detection
        ball_speeds = self.extract_ball_speeds(
            timeline, ball_timestamp_ms, ball_speed_mph
        )
        spin = self.detect_spin(ball_speeds, timeline.sample_rate_hz)

        logger.info(
            "[PROCESSOR] Spin analysis: %d ball speed samples in %.0f-%.0fms window, "
            "sample_rate=%.0f Hz, spin=%.0f RPM, snr=%.2f, quality=%s",
            len(ball_speeds),
            ball_timestamp_ms, ball_timestamp_ms + 50,
            timeline.sample_rate_hz,
            spin.spin_rpm, spin.snr, spin.quality,
        )
```

Replace with:

```python
        # Spin detection via amplitude envelope demodulation on raw I/Q
        spin = self.detect_spin(capture, ball_speed_mph, ball_timestamp_ms)

        logger.info(
            "[PROCESSOR] Spin result: %.0f RPM, SNR=%.2f, quality=%s",
            spin.spin_rpm, spin.snr, spin.quality,
        )
```

Note: the `capture` variable is already available in `process_capture` — it's passed as a parameter. Check that the `ProcessedCapture` creation still has access to `capture`. It does (line ~699: `capture=capture`).

- [ ] **Step 2: Run the spin tests**

Run: `uv run pytest tests/test_rolling_buffer.py -k "spin" -v`
Expected: All new spin tests PASS.

- [ ] **Step 3: Run the full rolling buffer test suite**

Run: `uv run pytest tests/test_rolling_buffer.py -v`
Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/openflight/rolling_buffer/processor.py
git commit -m "feat: wire envelope spin detection into process_capture pipeline"
```

---

### Task 5: Add scipy to dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Check if scipy is already a dependency**

Run: `grep scipy pyproject.toml`

If scipy is already listed, skip this task. If not:

- [ ] **Step 2: Add scipy to dependencies**

In `pyproject.toml`, find the `[project]` dependencies list and add `scipy`:

```toml
dependencies = [
    # ... existing deps ...
    "scipy>=1.10.0",
]
```

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "build: add scipy dependency for spin bandpass filter"
```

---

### Task 6: Validate against real session data

- [ ] **Step 1: Run the new spin detection against the driving range session**

Create a quick validation script (don't commit — just run it):

```python
import json, sys
sys.path.insert(0, 'src')
from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.rolling_buffer.types import IQCapture

processor = RollingBufferProcessor()

with open('session_logs/session_20260410_110759_range.jsonl') as f:
    entries = [json.loads(line) for line in f]

captures = [e for e in entries if e.get('type') == 'rolling_buffer_capture']
shots = [e for e in entries if e.get('type') == 'shot_detected']

print(f"Testing {len(captures)} captures from driving range session")
print()

detected = 0
for c in captures:
    if not c.get('i_samples') or not c.get('q_samples'):
        continue
    cap = IQCapture(
        sample_time=c.get('sample_time', 0),
        trigger_time=c.get('trigger_time', 0),
        i_samples=c['i_samples'],
        q_samples=c['q_samples'],
    )
    ball_speed = c.get('ball_speed_mph', 0)
    ball_ts = c.get('ball_timestamp_ms', 60)

    spin = processor.detect_spin(cap, ball_speed, ball_ts)
    if spin.spin_rpm > 0:
        detected += 1
        # Find matching shot for club info
        shot = shots[c['shot_number'] - 1] if c['shot_number'] <= len(shots) else {}
        club = shot.get('club', '?')
        print(f"  Shot {c['shot_number']} ({club}, {ball_speed:.0f}mph): "
              f"{spin.spin_rpm:.0f} RPM, SNR={spin.snr:.1f}, quality={spin.quality}")

print(f"\nDetected: {detected}/{len(captures)} ({100*detected//max(len(captures),1)}%)")
print("No more 1318/1538 RPM artifacts should appear")
```

Run: `python3 validate_spin.py` (or `uv run python validate_spin.py`)

Expected:
- No 1318 or 1538 RPM values
- Spin values in reasonable ranges per club (driver 2000-4000, iron 4000-8000, wedge 8000-12000)
- Detection rate may be lower initially — that's OK, honest "no spin" is better than fake values

- [ ] **Step 2: Clean up**

Delete the validation script (don't commit it).

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat: spin detection rework — envelope demodulation replaces secondary FFT"
```
