---
icon: lucide/move-diagonal
---

# Horizontal Launch and Club Path

The horizontal plane: where the ball started and where the club was going. Both
come from the same pre-impact frames, and both depend on a correctly set
target-line reference.

## Horizontal Ball Launch

TX2 measures horizontal ball launch from phase relative to the TX1/TX3 phase
center. The IWR6843LEVM TX2 baseline is one-half wavelength; OpenFlight applies
that board geometry and uses the OPS ball speed when removing TDM motion phase.
This keeps multipath in the TI range slope from rotating the horizontal result.

Each physical board and enclosure can retain a static target-line phase. Pass
the phase measured by horizontal aim calibration when starting OpenFlight:

```bash
scripts/start-kiosk.sh --iwr6843 \
  --iwr6843-horizontal-phase-reference-rad -0.33434
```

`-0.33434 rad` is the measured reference for the OpenFlight prototype setup,
not a universal default. It was calibrated from 18 wide-IQ16 shots against
TrackMan using the eight-frame horizontal estimator, then frozen before a
separate 41-shot holdout. The holdout produced 0.948 degrees MAE at full
coverage and 0.797 degrees MAE on the 37 shots that passed the 0.90 phase-
coherence gate.

Recalibrate this value after changing the radar board, enclosure, antenna
orientation, or target-line alignment. To evaluate a TrackMan-aligned session
and calculate a setup-specific phase reference, run:

```bash
uv run python scripts/analysis/evaluate_iwr_horizontal_models.py \
  path/to/trackman_openflight_aligned.csv
```

The value is subtracted in phase space before conversion to degrees. Omitting
it reports horizontal launch relative to the board's uncalibrated RF phase
zero, which can create a consistent left/right bias even when the enclosure is
aimed correctly. This is separate from `--iwr6843-azimuth-offset-deg`, which is
the geometric target-line correction used by the experimental club-path fit.

## Club Path

Club path is the horizontal direction the club head travels through impact,
measured from the six pre-impact frames the firmware retains. Positive is
in-to-out, negative out-to-in, following TrackMan. **Right-handed only.**

Measured on a first-principles fixture across ±12°, absolute error grows with
angle rather than staying flat: 0.034° at 4°, 0.092° at 8°, and 0.297° at
12°, worst at the largest angle measured (0.303° at −12°). The ±12° pair
(0.297° vs. 0.303°) differs by only about 0.006°, so the residual is
symmetric rather than sign-dependent. It is not, however, a fixed percentage
of the angle — 0.034° at 4° is under 1%, while 0.297° at 12° is about
2.5% — so quote it as ±0.3° working precision across the measured ±12° range
rather than a percentage. Two explanations for the growth are on the table
and neither is settled: it may be discretisation (range-bin quantisation,
the tracker's 1.2-bin inlier tolerance, phase inversion at small angles), or
it may be structural — the club's range comes from a *linear* fit against a
genuinely nonlinear true range, which fits the observed growth pattern
better. A future improvement may reduce the residual either way. It ships
experimental.

Do not extrapolate ±0.3° beyond ±12°: an out-of-band check during
development read about 0.57° at ±16°, roughly double the 12° figure and
consistent with the growth trend. That figure is indicative only — it is not
covered by a test — but a strong out-to-in path can exceed 12°, and an
operator trusting ±0.3° there would be relying on a number nobody measured.

### Set the target-line reference

Club path is relative to the target line, and the array calibration cannot
supply where the radar's boresight points. Measure the angle between boresight
and your target line, then pass it:

```bash
scripts/start-kiosk.sh --iwr6843 --iwr6843-azimuth-offset-deg 1.5
```

Positive means boresight points right of the target line. The value is added
to the measured path. Left at 0, club path is reported relative to boresight
rather than the target line, which is fine for separation testing but not for
absolute numbers.

This flag is not optional trim. The estimator fits `x(t)` and `y(t)` in
Cartesian coordinates and reports `path = atan2(v_y, v_x)`, so absolute
azimuth enters *additively* rather than cancelling out: a constant
per-element phase error from the shipped array calibration (measured on a
different board than the one it ships on) shifts the reported path by a
constant. `--iwr6843-azimuth-offset-deg` is what absorbs that shift, not a
convenience for aiming.

### Why this can't be the ball

Two independent checks keep a mis-tracked ball from being reported as club
path, and both were verified. First, the pre-impact time window excludes the
ball's flight — the club and ball share overlapping radial speed ranges, so
without the window the tracker would lock onto the ball. Second, even inside
that window the OPS club-speed cross-check rejects a ball track: a ball's
radial speed against club-speed bounds produces a projection factor of about
1.26, which falls outside the accepted 0.4–0.95 window.

### Validate with a separation test

Record three sessions of about 10 shots each, then compare them:

```bash
uv run python scripts/iwr6843/club_path_report.py \
  --out-to-in session_A.jsonl \
  --square     session_B.jsonl \
  --in-to-out  session_C.jsonl
```

It passes when the group means are ordered correctly and each adjacent gap
exceeds the within-group spread. Exit status is non-zero when they do not
separate.

A session can fail for three distinct reasons, and the report names which one
fired because each needs a different fix from the operator:

- **Groups out of order** — the swings themselves may not have been distinct
  enough; swing more deliberately between blocks.
- **A group has fewer than 5 accepted shots** — below that, a group's own
  stdev is too noisy to support a separation claim; hit more shots.
- **A gap falls below the 0.3° measurement floor** — grounded in the 0.303°
  max residual measured on the fixture above; a gap this small is below
  instrument precision at any sample size, so more shots won't fix it —
  re-check the geometry (mount, tee range, azimuth offset) instead.

The 0.3° floor sits at the measured residual, not comfortably above it —
for the intended use (in-to-out vs. out-to-in, which typically differ by
8–15°) that's irrelevant, but an operator chasing a marginal ~0.4° group
separation should know the floor isn't a wide safety margin.

### Reading `club_path.status` in the session log

| Status | What it means |
|---|---|
| `accepted` | Club path measured; `confidence` reflects fit quality |
| `rejected_requires_three_tx` | Capture wasn't a 3-TX dump; TX2 phase needs all three transmitters |
| `rejected_no_pre_impact_frames` | The capture contains no pre-impact window |
| `rejected_no_club_track` | No mover near the tee passed the club gates — check the tee range and that the radar sees the hitting area |
| `rejected_club_speed_mismatch` | The tracked mover's speed does not match the OPS club speed, so it is probably hands or body, not the club |
| `rejected_insufficient_snapshots` | Too few usable pre-impact samples |
| `rejected_azimuth_fit` | The azimuth track was too noisy to fit |
| `rejected_phase_wrap` | Phase swing far larger than physically possible; a broken track |
| `..._tdm_sign_fallback` suffix | The ball estimate was rejected, so the TDM sign came from the configured policy rather than measurement |

