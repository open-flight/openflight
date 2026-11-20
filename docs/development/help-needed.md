---
icon: lucide/hand-helping
---

# Help Needed

Work where an extra pair of hands, or an extra rig, makes the biggest
difference. Most of it is testing on real hardware rather than writing code.
Say what you are picking up on the
[OpenFlight Discord](https://discord.gg/w8hhG4WVMN) or in
[GitHub Discussions](https://github.com/open-flight/openflight/discussions), so
two people don't run the same test.

## Test the IWR6843 self-trigger

**The goal is to not need the sound trigger at all.** Experimental IWR6843
firmware detects the shot by itself and starts the capture, with no
microphone in the loop. It lives on the
[`feat/iwr-self-trigger`](https://github.com/open-flight/openflight/tree/feat/iwr-self-trigger)
branch and needs testing on real OpenFlight rigs. If you have a working build
with an IWR6843, say so on Discord.

If the self-trigger holds up, the SEN-14262, its `R17` resistor, its wiring
and its slot in the camera strip all go. That saves parts, and it removes the
build step people find hardest, so the whole build gets simpler.

The IWR is the better place to trigger from than the OPS243's own
[internal trigger](../reference/ops243-firmware-history.md). The OPS only
measures speed, so anything moving fast enough in its beam can fire it. The
IWR also sees range and angle, so it can tell a ball leaving the tee from
other motion.

Useful to report: missed shots, false triggers (and what caused them),
indoor versus outdoor, club types, and how it compares with the sound trigger
on the same session.

## Compare against TrackMan 4

We need many more side-by-side sessions with a **TrackMan 4**. More comparison
data is the only way to say how good OpenFlight is, and how much better each
change makes it. If you have access to a TrackMan 4, record a session with
both running on the same shots and share:

- the OpenFlight session log (`~/openflight_sessions/session_*.jsonl`),
- the TrackMan export for the same shots,
- the rig geometry: tee and net distance, radar height and tilt.

Every club and every condition helps. The
[launch angle field report](../how-it-works/launch-angle.md) lists the
specific A/B runs that are wanted most.

## Make updating older OPS243s almost free

Firmware 1.3.3 adds a UART bootloader. From 1.3.3 on, the radar can be updated
over the same `J3` wires OpenFlight already runs to the Pi
([AN-013-G](../radar/AN-013-G_Code-Update.pdf)), and sensors shipping now
should come with it. Sensors on older firmware have no bootloader. Their first
update still needs a debugger on the `J2` header: a SEGGER J-Link (about $76),
or the Raspberry Pi Debug Probe route in
[PR #320](https://github.com/open-flight/openflight/pull/320) (about $30).

What would help:

- **A Pi-only updater for 1.3.3 sensors**, doing what Memtool does over the
  Pi's UART, so later updates need nothing extra.
- **A cheaper first upgrade for older sensors.** That could be a second
  confirmed board for the Debug Probe route, or a way to drive `J2` straight
  from the Pi's GPIO pins.
- **Reports**: which firmware your sensor arrived with (`?V`), and which
  processor it reports (`?P`).

Background is in
[OPS243 internal trigger and firmware updates](../reference/ops243-firmware-history.md).

## Test what newer OPS243 firmware gives us

Newer OPS243-A firmware is worth testing for features, not just fixes. The
first one to test is the **transmit-frequency shift**: AN-010's `T=n` command
moves the carrier within the 24.0-24.25 GHz band (`?F` reads it back). In a
multi-bay setup, another launch monitor that happens to sit on the OPS243's
default 24.125 GHz could interfere with it. We want to validate whether
moving the OPS off that frequency avoids the interference, without hurting
ball-speed or spin accuracy, and whether the setting persists.

## Sensor fusion with the camera

Combining the camera with the radars is an interesting area, and
[Harjot](https://github.com/HarjotDhanota) is leading it. The clubhead-pose
work in [#245](https://github.com/open-flight/openflight/pull/245) and
[#250](https://github.com/open-flight/openflight/pull/250) is the place to
start. Help with testing, review, or the fusion maths is welcome.

## Test the mobile app

[OpenFlight Mobile](https://github.com/open-flight/openflight-mobile) is the
companion app. It connects to the OpenFlight server and shows live shot data,
and the goal is a complete standalone interface so the Pi can run headless
without the kiosk screen. It needs testers on both iOS and Android against a
real rig. Report issues in that repository.

## Azimuth calibration and the radar workbench

The IWR6843 azimuth calibration (can-target runs over 94 positions) and the
[OpenFlight Radar Workbench](https://github.com/Robinante/OpenFlight-Radar-Workbench)
used to analyse the captures are being discussed in
[Discussion #371](https://github.com/open-flight/openflight/discussions/371).
Repeat runs on other rigs, other targets, and ideas for turning it into a
standard calibration step are all welcome there.
