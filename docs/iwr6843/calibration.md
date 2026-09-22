---
icon: lucide/sliders-horizontal
---

# Calibration and Replay

Tune the estimator against known shots, understand where it stops being
trustworthy, and replay saved captures offline to test changes without
swinging a club.

## Run A Calibration Session

Stop the kiosk before running calibration so it releases BCM17 and both serial
ports. The calibration command uses the same OPS trigger, OPS processing, TI
capture, and LCMF estimator as the server.

```bash
uv run \
  --with gpiozero \
  --with lgpio \
  python scripts/iwr6843/calibrate.py \
  --shots 20 \
  --club 7i \
  --ops-port /dev/ttyAMA0 \
  --iwr6843-port /dev/ttyUSB0 \
  --cfg config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg \
  --tee-m 1.575 \
  --net-m 4.6 \
  --tilt-deg 10.4 \
  --radar-height-m 0.1524 \
  --ball-height-m 0.040
```

Add `--debug` when you want raw dumps for offline replay. Calibration output is
written under `~/openflight_sessions/iwr6843_calibration/<timestamp>/` unless
`--outdir` is supplied.

The terminal reports:

- OPS ball and club speed.
- TI launch angle or rejection reason.
- Track RMS and inlier count.
- Estimated ball-start range.
- A radar-consistency tilt candidate.

The tilt candidate is not source-of-truth launch-angle calibration, and on
current data it cannot recommend a tilt at all. It reports the tilt where the
LCMF component models agree best — it minimises `component_std_deg` — but that
score is monotonic in tilt across the swept window, so the minimum lands on
whichever end of the window the trend runs toward rather than on the real
mount angle. On the 2026-07-25 range session, with the mount physically set
and measured at 5.5°, a ±3° sweep recommended 2.5° on shots 1 and 4 and 8.5°
on shots 2 and 3: both edges of the window, never the truth, disagreeing with
itself by the full 6° width across four shots of one session.

**Set tilt by physical measurement.** Treat a candidate sitting on the edge of
the sweep as no answer rather than a recommendation — which, on every shot
checked so far, is what it returns.

Use `--no-tilt-sweep` when you only need capture diagnostics and faster shot
turnaround.

## Launch-Angle Estimator Limitations

The vertical launch angle comes from two channel models, `two8` and
`four4_path_tdm`. When they agree within 8° the estimate is their mean; when
they disagree the estimator selects the one whose objective has the sharper
minimum (its curvature) and reports the result at reduced confidence. Three
limits of that scheme are known and unresolved. They are deferred pending a
session paired with a reference instrument, which this repo does not have.

**The curvature criterion is not scale-normalised.** `four4_path_tdm`'s
objective spans a range 2–4× larger than `two8`'s, so most of the reported
"3.7–10.7× sharper" margin is model scale, not evidence quality — on one shot
the true margin is 1.14×. It is validated as a *degeneracy detector*: it
reliably catches the collapsed `two8` channel seen on the 2026-07-25 mount. It
is not validated as an accuracy ranker, and it is one-sided — a collapsed
`four4_path_tdm` would likely win the comparison anyway.

**Selecting is worse than averaging when both channels are healthy but
disagree.** Monte Carlo at 6° of noise: 4.26° RMS averaging against 5.79°
selecting, and 7.93° on the disagreeing subset alone. Selection pays off only
when one channel is genuinely broken, which is the case the 8° gate was cut
for.

**The 8° gate rests on one session.** Its justification is a gap between one
shot whose channels agreed to 4.59° and six that spread 15.9–20.2°, all from a
single session, club, geometry and mount tilt. Nothing establishes that the
gap sits in the same place at another tilt, with another club, or on another
board.

## Replay Saved Captures

Offline replay reruns LCMF without hardware and is the safest way to compare
estimator changes against identical radar data.

Replay a debug session JSONL:

```bash
uv run python scripts/iwr6843/replay.py \
  --input ~/openflight_sessions/session_YYYYMMDD_HHMMSS_home.jsonl \
  --cfg config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg \
  --tee-m 1.575 \
  --net-m 4.6 \
  --tilt-deg 10.4 \
  --radar-height-m 0.1524 \
  --ball-height-m 0.040 \
  --club 9i \
  --out replay.csv
```

Replay one dump:

```bash
uv run python scripts/iwr6843/replay.py \
  --input ~/openflight_sessions/iwr6843/shot.l3dump \
  --ball-speed-mph 105.9 \
  --cfg config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg \
  --club 9i \
  --tee-m 1.575 \
  --net-m 4.6 \
  --tilt-deg 10.4 \
  --radar-height-m 0.1524 \
  --ball-height-m 0.040
```

A session JSONL can only replay TI captures saved while `--debug` was active. A
standalone dump needs `--ball-speed-mph` because OPS speed is not stored in the
TI binary dump.

