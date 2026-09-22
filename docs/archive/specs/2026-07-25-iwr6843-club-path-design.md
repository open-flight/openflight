# IWR6843 Club Path From Pre-Impact Frames

!!! warning "ARCHIVED DOCUMENT"

    This is a historical design or implementation note, kept as a record of why
    the code is shaped the way it is. It describes the project as of the date in
    its filename and **is not a guide to follow** — commands, paths, and
    constants may no longer match the code. See the
    [Archive index](../index.md) for current alternatives.

**Date:** 2026-07-25
**Status:** Approved
**Scope:** Measure true club path (in-to-out / out-to-in) from the IWR6843's pre-impact frames, and fix three defects in the existing angle pipeline that would otherwise make the new measurement untrustworthy: a plain-mean channel combine, a span gate that rejects usable tracks, and hardcoded confidence values on both launch angles.

## Problem

`Shot.club_path_deg` exists and is wired end to end — session logger, WebSocket payload, GSPro `Path` field, and the `spin_axis_deg` derivation at `server.py:2156`. Its only producer was the deprecated K-LD7 horizontal radar, so the field is permanently `None` on the IWR6843 path.

The IWR6843 firmware retains 6 pre-impact frames explicitly for future club metrics. Nothing consumes them.

Separately, the 2026-07-25 range sessions exposed three defects in the vertical launch path that must be fixed alongside, because club path inherits the same machinery and feeds the same derived metric:

1. `lcmf.py:658` averages the two channel estimates unweighted. When one channel collapses, the reported angle is the mean of a good estimate and a broken one.
2. `REJECT_MIN_SPAN_S = 0.018` rejects tracks that produce good estimates.
3. `server.py:1892` and `:1900` hardcode `0.95` confidence for both launch angles, discarding the quality signals the estimators already compute.

## Evidence From Current Code And Captured Data

All findings below come from the 15 captures in `session_logs/iwr/` (sessions `20260725_130951` and `20260725_140533`), replayed offline.

### The club region is stored, and there is a club-like mover in it

`Geometry.loop_time` maps ring slot to time via `(frame - trigger_frame) % n_frames`, so slot-order 0 is the **oldest** retained frame and impact sits at slot-order 6. The pre-impact frames are therefore slot-orders 0–6.

Those frames carry range bins 20–72 = **0.94–3.38 m**, which covers the tee at 1.372 m. The observed window schedule matches `firmware/README.md` exactly on all seven captures of session `140533`: orders 0–6 → start bin 20, 7–11 → 32, 12–17 → 47.

Within the pre-impact frames every shot shows a strong mover in the club's neighbourhood, 60–1000× the frame-median power, closing from roughly 2.0 m to 1.55 m over the final 20 ms — about 22 m/s radial, consistent with the 66–79 mph club speeds the OPS243 measured for those same shots.

That mover is **not proven** to be the club head rather than hands or body. The design therefore treats identity as a gate, not an assumption: the club track's radial speed must be consistent with the OPS243 club speed for the shot, which is an independent measurement already available per shot.

### Range resolution and array shapes

- `range_res_m = RANGE_SPAN_M / range_fft_size` = 6.0 / 128 = **4.69 cm**. `geometry_from_header` on an unprojected 3-TX dump leaves `range_fft_size` unset and silently falls back to `6.0 / n_samples` = 11.3 cm. Always derive geometry from the projected 2-TX cube, as `process_dump` does.
- `tracking.mti_filter` returns a 5-D array `(frames, tx, loops, rx, bins)`. Power reductions must sum every axis except frames and bins; `movers_by_slot` sums `axis=(1, 2, 3)`.

### The `two8` channel collapses at this rig's geometry

Session `140533`, seven 7-irons, tilt 5.5°, radar height 0.229 m:

| shot | reported (mean) | `two8` | `four4_path_tdm` | component sd |
|---|---|---|---|---|
| 2 | 8.60 | −0.50 | 17.71 | 9.1 |
| 3 | 24.32 | 26.62 | 22.03 | 2.3 |
| 4 | 9.13 | −0.96 | 19.23 | 10.1 |
| 5 | 7.95 | −0.11 | 16.02 | 8.1 |
| 6 | 7.33 | −0.62 | 15.28 | 7.9 |
| 7 | 7.92 | −0.65 | 16.48 | 8.6 |

