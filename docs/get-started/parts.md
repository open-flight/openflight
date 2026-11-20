# OpenFlight Parts List

Hardware components for building the OpenFlight golf launch monitor.

> **Ordering shortcut:** A shared **[OpenFlight Mouser project](https://www.mouser.com/en/Tools/Project/Share?AccessID=4c97a00bbc)** is available for the parts Mouser stocks. Check it against the tables below before you order: anything Mouser does not carry has a direct vendor link here.

> **Next step after gathering parts:** See the [Raspberry Pi Setup Guide](../setup/raspberry-pi.md) for assembly and software installation.

## Core Components

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **OPS243 Radar** | Doppler radar for ball/club speed detection | [OmniPreSense](https://omnipresense.com/product/ops243-doppler-radar-sensor/) | $249 |
| **Raspberry Pi 5** | Main compute unit (4GB+ recommended) | [Adafruit](https://www.adafruit.com/product/5812) | $130 |
| **Raspberry Pi Touch Display 2 (7")** | The display. Portrait IPS panel with five-finger capacitive touch, on the Pi's DSI ribbon port, powered from the Pi's GPIO header. Print the [`Screen-RPI-Display-2.stl`](https://github.com/open-flight/openflight-enclosure/blob/main/stls/screen/v1/Screen-RPI-Display-2.stl) screen front for it. In the box: the GPIO power cable, two short ribbons (standard–standard and standard–mini), and eight M2.5 screws. You also need the two rows below | [Raspberry Pi](https://www.raspberrypi.com/products/touch-display-2/) | $60 |
| **Raspberry Pi Display Cable, Standard–Mini, 200 mm (SC1131)** | Display 2 accessory: the 22-way (Pi 5 "mini") to 15-way (display "standard") DSI ribbon. Buy **200 mm**. The ~100 mm ribbon in the Display 2 box does not reach the Pi in the v3 case, because the case mounts the Pi and UPS to the shell rather than to the back of the screen ([Cable lengths](#cable-lengths-enclosure-v3)). 300 and 500 mm fit but leave a loop to stow | [Raspberry Pi](https://www.raspberrypi.com/products/display-cable/) / [Mouser](https://www.mouser.com/ProductDetail/Raspberry-Pi/SC1131?qs=HoCaDK9Nz5eSyEpyddOkmQ%3D%3D) / [Amazon](https://www.amazon.com/dp/B0GX33S2C6) (Raspberry Pi's own listing; pick 200 mm) | ~$2 |
| **Jumper wires (female/male, 75 mm)** | Display 2 accessory. Header-pin extensions: the female end goes onto a Pi GPIO pin and the male end re-presents that pin for a second connector. They keep the 5V rail reachable for the OPS243 when the Display 2 power cable is also on the header, so one connector does not cover the whole rail. 75 mm is the shortest female/male length Mouser stocks (Adafruit 1953, Mouser 485-1953, 20-wire ribbon). $1.95 at Adafruit list; Mouser's price for 485-1953 is unverified | [Mouser](https://www.mouser.com/ProductDetail/Adafruit/1953?qs=GURawfaeGuBbX2LiaCDbnA%3D%3D) | $2 |

> **NOTE on OPS243-A-W (WiFi version):** The standard **OPS243-A** (USB only) is strongly recommended. The WiFi module on the OPS243-A-W drives the internal UART receive line, preventing direct connection to the Raspberry Pi GPIO UART (Layout A). However, if you already have the WiFi version, it can still be used over USB with a powered USB hub (Layout B) when paired with the IWR6843 angle radar.

> **Display alternative (cheaper): Hosyond 7" DSI Display.** Hosyond's 7-inch
> **DSI** panel (sold as "7inch DSI Display-A") is a landscape 800×480 IPS panel
> with five-point capacitive touch. It uses the same DSI ribbon port as the
> Display 2 and is listed for the Pi 4B and Pi 5. Buy the **DSI** version.
> Hosyond also sells HDMI panels, and those are not this part. Print the
> [`Screen-800x480.stl`](https://github.com/open-flight/openflight-enclosure/blob/main/stls/screen/v1/Screen-800x480.stl)
> screen front for it. Its connector is the 15-way "standard" one, so if the
> box has no Pi 5 ribbon long enough, the 200 mm SC1131 above fits it. It takes
> 5V from the Pi header as well, so the female/male jumpers apply the same way.
> [Hosyond](https://hosyond.com/) /
> [Amazon search](https://www.amazon.com/s?k=Hosyond+7+inch+DSI+display).
>
> **HDMI panels work, but are not recommended.** An HDMI touchscreen such as the
> HMTECH 7" 1024×600 runs too, with the
> [`Screen-1024x600.stl`](https://github.com/open-flight/openflight-enclosure/blob/main/stls/screen/v1/Screen-1024x600.stl)
> screen front. It needs a micro-HDMI cable for video plus two USB cables for
> touch and power, all of which have to be routed inside the case. A single
> DSI ribbon is simpler.

<details markdown="1">
<summary>Display and screen-front dimensions</summary>

Panel figures are from the makers' own pages. The screen-front figures are the
overall bounding box of each STL in
[openflight-enclosure `stls/screen/v1/`](https://github.com/open-flight/openflight-enclosure/tree/main/stls/screen/v1),
measured from the files as published on 2026-11-20.

| Panel | Resolution | Outline (W × H × D) | Active area | Power | Screen front to print | Screen front size |
|---|---|---|---|---|---|---|
| Raspberry Pi Touch Display 2, 7" | 720 × 1280, portrait | 120 × 189.5 × 15 mm | 86.94 × 154.56 mm | 2.4 W, from GPIO | `Screen-RPI-Display-2.stl` | 200.0 × 117.5 × 53.8 mm |
| Hosyond 7" DSI Display-A | 800 × 480, landscape | 192.96 × 110.76 × 20.13 mm | 154.21 × 85.92 mm | 2.2 W, from GPIO | `Screen-800x480.stl` | 200.0 × 117.5 × 53.8 mm |
| HMTECH 7" HDMI (not recommended) | 1024 × 600 | — | — | from USB | `Screen-1024x600.stl` | 200.0 × 119.6 × 53.8 mm |

Sources: the [Touch Display 2 product page](https://www.raspberrypi.com/products/touch-display-2/),
[documentation](https://www.raspberrypi.com/documentation/accessories/touch-display-2.html)
and 7-inch product brief; Hosyond's *7inch DSI Display-A User Manual*, linked
from [hosyond.com](https://hosyond.com/). The enclosure's own
[screen page](https://github.com/open-flight/openflight-enclosure/blob/main/docs/parts/screen.md)
does not yet say which panel the 800×480 front was drawn around, so the
Hosyond fit is from the outline above, not from a test print.

</details>

## Sound Trigger (for Rolling Buffer Mode)

The sound trigger detects club impact to precisely time radar captures. Essential for spin detection via rolling buffer mode.

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **SparkFun SEN-14262** | Sound Detector with envelope/gate outputs | [SparkFun](https://www.sparkfun.com/products/14262) | $12 |
| **Through-hole resistor** | For R17 pad on SEN-14262 to reduce sensitivity (see note) | Any electronics supplier | $1 |
| **Jumper wires (female/female, 300 mm)** | 8 wires out of one pack: the detector's `GATE` → `HOST_INT`, `VCC` and `GND`; the OPS243 → Pi ground run; the OPS243 `TxD`/`RxD`/5V of Layout A; and `GATE` → Pi BCM17 for the angle radar. Female both ends — every header they meet is male pins. Buy **300 mm**, not 150, so the camera strip lifts off with the detector still wired ([Cable lengths](#cable-lengths-enclosure-v3)). SparkFun PRT-09389, 10 wires, $4.95; Mouser's 474-PRT-09389 is unverified, and the 150 mm PRT-12796 pack covers only the OPS243 ↔ Pi runs | [Mouser](https://www.mouser.com/c/?q=PRT-09389) / [SparkFun](https://www.sparkfun.com/jumper-wires-premium-12-f-f-pack-of-10.html) | $5 |

> **R17 resistor:** The SEN-14262 is rated for 5V but runs at 3.3V in this setup, which can cause the GATE output to stick high. Soldering a resistor into the R17 through-hole position (in parallel with the onboard 100kΩ R3) reduces preamp gain and fixes this. Start with 47kΩ; use a lower value (e.g. 33kΩ) if the sensor is still too sensitive for your environment.

### Sound Trigger Wiring

```
SEN-14262               Raspberry Pi           OPS243
┌───────────┐          ┌──────────┐          ┌──────────┐
│ VCC ──────┼──────────┤ 3.3V     │          │          │
│           │          │          │          │          │
│ GATE ─────┼──────────┼──────────┼──────────┤ HOST_INT │
│           │          │          │          │ (J3 P3)  │
│ GND ──────┼──────────┤ GND      ├──────────┤ GND      │
│           │          │          │          │ (J3 P1)  │
└───────────┘          └──────────┘          └──────────┘
```

See [sound-trigger-wiring.md](../build/sound-trigger.md) for detailed instructions and troubleshooting.

> The OPS243's own internal trigger, the firmware it needs and the programmer
> used to update older sensors used to be listed here. None of that is needed
> to build OpenFlight. How it evolved is now in
> [OPS243 internal trigger and firmware updates](../reference/ops243-firmware-history.md).

## Angle Radar (TI IWR6843) — CURRENT

This is the supported angle radar. It measures vertical and horizontal launch
angle, and supplies the pre-impact frames club path is derived from.

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **TI IWR6843LEVM** | 60 GHz mmWave evaluation board, 4 RX × 3 TX. The kit comes with the micro-USB cable that connects it to the Pi, so there is no cable to buy | [TI](https://www.ti.com/tool/IWR6843LEVM) | $172 |
| **Jumper wire** | 1 wire: detector `GATE` → Pi BCM17 / physical pin 11, alongside the existing `GATE` → OPS `HOST_INT`. Female/female, out of the same 300 mm SparkFun PRT-09389 pack as the sound-trigger wires above, so it is not a separate purchase | [Mouser](https://www.mouser.com/c/?q=PRT-09389) | in that pack |

The board needs **custom firmware** — it does not work out of the box. The
stock TI demo does not expose the raw radar cube OpenFlight needs. A validated
prebuilt image ships in `firmware/releases/`, so you do not need the TI
toolchain to flash it.

You also need physical access to the board's **boot-mode switch (S1.1)** and
**RESET button** to flash. Both are on the LEVM itself; nothing to buy.

### IWR6843 Setup

Two connection layouts are supported, and which one you can use depends on your
OPS243 variant:

| Layout | OPS243 connection | Extra parts needed |
|--------|-------------------|--------------------|
| **A (validated)** | Pi GPIO UART header | 4 jumper wires (5V, GND, TX, RX) |
| **B** | Powered USB hub | [Powered USB hub](https://www.amazon.com/dp/B0CN3F9Y1Z) (~$20) |

Layout A keeps the TI board on USB and moves the OPS243 to the Pi's GPIO
header, which is what the power budget requires — the Pi cannot supply both
radars over USB.

> [!WARNING]
> Layout A does **not** work with a **WiFi-equipped OPS243-A**. Its onboard WiFi
> module already drives the radar's UART receive line, so the Pi cannot send it
> commands. WiFi OPS boards must use Layout B with a powered hub.

Full instructions: **[IWR6843 Operator Guide](../iwr6843/index.md)** for wiring,
flashing, mounting, and geometry; **[Connecting the OPS243 to the
Pi](../build/ops243-uart.md)** for the OPS side of Layout A.

### Optional Enclosure Inclinometer

An LIS3DH mounted to the enclosure base lets OpenFlight compensate the IWR6843
tilt when the rig is placed on uneven ground.

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **Adafruit LIS3DH breakout** | Triple-axis accelerometer with STEMMA QT connectors | [Adafruit product 2809](https://www.adafruit.com/product/2809) | $5 |
| **JST-SH cable kit (Qwiic-to-Dupont)** | Qwiic/STEMMA QT to female Dupont jumpers, used in the validated build. The LIS3DH plugs into its STEMMA QT socket and the Dupont ends push straight onto the Pi GPIO header, so no soldering is needed — the alternative is soldering a header onto the breakout and wiring that by hand | [Amazon](https://www.amazon.com/Connector-Compatible-Development-Sensors-Drivers/dp/B0GJPRX4YT) | ~$10 |
| **Qwiic-to-Dupont cable (single)** | Mouser-stocked equivalent of the kit above: one JST-SH 4-pin to female Dupont cable, 150 mm (SparkFun CAB-17261 / Mouser 474-CAB-17261, in the shared Mouser project; Adafruit 4397 is the same cable direct). Enough on its own for the LIS3DH → Pi header run, and it keeps the inclinometer orderable from Mouser. 150 mm is the only length made in this configuration — the shorter Qwiic cables have no Dupont end. Plug it into the LIS3DH socket nearer the Pi ([Cable lengths](#cable-lengths-enclosure-v3)) | [Mouser](https://www.mouser.com/ProductDetail/SparkFun-Electronics/CAB-17261?qs=DRkmTr78QAQLJE%2FDhtP97Q%3D%3D) / [Amazon](https://www.amazon.com/dp/B0992PHLBC) / [Adafruit 4397](https://www.adafruit.com/product/4397) | ~$2 |

See the **[LIS3DH Inclinometer Setup Guide](../build/inclinometer.md)** for wiring,
mounting, calibration, startup flags, and troubleshooting.

---

## Angle Radar (K-LD7) — DEPRECATED

> **⚠️ DEPRECATED — do not buy for new builds.** The K-LD7 angle radars have been superseded by a more capable radar chip. K-LD7 support remains in the software for existing builds but will not receive further development. The parts below are listed for reference only.

<details markdown="1">
<summary>K-LD7 parts and wiring (existing builds only)</summary>

Two K-LD7 modules measure launch angle (vertical) and club path / aim direction (horizontal). The OPS243 handles speed; the K-LD7s provide **angle and distance only** (speed data aliases above 62 mph).

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **RFbeam K-LD7 (×2)** | 24 GHz FMCW radar for angle + distance | [RFbeam](https://rfbeam.ch/product/k-ld7-radar-transceiver/) | ~$60 ea |
| **FTDI USB-to-Serial adapter (×2)** | 3.3V FTDI board for K-LD7 UART (e.g. FT232RL) | [Amazon](https://www.amazon.com/s?k=ftdi+3.3v+usb+serial) | ~$10 |

> **EVAL board not required.** The K-LD7 bare module communicates over 3.3V UART (TX, RX, VCC, GND). Any 3.3V FTDI USB-to-serial adapter works. The official K-LD7 EVAL board (~$120 each) is only needed if you want the RFbeam GUI software for configuration — OpenFlight configures the radar over serial automatically.

### K-LD7 Connection

Each K-LD7 connects via a 3.3V FTDI adapter, appearing as `/dev/ttyUSB*` on Linux.

```
K-LD7 Module (UART) → FTDI 3.3V Adapter → USB → Raspberry Pi
```

One unit is mounted vertically (launch angle), one horizontally (club path / aim direction). A `--kld7-angle-offset` parameter corrects for mounting geometry — see the [setup guide](../setup/raspberry-pi.md) for calibration.

</details>

## Accessories

> **Everything about power is in [Powering OpenFlight](power.md)**, including
> the barrel-jack polarity rules and the lithium-cell safety. Choosing how to
> feed the unit — the official 27 W supply, a Geekworm UPS HAT, a wide-input
> DC-to-USB-C module, a USB-C PD charger or power bank, or PoE — decides
> several parts at once, and one of those routes can destroy the whole build if
> it is wired backwards. The rows below are the accessories every build needs
> whichever route you pick.

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **Raspberry Pi Active Cooler** | Clip-on heatsink + fan for the Pi 5 (SC1148). Recommended: the kiosk runs the UI, radar capture, and FFT processing continuously, and a passively cooled Pi 5 throttles under sustained load | [Mouser](https://www.mouser.com/ProductDetail/Raspberry-Pi/SC1148?qs=HoCaDK9Nz5fqo0izK2taew%3D%3D) | $8 |
| MicroSD Card (32GB+) | For Pi OS and software | Any Class 10 | $10 |


## Optional

> The UPS HATs, the DC adapter, the panel jack and the power button are in
> **[Powering OpenFlight](power.md)**.

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **InnoMaker OV9281 global-shutter camera** | High-speed monochrome camera for experimental vision work. Camera software is not enabled in the production kiosk path | [Amazon](https://www.amazon.com/dp/B09WTP5GZH?th=1) | ~$30 |

See [Camera and YOLO Experiments](../development/camera-yolo.md) before buying the
camera; the standard setup does not install its optional software dependencies.

---

## Cable Lengths (Enclosure v3)

The parts rows above already say which length to buy; this section is the
measurement behind them, for anyone changing the enclosure or the wiring.
Ordinary builders can skip it. The DC-jack and power-button runs are kept here
with the rest, but the parts themselves are in
[Powering OpenFlight](power.md); the rear hole dimensions they have to fit are
[documented in the enclosure repository](https://github.com/open-flight/openflight-enclosure/blob/main/docs/parts/shell.md#rear-io-openings).

<details markdown="1">
<summary>Measured cable runs on the openflight-enclosure v3 case</summary>

Measured on the v3 CAD in the
[openflight-enclosure](https://github.com/open-flight/openflight-enclosure)
repository (`Open-Flight-Monitor-3.step`; the 2026-09-15 release and the PR #10
re-layout merged on 2026-09-21 share the same shell and mounts) from the features that
locate each part: the OPS243 and IWR6843LEVM models on the radar front, the
mic hole and the OV9281 on the camera strip, the X1202's 89 × 58 mm mount
pockets on the rear wall, the three Adafruit bays on the floor, the Display 2
bezel, and the three cutouts in the rear recess wall (the Ethernet coupler at
the left, a Ø12.5 mm hole in the middle that the CAD gives to the DC jack, and
a Ø12.5 mm hole at the right for the power button). The Pi, UPS, sound
detector and display are not in the CAD, so their connectors are placed from
their own drawings: the X1202 from Geekworm's interface photo (DC jack and
`XH2.54` DC input at its top-left corner, the external-button header `PSW` at
its bottom-left), the Pi 5 portrait on top of it with the USB ports up, the
GPIO header along its left edge and the DSI/CSI connectors along its right
edge, and the Display 2's FPC connector at the bottom centre of the panel.
Treat those as ±15 mm. Straight-line is connector to connector; routed is a
right-angle path along the walls plus the plug bodies. Buy the length in the
last column.

| Run | From → to | Straight-line | Routed | Buy |
|-----|-----------|---------------|--------|-----|
| OPS243 UART + 5V + GND (4 wires) | OPS243 `J3` (radar front, bottom right) → Pi GPIO header (rear wall) | ~60 mm | ~115 mm | 150 mm works; the 300 mm pack covers it |
| Sound trigger `GATE` → `HOST_INT` | Detector on the camera strip → OPS243 `J3` pin 3 | ~90 mm | ~150 mm | 300 mm |
| Sound trigger `VCC`, `GND`, and `GATE` → BCM17 | Detector → Pi GPIO header | ~70 mm | ~135 mm | 300 mm (150 mm reaches closed, not with the strip lifted off) |
| Inclinometer | Pi GPIO header → LIS3DH in the left floor bay | ~65 mm | ~135 mm | 150 mm Qwiic-to-Dupont, ~15 mm spare |
| IWR6843 USB | LEVM `J5` (top edge of the board, radar front) → Pi USB-A | ~50 mm | ~130 mm | the micro-USB cable in the LEVM kit; 150 mm is the floor |
| Touch Display 2 DSI | Pi `DISP` FPC connector (right edge of the Pi) → display FPC connector | ~65 mm | ~140 mm | 200 mm Standard–Mini (SC1131); the ~100 mm ribbon in the box does not reach |
| Camera CSI, if fitted | Pi `CAM` FPC connector → OV9281 on the camera strip | ~55 mm | ~125 mm | 200 mm Standard–Mini camera cable (SC1128); not in the tables |
| Ethernet, if fitted | Panel coupler in the rear recess (left) → Pi RJ45 | ~100 mm | ~195 mm | a 6 in / 15 cm patch only pulled straight; 1 ft / 30 cm is comfortable |
| DC panel jack, header option | Middle rear hole → X1202 `XH2.54` DC input, top-left of the UPS | ~45 mm | ~80 mm | any 150 mm jack lead |
| DC panel jack, barrel option | Middle rear hole → the X1202's own barrel jack, top-left corner of the UPS, opening up | ~35 mm | ~80 mm | any 150 mm jack lead plus a 150 mm plug lead |
| X1202 power button | Right rear hole → X1202 `PSW` header, bottom-left of the UPS | ~155 mm | ~245 mm | the 1152's 200 mm leads only pulled straight across the Pi stack |
| X1202 power button, holes swapped | Middle rear hole → `PSW`, with the DC jack in the right hole (~115 mm straight, ~170 mm routed to the `XH2.54` input) | ~105 mm | ~155 mm | the 1152's 200 mm leads with slack; a 200-250 mm jack lead |

The fronts unscrew from the shell (radar, then camera strip, then screen), so
servicing means lifting the camera strip off with the sound detector still on
it and laying it in front of the case, which adds ~100 mm to the two detector
runs. That, not the closed-case distance, is why the detector wires are
300 mm.

Both Ø12.5 mm rear holes are the same size, so which one takes the button and
which the DC jack is the builder's choice; the CAD puts the DC jack in the
middle. With the Adafruit 1152 leads, put the button in the middle hole
instead: it is the shorter run, and the jack lead is the easier one to buy
long.

</details>

## Enclosure Hardware (Inserts and Screws)

The heat-set inserts and screws are listed with the case, not here: see
**[Required hardware](https://github.com/open-flight/openflight-enclosure#documentation)**
in the [openflight-enclosure](https://github.com/open-flight/openflight-enclosure)
repository, which gives the insert size and count per printed part and the
screw lengths. One thing to plan for while you are there: the case screws sit
deep in the shell, so whatever driver matches the heads you buy needs about
90 mm of reach. A stubby one will not get to them. The insert
family and where to order it are being settled in
[openflight-enclosure#4](https://github.com/open-flight/openflight-enclosure/issues/4);
until that lands, buy what the enclosure page says for the parts you print.

**Two more parts if you fit a UPS HAT.** The shell has a round rear hole for a
**DC barrel jack** and another for a **power button**, and neither is filled by
anything on the enclosure page. They are not case hardware, so they are not in
the list above, and they are only needed on the UPS route. Both are in
[Powering OpenFlight](power.md#getting-power-to-it), along with the leads that
join the jack to the board. The holes they have to fit are
[documented in the enclosure repository](https://github.com/open-flight/openflight-enclosure/blob/main/docs/parts/shell.md#rear-io-openings).

## Cost Summary

Approximate, in USD. A few figures are unverified and the rows above say which.
Read it as stages rather than one number: each step below is a decision, and
the running total tells you what it costs to stop there.

### 1. What every build needs

| Group | What it covers | ~Price |
|---|---|---|
| Core | OPS243-A radar, Raspberry Pi 5, Touch Display 2 with its 200 mm ribbon and female/male jumpers | $443 |
| Sound trigger | SEN-14262, the `R17` resistor, the 300 mm jumper pack | $18 |
| Accessories | Active cooler, microSD | $18 |
| Power | [Route 1](power.md#route-1-the-official-27-w-usb-c-supply), the official 27 W supply, whose cable is captive | $14 |
| Enclosure | Printed parts, plus heat-set inserts and screws | $52 |
| **Base build** | a working unit: ball speed, club speed, smash factor, spin, estimated carry | **~$545** |

The enclosure is in here rather than under Optional because you need one
whichever radars you fit. Nothing measures repeatably until the boards are
held in a fixed, repeatable arrangement, and the **same printed set covers
both builds**: the radar front carries mounts for the OPS243 and the
IWR6843LEVM, so an OPS-only build prints exactly the same parts and leaves the
IWR mounts empty. The $52 assumes you print it yourself; add a print
service if you do not own a printer.

The core line is priced with the Touch Display 2. The Hosyond DSI panel in
[Core Components](#core-components) is the cheaper option, and its own listing
has the current price.

### 2. Add the angle radar

| Add | What it buys | ~Price | Running |
|---|---|---|---|
| **TI IWR6843LEVM** | measured launch angle and direction, and club path | $172 | **~$717** |
| 2× K-LD7 + FTDI adapters | the same, but **deprecated** — not for new builds | $140 | — |

The board is the whole cost here. Its micro-USB cable comes in the kit and
its one `GATE` → BCM17 jumper comes out of the sound-trigger pack, so neither
is charged separately.

### 3. Optional

Independent of each other; add the ones you want.

| Add | What it buys | ~Price |
|---|---|---|
| Inclinometer | LIS3DH and its Qwiic-to-Dupont cable, so the rig can sit on uneven ground | $15 |
| Camera | InnoMaker OV9281, experimental vision work only | $30 |
| **Battery power, [X1202](power.md#route-2-the-geekworm-x1202-x1206-ups-hat)** | HAT $48, four 18650s $24, 12 V adapter $15, panel DC jack $8, the lead into the board $2-3, and a 12 mm button with leads $6 — $104 in all. It **replaces** the $14 supply in step 1, so the net add is | **+$90** |
| Battery power, [X1206](power.md#route-2-the-geekworm-x1202-x1206-ups-hat) instead | the same list with a $52 HAT and four 21700s at $32 | +$102 |

### Worked totals

| Build | ~Price |
|---|---|
| **Base build — OPS243 only, in its case** | **~$545** |
| + angle radar | ~$717 |
| + inclinometer and camera | ~$762 |
| **Everything, battery powered (X1202)** | **~$852** |
| Everything, with the X1206 instead | ~$864 |

> **The battery line includes its own wiring.** A UPS HAT cannot be powered in
> a closed case without a DC adapter, a panel jack and the leads that join
> them, so all of it is in that figure and itemised in
> [Powering OpenFlight](power.md#route-2-the-geekworm-x1202-x1206-ups-hat).
>
> **If you can live with a supply that does not detach**, the same HAT will run
> on the official 27 W supply from step 1 instead. Both boards take 5 V 5 A on
> their own USB-C socket, so the adapter, the panel jack and the leads come out
> of the list: **+$78** rather than +$90 on the X1202, and no barrel plug in the
> build to get backwards. Nothing unplugs at the case and the cells charge more
> slowly, so it is a trade rather than an upgrade —
> [both sides of it are here](power.md#route-2b-the-same-ups-on-a-captive-usb-c-supply).

The enclosure hardware line is an allowance for the v3 set (short heat-set
inserts in three sizes, plus the case screws and the board screws) at pack
prices. It was $35 for the v2 set and is
carried unchanged until the v3 set is priced against the insert decision in
[openflight-enclosure#4](https://github.com/open-flight/openflight-enclosure/issues/4).

Cell prices are estimates: ~$6 each for an 18650 (a Samsung 35E, Molicel P28A
or LG MJ1 sells for about that) and ~$8 each for a 21700 (a Samsung 50E or
Molicel P42A sells for $6-9). With the X1206 the HAT is $52 at Geekworm list
and its four cells come to $32, so the battery line is $116 gross and
+$102 net, and the full build reaches ~$864. Nothing else
changes: the X1206 V2.0 carries its four 21700 holders on the board, uses the
same power-button header, and takes the same 12 V adapter.

OpenFlight works without any angle radar: you get ball speed, club speed, smash
factor, spin rate, and estimated carry. The angle radar adds measured launch
angle (vertical and horizontal) and is what club path is derived from.

If you are building new, buy the **IWR6843**, not the K-LD7s. It costs a little
more than the two K-LD7s plus their FTDI adapters ($172 vs $140) and replaces
both of them with one board. The K-LD7 path is **deprecated** and kept only so
existing builds keep working.
