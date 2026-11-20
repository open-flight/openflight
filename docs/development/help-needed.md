---
icon: lucide/hand-helping
---

# Help Needed

Work where an extra pair of hands, or an extra rig, makes the biggest
difference. Most of it is testing on real hardware rather than writing code.
The more tests we run, the better, and the same test run on several rigs is
worth more than one run. Say what you are picking up and how you are running it
on the [OpenFlight Discord](https://discord.gg/w8hhG4WVMN) or in
[GitHub Discussions](https://github.com/open-flight/openflight/discussions), so
we can compare results and help each other.

## Test the IWR6843 self-trigger

**The goal is to not need the sound trigger at all.** Experimental IWR6843
firmware detects the shot by itself and starts the capture, with no
microphone in the loop. It lives on the
[`feat/iwr-self-trigger`](https://github.com/open-flight/openflight/tree/feat/iwr-self-trigger)
branch and needs testing on real OpenFlight rigs. If you have a working build
with an IWR6843, say so on Discord.

**Test it on the new radar mount.** Print the
[new radar front](#print-the-new-radar-front) first. It is in Cormac's draft
enclosure PR,
[openflight-enclosure#12](https://github.com/open-flight/openflight-enclosure/pull/12):
print
[`Front-Radar-No-Fill.stl`](https://github.com/Cormac131/openflight-enclosure/blob/feat/self-tapping/stls/radar/experimental/Front-Radar-No-Fill.stl),
and the full CAD model is
[`Open-Flight-Monitor-3.step`](https://github.com/Cormac131/openflight-enclosure/blob/feat/self-tapping/step/Open-Flight-Monitor-3.step)
on the same branch. It gives you access to
the IWR's SOP switches for reflashing, and it puts the IWR antenna array in
line with the camera. Self-trigger results from that mount carry forward,
because it is the preferred mount for new builds. Results from the old front
are not directly comparable: the antenna sits somewhere else, so the geometry
and the trigger zone differ.

If the self-trigger holds up, the SEN-14262, its `R17` resistor, its wiring
and its slot in the camera strip all go. That saves parts, and it removes the
build step people find hardest, so the whole build gets simpler.

The IWR is the better place to trigger from than the OPS243's own
[internal trigger](../reference/ops243-firmware-history.md). The OPS only
measures speed, so anything moving fast enough in its beam can fire it. The
IWR also sees range and angle, so it can tell a ball leaving the tee from
other motion.

**Once the azimuth is calibrated, the self-trigger could use a corridor.**
The trigger currently watches a range around the tee, so anything at that
distance can fire it: a practice swing, or someone moving beside the ball at
the same distance from the radar. With a calibrated azimuth zero (see
[Azimuth calibration](#azimuth-calibration-and-the-radar-workbench)), the IWR
knows where its target line is. It could then fire only on motion inside a
narrow corridor along that line, and ignore the same motion off to the side.
In theory that means fewer false triggers. Testing it needs the azimuth
calibration first, then sessions with deliberate practice swings and walking
beside the ball, counted with and without the corridor.

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

Updating the OPS243 firmware no longer needs a programmer.
OmniPreSense's [AN-013-H](../radar/AN-013-H_Code-Update.pdf) (November 2026)
updates any sensor over the same `J3` UART wires OpenFlight already runs to the
Pi. A sensor on 1.3.3 or later enters boot mode with the `B1` command. An older
sensor enters it when you hold `J2` pin 2 to ground while power-cycling it, so
even a sensor that shipped before 1.3.3 can be brought up to date with a thin
wire. Sensors shipping now should come with 1.3.3. The J-Link and the Raspberry
Pi Debug Probe route in
[PR #320](https://github.com/open-flight/openflight/pull/320) still work, but
they are no longer the only way.

What would help:

- **Try the grounding method on an OpenFlight build** and report it: your
  sensor's starting firmware (`?V`) and processor (`?P`), whether it entered
  boot mode, and whether factory settings and saved settings survived.
- **A Pi-only updater**, doing what Memtool does over the Pi's UART. Memtool
  is a desktop tool; with the radar already wired to the Pi, an updater on the
  Pi would make updates free for everyone.
- **A clear write-up for the `J2` pin 2 step**: which thin wire fits, and where
  to ground it on the OpenFlight radar front without taking the radar out.

Background is in
[OPS243 internal trigger and firmware updates](../reference/ops243-firmware-history.md).

## Test what newer OPS243 firmware gives us

Newer OPS243-A firmware is worth testing for features, not just fixes. The
first one to test is the **transmit-frequency shift**: the `T=n` command in
[AN-010-AF](../radar/AN-010-AF_API_Interface.pdf)
moves the carrier within the 24.0-24.25 GHz band (`?F` reads it back). In a
multi-bay setup, another launch monitor that happens to sit on the OPS243's
default 24.125 GHz could interfere with it. We want to validate whether
moving the OPS off that frequency avoids the interference, without hurting
ball-speed or spin accuracy, and whether the setting persists.

## Print the new radar front

**This is the preferred radar mount to print for new builds**, and the one to
use if you want to help with the IWR6843 self-trigger, the camera, sensor
fusion or the IWR6843 setup. It comes from Cormac's draft enclosure PR,
[openflight-enclosure#12](https://github.com/open-flight/openflight-enclosure/pull/12),
and is meant to be the radar mount from now on, so testing on it now means
your results carry forward. Use
[`Front-Radar-No-Fill.stl`](https://github.com/Cormac131/openflight-enclosure/blob/feat/self-tapping/stls/radar/experimental/Front-Radar-No-Fill.stl)
from the `feat/self-tapping` branch for heat-set inserts, or
`Front-Radar-No-Fill-Self-Tapping.stl` next to it for screws only.

It moves the IWR6843 inward, which does two things:

- **It puts the IWR antenna array in line with the camera.** The radar and the
  camera then look at the ball from nearly the same place, and that alignment
  is what makes radar-and-camera fusion work possible.
- **It makes the board's SOP (boot-mode) switches on S1 easier to reach**, so
  you can reflash the IWR without taking the case apart. That matters while
  the IWR firmware is still changing.

More revisions to the enclosure are coming, so feedback on fit, access and
print quality goes on that PR.

## Radome tests

At the moment the radar front leaves both radars **exposed**: nothing covers
them, because we have not done enough testing to know what can go in front
without hurting the measurements. A cover, or radome, would protect the boards
from dust, rain and stray balls. We would greatly appreciate tests of whether
some material can sit in front of the radars without performance suffering
too much:

- **Different materials and thicknesses**, for example printed PETG and PLA
  panels at a few wall thicknesses, polycarbonate, acrylic and fabric. Note
  the infill and print orientation for printed panels.
- **Both radars.** The OPS243 is at 24 GHz and the IWR6843 at 60 GHz, so a
  material that is fine for one may not be for the other.
- **What to compare**, with and without the cover on the same shots: OPS243
  ball and club speed and detection rate, IWR6843 launch angle and its
  confidence, and missed shots.
- **Feasibility**: how a cover would mount on the radar front, and whether it
  can be printed with the case.

Post results, and ideas for how a cover could work, in
[Discussion #154](https://github.com/open-flight/openflight/discussions/154),
which asks exactly this question.

## Measure the setup geometry automatically

Every IWR6843 setup needs its geometry typed in as launch arguments: the
distance to the ball, the distance to the net, the radar height and the ball
height ([Mount, aim, and measure](../iwr6843/mounting.md#measure-the-geometry)).
That makes setup harder than it should be. It is also easy to get wrong: people
set the values once and forget to adjust them for the next session, when the
mat, the bay or the tee position has changed. Wrong geometry costs the IWR its
launch angles without raising any error.

Could the IWR6843 measure the distance to the ball itself, on its own or
together with the camera, so you would not need to enter it? The ball is a
static return in front of the radar before every shot, and the camera can see
where it sits. What would help:

- **A ball-range measurement from the IWR's own frames** at address, checked
  against a tape on real rigs, with the mat and the golfer's feet nearby.
- **The camera's view of the ball** as a second estimate, or as a check on
  the radar's.
- **Height and net distance** too, if they can be inferred, so a session
  could start with no geometry arguments at all, and warn when the measured
  setup differs from the saved one.

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

## Make the software better

The hardware is only half of it. A train of open pull requests improves how
OpenFlight is used day to day, and they need review and testing on real
kiosks:

- [#284](https://github.com/open-flight/openflight/pull/284): each profile
  hides the clubs it doesn't carry.
- [#343](https://github.com/open-flight/openflight/pull/343): custom clubs,
  with several of the same type allowed.
- [#329](https://github.com/open-flight/openflight/pull/329): browsing past
  sessions, with camera replay.
- [#239](https://github.com/open-flight/openflight/pull/239): first-run kiosk
  setup and persisted live view modes.

They touch the same data: profiles, clubs, sessions and settings. Today much of
that is written to flat JSON files, so each feature brings its own file and
format. **Moving data and settings to SQLite** would give them one store to
share, with real queries for history and safer writes. Help with designing that
storage, migrating the existing files, and reviewing the PRs against it is very
welcome.

## Azimuth calibration and the radar workbench

The IWR6843 azimuth calibration (a can-target hallway trial on 2026-11-18 put
the azimuth zero about 4–5° off;
[release with photos and captures](https://github.com/HuggeK/OpenFlight-Radar-Workbench/releases/tag/can-azimuth-2026-11-18-trial),
[method](https://github.com/HuggeK/OpenFlight-Radar-Workbench/issues/2)) and the
[OpenFlight Radar Workbench](https://github.com/Robinante/OpenFlight-Radar-Workbench)
used to analyse the captures are being discussed in
[Discussion #371](https://github.com/open-flight/openflight/discussions/371).
Repeat runs on other rigs, other targets, and ideas for turning it into a
standard calibration step are all welcome there.
