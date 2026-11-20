---
icon: lucide/ruler
---

# Mount, Aim, and Measure

Where the radar sits and how it is angled determines every launch angle it
reports. Measure the geometry rather than estimating it — the numbers you
record here go straight into the runtime.

## Mount And Aim The Radar

Mount the radar behind the ball with the antenna face pointing down the target
line. The validated enclosure rotates the board so its vertical virtual array
is physically vertical, with the TX antennas above the RX antennas.

The IWR6843 can start at approximately the same upward tilt as the OPS243,
typically around 10 degrees, when both antenna faces are mounted parallel. Treat
10 degrees as a mounting starting point, not a universal calibration value.
Measure the IWR6843 antenna-face tilt independently and enter that measured
value in OpenFlight.

The validated rig had the IWR6843 antenna center approximately 6 inches
(`0.1524 m`) above the floor surface under the radar, in the old enclosure on a
stand. A unit standing on its feet in the current case sits much lower, about
`0.050 m` (see [Which point to measure from](#which-point-to-measure-from)).
Measure vertically from the floor to the center of the antenna array, not to
the enclosure bottom or mounting feet, and enter what you measure.

Mounting requirements:

- Aim the antenna face toward the intended start line, not diagonally across
  the hitting area.
- Keep the antenna face unobstructed.
- Keep the board rotation consistent with the validated enclosure.
- Use a rigid mount. Small mechanical shifts can appear as angle bias.
- Measure tilt against the antenna face or a known-parallel enclosure surface.
- Re-measure after moving to a different floor, mat, bay, or stand.

A corner reflector placed on the target line can verify horizontal aim. It is
useful for alignment and static health checks, but it does not replace moving
golf-ball validation.

## Measure The Geometry

OpenFlight needs these physical inputs:

| Argument | Measurement |
|---|---|
| `--iwr6843-tee-m` | Distance from the **antenna center** to the ball center, along the line the radar looks down (see [Which point to measure from](#which-point-to-measure-from)) |
| `--iwr6843-net-m` | Distance from the **antenna center** to the net or screen |
| `--iwr6843-tilt-deg` | Antenna-face mount tilt from an inclinometer |
| `--iwr6843-radar-height-m` | Antenna-center height above the floor reference (see [Which point to measure from](#which-point-to-measure-from)) |
| `--iwr6843-ball-height-m` | Ball-center height above the same floor reference |

!!! danger "Measure your own unit. The defaults are from the old enclosure."

    The built-in values were measured on the validated rig, which used the old
    enclosure on a stand. They are a starting point for that rig, not for
    yours. On the current case they are wrong. The radar height alone is three
    times too high for a unit on its feet. Wrong geometry does not raise an
    error: the IWR6843 just reports biased launch angles, or rejects every ball
    track and reports none. Measure each value on your own unit, and measure
    again whenever the unit, the mat or the bay changes.

    | Argument | Default | Where the default comes from |
    |---|---|---|
    | `--iwr6843-tee-m` | `1.575` | Validated rig |
    | `--iwr6843-net-m` | `4.6` | Validated rig |
    | `--iwr6843-radar-height-m` | `0.1524` | Reference calibration JSON (validated rig, old enclosure on a stand) |
    | `--iwr6843-tilt-deg` | `10.40` | Reference calibration JSON (validated rig) |
    | `--iwr6843-ball-height-m` | `0.040` | A typical iron ball center, not your mat |

    Write your values into your start script so every session uses them, and
    re-check them when you set up somewhere new. The values are easy to set
    once and then forget when the setup changes.

![Side view: radar height from the floor to the IWR6843 antenna centre, ball height from the same floor to the ball centre, tee-m from the antenna centre to the ball centre, net-m from the antenna centre to the net, and tilt of the antenna face from vertical. Top view: the antenna centre 46.4 mm from the case's left side seen from the front, on the target line through the ball to the net.](../assets/iwr6843-setup-measurements.svg)

*What each geometry argument measures. Every distance and height starts at the
antenna center, the green crosshair, and both heights use the floor the ball
sits on.*

### Which point to measure from

**The antenna center is the middle of the virtual array, not the middle of
the board or of the RX row.** TI's IWR6843LEVM User's Guide
([SWRU585](https://www.ti.com/lit/pdf/SWRU585), section 4, Figures 4-1 and
4-2) shows four RX patches λ/2 apart in a row and a separate group of three TX
patches: TX1 and TX3 2λ apart, with TX2 offset λ/2 between them. The twelve
TX→RX pairs form the virtual array the angles come from. Each pair measures
range and phase as if from the point halfway between its TX and its RX, so
the array's center is halfway between the center of the RX row and the center
of the TX group.

In the validated orientation (TX above RX) that point is **in the gap between
the top of the RX column and the bottom of the TX group**, about 8 mm above
the RX column's middle and 1.5 mm to the TX1/TX3 side of its center line. The
patches are printed on the board's top copper, so the antenna face is the PCB's
front surface.

![IWR6843LEVM antennas as mounted, TX above RX: the RX row centre (blue), the TX group centre (orange) and the antenna centre to measure to (green crosshair)](../assets/iwr6843levm-antenna-centre.png)

*TI SWRU585 Figure 4-1 (© 2021 Texas Instruments), turned to the mounted
orientation and annotated. The patch centres were located from the image; the
green crosshair is the midpoint of the blue (RX) and orange (TX) centres.*

On the current radar front (`Front-Radar-No-Fill`, the self-tapping/insert
front in [openflight-enclosure#12](https://github.com/open-flight/openflight-enclosure/pull/12)),
the enclosure CAD puts that point at:

| What | Value |
|---|---|
| Height above the case base (shell bottom, without feet) | 45.3 mm |
| Height above the surface the printed feet stand on | 50.3 mm (the feet add 5 mm) |
| From the case's left side, seen from the front | 46.4 mm |
| Antenna face behind the radar front's face, square to the face | 26.6 mm |
| Horizontally behind the radar front's face at antenna height | 27.0 mm |
| Horizontally behind the front's bottom lip | 34.6 mm |

These come from the board model (`reference-models/radars/IWR6843LEVM.step`,
whose patch outlines match SWRU585) placed on the radar front's four M3
pockets. Check them with a tape on your own unit; a front from another branch
or an older print moves the board.

One unit measured with a ruler to the green crosshair
([#339](https://github.com/open-flight/openflight/issues/339), 2026-11-18):

| What | Tape | CAD |
|---|---|---|
| Antenna center above the case bottom, no feet | 52.5 mm | 45.3 mm |
| Antenna face behind the front face | 27 mm | 27.0 mm |

The depth matches the CAD. The height came out 7.2 mm above it, which is why
your own tape wins over both tables: a different print of the radar front or
the shell moves it. 7 mm is about 0.25° of elevation at 1.6 m. That unit runs
with `--iwr6843-radar-height-m 0.0525`, plus the height of its feet when they
are fitted. More measurements from other units will settle a default for the
current front.

What that means for the tape:

- **`tee-m` and `net-m` are taken from the antenna center.** With a tape,
  start at the face of the radar front at antenna height, directly in front
  of the IWR (not the bottom lip, which sticks out about 8 mm further), run it
  horizontally along the target line, and add the 27 mm depth to the antenna
  face. `net-m` runs from the unit to the net, not from the ball to the net.
- **`radar-height-m`** is measured to the antenna center above, from the floor
  the ball sits on. A unit standing on its feet directly on that floor reads
  about 0.050 m, not the 0.1524 m default, which assumes the old enclosure on
  a stand.
- **`ball-height-m`** is the mat height plus the ball's 0.021 m radius,
  measured from the same floor. A ball on a 4 cm mat is about 0.061 m, not the
  0.040 m default.
- **The target line and the azimuth check** run through the same point seen
  from above: 46.4 mm from the case's left side.

!!! note "On the `feat/iwr-self-trigger` branch"

    The self-trigger branch changes the `tee-m` and `net-m` reference: there
    they are taped from the radar front's face and the software adds 30 mm
    (`ARRAY_DEPTH_M`) itself, so you do **not** add the depth. Check which one
    your checkout runs.

<details markdown="1">
<summary>What goes wrong with the wrong values</summary>

From a 2026-11-01 studio session with TrackMan running alongside
([#339](https://github.com/open-flight/openflight/issues/339)):

- **Net entered as ball → net** (3.0 m) instead of unit → net (5.30 m). The
  tracker stopped 0.25 m short of that net and searched from 2.25 m, which left
  about 10 ms of flight, so it rejected every track. Replaying the same dumps
  with 4.6 m tracked every real shot.
- **Ball on a 4 cm mat left at the 0.04 m default** instead of about 0.061 m.
  Replaying 31 shots against TrackMan, the corrected height cut the launch
  bias from +0.87° to +0.39° on the good tracks.
- **Radar height measured to the board center**, because it is not obvious
  where the antennas are on the IWR6843LEVM. That is what the figure above is
  for.

</details>

!!! note "Saved phone calibration"

    A phone app can measure and save the TI tilt instead; see
    [Calibrate TI radar tilt with the phone](../ios-ble.md#calibrate-ti-radar-tilt-with-the-phone).
    At startup the tilt comes from `--iwr6843-tilt-deg` if given, otherwise from
    the saved phone calibration in
    `~/.config/openflight/iwr6843_phone_orientation.json`, otherwise from the
    calibration JSON.

Measurement guidance:

- Measure `tee-m` from the antenna center to the centre of the ball, along the line the radar looks down. With a tape, the easy way is to measure from the radar front's face at antenna height and add the depth of the antenna face behind it (27 mm on the current radar front, see the table above). Measure to the ball, not the golfer's feet: the golfer stands beside the ball and is a strong standing return of their own, and their feet are not where the club meets the ball.
- Measure `net-m` from the same antenna center. It only matters when hitting into a net (`--iwr6843-flight net`, the default), where ball tracks stop 0.25 m short of it; in `range` and `course` flight it is ignored.
- Measure the radar height and the tilt from the antenna center (the virtual-array center above), not the enclosure edge or mounting feet.
- The horizontal distance to the ball is fine for `tee-m`: at 2 m the ball's height above the radar changes the slant range by under a centimetre.
- Keep `net-m` honest so late net reflections can be excluded.
- Measure radar and ball height from the same floor reference. If the radar and
  ball sit on different surfaces, extend a common level reference between them.
- Add an elevated mat to ball height. A 1 inch mat adds approximately `0.0254 m`.
- A typical iron ball center is around `0.040 m`; a driver tee is higher.
- Do not reuse a tilt value after moving the rig unless you verify it again.

The checked-in reference calibration contains the array correction used by the
validated radar. It provides a known starting point, not a universal factory
calibration. The operator calibration session below checks geometry and
estimator consistency; it does not regenerate the file's per-element complex
array correction. A different radar board or antenna orientation may require a
new corner-reflector array calibration before source-of-truth accuracy can be
expected.

