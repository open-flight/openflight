---
icon: lucide/list-ordered
---

# Build Order

The hardware has genuine prerequisites. Each step below assumes the previous one
is done **and verified** — the point of the order is that when something breaks,
only one thing changed.

!!! tip "Verify at every step"

    The most common way to lose an afternoon is to wire everything, turn it on,
    and get nothing. Each step here ends with a check that produces visible
    output. Do not move on until it passes.

## 0. Order the parts

**→ [Parts list](parts.md)**

Some items ship slowly. Order everything before you start, including the R17
resistor (47 kΩ, and a 33 kΩ as backup) for the sound detector.

## 1. Wire the sound trigger

**→ [Sound trigger wiring](../build/sound-trigger.md)**

Solder the R17 gain resistor, then wire `GATE` → `HOST_INT` and the shared
ground. This is the one soldering job in the build.

**Verify:** the GATE LED flashes when you clap and goes out again. If it stays
lit, fit a lower-value R17.

## 2. Set up the Raspberry Pi

**→ [Raspberry Pi setup](../setup/raspberry-pi.md)**

Install the OS and dependencies, run the setup script.

**Verify:** the server starts in mock mode — `scripts/start-kiosk.sh --mock`.

## 3. Persist rolling-buffer mode

**→ [Rolling buffer setup](../setup/rolling-buffer.md)**

One time, per radar. The OPS243 must hold rolling-buffer mode in flash or
hardware triggers will not work.

**Verify:** `test_rolling_buffer_persist.py --test` reports triggers when you
clap.

!!! success "You now have a working launch monitor"

    Ball speed, club speed, and carry all work from here. Everything below adds
    angles. Play with it at this stage before continuing — it confirms the whole
    trigger and capture path is sound.

## 4. Move the OPS243 to the GPIO UART

**→ [OPS243 → GPIO UART](../build/ops243-uart.md)**

Required before the IWR6843, which needs the USB bus and real current. Do this
migration on its own; adding the TI radar at the same time makes any failure
ambiguous.

**Verify:** the diagnostic reports `230400 baud` and roughly 22 KB/s. A pass
reporting 19,200 is treated as a failure on purpose — it means the radar never
received the `I5` command, so check the Pi pin 8 → J3 pin 6 wire.

## 5. Add the IWR6843 angle radar

**→ [IWR6843 operator guide](../iwr6843/index.md)**

The largest step, and itself ordered: [wiring](../iwr6843/wiring.md) →
[flashing](../iwr6843/flashing.md) → [mounting and geometry](../iwr6843/mounting.md)
→ [start and verify](../iwr6843/verify.md).

**Verify:** [the first capture](../iwr6843/verify.md#verify-the-first-capture)
returns a sane launch angle.

## Optional extras

Any order, once the above works.

| Add-on | Guide | Why |
| --- | --- | --- |
| Inclinometer | [LIS3DH](../build/inclinometer.md) | Compensates enclosure tilt so the IWR6843 angle stays honest if the unit is bumped |
| Battery | [Geekworm X1202/X1206](../build/battery.md) | Portable operation with real telemetry and low-battery warnings |
| Enclosure | [IARC v3 case](../build/enclosure.md) | Printed housing for the whole assembly |
| Simulators | [Connectors](../using/simulator/index.md) | Stream shots to GSPro, OpenGolfSim, PAR-TEE, E6 |
| Cloud sync | [Cloud sync](../using/cloud-sync.md) | Push filtered sessions to FlightWeb |
| Log shipping | [Observability](../using/observability.md) | Query sessions in Grafana Cloud |

## When something does not work

Start at the **[troubleshooting symptom index](../troubleshooting/index.md)**,
which routes by what you are seeing rather than by which component you suspect.
