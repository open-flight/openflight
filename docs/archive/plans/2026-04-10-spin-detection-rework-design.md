# Spin Detection Rework: Amplitude Envelope Demodulation

!!! warning "ARCHIVED DOCUMENT"

    This is a historical design or implementation note, kept as a record of why
    the code is shaped the way it is. It describes the project as of the date in
    its filename and **is not a guide to follow** — commands, paths, and
    constants may no longer match the code. See the
    [Archive index](../index.md) for current alternatives.

## Problem

The current spin detection extracts ~30 ball speed values from overlapping FFT windows (937 Hz effective sample rate), then runs a secondary 256-point FFT to find spin modulation frequency. With only 30 data points, the frequency resolution is 220 RPM/bin — too coarse to detect real spin. 81% of captures show spectral leakage artifacts, and the 19% that "pass" are Hann window sidelobe artifacts at exactly 1318 and 1538 rpm (FFT bins 6 and 7).

## Approach

Work directly on the raw 4096 I/Q samples (30 kHz, 136.5ms) instead of ~30 extracted speed values. The golf ball seam creates amplitude modulation at **2x spin rate** as it crosses the radar beam twice per revolution. Bandpass filter the I/Q signal around the ball's Doppler frequency, extract the amplitude envelope, then find the spin frequency using FFT (primary) with autocorrelation fallback for low-cycle signals (drivers).

This gives us ~100x more data points for spin analysis and ~2x finer frequency resolution.

## Signal Processing Pipeline

```
Raw I/Q (4096 samples, 30 kHz, from IQCapture)
    │
    ├─ Existing pipeline → ball_speed_mph (from process_capture)
    │
    ▼
1. Convert ball speed to Doppler frequency
   ball_doppler_hz = 2 * ball_speed_mps / wavelength_m

2. Bandpass filter I/Q around ball_doppler_hz
   - Complex signal: I + jQ
   - 4th order Butterworth bandpass, ±200 Hz around ball Doppler
   - Using scipy.signal.sosfiltfilt (zero-phase, no group delay)
   - Isolates ball return from club, body movement, and noise

3. Extract amplitude envelope
   envelope = abs(filtered_complex_signal)
   Full capture: ~4096 samples at 30 kHz

4. Trim to ball-present window
   - Use ball_timestamp_ms from existing speed detection to locate ball onset
   - Convert to sample index: start_sample = ball_timestamp_ms * 30
   - Take from ball onset to end of capture (typically 30-80ms = 900-2400 samples)
   - Require minimum 600 samples (~20ms) to proceed

5. Remove DC offset, apply Hann window
   - Subtract mean from envelope (removes DC component)
   - Apply Hann window to reduce spectral leakage

6. Primary detection: FFT on envelope
   - Zero-pad to 8192 for spectral interpolation
   - Frequency resolution: 30000/8192 = 3.66 Hz ≈ 110 RPM
   - Search for peak in seam frequency range: 80-670 Hz
     (corresponds to 2400-20000 RPM via 2x seam relationship)
   - Calculate SNR: peak magnitude / median of valid frequency range
   - Reject peaks in first 2 bins (DC leakage)

7. Fallback detection: Autocorrelation on envelope
   - Used when FFT SNR is marginal (< 5.0) but above noise (> 2.0)
   - Compute normalized autocorrelation of the windowed envelope
   - Search for first significant peak at lag corresponding to 80-670 Hz
   - Lag range: 30000/670 = 45 samples to 30000/80 = 375 samples
   - Peak must exceed 0.3 normalized correlation to be valid
   - If autocorrelation confirms FFT frequency (within 10%), boost confidence

8. Convert seam frequency to spin RPM
   spin_rpm = (peak_frequency_hz / 2) * 60
   Divide by 2 because seam crosses beam twice per revolution.

9. Quality assessment
   - "high": FFT SNR >= 8 AND >= 5 seam cycles in the trimmed window
   - "medium": FFT SNR >= 5, OR autocorrelation confirms FFT peak
   - "low": FFT SNR >= 3, marginal detection
   - Reject: SNR < 3, or peak in first 2 bins, or fewer than 2 seam cycles
```

## Expected Detection Performance

