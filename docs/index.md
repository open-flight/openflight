---
icon: lucide/radar
---

# OpenFlight

**A golf launch monitor you build yourself.** An OPS243-A Doppler radar measures
ball and club speed; a TI IWR6843 mmWave radar measures launch angle and club
path; a Raspberry Pi ties them together and streams shots to your simulator.

Everything here is open source, and every part is off the shelf.

---

## What it measures

| Metric | Source | Notes |
| --- | --- | --- |
| Ball speed | OPS243-A | Rolling-buffer I/Q, FFT mode extraction |
| Club speed | OPS243-A | Pre-impact window of the same capture |
| Launch angle | IWR6843 | LCMF-v1 over the raw radar cube |
| Launch direction | IWR6843 | Horizontal plane |
| Club path | IWR6843 | Pre-impact frames |
| Spin rate | OPS243-A | **Experimental** — not used for carry by default |
| Carry | Computed | RK4 trajectory with drag and Magnus |

## Start here

<div class="grid cards" markdown>

- :material-cart-outline: **[Parts list](get-started/parts.md)**

    What to buy, with purchase links and a cost summary.

- :material-cog-outline: **[Raspberry Pi setup](setup/raspberry-pi.md)**

    Install, configure, and auto-start the software.

- :material-flash-outline: **[Sound trigger wiring](build/sound-trigger.md)**

    Wire the SEN-14262 to the OPS243-A. Do this first.

- :material-angle-acute: **[IWR6843 operator guide](iwr6843/index.md)**

    Wire, flash, mount, aim, and calibrate the angle radar.

</div>

## Build order

The hardware has real prerequisites — doing these out of order means redoing
work.

1. **[Parts list](get-started/parts.md)** — order everything before starting.
2. **[Sound trigger wiring](build/sound-trigger.md)** — solder R17, then wire
   `GATE` → `HOST_INT`. The rolling-buffer capture depends on this.
3. **[Raspberry Pi setup](setup/raspberry-pi.md)** — through the one-time
   rolling-buffer flash-persist step. Confirm you can capture with the OPS243
   alone before adding anything else.
4. **[Move the OPS243 to the Pi GPIO UART](build/ops243-uart.md)** —
   required before the IWR6843, which needs the USB bus.
5. **[IWR6843 operator guide](iwr6843/index.md)** — wire, flash, mount, aim,
   and measure the geometry.
6. Optional: **[inclinometer](build/inclinometer.md)**,
   **[battery](using/battery.md)**, **[simulator connectors](using/simulator/index.md)**,
   **[cloud sync](using/cloud-sync.md)**.

## Once it's running

- **[Simulator connectors](using/simulator/index.md)** — stream shots to
  [GSPro](using/simulator/gspro.md), [OpenGolfSim](using/simulator/opengolfsim.md),
  [PAR-TEE](using/simulator/partee.md), and others.
- **[Swing speed training](using/swing-speed.md)** — club-only mode for air
  swings and speed sticks. No ball strike, no sound trigger.
- **[Cloud sync](using/cloud-sync.md)** — push filtered sessions to FlightWeb.
- **[Observability](using/observability.md)** — ship session logs to Grafana Cloud.

## Understanding the numbers

- **[Rolling buffer and spin detection](how-it-works/rolling-buffer.md)** —
  how a capture becomes a ball speed, and why spin is still experimental.
- **[Dechirped-sideband spin replay](development/spin-replay.md)** — the next-gen
  spin estimator test bench.

!!! warning "The K-LD7 angle radars are deprecated"

    The supported angle radar is the **TI IWR6843**. Don't buy K-LD7s for a new
    build — their software support is retained for existing builds only. See
    [Legacy (K-LD7)](legacy/index.md).

!!! info "The IWR6843 needs custom firmware"

    The stock TI demo doesn't expose the raw radar cube OpenFlight needs. A
    validated prebuilt image ships in `firmware/releases/`, so flashing it does
    not require the TI toolchain.
