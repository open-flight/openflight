---
icon: lucide/rocket
---

# Quick Start

From assembled, verified hardware to a shot on screen.

!!! note "Not yet assembled?"

    Work through the [build order](build-order.md) first. This page assumes the
    trigger is wired, the Pi is set up, and rolling-buffer mode is persisted.

## Start it

```bash
scripts/start-kiosk.sh
```

That is the supported entry point. It activates the virtualenv, builds the UI if
needed, and launches the server with the rolling buffer and sound trigger.

The UI is at **`http://localhost:8080`** on the Pi, or
`http://<hostname>.local:8080` from another device on the LAN.

## Common variants

=== "No hardware"

    ```bash
    scripts/start-kiosk.sh --mock
    ```

    Simulated shots. Useful for UI work and for confirming the software side
    before the radars arrive.

=== "With the angle radar"

    ```bash
    scripts/start-kiosk.sh \
      --iwr6843 \
      --radar-port /dev/ttyAMA0 \
      --iwr6843-tee-m 1.575 \
      --iwr6843-net-m 4.6
    ```

    The geometry values are examples. **Measure your own** — see
    [mounting and geometry](../iwr6843/mounting.md).

=== "Swing speed only"

    ```bash
    scripts/start-kiosk.sh --swing-speed
    ```

    Club-only mode for air swings and speed sticks. No ball, no sound trigger.

=== "With a simulator"

    ```bash
    cp config/sim.example.json config/sim.json
    # edit config/sim.json for your simulator's host and port
    scripts/start-kiosk.sh --sim
    ```

    See [simulator connectors](../using/simulator/index.md).

Every flag is listed in the [CLI reference](../reference/cli.md).

## Your first shot

1. Place the OPS243-A **3–5 feet behind the tee**, pointing down the target
   line. See [radar positioning](../how-it-works/positioning.md).
2. Hit a ball.
3. The UI should show ball speed, club speed, smash factor, and carry —
   plus launch angle and club path if the IWR6843 is running.

If nothing appears, the trigger is the usual cause. Clap near the sound detector
and watch for the GATE LED; then check the
[troubleshooting index](../troubleshooting/index.md).

## What gets recorded

Every session writes a JSONL log to `~/openflight_sessions/session_*.jsonl`,
including the raw I/Q captures. That is what makes offline analysis and
estimator work possible later — see the
[session log reference](../reference/session-log.md).

Disable it with `--no-logging` if you would rather not.

## Next

- **[Running & modes](../using/running.md)** — the full set of run modes
- **[TV display mode](../using/display.md)** — put the UI on a TV or tablet
- **[Simulator connectors](../using/simulator/index.md)** — stream shots out
