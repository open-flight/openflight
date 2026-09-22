# OPS243 Internal Hardware Trigger

OpenFlight’s `hardware` trigger mode lets the OPS243-A decide when a rolling-buffer capture starts. It is opt-in; the kiosk default remains the existing SEN-14262 sound trigger.

## OPS243 firmware prerequisite

Hardware-trigger mode requires **OPS243-A firmware v1.3.2 or newer in the 1.3
release train**. Firmware v1.3.1 has a vendor data-sequence bug and is rejected.
The driver queries `?V` before sending the internal trigger setup commands and
fails fast if the reported version is from an older or different release train,
malformed, or unavailable. Update the physical OPS243 using the manufacturer’s
firmware procedure before running `--trigger hardware`; this application does
not flash the radar.

## Methodology

The host configures the radar once and then waits for the radar’s completed rolling-buffer dump. The host does not poll speed reports or send `S!` for each shot:

1. Put the radar in idle mode with `PI`.
2. Set a temporary outbound `ST-90` guard before entering rolling-buffer mode.
   `GC` can restore the detector units to m/s before `US` is sent; 90 is below
   the 30 ksps ceiling of 93.2 m/s and is still above ordinary startup motion
   if the board retained mph units.
3. Enter rolling-buffer mode with `GC`, then resend the guard because `GC`
   resets detector settings.
4. Restore the detector settings that `GC` resets: 30 ksps and MPH units first,
   then raise the temporary guard to `ST-200` while the remaining settings are
   written: 128 samples, `X=2`, outbound filtering, JSON plus magnitude output,
   and the configured `S#n` split.
5. Restore the magnitude gate `SMn`, then restore the requested `ST-n` last and allow the 4,096-sample history to fill.
6. Wait for the board-triggered dump, parse the I/Q payload, and reject it if it has no outbound ball-speed reading at or above 35 mph.
7. Re-arm with the same temporary guard around `GC`, restore the cached settings, and wait for the buffer to fill again. A serial write timeout keeps the capture and reports a retryable re-arm failure instead of discarding the shot.

The implementation is intentionally limited to the OPS243 trigger path; the
experimental analysis and UI work remain outside this focused change.

## Defaults and command

| Setting | Hardware-mode default |
|---|---:|
| Trigger threshold | 25 mph |
| Trigger magnitude | 25 (`SM25`) |
| Pre-trigger split | 6 segments (`S#6`) |
| Sample rate | 30 ksps (required) |
| Minimum accepted outbound ball speed | 35 mph |

Run the mode directly with:

```bash
openflight-server \
  --trigger hardware \
  --trigger-threshold 25 \
  --trigger-magnitude 25 \
  --pre-trigger-segments 6 \
  --sample-rate 30
```

The kiosk script forwards the same settings:

```bash
scripts/start-kiosk.sh --trigger hardware
```

Use `--trigger-threshold`, `--trigger-magnitude`, and `--pre-trigger-segments` to override the hardware path. `S#6` applies only to this new mode. The established sound path continues to use `--sound-pre-trigger` and keeps its existing default and behavior:

```bash
scripts/start-kiosk.sh --trigger sound --sound-pre-trigger 16
```

The internal trigger does not depend on the SEN-14262 sound edge. Existing sound-trigger wiring can remain installed, but selecting `hardware` is the software choice that activates the OPS243 internal trigger.

## Raspberry Pi retest checklist

Before treating a PR as ready for merge, run the hardware path on the target Pi and record the observations in the PR body:

Also run the unchanged sound path with the same representative shots:

```bash
scripts/start-kiosk.sh --trigger sound
```

I was unable to regression test the sound trigger.
