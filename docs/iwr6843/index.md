# IWR6843 Operator Guide

This guide covers the supported OpenFlight setup for the TI IWR6843LEVM. It
starts with an OpenFlight-ready Raspberry Pi and an unconfigured radar, then
walks through wiring, firmware flashing, mounting, measurement, startup,
verification, calibration, and offline replay.

The production system uses two radars:

| Device | Responsibility |
|---|---|
| OPS243 | Sound-triggered shot detection, ball speed, and club speed |
| IWR6843 | Short-window radar capture, vertical launch angle, and experimental horizontal direction |

The sound detector sends the same impact edge to both systems. The OPS243
freezes its rolling buffer directly. The Raspberry Pi receives that edge on
BCM17 and immediately asks the IWR6843 firmware to finish and dump its rolling
frame ring.

For firmware development, architecture, and build instructions, see
[firmware developer guide](../development/firmware.md).
For a plain-language explanation and the July 2026 TrackMan baseline, see the
[IWR6843 launch-angle field report](../how-it-works/launch-angle.md).

## Current Configuration

Flash one configurable firmware image, then select a runtime profile:

| Component | Current file or value |
|---|---|
| Firmware | `firmware/releases/l3_dump_configurable_capture_20260818.bin` |
| Wide/default config | `config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg` |
| Dense/advanced config | `config/iwr6843_l3dump_dense_36f2ms_53bin_iq8.cfg` |
| Dense/wide-late experimental config | `config/iwr6843_l3dump_dense_36f2ms_53bin_iq8_wide_late.cfg` |
| Reference array calibration | `config/iwr6843_calibration_reference.json` |
| Firmware size | 346,820 bytes |
| Firmware SHA-256 | `823ddd18a231d0004020de6262160d6863384cccac6674bae6f7d0fcea58f955` |
| Transmitters / receivers | 3 TX / 4 RX |
| Loops | 12 per frame |
| Movie duration | 72 ms |

### Choose A Profile

| Profile | Wide/default | Dense/advanced | Dense/wide-late experimental |
|---|---:|---:|---:|
| Frames and spacing | 24 at 3 ms | 36 at 2 ms | 36 at 2 ms |
| Saved window | 53 bins | 53 bins | 53 bins |
| Storage | IQ16 | Fixed-scale IQ8 | Fixed-scale IQ8 |
| Complete dump | 732,812 bytes | 549,764 bytes | 549,764 bytes |
| Choose it for | Ball flight and setup tolerance | Dense impact sampling | Testing slower-shot late-flight coverage |

Start with **wide/default**. Its wider range window is more tolerant of tee
placement, ball speed, and setup geometry, while IQ16 retains full signal
fidelity. Its live inclinometer-adjusted LCMF output measured 0.86 degree MAE
across all 59 matched 9-iron and 7-iron shots in an August 9 TrackMan session,
with 0.70 degree P50 and 1.75 degree P90 absolute error. Select
**dense/advanced** when temporal density around impact is the priority. It now
preserves the same 53-bin range span while using fixed-scale IQ8 and EDMA
packing to fit 36 frames in L3. The 2 ms IQ8 transport has passed hardware
cadence testing with a 0.0089% HWA miss rate and no EDMA errors, but the 53-bin
dense profile still needs source-of-truth TrackMan MAE validation; its
horizontal and club metrics remain experimental.

The **dense/wide-late experimental** profile is identical to dense/advanced
except that its final six ball frames remain in bins 47-99 instead of shifting
outward to bins 64-116. Use it to test whether the outward late-flight shift,
rather than IQ8 storage, causes reduced accuracy on slower shots. It has not
been validated against TrackMan.

Changing profiles does not require reflashing. It changes only the config
passed to `--iwr6843-config`. All profiles use the same host-side mount-tilt
path, including live inclinometer correction when `--inclinometer` is enabled.
They also use the measured positive TDM sign for normal TX order. Automatic
sign selection is reserved for offline diagnostics because multipath can select
the mirrored sign and collapse the vertical two8 channel.

On the Pi, verify the checked-in image with:

```bash
sha256sum firmware/releases/l3_dump_configurable_capture_20260818.bin
```

## Before You Start

You need:

- A Raspberry Pi running OpenFlight.
- A TI IWR6843LEVM and a data-capable USB cable.
- An OPS243 radar connected through either the Pi GPIO UART or a separately
  powered USB hub.
- A configured SparkFun SEN-14262 sound detector or the equivalent supported
  trigger. Complete the [sound-trigger wiring guide](../build/sound-trigger.md)
  first.
- A stable Pi power supply and stable power for every USB-connected radar.
- Access to the IWR6843 boot-mode switch and RESET button.
- Measurements for radar-to-ball distance, radar-to-net distance, radar height,
  ball height, and radar tilt.

Run all commands from the OpenFlight repository root unless a section says
otherwise.


## The setup path

Work these in order — each depends on the one before it.

<div class="grid cards" markdown>

- :material-numeric-1-circle-outline: **[Wiring](wiring.md)**

    Power and data layout, Pi UART, serial and GPIO permissions, sound-trigger
    line, and identifying the TI serial port.

- :material-numeric-2-circle-outline: **[Flash the firmware](flashing.md)**

    Boot mode, ROM bootloader, flashing the configurable image, and returning
    to functional mode.

- :material-numeric-3-circle-outline: **[Mount, aim, and measure](mounting.md)**

    Physical placement and the geometry measurements the runtime needs.

- :material-numeric-4-circle-outline: **[Start and verify](verify.md)**

    Launch with your geometry and confirm the first capture is sane.

- :material-numeric-5-circle-outline: **[Horizontal launch and club path](club-path.md)**

    Target-line reference, separation test, and reading club path.

- :material-numeric-6-circle-outline: **[Calibration and replay](calibration.md)**

    Calibration sessions, estimator limits, and offline capture replay.

- :material-wrench: **[Troubleshooting](troubleshooting.md)**

    When the radar does not enumerate, dump, or report sane angles.

</div>

## Related

- [Low-confidence vertical recovery](low-confidence-recovery.md) — the
  OPS-guided fallback policy and its validation.
- [Firmware developer guide](../development/firmware.md) — build the image
  from source.