`four4_path_tdm` alone gives **17.8° ± 2.5°**, against this codebase's own table expectation of 17.1° for a 7-iron. The reported mean is 10.9°. The operator confirmed the shots flew normally, which is incompatible with 8° launches.

`two8` is also hypersensitive to assumed tilt — on identical data it reads −1.4° at tilt 0, −0.5° at 5.5°, and +16.6° at 10.4°, while `four4_path_tdm` moves roughly 1:1 with tilt as physics requires. A 4.9° change in one input moving an output 17° indicates an ill-conditioned fit, not a measurement. `two8` is also the only channel that does not receive the TDM cross-phase correction applied at `lcmf.py:234-241`, which is a plausible but unproven root cause.

### The span gate rejects usable tracks

Replaying with `REJECT_MIN_SPAN_S` relaxed from 0.018 to 0.015:

| session | accepted at 18 ms | at 15 ms | recovered four-path value |
|---|---|---|---|
| `140533` | 6/7 | 7/7 | shot 1 → 19.25° |
| `130951` | 1/7 | 2/7 | shot 6 → 21.16° |

Recovered values sit inside the existing cluster. Remaining failures are at 12.5–12.9 ms with 16–24 inliers and are genuinely thin.

Relaxing the gate **alone makes the product worse**: shot 1 at a 15 ms gate reports 8.79°, the mean of `two8 = −1.66` and `four4 = 19.25`. The relaxation is only worth shipping together with robust channel selection.

Gating on `component_std_deg` is also harmful in isolation: spread is 8–11° on nearly every shot *because* `two8` is broken, so an agreement gate would cut coverage from 7/7 to 1/7. Component agreement becomes a usable signal only once two healthy channels exist.

### Horizontal launch is healthy but reported dishonestly

HLCMF-v0 coherence across both sessions is 0.71–0.95, comfortably above its own 0.25 bar. `server.py:1900` reads `horizontal_confidence` into a local variable and then assigns `0.95` regardless.

## Approach

Estimate club path from the **rate of change** of the club's azimuth across the pre-impact frames, rather than from absolute azimuth.

Club path is a direction of travel, so it needs at least two spatial samples over time; a single azimuth reading cannot produce it. Given samples over time, two formulations are available:

- **Absolute:** convert each frame's phase to an absolute azimuth, build a Cartesian track, fit a line. Depends on `elem_phase_rad` from `iwr6843_calibration_reference.json`, measured on a different board.
- **Differential (chosen):** fit the azimuth *rate* and discard the constant offset. A per-element phase error common to every frame cancels in the difference.

The differential form is chosen because the 2026-07-25 sessions are a direct demonstration of what borrowed array calibration does to an absolute angle estimate. It still requires one external constant — where boresight sits relative to the target line — but that is a mount-aim value measured with a tape and a string, not inherited from another board.

```
path_deg = degrees(atan2(r · θ̇, ṙ)) + aim_offset_deg
```

where `θ̇` is the fitted azimuth rate, `ṙ` the club track's range rate, and `r` the club's range at impact.

### Expected precision

For a 3° club path at r ≈ 1.7 m and ṙ ≈ 22 m/s, the club moves ~2.3 cm laterally over the final 20 ms — roughly 0.8° of azimuth change, about 0.04 rad of phase. Against per-snapshot phase noise at the observed SNR, averaged over ~72 snapshots, this lands near **±1–2° of club path**.

That is sufficient to separate deliberate in-to-out from out-to-in swings (8–15° apart) and insufficient for a degree-level accuracy claim. The validation plan is set accordingly. This estimate is analytical and must be confirmed or refuted by the separation test.

