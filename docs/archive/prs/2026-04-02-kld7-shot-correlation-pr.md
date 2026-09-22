# Improve K-LD7 Shot Correlation And False-Positive Filtering

!!! warning "ARCHIVED DOCUMENT"

    This is a historical design or implementation note, kept as a record of why
    the code is shaped the way it is. It describes the project as of the date in
    its filename and **is not a guide to follow** — commands, paths, and
    constants may no longer match the code. See the
    [Archive index](../index.md) for current alternatives.

## Summary

This change set does three things:

1. Makes K-LD7 ball detection reproducible on the provided `.pkl` captures
2. Improves live K-LD7 correlation by anchoring it to the OPS243 impact timestamp
3. Adds a conservative club-and-speed sanity guard so obviously implausible radar launch angles do not override the existing estimate

## Problem

The raw K-LD7 data does contain the golf ball, but the ball return is brief and
easy to confuse with club, body, net, and multipath reflections.

The two failure modes that showed up in the repo data were:

- **Timing mismatch in the live path**: K-LD7 burst selection used callback wall clock instead of the OPS243 impact timestamp
- **False-positive angle selection**: some far-range bursts produce launch angles that are physically implausible for the selected club and measured ball speed

## Theory

The key insight from the April 2 captures is that ball detection works better as
a **sequence problem** than a **strongest-target problem**.

The repeatable pattern is:

1. A close-range club transition at roughly `0.8-2.5 m`
2. Followed `120-350 ms` later by a far-range burst at roughly `4.1-4.6 m`
3. The ball burst may contain multiple far PDAT targets, so one coherent path must be chosen inside the burst

That gets us much closer to the correct burst, but it still does not guarantee
that the selected burst angle is physically reasonable.

The next theory was therefore:

> If a radar launch angle strongly disagrees with the selected club and OPS243 ball speed, it should be treated as a likely false positive and should not override the existing launch estimate.

## What Changed

### 1. K-LD7 tracker improvements

- Added coherent far-target selection inside a burst instead of averaging all far PDAT detections together
- Added offline `find_probable_shots()` pairing logic for club-transition to ball-burst sequences
- Added reproducible capture analysis through `scripts/analyze_kld7.py --pair-shots`

### 2. Live timestamp fix

- `Shot` now carries the OPS243 impact timestamp
- K-LD7 correlation uses that impact timestamp instead of delayed callback time

### 3. False-positive guard

- Added a wide club-family-based sanity check for K-LD7 vertical launch angles
- Expected launch comes from the existing club-and-speed model
- Implausible K-LD7 vertical angles are rejected
- When rejected, the shot falls back to the existing estimated launch angle instead of shipping the radar spike as truth

## Evidence

### Test suite

- `PYTHONPATH=src .venv/bin/pytest -q`

Result:

- `256 passed, 2 skipped`

### Real session audit

From `session_logs/session_20260402_121507_range.jsonl`:

- `11` driver shots were audited against the club-and-speed model
- `3` shots are clear outliers: `3`, `9`, `11`
- Those outliers differ from the expected launch by about `23-30°`

This gives a real backyard validation set for the false-positive guard.

### Provided K-LD7 captures

Running:

```bash
PYTHONPATH=src .venv/bin/python scripts/analyze_kld7.py <capture.pkl> --pair-shots
```

currently finds:

- `4` probable shots in `kld7_capture_20260402_134323-wedge.pkl`
- `5` probable shots in `kld7_capture_20260402_135117-wedge.pkl`
- `5` probable shots in `kld7_capture_20260402_135243-7i.pkl`
- `6` probable shots in `kld7_capture_20260402_135412-7i.pkl`

The broad club-family audit flags:

- the two `~63°` wedge candidates in `kld7_capture_20260402_135117-wedge.pkl`
- the `79.4°` 7-iron candidate in `kld7_capture_20260402_135412-7i.pkl`

while still keeping more moderate angles such as the `40.3°` 7-iron candidate
in the plausible bucket for now.

## Why This Is A Good Next Step

This guard is intentionally conservative.

It does **not** try to perfectly classify every shot from only four captures.
It does something simpler and more valuable:

- prevents obviously bad radar spikes from becoming the final launch number
- preserves plausible radar measurements
- keeps the existing estimate path as a safe fallback
- leaves room for future improvements such as camera cross-checks and per-club priors

## Remaining Work

The biggest remaining opportunity is to replace this broad guardrail with
stronger evidence:

1. More labeled K-LD7 captures with known clubs and shot counts
2. Camera-derived launch angle on the same shots
3. Logging the chosen coherent radar path for each accepted or rejected burst

That would allow the next iteration to move from **wide guardrails** to **real calibration**.
