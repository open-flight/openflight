---
icon: lucide/flag
---

# Get Started

Four pages, in order. If you are deciding whether to build one, start with the
overview; if you have already decided, go straight to the parts list.

<div class="grid cards" markdown>

- :material-help-circle-outline: **[Overview](overview.md)**

    What the system measures, how accurate it is, and how a swing becomes a row
    of numbers.

- :material-cart-outline: **[Parts list](parts.md)**

    Everything to buy, with links and a cost summary. Some items have long lead
    times — order first.

- :material-format-list-numbered: **[Build order](build-order.md)**

    The sequence and its prerequisites. Doing these out of order means redoing
    work.

- :material-rocket-launch-outline: **[Quick start](quick-start.md)**

    Assembled hardware to first shot.

</div>

## The short version

OpenFlight measures a golf shot with two radars and a Raspberry Pi:

- An **OPS243-A** 24 GHz Doppler radar gives ball speed and club speed from a
  raw I/Q capture, triggered by a sound sensor that hears the strike.
- A **TI IWR6843** 60 GHz mmWave radar gives launch angle, launch direction,
  and club path from the raw radar cube.
- A **ballistic simulator** turns those into carry.

Shots are streamed to a React UI and, optionally, to GSPro, OpenGolfSim, or
another simulator.

## Before you commit

!!! info "What this is not"

    OpenFlight is a working DIY launch monitor, not a commercial product. Expect
    to solder one joint, flash custom radar firmware, measure your rig's geometry
    with a tape measure, and read a troubleshooting page or two.

    Spin rate in particular is **experimental** and is not used for carry by
    default — see [rolling buffer and spin detection](../how-it-works/rolling-buffer.md).

!!! warning "Don't buy K-LD7 radars"

    The K-LD7 angle radars are deprecated and superseded by the IWR6843. Their
    software support is retained for existing builds only. See
    [Legacy (K-LD7)](../legacy/index.md).
