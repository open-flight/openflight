---
icon: lucide/sliders
---

# Constants

Values compiled into the code rather than passed as flags. Change them by
editing the module, not the command line.

## Capture and FFT

`src/openflight/rolling_buffer/processor.py`

| Constant | Value | Meaning |
| --- | --- | --- |
| `SAMPLE_RATE` | 30,000 | Samples per second |
| `WINDOW_SIZE` | 128 | Samples per FFT window |
| `FFT_SIZE` | 4,096 | Zero-padded FFT size |
| `STEP_SIZE_STANDARD` | 128 | Non-overlapping step |
| `STEP_SIZE_OVERLAP` | 32 | Overlapping step, high resolution |
| `DC_MASK_BINS` | 150 | ~15 mph exclusion zone around DC |
| `MAGNITUDE_THRESHOLD` | 3 | Minimum peak magnitude |
| `MIN_PEAK_SEPARATION_BINS` | 50 | ~5 mph; rejects sidelobe duplicates |
| `MAX_PEAKS_PER_DIRECTION` | 3 | |

`--sample-rate` overrides the rate at runtime. Lowering it lengthens the buffer
but reduces the maximum measurable speed: 25 ksps gives 174 mph over 164 ms,
27 ksps gives 187 mph over 152 ms.

## Radar physics

| Constant | Value | Meaning |
| --- | --- | --- |
| `WAVELENGTH_M` | 0.01243 | 24.125 GHz |
| `MPS_TO_MPH` | 2.23694 | |
| `ADC_RANGE` | 4,096 | 12-bit ADC |
| `VOLTAGE_REF` | 3.3 | Reference voltage |

At 24.125 GHz, 1 mph produces roughly a 71.7 Hz Doppler shift.

## Club extraction

| Constant | Value |
| --- | --- |
| `CLUB_BRANCH_HISTORY_MS` | 30.0 |
| `CLUB_BALL_ONSET_SEARCH_MS` | 10.0 |
| `CLUB_BALL_MATCH_MIN_MPH` | 4.0 |
| `CLUB_BALL_MATCH_FRACTION` | 0.06 |
| `CLUB_PLATEAU_LOOKBACK_MS` | 18.0 |
| `CLUB_PLATEAU_GUARD_MS` | 4.0 |
| `CLUB_PLATEAU_QUANTILE` | 0.70 |
| `CLUB_BALL_CONTAMINATION_RATIO` | 0.95 |
| `CLUB_TERMINAL_START_MS` | −2.5 |
| `CLUB_TERMINAL_END_MS` | 1.0 |
| `CLUB_MAX_PLAUSIBLE_SPEED_MPH` | 150.0 |

## Spin detection

Amplitude-envelope demodulation. See
[rolling buffer & spin detection](../how-it-works/rolling-buffer.md).

| Constant | Value | Meaning |
| --- | --- | --- |
| `SPIN_BANDPASS_BW_HZ` | 700 | ±700 Hz around ball Doppler |
| `SPIN_BANDPASS_ORDER` | 4 | Butterworth order |
| `SPIN_ENVELOPE_FFT_SIZE` | 8,192 | Zero-padded envelope FFT |
| `SPIN_MIN_SEAM_HZ` | 33.0 | ~2,000 RPM floor |
| `SPIN_MAX_SEAM_HZ` | 200.0 | 12,000 RPM ceiling |
| `SPIN_MIN_SAMPLES` | 600 | ~20 ms minimum ball signal |
| `SPIN_SNR_HIGH` | 8.0 | High-confidence threshold |
| `SPIN_SNR_MEDIUM` | 5.0 | Medium-confidence threshold |
| `SPIN_SNR_MIN` | 2.5 | Minimum to report at all |
| `SPIN_AUTOCORR_MIN` | 0.3 | Minimum normalised correlation |
| `SPIN_MIN_CYCLES` | 2 | Minimum seam cycles |
| `SPIN_DC_LEAKAGE_BINS` | 1 | Low bins zeroed |
| `SPIN_LOW_BAND_SUSPECT_MAX_RPM` | 3,100.0 | |
| `SPIN_DETREND_POLY_ORDER` | 3 | |
| `SPIN_SIGNAL_LOSS_SMOOTH_SAMPLES` | 90 | ~3 ms moving average |

`SPIN_CONFIDENCE_HIGH` (0.7, in `launch_monitor.py`) is the threshold above
which measured spin is trusted for physics.

## Ballistics

`src/openflight/ballistics.py` — see
[ballistics & carry](../how-it-works/ballistics.md) for the model and its
references.

| Constant | Value |
| --- | --- |
| `BALL_MASS_KG` | 0.04593 |
| `BALL_RADIUS_M` | 0.02135 |
| `AIR_DENSITY_STD` | 1.225 |
| `CD_BASE` | 0.205 |
| `CD_SPIN_COEFF` | 0.18 |
| `CL_SATURATION` | 0.32 |
| `CL_HALF_SP` | 0.15 |
| `SPIN_DECAY_RATE` | 0.04 |
| `GRAVITY` | 9.81 |
| `DT_SECONDS` | 0.002 |
| `MAX_FLIGHT_SECONDS` | 15.0 |
| `SAMPLE_INTERVAL_S` | 0.05 |

## Thresholds

| Value | Setting |
| --- | --- |
| Minimum ball speed | 15 mph (35 mph in speed-triggered mode) |
| Trigger latency, `sound` | ~10 µs |
| Trigger latency, `speed` | ~5–6 ms |
| Rolling-buffer dump | 40,556 bytes |
| K-LD7 OS-CFAR threshold factor | 8.0 *(deprecated hardware)* |

## Related

- [CLI flags](cli.md) — what can be changed at runtime
- [Configuration files](configuration.md) — settings held in files