> **Superseded by implementation (2026-07-25).** The polar formulation above
> (fit the azimuth *rate*, `path_deg = atan2(r · θ̇, ṙ) + aim_offset_deg`) was
> not what shipped, including the "Differential" bullet's rejection of the
> Absolute/Cartesian option above — that option is what `club.py` actually
> implements. This note records the divergence rather than rewriting the
> sections above; see `club.py`'s module docstring for the full derivation.
>
> The shipped formulation is Cartesian, not polar: each sample converts to
> `x = r · cos(az)`, `y = r · sin(az)`, weighted linear fits recover
> `v_x`/`v_y` directly, and `path_deg = atan2(v_y, v_x) + aim_offset_deg`.
> Both `x(t)` and `y(t)` are exactly linear in time for straight-line motion,
> so this fit is unbiased in the continuous limit — the polar/rate form was
> not (a fitted azimuth rate is a window-averaged rate through the fit's own
> mean time, not the instantaneous rate at impact; see `club.py` for the
> measured discrepancy on a synthetic fixture).
>
> Measured precision (first-principles fixture, `test_iwr6843_club_path.py`)
> is **±0.3° across ±12°** — 0.034° at 4°, 0.092° at 8°, 0.297° at +12°,
> 0.303° at −12°, growing with angle — not the "±1–2°" estimated above.
>
> The cancellation mechanism is also corrected here: absolute azimuth enters
> **additively** (`+ aim_offset_deg`), so it is only the *rate*/velocity
> terms that are immune to a constant per-element phase error, which is what
> makes `--iwr6843-azimuth-offset-deg` load-bearing rather than a mount-aim
> nicety. And the cancellation does not come from `elem_phase_rad` in
> `iwr6843_calibration_reference.json` as implied above — `club.py` never
> calls `cal.apply`. It comes from the TX2-vs-reference-antenna phase
> *difference* computed in `doa.tx2_phase_at`, which is why a common
> per-element error cancels regardless of which board it was measured on.
>
> The "azimuth-rate fit residual" named in the Failure Modes table below
> (`rejected_azimuth_fit`) is, correspondingly, now a cross-range fit
> residual (`club.py:277`), not a rate residual.

## Architecture

One new module, `src/openflight/iwr6843/club.py`, owns the club measurement. It stays out of `lcmf.py` (683 lines, owns vertical launch) and `shot.py` (owns the ball) because club path has its own gates, failure modes, and confidence.

Two targeted improvements to existing code, both non-breaking:

- **`tracking.py`** — `find_ball` hardcodes `BALL_GATES_M` and `SPEED_BOUNDS_MS` as module constants. Add them as keyword parameters defaulting to today's values so the club track reuses the RANSAC range-walk fitter instead of duplicating it. The function's docstring already notes that the club steals the track when gates are wrong, so the fitter demonstrably finds clubs; it needs pointing at one deliberately.
- **`doa.py`** — the per-`(frame, loop)` TX2 phase construction currently lives inside `lcmf._tx2_horizontal_proxy`. Extract it as a shared helper used by both the ball's horizontal proxy and the club estimator, so the TDM correction and per-RX circular median exist once.

`club.py` depends on `tracking`, `doa`, and `dump`. It does **not** depend on `lcmf`, keeping the two estimators independent.

### Data flow

```
capture.raw ──► process_dump()        ──► ball track      (existing)
            ├─► estimate_lcmf_v1()    ──► launch angles   (existing)
            └─► estimate_club_path()  ──► ClubPathResult  (new)
                      │
                runtime.process_shot() → IWR6843ShotResult(capture, measurement, club_path)
                      │
                server.py → shot.club_path_deg (gated on confidence)
                      │
                ├─ session log: iwr6843_capture.club_path
                ├─ WebSocket: club_path_deg          (field exists)
                └─ GSPro: Path                        (already mapped)
```

`runtime.py` is the seam: `IWR6843ShotResult` gains a `club_path` field and `process_shot` calls the new estimator alongside the existing one. No changes to the capture monitor.

## Components

### `club.py`

