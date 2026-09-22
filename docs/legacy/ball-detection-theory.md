# K-LD7 Ball Detection Theory

!!! warning "DEPRECATED HARDWARE"

    The K-LD7 angle radars are deprecated. The supported angle radar is the
    **TI IWR6843** — see the [IWR6843 operator guide](../iwr6843/index.md).
    Do not buy K-LD7s for a new build; this page is kept for existing builds
    and for reading historical session logs.

> **⚠️ DEPRECATED:** The K-LD7 angle radars are deprecated — OpenFlight has moved to a more capable radar chip for angle measurement. This document is kept for existing K-LD7 builds only.

## Problem

The RFbeam K-LD7 is being used to estimate launch angle and club angle of attack
from raw `PDAT` and `TDAT` frames captured during a golf swing. The hard part is
not whether the radar sees *anything* during a swing. It does. The hard part is
separating the brief golf-ball return from stronger club, body, and net returns.

The four captures in `session_logs/` confirmed that the ball data is present, but
it does not appear as the strongest target in the raw stream. A simple
"highest-magnitude target wins" rule is wrong for this use case.

## Why The Raw Radar Data Is Tricky

### 1. Speed aliases at golf-ball velocity

The K-LD7 max speed setting is `100 km/h`. Golf balls travel much faster than
that, so the reported speed is aliased and cannot be trusted as a true physical
ball speed. This means the detector must treat speed as a *weak filter* rather
than a direct measurement.

Practical implication:

- `speed` can be used to reject obviously slow clutter
- `speed` cannot be used to estimate actual launch speed
- `distance` and `timing` matter more than `speed`

### 2. The ball only exists for a few frames

At the current radar settings the frame rate is roughly `34 fps`, so a full-speed
ball often appears in only `1-3` frames. By contrast, the golfer's body, club,
and follow-through can occupy many more frames and often produce stronger returns.

Practical implication:

- `TDAT` alone is not enough
- `PDAT` is the primary source for the ball
- the detector must be built around short bursts, not long tracks

### 3. A single "ball burst" can still contain multiple far targets

Even after filtering for far-range, fast targets, a burst can still contain
multiple plausible `PDAT` returns in the same frame. These likely come from a
mix of ball, net, multipath, and other transient reflections. Averaging every
qualifying far target together smears the angle and produces unstable output.

Practical implication:

- burst selection is not enough
- one coherent target path must be chosen *inside* the burst

## What The Captures Showed

Across the four captures, the most repeatable signature was:

1. A close-range speed transition at roughly `0.8-2.5 m`
2. Followed `120-350 ms` later by a far-range burst at roughly `4.1-4.6 m`
3. The far-range burst usually carried lower magnitude than the club event

This pattern is the key breakthrough. The ball should not be found by asking
"what is strongest in the whole buffer?" It should be found by asking:

> "What plausible far-range burst appears immediately after a plausible club event?"

That turns the detection problem from one giant classification problem into a
sequence problem.

## Detector Strategy

The current detector is built around four stages.

### Stage 1: Ball candidate filter

A target is considered a possible ball only if it is:

- fast enough to reject slow clutter
- far enough away to avoid close-range club/body returns
- strong enough to avoid obvious noise

In code this is the far-range, fast-target filter in
`src/openflight/kld7/tracker.py`.

### Stage 2: Burst formation

Qualifying ball targets are grouped into bursts when consecutive frames are close
enough in time. This reflects the physical reality that a struck ball should
appear as a brief transient event, not a long-lived track.

### Stage 3: Coherent target selection inside the burst

This is the major correction to the original approach.

Instead of magnitude-weighting *every* far target in the burst, the detector now
scores target-to-target continuity across frames:

- small angle jumps are preferred
- small distance jumps are preferred
- higher magnitude still helps, but only after continuity

That produces a single coherent far-target path. The launch angle is then
computed from that path, not from the union of all far targets.

This matters because many noisy bursts contained both reasonable and obviously
bad far-angle candidates. A plain weighted average would blend them together.

### Stage 4: Club-to-ball pairing

For offline analysis, the tracker now pairs:

- a close-range club transition
- with the best far-range burst that appears shortly after it

The pairing window is currently:

- minimum delay: `80 ms`
- maximum delay: `350 ms`

That window came from the real captures, not from arbitrary tuning.

## Why The Live Path Needed A Timestamp Fix

The K-LD7 selector used to receive `time.time()` when the server callback fired.
That callback runs *after* the OPS243 shot has already been buffered and processed,
which means the timestamp can lag the true impact moment.

