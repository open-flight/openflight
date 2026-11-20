---
icon: lucide/history
---

# OPS243 Internal Trigger and Firmware Updates

How OpenFlight's use of the OPS243-A's own trigger, and of its firmware
updates, has evolved. This material used to sit in the
[parts list](../get-started/parts.md). It moved here because none of it is
needed to build OpenFlight: the sound trigger is still the default, and the
[IWR6843 self-trigger](../development/help-needed.md#test-the-iwr6843-self-trigger)
is the route we are working toward to replace it. The operating guide for the
mode itself is still the [Internal Hardware Trigger guide](../hardware-trigger.md).

## Why an internal trigger was wanted

A microphone cannot tell your strike from someone else's, and it cannot tell
either from a door, a ball hopper or the next bay over. That is the nature of
listening for a bang. It is why the SEN-14262 needs its `R17` gain trimmed to
your room in the first place, and why a noisy range is where it does worst.
The internal trigger fires on the radar's own speed reading instead, so it
responds to something moving in front of the sensor rather than to sound in
the building. In theory that makes it both less error-prone and usable in loud
places the sound trigger cannot cope with. Nobody has published a
false-trigger comparison between the two, so treat that as the expectation
rather than a measured result.

The limit is that the OPS243 only measures speed. Anything moving fast enough
in its beam can fire it, which is why the longer-term plan is to let the
IWR6843 decide when a shot happened. It sees angle and range as well as speed.

## Firmware 1.3.1 and 1.3.2

Hardware mode lets the OPS243-A start the rolling-buffer capture from its own
speed trigger, so the sound detector, its resistor and its wiring are not
needed. It needs OPS243-A firmware **1.3.2 or newer in the 1.3 release
train**. Firmware 1.3.1 is rejected because of a vendor data-sequence bug. To
check, plug the radar into USB, open a serial terminal, send `?V`, and read the
version it prints back.

- **1.3.2 or later.** Nothing to buy; use
  `scripts/start-kiosk.sh --trigger hardware`. OmniPreSense
  [told the project on 2026-09-10](https://github.com/open-flight/openflight/pull/221#issuecomment-5619646576)
  that 1.3.2 went onto the sensors shipping from that build on. 1.3.1 had gone
  to some earlier customers with a late bug.
- **1.3.1 or older.** You flash it yourself with a programmer on the radar's
  keyed `J2` JTAG header. That is a 10-pin 1.27 mm Cortex debug header, not the
  `J3` UART header OpenFlight wires to. The procedure at the time was AN-013
  rev D: a SEGGER J-Link, Infineon's free XMCFlasher in Serial Wire Debug mode
  with the XMC4500-1024 target selected, and a hex file OmniPreSense hand out
  on request. Sandy at OmniPreSense
  [said on the project Discord](https://github.com/open-flight/openflight/pull/221#issuecomment-5619646576):
  *"If you have a Segger programmer, and would like to update the code on your
  OPS243, please send me a message here or via email on our website"*. Message
  them on Discord, or use the
  [contact page on omnipresense.com](https://omnipresense.com/contact/).
  Send `?P` first and pick the XMC4700 in XMCFlasher instead if the board
  reports that part
  ([note on the PR](https://github.com/open-flight/openflight/pull/221#issuecomment-5463503457)).
  Do not press Erase in XMCFlasher: it clears the factory settings some sensors
  carry and anything saved to persistent memory. On Windows, run the J-Link
  driver installer as administrator and tick the legacy J-Link USB driver, or
  XMCFlasher will not find the probe
  ([upgrade report](https://github.com/open-flight/openflight/pull/221#issuecomment-5756563718)).

The programmer the parts list used to carry for this was the
**SEGGER J-Link EDU Mini** (Adafruit 3571, about $76), with the 9-pin 0.05"
Cortex target cable that fits `J2` in the box. That is about four times the
sound trigger's $18, and it is a one-off tool rather than a part of the
monitor. It is no longer listed: most builders do not need it.

## Firmware 1.3.3 and updating over the UART

OmniPreSense's code-update note was revised twice in November 2026.
**AN-013-G** (2026-11-06) added a second update method: reflashing over the
**UART on `J3`** with Infineon's free Memtool and a 3.3 V USB-to-UART cable,
for sensors that already run **firmware 1.3.3**.
**[AN-013-H](../radar/AN-013-H_Code-Update.pdf)** (2026-11-17,
[source](https://omnipresense.com/wp-content/uploads/2026/11/AN-013-H_Code-Update.pdf))
extends that to **older code**: a sensor earlier than 1.3.3 can be brought to
1.3.3 or later the same way, with no programmer.

AN-013-H wires the cable to `J3` pin 9 (power), pin 10 (ground), pin 7 (TxD)
and pin 6 (RxD), at 19,200 baud. Those are the same four pins OpenFlight's
[GPIO UART connection](../build/ops243-uart.md) already runs to the Pi. The
only difference between old and new code is how the sensor gets into boot
mode:

- **1.3.3 or later:** send `B1`, then the reset command `P!`. The sensor
  answers `{"BootMode" : "ASC_BSL"}`.
- **Older than 1.3.3:** `B1` does not exist yet. With the sensor powered, hold
  a wire from `J2` pin 2 to ground (for example the metal around a mounting
  hole), then remove and reapply power. The processor comes up in boot mode,
  and the green heartbeat LED stays off. The `J2` pins are close together, so
  you need a thin jumper wire; the UART jumpers do not fit.

Then Memtool opens the new code file, connects, and flashes it. Power-cycle
afterwards and check the version with `?V`.

What that means for OpenFlight:

- **No sensor needs a programmer any more**, by AN-013-H. The J-Link route and
  the Raspberry Pi Debug Probe route in
  [PR #320](https://github.com/open-flight/openflight/pull/320) remain options,
  but the grounding method needs only a thin wire and the UART connection a
  build already has.
- **Sensors shipping now should come with 1.3.3**; check `?V` on arrival.
- **The goal is to update the OPS with only the Pi.** Memtool is a desktop
  tool, but the radar is already wired to the Pi's UART in an OpenFlight build.
  A Pi-side updater would need nothing extra to buy. That is listed under
  [Help needed](../development/help-needed.md#make-updating-older-ops243s-almost-free).