```
CLUB_SEARCH_HALF_WIDTH_M = 0.6           # window around the tee
CLUB_SPEED_BOUNDS_MS = (10.0, 45.0)      # radial; 66-79 mph clubs read ~22 m/s
CLUB_MIN_FRAMES = 4
CLUB_MIN_SNAPSHOTS = 24
CLUB_MAX_AZIMUTH_FIT_RESIDUAL_DEG = 0.5  # provisional, see below
CLUB_SPEED_PROJECTION_RANGE = (0.4, 0.95)

@dataclass ClubPathResult:
    status, path_deg, confidence,
    azimuth_rate_dps, range_rate_ms, club_range_m,
    n_frames, n_snapshots, fit_residual_deg,
    track_rms_bins, track_inliers, track_span_s

find_club(mti, geo, *, tee_range_m) -> BallTrack | None
estimate_club_path(raw, cal, *, ops_club_speed_mph, aim_offset_deg,
                   tdm_sign) -> ClubPathResult
```

Pipeline: parse and project the dump → MTI → `find_club` over the pre-impact window → per `(frame, loop)` TX2 phase from the shared `doa` helper → line fit of azimuth versus time → combine with the track's range rate → apply the aim offset and TrackMan sign.

**Club-speed gate as a projection window, not a tolerance.** The club track measures *radial* speed, which is the true club speed times an unknown projection factor. The observed 22 m/s against a 66 mph (29.5 m/s) club gives a factor of ~0.66. The gate therefore requires the track's radial speed to fall between 0.4× and 0.95× the OPS club speed in m/s, rather than within a symmetric tolerance of it. A symmetric tolerance would either reject every valid shot or admit anything.

**`CLUB_MAX_AZIMUTH_FIT_RESIDUAL_DEG` is provisional at 0.5°.** It cannot be derived analytically. Implementation sets it from the residual distribution observed across the 15 local captures plus the synthetic tests' noise floor, and the chosen value is recorded in the implementation plan. Until then 0.5° is a starting point, not a validated threshold.

**Sign conventions, stated explicitly because they are the easiest thing to get backwards:**

- `path_deg` follows TrackMan for a **right-handed** golfer: positive is in-to-out (club travelling rightward relative to the target line), negative is out-to-in. Left-handed operation is not handled; the existing `club_path_deg` contract has the same limitation and changing it is out of scope.
- `aim_offset_deg` is **added** to the measured path. A positive offset means the radar's boresight points to the *right* of the target line. It is exposed as `--iwr6843-azimuth-offset-deg`, following the existing `--kld7-angle-offset` precedent, and defaults to 0 with a log note that the reported path is then relative to boresight rather than the target line.

**`tdm_sign` source.** `estimate_club_path` does not resolve the TDM sign itself. The caller passes it: `runtime.process_shot` forwards `measurement.tdm_sign_used` from the ball estimate when one exists, and otherwise falls back to the configured `tdm_sign_policy` (`"positive"` in production). A club path computed on the fallback is still emitted but its status records `tdm_sign_fallback` so replay can tell the two cases apart.

### Fix 1 — robust channel selection (`lcmf.py`)

Replace `raw_angle_deg = float(np.mean(component_values))` with a robust combine: when the channel spread exceeds `CHANNEL_SPREAD_MAX_DEG = 8.0`, keep the channel with better own-fit evidence and multiply confidence by `SINGLE_CHANNEL_CONFIDENCE_FACTOR = 0.7`; otherwise take the mean as today.

The 8.0° threshold is set from the captured data rather than picked: the one shot whose channels agree has a spread of 4.59° (26.62 vs 22.03), while the collapsed shots span 15.9–20.2°. Nothing observed falls between, so the boundary sits in a genuine gap. The 0.7 derate is a policy choice, not a measurement — a single-channel estimate has no cross-check, and 0.7 keeps it above the fallback-estimate confidence of 0.5 while marking it as weaker than a corroborated one.

Shot 1 becomes 19.25° instead of 8.79°. The reference rig's behaviour at 10.4° tilt is unchanged because its channels agree. No channel model internals change.

### Fix 2 — span floor (`shot.py`)

`REJECT_MIN_SPAN_S = 0.015`. Ships with Fix 1, never alone.

### Fix 3 — derived confidence (`server.py`)

Vertical confidence derives from channel agreement and evidence count; horizontal confidence derives from HLCMF-v0 coherence. `spin_axis_deg` is emitted only when both legs clear their thresholds, rather than appearing the moment `club_path_deg` becomes non-null.

## Non-Goals