This made burst selection weaker than it needed to be. If the radar is choosing
the burst closest to a delayed callback time, it may bias toward the wrong burst
inside a busy ring buffer.

The live path now carries the OPS243 peak ball-reading timestamp through the
`Shot` object and uses that impact timestamp when correlating K-LD7 data.

## What This Solves

This work materially improves three things:

### 1. Reproducibility

The repo now has an offline command:

```bash
PYTHONPATH=src uv run python scripts/analysis/analyze_kld7.py \
  <capture.pkl> --pair-shots
```

That prints probable club-to-ball pairs from a long capture. This turns the
provided `.pkl` files into a repeatable analysis workflow.

### 2. Angle stability

The detector no longer collapses multiple far targets into one weighted average.
It picks a coherent path first, which reduces angle smearing on noisy bursts.

### 3. Better live correlation

K-LD7 is now aligned to the real OPS243 impact time rather than delayed callback
time, which is a better proxy for when the ball should appear in the K-LD7 buffer.

## What This Does Not Solve Yet

This is a strong improvement, but it is not a magic classifier.

Open problems:

- Some bursts still produce high-angle outliers
- Multipath and net reflections still exist in the far range
- The K-LD7 still provides only a handful of frames per shot
- Reported `speed` is still aliased and should not be treated as truth

The detector now exposes these cases more honestly through lower confidence or
through unusual paired angles, rather than hiding them behind a bad average.

## Practical Next Steps

The next highest-leverage improvements are:

1. Collect more labeled captures with known shot counts and club types
2. Add per-club angle priors so obviously impossible candidates are penalized
3. Compare radar-derived launch angle against camera-derived angle on the same shots
4. Log the chosen coherent target path for each shot, not just the final summary

## Evidence From The Current Captures

The strongest labeled validation so far is:

- `session_logs/kld7_capture_20260402_135117-wedge.pkl`
- metadata says `expected_shots = 5`
- the current `find_probable_shots()` heuristic returns `5`

That does not prove the angles are perfect, but it is a strong sign that the
sequence logic is isolating real shot events rather than random clutter.

## Capture Snapshot

Running the offline analyzer with:

```bash
PYTHONPATH=src uv run python scripts/analysis/analyze_kld7.py \
  <capture.pkl> --pair-shots
```

currently produces:

| Capture | Club label | Expected shots | Probable shots found | Notes |
| --- | --- | ---: | ---: | --- |
| `kld7_capture_20260402_134323-wedge.pkl` | unlabeled | unknown | 4 | All pairings follow the `283-346 ms` club-to-ball pattern |
| `kld7_capture_20260402_135117-wedge.pkl` | `wedge` | 5 | 5 | Best current regression file; includes two higher-angle far-burst outliers |
| `kld7_capture_20260402_135243-7i.pkl` | unlabeled | unknown | 5 | Cleanest mixed set of moderate-confidence 7-iron candidates |
| `kld7_capture_20260402_135412-7i.pkl` | unlabeled | unknown | 6 | Strongest confidence scores overall, plus one obvious high-angle outlier |

This gives two useful conclusions:

1. The detector is consistently finding shot-like club-to-ball sequences across
   all four recordings, not just in one cherry-picked file.
2. The remaining errors are now visible as specific outlier pairings, which is
   much easier to improve than a detector that hides bad returns inside a broad
   weighted average.

## Remaining Failure Mode

The dominant remaining failure mode is not missing shots. It is selecting a
real far-range burst whose angle is physically implausible for the club or
range setup.

Examples from the current captures include:

- `63.2°` and `63.1°` candidates in `kld7_capture_20260402_135117-wedge.pkl`
- `79.4°` in `kld7_capture_20260402_135412-7i.pkl`

Those detections are useful because they point to the next filter to add:
keep the sequence logic, then penalize far-burst paths whose launch angle is
incompatible with the expected club window or with a camera-derived reference.

## Live False-Positive Guard

The live path now applies a wide club-and-speed sanity check before trusting a
K-LD7 vertical launch angle.

The logic is intentionally conservative:

- compute the expected launch angle from the selected club and OPS243 ball speed
- allow a wide club-family-specific error window
- reject only radar angles that are far outside that window
- fall back to the existing club-and-speed estimate when the radar angle is not plausible

This is not a precision tuning mechanism. It is a guardrail against obvious
false positives.

## Why This Guard Is Defensible

The repo already contains a real backyard session log with shot speed and launch
data:

- `session_logs/session_20260402_121507_range.jsonl`

Auditing the `11` driver shots in that log against the club-and-speed launch
model shows:

