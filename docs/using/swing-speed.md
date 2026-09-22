# Swing Speed Training

OpenFlight can run a club-only swing speed training mode for air swings, speed
sticks, and overspeed training. This mode uses the OPS243-A fast speed stream
and does not require a ball strike or SparkFun sound trigger.

```bash
scripts/start-kiosk.sh --swing-speed
```

Optional tuning:

```bash
scripts/start-kiosk.sh --swing-speed \
  --swing-speed-threshold 35 \
  --swing-speed-min-readings 3 \
  --swing-speed-single-peak 60 \
  --swing-speed-num-reports 8 \
  --swing-speed-end-ms 1000 \
  --swing-speed-cooldown-ms 900 \
  --swing-speed-rejected-cooldown-ms 100
```

## How It Works

The OPS243-A is configured for fast outbound speed reporting. A swing rep starts
when outbound speed crosses the threshold, keeps collecting qualifying readings
while the club or training stick is moving through the hitting zone, then emits
the peak speed after a short quiet period. A rep must include multiple
qualifying readings so a single hand-motion or noise spike does not count as a
swing. Very fast peaks can still count from a single reading, which helps with
short indoor swings where the club is only in the radar beam for a moment.

The threshold is also applied to the OPS243 speed filter so the radar does not
stream low-speed background movement while waiting for a swing.
Swing speed mode requests multiple OPS speed reports and uses the fastest
outbound candidate, since the strongest radar return can be a slower part of the
club or body rather than the club head.
The default `--swing-speed-num-reports 8` keeps weaker but faster club-head
candidates in the stream.

The server emits a `swing_speed` WebSocket event:

```json
{
  "event": {
    "peak_speed_mph": 101.4,
    "timestamp": "2024-01-15T10:30:00",
    "duration_ms": 348,
    "reading_count": 9,
    "trigger_speed_mph": 32.2,
    "peak_magnitude": 42,
    "unit": "mph",
    "mode": "swing-speed"
  },
  "stats": {
    "swing_count": 3,
    "best_speed_mph": 105.2,
    "avg_speed_mph": 101.8,
    "last_speed_mph": 101.4
  }
}
```

## Radar Placement

Place the OPS243-A behind the swing area and point it down the target line, the
same general position used for launch monitor mode. The radar measures radial
speed, so the downswing and follow-through should move mostly away from the
radar.

Some speed sticks may have a weaker radar return than a metal club head. If
readings are inconsistent, test with a real club first to confirm setup, then
consider a small, secure radar-reflective marker on the training device.

If hand motion or room noise creates false reps, raise the threshold or require
more readings:

```bash
scripts/start-kiosk.sh --swing-speed \
  --swing-speed-threshold 45 \
  --swing-speed-min-readings 4 \
  --swing-speed-single-peak 70 \
  --swing-speed-end-ms 1000
```

## Radar Mode

Training runs the OPS243-A in CW speed-reporting mode, not rolling buffer.
`OPS243Radar.configure_for_swing_speed_training()` owns that setup and sends
`GS` — per [AN-010-AD](../radar/AN-010-AD_API_Interface.pdf) (API Commands, p21)
`GS` selects "CW operation only" and is the documented way out of rolling-buffer
mode. `restore_rolling_buffer_mode()` hands the radar back with `GC`.

Two things to know before changing this path:

- **`GC`, not `G1`.** AN-010-AD records that rolling-buffer mode "previously was
  G1". The older [AN-027](<../radar/AN-027-A_Rolling Buffer.pdf>) app note still
  documents the pre-rename `G1`/`G0` pair, so treat AN-010-AD as authoritative.
  `tests/test_ops243_mode_commands.py` pins the byte sequences.
- **Training never writes flash.** No `A!` on this path. The HOST_INT trigger
  workaround requires the board to *boot* into rolling-buffer mode from
  persistent memory, so persisting speed mode would break launch mode until
  `test_rolling_buffer_persist.py --setup` was re-run.

The setup is kept separate from `configure_for_speed_trigger()`, which tunes the
radar to detect a swing and hand off to the rolling buffer for the launch
monitor. Retuning that path must not silently change training measurements; the
two configurations are expected to diverge.

## Notes

Swing speed mode is separate from launch monitor shots. It reports club-only
training reps and does not calculate ball speed, smash factor, spin, launch
angle, or carry.
