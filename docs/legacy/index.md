# K-LD7 Launch Angle & Direction

!!! warning "DEPRECATED HARDWARE"

    The K-LD7 angle radars are deprecated. The supported angle radar is the
    **TI IWR6843** — see the [IWR6843 operator guide](../iwr6843/index.md).
    Do not buy K-LD7s for a new build; this page is kept for existing builds
    and for reading historical session logs.

The K-LD7 radars measure the ball's **launch angle** (vertical) and
**launch direction** (horizontal aim) to complement the OPS243's ball
speed. Two modules are used: one mounted to look in the vertical plane,
one in the horizontal plane. They are enabled together with a single
flag.

- **Launch angle (vertical)** — extracted with the **two-ray**
  estimator, which models the indoor ground-bounce multipath directly.
  Indoor accuracy is ~2–3° on irons and wedges.
- **Launch direction (horizontal)** — uses the legacy estimator (see
  [Horizontal / aim](#horizontal-aim) below).
- Both axes are filtered by the OPS243 ball speed and correlated to the
  shot via the OPS impact timestamp.

For how the estimator works internally, see
[Launch angle explained](launch-angle-explained.md) and
[kld7-ball-detection-theory.md](ball-detection-theory.md).

## Enabling it

```bash
scripts/start-kiosk.sh --kld7 --kld7-mount-tilt <degrees>
```

`--kld7` turns on both radars (vertical + horizontal auto-detected) and,
with them, the two-ray launch-angle estimator and the ball-speed cosine
correction. The only value you must supply is the **mount tilt** — the
rest have sensible defaults you can override.

## Measuring the rig

The estimator is geometry-sensitive: a wrong physical parameter shows up
directly as a launch-angle error. Measure these once whenever the rig
moves and pass them on the command line.

| Parameter | Flag | Default | How to measure | Sensitivity |
|---|---|---|---|---|
| **Mount tilt** | `--kld7-mount-tilt` | **required** | Phone inclinometer app against the radar face | Offsets launch angle ~1:1 — the most important number, which is why there is no default |
| Radar height | `--kld7-radar-height-inches` | 4.0 | Center of the radar above **the surface the ball sits on** (the mat top, *not* the floor) | ~0.7–0.8° per inch |
| Radar-to-ball distance | `--kld7-ball-distance` | 5.0 ft | Tape from the radar face to the tee | ~1–1.5° per half-foot (partly self-corrected by the range clock) |
| Boresight offset | `--kld7-angle-offset` | 1.5 | Not user-measurable — requires a corner reflector against a truth source. Leave at the default unless you have calibrated your own mount. | Constant bias on launch angle |

> **Why mount tilt is required but boresight offset is not:** tilt varies
> with every setup and is easy to measure with a phone, so a stale default
> would silently corrupt results. Boresight offset needs lab calibration
> (corner reflector + a truth launch monitor); `1.5°` is our
> corner-reflector-derived value and the right default for the standard
> mount.

## Other flags

| Flag | Purpose |
|---|---|
| `--kld7-port` / `--kld7-horizontal-port` | Override serial port autodetection |
| `--kld7-horizontal-offset` | Boresight offset for the horizontal radar |
| `--kld7-vertical-raw` | Emit the raw vertical estimator output with **all display gating bypassed** (plausibility, lane, and confidence guards). For debugging/validation only. |
| `--calculated-spin` | Off by default. Replaces radar spin with the kinematic estimate (`170·v·sin(LA)^1.2`). Opt-in; see the spin notes. |

## Horizontal / aim

`--kld7` also enables the **horizontal** radar for launch direction, but
that axis still uses the **legacy** estimator — the two-ray upgrade
applies to the vertical (launch-angle) axis only. The horizontal radar
runs with its defaults; `--kld7-horizontal-offset` sets its boresight and
`--kld7-horizontal-port` overrides the serial port. Launch direction is
therefore lower-fidelity than launch angle and is not gated or corrected
by the two-ray pipeline.

## Troubleshooting

See [kld7-troubleshooting.md](troubleshooting.md) for detection and
serial issues.