- Changing the HLCMF-v0 horizontal algorithm. Fix 3 only stops its coherence being discarded.
- Changing the `two8` channel model, including adding the missing TDM cross-phase. Fix 1 routes around it. If the cross-phase is the root cause, that is a separate change owned by the estimator's author, with its own TrackMan validation.
- Attack angle (vertical club path). Shares the pre-impact dependency and is a natural follow-on, but out of scope here.
- Any firmware change. The required data is already captured.

## Failure Modes

Club path is an independent estimator. Every failure leaves `club_path_deg` at `None` and does not affect ball speed, club speed, spin, or launch angle.

| Status | Cause |
|---|---|
| `rejected_no_club_track` | no mover in the tee window passes the club gates |
| `rejected_club_speed_mismatch` | track radial speed inconsistent with OPS club speed |
| `rejected_insufficient_snapshots` | fewer than `CLUB_MIN_FRAMES` / `CLUB_MIN_SNAPSHOTS` survive |
| `rejected_azimuth_fit` | azimuth-rate fit residual above threshold |
| `rejected_phase_wrap` | \|Δφ\| across the window exceeds π/2; the true change is ~0.04 rad, so anything near a wrap is a bad track |
| `rejected_no_pre_impact_frames` | `trigger_frame` leaves no pre-impact window |

## Logging

`iwr6843_capture` gains a `club_path` block carrying every `ClubPathResult` field.

The ball measurement dict gains **`track_span_s`**. Track span is the gate that rejected 7 of 8 shots in session `130951` and 1 of 7 in `140533`, and it is currently invisible without an offline replay. Any threshold that rejects a value must record the value it rejected.

## Testing

Synthetic-first, following `test_iwr6843_pipeline.py`, which packs a known target into the real wire format via `pack_dump`. Real captures cannot be committed: `.gitignore` excludes `session_logs/*/` and `session_logs/session_*.jsonl`.

- **Club path math** — synthesise a club with known azimuth and range rates; assert the recovered path within tolerance. Golden cases: pure radial → 0°, known lateral → known in-to-out, mirrored → out-to-in with the sign flipped. This test pins the sign convention.
- **Robust channel selection** — table-driven: spread above threshold drops the outlier, agreement takes the mean, single-channel derates confidence. Includes the exact `two8 = −1.66 / four4 = 19.25` case asserting 19.25°.
- **Span gate boundary** — 14.9 ms rejected, 15.1 ms accepted.
- **Confidence derivation** — low horizontal coherence yields low confidence; a regression test asserts `0.95` never appears as a literal confidence for either angle.
- **Spin axis gating** — emitted only when both legs pass; absent when either fails.
- **Failure-mode coverage** — one test per status in the table above.
- **Real-dump regression, opt-in** — `skipif`-guarded over `session_logs/iwr/`, asserting shot 1 → 19.25° and that the five collapsed shots report their four-path value. Skips on CI, runs locally.

## Validation

Club path has no ground truth at the operator's range, so acceptance is a **directional separation test** rather than an accuracy figure — the method PR #155 used for its horizontal launch proxy.

Three sessions of 10 shots each: deliberate in-to-out, square, deliberate out-to-in. The session boundary is the label, so no live intent-tagging is required.

`scripts/iwr6843/club_path_report.py` takes the three session JSONLs and reports group means, within-group spread, and whether the ordering separates.

**Pass criteria:** group means ordered correctly (out-to-in < square < in-to-out), and the gap between adjacent group means exceeding the within-group spread.

The feature ships marked experimental, reporting a separation result rather than an accuracy claim, until a TrackMan session can score it.

## Risks

- **The pre-impact mover may not be the club head.** Mitigated by the OPS club-speed cross-check as an acceptance gate rather than an assumption. If the mover turns out to be hands or body, the club-speed gate rejects and club path stays `None` — the failure is visible, not silent.
- **±1–2° precision may be too coarse even for separation** if the operator's deliberate in-to-out and out-to-in swings differ by less than a few degrees. The separation test detects this directly.
- **The aim offset is a new manual measurement.** A wrong offset biases every club path by a constant. It is recorded in the session config so a bad value can be corrected in replay rather than requiring a re-shoot.
