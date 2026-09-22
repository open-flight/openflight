---
icon: lucide/play
---

# Start and Verify

Bring the system up with the geometry you measured, then confirm the first
capture looks right before hitting a full session.

## Start OpenFlight

For the first run, use `--debug`. This retains each TI dump for inspection and
offline replay. This example uses the Option A GPIO UART path. Replace the
example geometry with your measurements:

```bash
scripts/start-kiosk.sh --debug \
  --radar-port /dev/ttyAMA0 \
  --iwr6843 \
  --iwr6843-port /dev/ttyUSB0 \
  --iwr6843-config config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg \
  --iwr6843-tee-m 1.575 \
  --iwr6843-net-m 4.6 \
  --iwr6843-tilt-deg 10.4 \
  --iwr6843-radar-height-m 0.1524 \
  --iwr6843-ball-height-m 0.040 \
  --session-location home
```

For Option B, replace `/dev/ttyAMA0` after `--radar-port` with the OPS USB serial
device, preferably its stable `/dev/serial/by-id/...` path.

The example uses the recommended wide profile. To test dense impact sampling,
change only the config argument to:

```text
--iwr6843-config config/iwr6843_l3dump_dense_36f2ms_53bin_iq8.cfg
```

To test dense sampling while retaining the wide profile's late-flight window,
use the experimental profile:

```text
--iwr6843-config config/iwr6843_l3dump_dense_36f2ms_53bin_iq8_wide_late.cfg
```

Passing `--iwr6843-config` explicitly keeps the selected profile visible in the
launch command and session log.

The OPS port can also be supplied as `--ops-port /dev/ttyAMA0`. `--port` means
the web-server port, so do not use it for the OPS serial device.

The TI port can be omitted after the custom firmware is running; OpenFlight
probes available USB serial ports for the expected CLI. Supplying
`--iwr6843-port` is clearer during initial setup and avoids ambiguity when
multiple USB serial devices are connected.

Once the setup is stable, remove `--debug` for normal operation. The server
still processes TI captures in memory, but it does not write a dump for
every shot. Session JSONL entries only contain a dump path when debug capture
is enabled.

## Verify The First Capture

Healthy startup includes messages similar to:

```text
[IWR6843] Configured on BCM17 using /dev/ttyUSB0 (..., waiting for OPS)
[IWR6843] Armed on BCM17
[SERVER] IWR6843 initialized (... firmware boundary freeze)
```

Use one clap to verify the shared trigger and dump transfer. A clap is not a
golf ball, so `rejected_by_ball_tracker` is expected. The important result is a
complete capture:

```text
[IWR6843] Trigger #1: dumping firmware-frozen L3 ring
[IWR6843] Capture #1 complete: 732812 bytes
```

Firmware health should show an active sensor, increasing frame/wrap counters,
and no RF faults:

```text
active=1 ... rf_faults=0
```

Then hit a ball. A trusted result logs `Angle source: radar`. A shot may still
appear in the UI with an estimated angle when the TI capture completes but the
ball track does not meet the acceptance gates.

In debug mode, verify that the session contains an `iwr6843_capture` entry, a
`temperature_report` object, and a `capture_path` pointing to the saved
`.l3dump` file.
