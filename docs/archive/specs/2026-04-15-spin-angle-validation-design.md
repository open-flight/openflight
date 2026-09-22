# Spin & Angle Data Quality Validation

!!! warning "ARCHIVED DOCUMENT"

    This is a historical design or implementation note, kept as a record of why
    the code is shaped the way it is. It describes the project as of the date in
    its filename and **is not a guide to follow** — commands, paths, and
    constants may no longer match the code. See the
    [Archive index](../index.md) for current alternatives.

**Date:** 2026-04-15
**Status:** Approved
**Scope:** Tighten spin confidence scoring, add hard RPM ceiling, add angle bounds, fix missing horizontal plausibility check

## Problem

Session `session_20260415_123037_range.jsonl` (6 shots) revealed three data quality issues:

1. **Repeated spin values**: 4/6 shots returned exactly 2637 RPM (FFT bin 12 of the 8192-point envelope FFT). These are not real spin measurements — the detector is locking onto the same low-frequency spectral feature. All received `spin_confidence >= 0.7`, meaning the ballistics module's fallback policy treats them as trustworthy.
2. **Impossible spin value**: Shot 3 returned 20,455 RPM (340.9 Hz) despite `SPIN_MAX_SEAM_HZ = 200.0`. This exceeds the physical maximum for any golf ball. It got `spin_confidence = 0.7`.
3. **Bad launch angles**: Shot 4 had `launch_angle_horizontal = -34.0` (physically impossible). Shot 3 had `launch_angle_vertical = 31.2` at 101 mph ball speed (implausible for that speed). The horizontal angle path in `server.py` has no plausibility check — angles are accepted unconditionally.

## Approach

Fix at the source: add validation inside the existing detection code (`processor.py` for spin, `radc.py` for angles) rather than a separate validation layer. Each shot is evaluated independently (no cross-shot state).

## Changes

### 1. Spin: Hard RPM ceiling (processor.py)

Add a final safety-net check in `detect_spin()` before returning `SpinResult`:

```python
if spin_rpm > SPIN_MAX_SEAM_HZ * 60:  # 12000 RPM
    return SpinResult.no_spin_detected("Spin exceeds physical maximum")
```

This catches any code path that bypasses the FFT frequency mask, including the autocorrelation override (lines 573-576) which sets `spin_rpm = acorr_rpm` without rechecking bounds.

### 2. Spin: Tighten confidence scoring (processor.py)

Current scoring (lines 593-604) gives `confidence = 0.7` for `SNR >= 5.0 OR autocorr_confirmed`. This is too generous — 0.7 is the ballistics module's "high" threshold for trusting measured spin.

New scoring matrix:

| Condition | Confidence |
|-----------|-----------|
| `SNR >= 8.0` AND `seam_cycles >= 5` | 0.9 |
| `SNR >= 8.0` AND `seam_cycles >= 3` | 0.8 |
| `SNR >= 5.0` AND (`seam_cycles >= 3` OR `autocorr_confirmed`) | 0.7 |
| `SNR >= 5.0` OR `autocorr_confirmed` | 0.5 |
| `SNR >= 3.0` | 0.3 |

Key change: 0.7 now requires **both** decent SNR **and** either enough seam cycles or autocorrelation confirmation. Bare `SNR >= 5.0` without cycle validation drops to 0.5 — below the ballistics "high" threshold. This directly targets the 2637 RPM shots that were getting 0.7 on thin evidence.

### 3. Spin: Modulation depth confidence floor (processor.py)

After the existing modulation depth check (line ~494), add a confidence cap: if `modulation_depth < 0.01` (1%), limit the maximum confidence to 0.5 regardless of SNR. Real spin modulation is 1-5%; below 1% we're likely seeing noise with a coincidental spectral peak.

Implementation: set a flag (e.g., `weak_modulation = modulation_depth < 0.01`) and apply `confidence = min(confidence, 0.5)` before returning.

### 4. Spin: Shared confidence constant (launch_monitor.py)

Extract `SPIN_CONFIDENCE_HIGH = 0.7` into `launch_monitor.py` and import it in:
- `processor.py` (for quality label assignment)
- `ballistics.py` (pending PR #61, currently duplicates the threshold)

### 5. Angles: Hard bounds in extract_launch_angle() (radc.py)

Before appending to `results` (after line 422), reject angles outside physical limits:

- Vertical: reject if `corrected_angle < 0.0` or `corrected_angle > 45.0`
- Horizontal: reject if `abs(corrected_angle) > 15.0`

Requires threading `orientation` ("vertical" or "horizontal") from `KLD7Tracker` through `_extract_ball_radc()` into `extract_launch_angle()`. The tracker already knows its orientation.

### 6. Angles: Horizontal plausibility check (server.py)

Add a guard at lines 997-1002 for horizontal angles, matching the pattern used for vertical:

```python
if kld7_angle_h and kld7_angle_h.horizontal_deg is not None:
    if abs(kld7_angle_h.horizontal_deg) <= 15.0:
        shot.launch_angle_horizontal = kld7_angle_h.horizontal_deg
        ...
    else:
        logger.warning(...)
```

Simpler than the vertical guard — no club-based estimate needed since horizontal aim offset doesn't vary by club.

## Tests

### Spin validation tests
- `detect_spin()` returns `no_spin_detected` when peak frequency exceeds `SPIN_MAX_SEAM_HZ`
- `SNR=6.0, seam_cycles=2` now scores 0.5 (was 0.7)
- `SNR=6.0, seam_cycles=4, autocorr_confirmed=True` scores 0.7
- Modulation depth < 1% caps confidence at 0.5
- `SPIN_CONFIDENCE_HIGH` constant is used consistently across modules

### Angle validation tests
- `extract_launch_angle()` rejects vertical angles outside [0, 45]
- `extract_launch_angle()` rejects horizontal angles outside [-15, +15]
- Borderline-valid angles (44.9 vertical, 14.9 horizontal) pass through
- Server-level horizontal plausibility rejection works

All tests are unit tests on detection functions — no hardware or I/Q fixtures needed. Mock FFT output / peak values to exercise the validation gates.

## Files Modified

| File | Changes |
|------|---------|
| `src/openflight/rolling_buffer/processor.py` | Hard RPM cap, confidence rescoring, modulation depth floor |
| `src/openflight/launch_monitor.py` | Extract `SPIN_CONFIDENCE_HIGH` constant |
| `src/openflight/kld7/radc.py` | Hard angle bounds, accept `orientation` parameter |
| `src/openflight/kld7/tracker.py` | Pass `orientation` through to `extract_launch_angle()` |
| `src/openflight/server.py` | Horizontal angle plausibility check |
| `tests/test_rolling_buffer.py` (or new file) | Spin validation tests |
| `tests/test_kld7_radc.py` (or new file) | Angle validation tests |

## Out of Scope

- Trigger latency investigation (17-28s latencies — separate debugging session)
- Cross-shot spin tracking / repeated-bin detection
- Per-club adaptive angle bounds
- Changes to the ballistics PR (#61) beyond importing the shared constant
