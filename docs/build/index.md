---
icon: lucide/hammer
---

# Build

Assembling the hardware. The order matters — each step assumes the one before
it is done and verified.

<div class="grid cards" markdown>

- :material-numeric-1-circle-outline: **[Sound trigger wiring](sound-trigger.md)**

    Solder R17, wire `GATE` → `HOST_INT`. Everything downstream depends on this
    trigger path.

- :material-numeric-2-circle-outline: **[OPS243 → GPIO UART](ops243-uart.md)**

    Move the OPS243 off USB. Required before the IWR6843, which needs the bus.

- :material-numeric-3-circle-outline: **[IWR6843 angle radar](../iwr6843/index.md)**

    Wire, flash, mount, aim, and calibrate. The largest single step.

- :material-plus-circle-outline: **[Inclinometer](inclinometer.md)**

    *Optional.* LIS3DH enclosure-level tilt compensation.

- :material-battery-outline: **[Battery](battery.md)**

    *Optional.* Geekworm X1202/X1206 UPS with native telemetry.

- :material-cube-outline: **[Enclosure & case](enclosure.md)**

    *Optional.* The printed IARC v3 case.

</div>

## Before you start

Order everything from the **[parts list](../get-started/parts.md)** first — some
items have long lead times, and a partial build cannot be verified end to end.

You will need a soldering iron for exactly one joint: the R17 gain resistor on
the SEN-14262. See [sound trigger wiring](sound-trigger.md#before-you-wire-solder-r17).
