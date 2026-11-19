# Rolling Buffer Capture and Spin Detection

OpenFlight uses the OPS243-A rolling buffer for production shot capture. Spin
estimation from that capture remains experimental.

## Capture pipeline

1. The SEN-14262 detects impact and triggers the OPS243-A through `HOST_INT`.
2. The radar freezes and returns 4,096 I samples and 4,096 Q samples.
3. `RollingBufferProcessor` extracts ball and club speed from short FFT windows.
4. An overlapping timeline separates the club, impact, and ball regions.
5. An ungated multitaper estimator records an experimental spin candidate.

The normal kiosk command uses this pipeline:

```bash
scripts/start-kiosk.sh
```

## One-time radar setup

The OPS243-A firmware changes the `HOST_INT` behavior when rolling-buffer mode
is entered at runtime, so the mode has to be saved to flash once and the radar
power-cycled — see **[Rolling buffer setup](../setup/rolling-buffer.md)**.

The runtime then starts in the persisted `GC` rolling-buffer mode without
re-entering it. See [Sound trigger wiring](../build/sound-trigger.md) for the
recommended direct hardware trigger.

The OPS243 internal speed trigger is available as a separate, opt-in capture
strategy. It requires OPS243-A firmware v1.3.2 or newer in the 1.3 release
train, uses the tested 30 ksps configuration, and defaults to `S#6`; see
the [Internal Hardware Trigger guide](../hardware-trigger.md) for its command
ordering, re-arm behavior, and Raspberry Pi validation checklist. The sound
path and its defaults remain unchanged.

## Current defaults

| Setting | Value |
|---|---:|
| OPS243-A mode | Persisted rolling buffer (`GC`) |
| Sample rate | 30,000 samples/s |
| Capture | 4,096 I + 4,096 Q samples |
| Capture duration | About 136.5 ms |
| Pre-trigger split | 16 of 32 blocks |
| Speed window | 128 samples |
| FFT size | 4,096 |
| Overlap step | 32 samples (937.5 windows/s) |
| Production trigger | SEN-14262 `GATE` to OPS243-A `HOST_INT` |

Use `--buffer-split post-heavy` when more post-impact data is needed, or
`--buffer-split pre-heavy` when the club approach matters more:

```bash
scripts/start-kiosk.sh --buffer-split post-heavy
```

## Experimental spin output

Every valid capture runs the ungated multitaper estimator. Its candidate:

- is labeled **EXPERIMENTAL** in the UI;
- is logged as `spin_method: "multitaper_ungated"`;
- is constrained to 1,500–11,000 RPM; and
- does not drive spin-adjusted carry.

Treat individual values as evaluation data, not ground truth. Multipath and
capture geometry can produce plausible but incorrect candidates, and current
blind validation is not accurate enough for production carry calculations.

For offline comparison and estimator work, use
[Spin Replay and Diagnostics](../development/spin-replay.md). Raw captures are stored
in the session JSONL logs described in the
[observability guide](../using/observability.md#session-log-format).

## Related guides

- [Raspberry Pi Setup](../setup/raspberry-pi.md)
- [Sound Trigger Wiring](../build/sound-trigger.md)
- [OPS243 Internal Hardware Trigger](../hardware-trigger.md)
- [Spin Replay and Diagnostics](../development/spin-replay.md)
