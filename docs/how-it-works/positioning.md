---
icon: lucide/map-pin
---

# Radar Positioning

The two radars have different placement requirements. They are not
interchangeable and they do not share a mount.

## OPS243-A

Place it **3–5 feet behind the tee**, pointing down the target line.

```
                Ball flight direction
                ======================>

  [Tee]  ←--- 3-5 ft ---→  [OPS243-A]
```

Doppler measures the velocity component **along the beam**. Pointing down the
target line means the ball's flight is nearly parallel to the beam, so the
cosine error is small. Off-axis placement under-reads speed by $\cos\theta$.

The tolerance here is forgiving — a few degrees of misalignment costs a fraction
of a percent. That is why the OPS243 has no calibration step while the IWR6843
has several.

## IWR6843

**Do not assume it shares the OPS243 position.** It has stricter mounting and
measurement requirements, and its geometry values are runtime inputs rather than
approximations.

You must measure and supply:

| Value | Flag |
| --- | --- |
| Antenna-centre to tee slant range | `--iwr6843-tee-m` |
| Antenna-centre to net range | `--iwr6843-net-m` |
| Ball-centre height above the mat | `--iwr6843-ball-height-m` |
| Mount tilt | from the calibration JSON, or `--iwr6843-tilt-deg` |
| Antenna-centre height | from the calibration JSON, or `--iwr6843-radar-height-m` |

Full procedure: **[mounting, aiming, and measuring](../iwr6843/mounting.md)**.

!!! warning "Wrong geometry fails silently"

    A bad tee distance or tilt does not produce an error — it produces a
    plausible but wrong launch angle. Measure rather than estimate, and re-measure
    after moving or bumping the unit.

    Fitting the [LIS3DH inclinometer](../build/inclinometer.md) makes tilt
    self-correcting, which is the main reason to bother with it.

## Target line reference

Club path and launch direction are reported relative to a target line, which the
system cannot infer. Two flags set the reference:

- `--iwr6843-azimuth-offset-deg` — azimuth of the radar boresight relative to
  the target line. Positive means boresight points right of the target line.
  With `0`, club path is reported relative to boresight rather than the target.
- `--iwr6843-horizontal-phase-reference-rad` — the static target-line phase from
  horizontal aim calibration.

See [horizontal launch and club path](../iwr6843/club-path.md#set-the-target-line-reference).

## Indoor considerations

Indoors, the floor produces a strong ground-bounce return that arrives just
after the direct path. The launch-angle estimator models this multipath
explicitly rather than trying to filter it out — which is why the net distance
matters as an input, not just as a safety consideration.

Nets beyond the roughly 11-foot FSK range wrap need de-aliasing; far-flight
frames are unwrapped and kept rather than dropped. Nets at or inside the wrap
are unaffected.

## Related

- [Mounting and geometry](../iwr6843/mounting.md)
- [Parts list](../get-started/parts.md) — mounting hardware
- [Enclosure & case](../build/enclosure.md) — the printed housing