- `8` shots are already within the expected guardrail
- `3` shots are clear outliers: shots `3`, `9`, and `11`

Those outliers are not minor disagreements. Their launch angles are roughly
`23-30°` away from what the selected club and ball speed predict.

That is exactly the kind of error this guard is meant to catch.

## What The Guard Catches In The Current Captures

Using broad club-family priors on the provided K-LD7 captures:

- `kld7_capture_20260402_135117-wedge.pkl` flags the two `~63°` wedge candidates as suspect
- `kld7_capture_20260402_135412-7i.pkl` flags the `79.4°` 7-iron candidate as suspect
- moderate but still plausible shots such as `40.3°` on one 7-iron capture remain accepted

That is the desired behavior for this stage:

- reject the obvious false positives
- keep the plausible-but-imperfect shots
- avoid overfitting to these four files

## Future angle-extraction improvements (literature backed)

The K-LD7 has only **2 receive antennas per axis**. Phase-comparison
monopulse on a 2-element array is the absolute floor of array-radar
DOA estimation: every multi-element technique (MUSIC, ESPRIT,
beam-space MUSIC, Capon, beamforming) requires ≥3 elements. That is
why TrackMan and FlightScope ship 16-26-element phased arrays. With
2 channels we are fundamentally limited to one unambiguous angle per
range/Doppler bin, and our angle accuracy is bounded by the
Cramér-Rao lower bound, which scales as `1 / (SNR · √N_snapshots)`.

A literature-backed list of improvements that are still possible
inside the 2-channel envelope, in roughly increasing implementation
cost:

### Done — multi-bin centroid angle (Zhang et al., Sensors 2016)

The per-frame angle is the magnitude²-weighted centroid of the
per-bin angles inside the spectral peak (bins above
`centroid_floor_frac × peak`, within `CENTROID_SEARCH_BINS` of the
peak bin), rather than the raw angle at a single peak bin. For a
range-spread target whose energy spreads across several FFT bins
(Hann-window leakage and intra-frame Doppler smear), this integrates
the angle estimate across all the energy in the peak. Implemented
in `extract_launch_angle`.

Reference: Zhang, Y. et al. *A Novel Monopulse Angle Estimation
Method for Wideband LFM Radars*, Sensors 2016, 16(6):817 (PMC4934243).

### Future — use the F2 frequency channel (delta-frequency interferometry)

The K-LD7 RADC layout reserves channels for `f1a`, `f2a`, **`f1b`**.
We currently process only `f1a` and `f2a` (I/Q at the radar's first
carrier frequency). RFbeam's second carrier (`f1b`) is what enables
**target ranging** and **angle-disambiguation** through *delta-
frequency interferometry*: the phase difference at two slightly
different carriers gives an independent angle estimate via a
different baseline. Combining the two estimates is well-studied for
synthetic-aperture radar — see GAMMA Remote Sensing's processing
notes (`docs/refs/...` once we save them) and any standard
multi-frequency InSAR tutorial.

Cost: medium. We need to confirm the K-LD7 carrier spacing is
documented well enough to use, and we need to handle 2π
disambiguation between the two estimates.

### Future — joint Doppler-DOA maximum-likelihood estimation

Instead of `FFT → pick peak bin → read phase`, do a joint
maximum-likelihood search over `(velocity, angle)` directly on the
two-channel raw I/Q. The cost function is the negative log-likelihood
of the 2-channel signal given a complex sinusoid at angular velocity
`ω` with phase difference `φ`. For a 256-sample frame this is a tiny
2D grid search and is provably optimal at low SNR / few-snapshot
scenarios — exactly our regime.

Reference: Joint Doppler and DOA Estimation Using (Ultra-)Wideband
FMCW Signals, Signal Processing 165 (2019), 105–122.

Cost: high. New code path, careful initialisation to avoid local
minima, and we need to verify wall-clock cost on the Pi against the
real-time budget.

### Future — multi-frame phase tracking

When the ball is visible for 2-3 frames in a row, its angle should be
approximately constant (it's flying in a straight line over ~50 ms).
Add a constant-or-linear angle model fit across the cluster, with
SNR-weighted least squares. Reject frames whose estimate disagrees
by `>Nσ` from the joint fit. This is essentially what Kalman-filter-
based radar trackers do; in our setup it is a small change once we
have a working per-frame estimator we trust.

Cost: low, but only worth doing once the per-frame estimator is
solid. Currently the ball is only visible 1-2 frames per shot for
fast-ball captures, so there is rarely enough data to fit.