| Club | Typical Spin | Seam Freq | Cycles in 60ms | Expected Result |
|------|-------------|-----------|----------------|-----------------|
| Wedge | 8000-10000 rpm | 267-333 Hz | 16-20 | High confidence |
| 9-iron | 7000-9000 rpm | 233-300 Hz | 14-18 | High confidence |
| 7-iron | 5000-7000 rpm | 167-233 Hz | 10-14 | High confidence |
| 5-iron | 4000-5500 rpm | 133-183 Hz | 8-11 | Medium-high |
| 3-iron | 3500-4500 rpm | 117-150 Hz | 7-9 | Medium |
| Driver | 2500-3500 rpm | 83-117 Hz | 5-7 | Medium (FFT+autocorrelation) |
| Driver (low) | 2000-2500 rpm | 67-83 Hz | 4-5 | Low-medium (autocorrelation) |

## Bandpass Filter Design

- Type: 4th order Butterworth
- Center: ball_doppler_hz (computed from OPS243 ball speed)
- Bandwidth: ±200 Hz (400 Hz total)
- Implementation: `scipy.signal.butter` + `sosfiltfilt` (sos form for numerical stability)
- The ±200 Hz window captures the ball's Doppler peak plus spin-induced spectral broadening while rejecting club returns (which are at a different Doppler frequency)

### Doppler Frequency Examples

| Ball Speed | Doppler Frequency | Filter Band |
|-----------|-------------------|-------------|
| 80 mph | ~1930 Hz | 1730-2130 Hz |
| 120 mph | ~2900 Hz | 2700-3100 Hz |
| 160 mph | ~3860 Hz | 3660-4060 Hz |

All within the 0-15 kHz Nyquist bandwidth of 30 ksps sampling.

## Files Changed

### Modified: `src/openflight/rolling_buffer/processor.py`

**`detect_spin()`** — Replace internals entirely. New signature:
```python
def detect_spin(self, capture: IQCapture, ball_speed_mph: float, 
                ball_timestamp_ms: float) -> SpinResult:
```
Instead of the current:
```python
def detect_spin(self, ball_speeds: List[float], sample_rate_hz: float) -> SpinResult:
```

**`process_capture()`** — Update to pass `IQCapture` and `ball_speed_mph` to `detect_spin` instead of extracted ball speed values.

**Keep `extract_ball_speeds()`** — still used for the speed timeline, just no longer feeds spin detection.

### Unchanged
- `SpinResult` dataclass in `types.py` — same fields: `spin_rpm`, `confidence`, `snr`, `quality`
- `server.py` — consumes `SpinResult` from `ProcessedCapture`, no changes
- Session logger, UI, Alloy pipeline — all consume spin_rpm/quality unchanged
- Trigger, monitor, K-LD7 code — unrelated

## Constants

```python
# Spin detection via amplitude envelope demodulation
SPIN_BANDPASS_BW_HZ = 200       # ±200 Hz around ball Doppler
SPIN_BANDPASS_ORDER = 4          # Butterworth filter order
SPIN_FFT_SIZE = 8192             # Zero-padded FFT for envelope
SPIN_MIN_SEAM_HZ = 80           # 2400 RPM minimum (seam = 2x spin)
SPIN_MAX_SEAM_HZ = 670          # 20000 RPM maximum
SPIN_MIN_SAMPLES = 600          # ~20ms minimum ball signal
SPIN_SNR_HIGH = 8.0             # High confidence threshold
SPIN_SNR_MEDIUM = 5.0           # Medium confidence threshold
SPIN_SNR_MIN = 3.0              # Minimum to report
SPIN_AUTOCORR_THRESHOLD = 0.3   # Minimum normalized correlation
SPIN_MIN_CYCLES = 2             # Minimum seam cycles to report
```

## Testing Strategy

1. **Synthetic signal test**: Generate I/Q with known Doppler frequency and amplitude modulation at known spin rate. Verify `detect_spin` recovers the correct RPM.
2. **Multiple spin rates**: Test at 3000, 5000, 7000, 10000 RPM to cover the full range.
3. **No-spin test**: Flat amplitude envelope (no modulation) should return no spin.
4. **Noise robustness**: Add white noise to synthetic signal, verify detection degrades gracefully.
5. **Real capture validation**: Run against the driving range session data (session_20260410) to check that the new method doesn't produce the 1318/1538 artifacts.

## Validation Against Real Data

After implementation, re-analyze `session_logs/session_20260410_110759_range.jsonl` (68 shots from driving range). Success criteria:
- No more 1318/1538 rpm quantized values
- Iron/wedge spin detection rate > 50% (currently 19% but with fake values)
- Detected spin values in expected ranges per club (driver 2500-3500, iron 4000-8000, wedge 8000-12000)
- "No spin detected" is preferred over reporting incorrect values
