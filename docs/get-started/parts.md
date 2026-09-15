# OpenFlight Parts List

Hardware components for building the OpenFlight golf launch monitor.

> **Ordering shortcut:** A shared **[OpenFlight Mouser project](https://www.mouser.com/en/Tools/Project/Share?AccessID=4c97a00bbc)** is available for the parts Mouser stocks — open it, save it to your own Mouser account, and add the whole list to your cart in one step instead of searching for each item. Check it against the tables below before you order: anything Mouser does not carry has a direct vendor link here.

> **Next step after gathering parts:** See the [Raspberry Pi Setup Guide](../setup/raspberry-pi.md) for assembly and software installation.

## Core Components

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **OPS243 Radar** | Doppler radar for ball/club speed detection | [OmniPreSense](https://omnipresense.com/product/ops243-doppler-radar-sensor/) | $249 |
| **Raspberry Pi 5** | Main compute unit (4GB+ recommended) | [Adafruit](https://www.adafruit.com/product/5812) | $130 |
| **7" Touchscreen Display** | HMTECH 7" 1024x600 IPS display | [Amazon](https://www.amazon.com/dp/B0D3QB7X4Z) | $46 |

> **NOTE on OPS243-A-W (WiFi version):** The standard **OPS243-A** (USB only) is strongly recommended. The WiFi module on the OPS243-A-W drives the internal UART receive line, preventing direct connection to the Raspberry Pi GPIO UART (Layout A). However, if you already have the WiFi version, it can still be used over USB with a powered USB hub (Layout B) when paired with the IWR6843 angle radar.

> **Display alternative:** The [Raspberry Pi Touch Display 2](https://www.raspberrypi.com/products/touch-display-2/) (7" 720x1280, MIPI DSI) also works with the Pi 5. If you use it, print the `Touch_Display2_backplate.stl` and `Touch_Display2_shell.stl` from the IARC case instead of `monitor_shell.stl` — see the [IARC case instructions](../build/enclosure.md).

## Sound Trigger (for Rolling Buffer Mode)

The sound trigger detects club impact to precisely time radar captures. Essential for spin detection via rolling buffer mode.

> **Optional path, not merged yet:** [PR #221](https://github.com/open-flight/openflight/pull/221) adds an opt-in `--trigger hardware` mode in which the OPS243 fires the rolling-buffer dump from its own internal speed trigger, with no SEN-14262 in the loop. It requires OPS243-A firmware v1.3.1. If it lands and your OPS243 can be updated to that firmware, the parts in this section become optional. Until then the sound trigger is the supported trigger and stays in the totals.

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **SparkFun SEN-14262** | Sound Detector with envelope/gate outputs | [SparkFun](https://www.sparkfun.com/products/14262) | $12 |
| **Through-hole resistor** | For R17 pad on SEN-14262 to reduce sensitivity (see note) | Any electronics supplier | $1 |
| **Jumper wires (female/female, 300 mm)** | 8 wires out of one pack: `GATE` → `HOST_INT`, `VCC` → 3.3V and `GND` → `GND` from the sound detector, the OPS243 → Pi ground run, the OPS243 `TxD`/`RxD`/5V wires of Layout A, and the `GATE` → Pi BCM17 wire the angle radar needs. Female on both ends — the Pi GPIO header, the OPS243 J3 header, and headers soldered to the SEN-14262 are all male pins. 300 mm, not 150, because the sound detector sits on the back of the screen in the front half of the v2 enclosure while the OPS243 hangs from the bottom-right standoffs of the back half: that `GATE` → `HOST_INT` run is ~130 mm in a straight line, ~175 mm as routed, and longer still when the front half is lifted off for service, so a 150 mm wire does not reach (see [Cable lengths](#cable-lengths-enclosure-v2)). SparkFun PRT-09389: 10 wires, 12 in / 305 mm, $4.95 at SparkFun list; Mouser's listing (474-PRT-09389) is unverified. The 150 mm PRT-12796 pack covers only the OPS243 ↔ Pi runs, which are ~50 mm | [Mouser](https://www.mouser.com/c/?q=PRT-09389) / [SparkFun](https://www.sparkfun.com/jumper-wires-premium-12-f-f-pack-of-10.html) | $5 |

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

## Angle Radar (TI IWR6843) — CURRENT

This is the supported angle radar. It measures vertical and horizontal launch
angle, and supplies the pre-impact frames club path is derived from.

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **TI IWR6843LEVM** | 60 GHz mmWave evaluation board, 4 RX × 3 TX | [TI](https://www.ti.com/tool/IWR6843LEVM) | $150 |
| **Micro-USB cable (data-capable), 250-300 mm** | Connects the LEVM's CP2105 serial bridge to the Pi — the LEVM's USB port is micro-USB. Charge-only cables will not enumerate. Length: on the v2 enclosure the board hangs antenna-out from the bottom-left standoffs, turned so the TX antennas sit above the RX antennas, which puts its micro-USB (`J5`) at the corner facing the Pi: ~70 mm from the Pi's USB-A ports in a straight line, ~110 mm as routed. 150 mm is the shortest that still leaves room for the two plug bodies and the bends; 250-300 mm (10-12 in) is comfortable | Any | $5 |
| **Jumper wire** | 1 wire: detector `GATE` → Pi BCM17 / physical pin 11, alongside the existing `GATE` → OPS `HOST_INT`. Female/female again — comes out of the same 300 mm SparkFun PRT-09389 pack as the sound-trigger wires above. On the v2 enclosure the detector (on the screen, front half) to the Pi header is ~85 mm in a straight line and ~120 mm as routed; 150 mm reaches with the case closed but not with the front half lifted off, which is why the pack is 300 mm | [Mouser](https://www.mouser.com/c/?q=PRT-09389) | $1 |

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
flashing, mounting, and geometry; **[Moving the OPS243 to the Pi GPIO
UART](../build/ops243-uart.md)** for the OPS side of Layout A.

### Optional Enclosure Inclinometer

An LIS3DH mounted to the enclosure base lets OpenFlight compensate the IWR6843
tilt when the rig is placed on uneven ground.

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **Adafruit LIS3DH breakout** | Triple-axis accelerometer with STEMMA QT connectors | [Adafruit product 2809](https://www.adafruit.com/product/2809) | $5 |
| **JST-SH cable kit (Qwiic-to-Dupont)** | Qwiic/STEMMA QT to female Dupont jumpers, used in the validated build. The LIS3DH plugs into its STEMMA QT socket and the Dupont ends push straight onto the Pi GPIO header, so no soldering is needed — the alternative is soldering a header onto the breakout and wiring that by hand | [Amazon](https://www.amazon.com/Connector-Compatible-Development-Sensors-Drivers/dp/B0GJPRX4YT) | ~$10 |
| **Qwiic-to-Dupont cable (single)** | Mouser-stocked equivalent of the kit above: one JST-SH 4-pin to female Dupont sockets cable (Adafruit 4397, Mouser 485-4397). Enough on its own for the LIS3DH → Pi header run, and it keeps the whole inclinometer orderable from Mouser. 150 mm is the only length Adafruit makes in this JST-SH-to-female-socket configuration; the shorter 50-100 mm Qwiic cables are Qwiic-to-Qwiic and have no Dupont end. The chain does not have to stop at the LIS3DH: its second STEMMA QT socket can carry a Qwiic-to-Qwiic cable further down to the DS3502 digital potentiometer in the Optional table (sound-trigger gain research), so one Pi-to-header cable serves both boards. Length check on the v2 enclosure: the Pi header to the LIS3DH pad on the floor, left of the UPS stack, is ~105 mm in a straight line and ~140 mm routed down the side of the stack, so the 150 mm cable reaches with about a centimetre to spare — plug it into the LIS3DH socket nearer the Pi | [Mouser](https://www.mouser.com/en/ProductDetail/Adafruit/4397) | ~$1 |

See the **[LIS3DH Inclinometer Setup Guide](../build/inclinometer.md)** for wiring,
mounting, calibration, startup flags, and troubleshooting.

---

## Angle Radar (K-LD7) — DEPRECATED

> **⚠️ DEPRECATED — do not buy for new builds.** The K-LD7 angle radars have been superseded by a more capable radar chip. K-LD7 support remains in the software for existing builds but will not receive further development. The parts below are listed for reference only.

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

## Power & Accessories

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **27W USB-C Power Supply** | Official Pi 5 power supply (5.1V 5A). **It must be this supply, or one that negotiates 5V at 5A over USB PD.** The Pi 5 only releases its full downstream USB power budget when the supply reports 5A, and the two radars need that budget; a standard USB-C PD charger tops out at 3A at 5V, the Pi then caps the USB ports, and the radars brown out or fail to enumerate. Plug it straight into the Pi: do not route it through a USB-C extension or panel-mount pass-through in the case, because the extra contact resistance causes voltage sag and can make the 5V 5A negotiation fail. Not needed if you power the Pi from the UPS HAT (see below) | [Adafruit](https://www.adafruit.com/product/5814) | $14 |
| **Raspberry Pi Active Cooler** | Clip-on heatsink + fan for the Pi 5 (SC1148). Recommended: the kiosk runs the UI, radar capture, and FFT processing continuously, and a passively cooled Pi 5 throttles under sustained load | [Mouser](https://www.mouser.com/en/ProductDetail/Raspberry-Pi/SC1148) | $8 |
| **Jumper wires (female/male, 75 mm)** | Header-pin extensions: the female end goes onto a Pi GPIO pin and the male end re-presents that pin for a second connector. Used here to keep the 5V rail reachable for the OPS243 when the Touch Display 2 is also wired to the header, instead of one connector covering the whole rail. 75 mm is the shortest female/male length Mouser stocks (Adafruit 1953, Mouser 485-1953, 20-wire ribbon). $1.95 at Adafruit list; Mouser's price for 485-1953 is unverified | [Mouser](https://www.mouser.com/en/ProductDetail/Adafruit/1953) | $2 |
| MicroSD Card (32GB+) | For Pi OS and software | Any Class 10 | $10 |
| USB-A to Micro-USB Cable | For OPS243 radar connection | Any | $5 |

> **Cheaper and simpler with the UPS HAT:** if you fit the Geekworm X1202/X1206 from the Optional table, skip the 27W USB-C supply. Any barrel-jack supply that gives the UPS enough power feeds it (Geekworm asks for 3A or more anywhere in its 6-18V range; at 12V that also charges the cells at full rate while the Pi runs flat out, at lower voltages it does not; see the adapter row in the Optional table), and the UPS delivers 5.1V 5A to the Pi over its pogo pins; the Geekworm setup script sets `PSU_MAX_CURRENT=5000` so the Pi treats it as a 5A supply and keeps the full USB budget. For a device that lives in a case, the DC barrel jack is the better input either way: there is no USB PD negotiation to fail and no USB-C extension to sag, and a 12V adapter you already own will do.

> **UPS safety, read before the first charge:** **Never charge the 18650 cells below 0 °C (32 °F).** Lithium-ion cells charged below freezing plate lithium onto the anode, which permanently damages them and can make them unsafe; bring a cold rig indoors or let it warm up before connecting power. **Never connect the UPS's USB-C input and its barrel jack at the same time.** If you do power the UPS from the USB-C supply, plug it into the **UPS board's** USB-C socket, never into the Pi's own USB-C port while the Pi sits on the UPS. Details in the [Geekworm operator guide](../build/battery.md).

## Optional

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **Geekworm X1202 UPS HAT** | Rechargeable Pi 5 power using four matching flat-top 18650 Li-ion cells. Cells are not included | [Geekworm](https://geekworm.com/products/x1202) / [Amazon](https://www.amazon.com/dp/B0CRZ4ZXQW) | ~$48 + cells |
| **Geekworm X1206 UPS HAT** | Larger rechargeable Pi 5 power option using four matching flat-top 21700 Li-ion cells (unprotected only, per Geekworm), advertised up to 20,000mAh total. The four 21700 holders are on the board, like the X1202's 18650 holders, so no separate holder is needed; cells are not included. Same XH2.54 power-button header as the X1202, so the button row below fits it too. Its V2.0 DC input is 9-18V at 3A or more (narrower than the X1202's 6-18V), so the 12V examples in the adapter row apply; USB-C input is 5V 5A. No Amazon listing was found, so it is a Geekworm-direct order today | [Geekworm](https://geekworm.com/products/x1206) | $52 + cells |
| **X1202 power button: Adafruit 16 mm momentary button (1445) + XH quick-connect leads (1152)** | The X1202/X1206 expose their external power button on an XH2.54 2-pin header and need a momentary (spring-back) switch: the board reads press length the way the Pi 5 power button does, so a latching or toggle switch will not work. The 1445 is a 16 mm panel-mount momentary push button (normally open, two 0.11" tabs). The 1152 pack holds ten 20 cm wire pairs, each ending in a 2-pin JST XH plug for the header and two pre-crimped 0.11" quick-connects that push onto the button's tabs, so nothing is soldered or crimped; Adafruit's 1445 page names the 1152 pairs as its wiring. Needs a 16 mm panel hole. Adafruit showed the 1152 out of stock when checked and its Amazon listing (B00SK6M36U) as unavailable; Mouser's stock is unverified. **Lead length:** the button hole is in the top wall at the left and the X1202's `PSW` header at the bottom-right of the UPS board, ~180 mm apart in a straight line, so the 1152's 200 mm leads reach with ~20 mm to spare taken straight across the Pi stack; routed around the stack the run would be ~250 mm, which is accepted as not needed for this one cable | [Mouser (1445)](https://www.mouser.com/ProductDetail/Adafruit/1445) / [Mouser (1152)](https://www.mouser.com/ProductDetail/Adafruit/1152) / [Adafruit](https://www.adafruit.com/product/1445) | ~$6 |
| **DC adapter for the X1202 (5.5 × 2.1 mm barrel, center positive, 6-18V, 3A or more)** | Feeds the X1202 through its barrel jack instead of USB-C, which is the better input for a cased build: no USB PD negotiation to fail and no USB-C extension to sag. The X1202 accepts 6-18V DC on that jack and converts it to the 5.1V 5A the Pi 5 needs while also charging the cells. Geekworm's stated requirement is a current, not a wattage: "6-18Vdc, ≥3A", with charging at up to 3.2A into the cells; it publishes no watt figure. Where the power goes: the Pi 5 can draw up to 25.5W (5.1V × 5A) with both radars on its USB budget, and charging adds up to about 12W when the cells are low, plus converter losses. So the same 3A buys different things at different voltages: at 12V (36W) it runs the Pi at full load and charges at the same time, which is why Geekworm's own adapters are 12V; at 9V (27W) it runs the Pi but charging slows under load; at 6V (18W) it cannot carry a full Pi load and the cells drain while plugged in. **Check the amps against the voltage** rather than treating "6-18V" as "any adapter": a little over 25W is enough to run the Pi, not to run it and charge at full rate, and 12-18V at 3A or more covers both. Examples: MEAN WELL GST36 (12V 3A; GST36U12-P1J US plug, GST36E12-P1J EU plug, both at Mouser) or Geekworm's own PSU60 (12V 5A, also sold as an Amazon bundle with the X1202). Never connect the DC jack and the USB-C input at the same time | [Mouser (EU plug)](https://www.mouser.com/en/ProductDetail/MEAN-WELL/GST36E12-P1J) / [Mouser (US plug)](https://www.mouser.com/c/?q=GST36U12-P1J) / [Amazon (PSU60)](https://www.amazon.com/dp/B0BDF89DCB) | ~$15 |
| **InnoMaker OV9281 global-shutter camera** | High-speed monochrome camera for experimental vision work. Camera software is not enabled in the production kiosk path | [Amazon](https://www.amazon.com/dp/B09WTP5GZH?th=1) | ~$30 |
| **Adafruit DS3502 digital potentiometer** | I2C-controlled 10K digital potentiometer (STEMMA QT / Qwiic). Intended for the SEN-14262 `R17` gain trim: installed in series with a fixed 37kΩ resistor it gives a software-adjustable 37-47kΩ range, so preamp gain can be tuned from code instead of desoldering and swapping a fixed resistor. **Not yet built or tested** — no code drives it, and [sound-trigger-wiring.md](../build/sound-trigger.md) still assumes a soldered R17. Wiring plan: further down the same Qwiic chain as the inclinometer (Pi → Qwiic-to-Dupont → LIS3DH → Qwiic-to-Qwiic → DS3502), so it takes no extra Pi header pins. Research item for the sound-trigger path only; moot if the [PR #221](https://github.com/open-flight/openflight/pull/221) internal trigger replaces the sound trigger | [Adafruit](https://www.adafruit.com/product/4286) | ~$5 |
| **STEMMA QT / Qwiic-to-Qwiic cable (for the DS3502)** | The link from the LIS3DH's second STEMMA QT socket down to the DS3502, so the digital potentiometer joins the same I2C chain without taking any Pi header pins. Only needed if the DS3502 is fitted. Sold in 50-400 mm lengths; 100 mm (Adafruit 4210) is right if the DS3502 sits beside the LIS3DH on the enclosure floor, which is the sensible place for it: the two wires from its wiper to `R17` on the sound detector then run to the front half of the case (~175 mm straight, ~300 mm routed, so they come out of the same 300 mm jumper pack) rather than the I2C chain doing so | [Adafruit](https://www.adafruit.com/product/4210) | ~$1 |

See [Camera and YOLO Experiments](../development/camera-yolo.md) before buying the
camera; the standard setup does not install its optional software dependencies.

---

## Cable Lengths (Enclosure v2)

The parts rows above already say which length to buy; this section is the
measurement behind them, for anyone changing the enclosure or the wiring.
Ordinary builders can skip it.

<details markdown="1">
<summary>Measured cable runs on the v2 enclosure</summary>

Measured on the 2026-09-09 Onshape export of the v2 back housing (the
`back-pi-display2` variant, for the Touch Display 2), from the insert pockets
and standoffs that locate each board: the OPS243 on the 67 × 82 mm pattern at
the bottom right, the IWR6843LEVM on the 48.5 × 49 mm pattern at the bottom
left, the X1202 with the Pi stacked on it over the battery hatch, the LIS3DH on
its 21 × 13 mm pad on the floor to the left of that stack, and the camera on
the top shelf. The sound detector's position comes from the build, not the
CAD: it is screwed to the back of the display in the front half, top-right of
the case when closed, and its depth is assumed. Straight-line is
connector-to-connector; routed adds the bends around the Pi/UPS stack and over
the rim. Buy the length in the last column.

| Run | From → to | Straight-line | Routed | Buy |
|-----|-----------|---------------|--------|-----|
| OPS243 UART + 5V + GND (4 wires) | OPS243 `J3` (bottom-right standoffs, header turned toward the Pi) → Pi GPIO header | ~50 mm | ~80 mm | 150 mm works; the 300 mm pack covers it |
| Sound trigger `GATE` → `HOST_INT` | Detector on the screen (front half) → OPS243 `J3` pin 3 | ~130 mm | ~175 mm | 300 mm |
| Sound trigger `VCC`, `GND`, and `GATE` → BCM17 | Detector → Pi GPIO header | ~85 mm | ~120 mm | 300 mm (150 mm reaches closed, not opened) |
| Inclinometer | Pi GPIO header → LIS3DH on the floor, left of the UPS stack | ~105 mm | ~140 mm | 150 mm Qwiic-to-Dupont, ~10 mm spare |
| DS3502, if fitted | LIS3DH second socket → DS3502 beside it | ~25 mm | ~60 mm | 100 mm Qwiic-to-Qwiic |
| IWR6843 USB | LEVM `J5` (bottom-left standoffs, TX above RX, so `J5` faces the Pi) → Pi USB-A | ~70 mm | ~110 mm | 250-300 mm micro-USB; 150 mm is the floor |
| X1202 power button | 16 mm button in the top wall, left → X1202 `PSW` header, bottom-right of the UPS | ~180 mm | ~250 mm | the 1152's 200 mm leads, run straight across the Pi stack |
| Case DC jack (not in the tables yet) | Panel jack in the top shelf, right of the camera → X1202 barrel jack, bottom-left of the UPS | ~145 mm | ~205 mm | 250 mm pigtail |

Both radars hang from the tops of their standoffs with the antenna side facing
the back wall, so their connectors point at the floor and the wires plug in
from below; the Pi header is level with the rim. The halves are held together
by through-bolts, so servicing means lifting the front half off and laying it
beside the back half, which adds ~100 mm to the two detector runs. That, not
the closed-case distance, is why the detector wires are 300 mm.

</details>

## Enclosure Hardware (Inserts, Screws, Plunger)

What the v2 case needs to hold its boards and itself together, counted from
the insert pockets in the v2 back housing plus the front housing and kickstand
as drawn in the August assembly. The parts follow the decisions in
[openflight-enclosure#4](https://github.com/open-flight/openflight-enclosure/issues/4):
PEM's straight-wall brass IUTB heat-set inserts and Keystone pan-head screws
from Mouser, and the two items Mouser does not carry from Amazon. Prices are
small-quantity list prices from distributors that publish them (Bossard for
the inserts, DigiKey for the Keystone parts, J.W. Winco for the plunger);
Mouser's own prices and minimum quantities are unverified, see the note under
the table.

| Part | Needed | Where it goes | Link | ~Price |
|------|--------|---------------|------|--------|
| **PEM SI IUTB-M2.5 heat-set insert** (brass, 5.74 mm long, Ø4.0 mm hole, 6.5 mm deep) | 24 (buy 30) | 20 in the back housing: OPS243 ×4, IWR6843LEVM ×4, LIS3DH ×4, X1202 stack ×4, battery hatch ×4; 4 in the front housing for the through-bolts | [Mouser](https://www.mouser.com/ProductDetail/SI/IUTB-M25) | $6 |
| **PEM SI IUTB-M2 heat-set insert** (brass, 4.0 mm long, Ø3.2 mm hole) | 4 (buy 10) | camera board | [Mouser](https://www.mouser.com/ProductDetail/SI/IUTB-M2) | $2 |
| **PEM SI IUTB-M3 heat-set insert** (brass, 5.74 mm long, Ø4.0 mm hole) | 2 (buy 10) | kickstand pegs into the handle | [Mouser](https://www.mouser.com/ProductDetail/SI/IUTB-M3) | $2 |
| **M2.5 × 6 pan-head screw** (Keystone Electronics 29301, steel, zinc) | 20 (buy 25) | the same five sets of four: OPS243, IWR6843LEVM, LIS3DH, X1202, battery hatch. 1.6 mm PCB + 3.4 mm into the insert; the hatch is 3.5 mm thick, so the same screw does | [Mouser](https://www.mouser.com/c/?q=Keystone%2029301) | $5 |
| **M2 × 4 stainless pan-head screw** (APM HEXSEAL RM2X4MM 5701, O-ring under the head) | 4 | camera board; the only metal M2 × 0.4 screw Mouser stocks, about $1.15 each. Essentra 50M020040D006B (M2 × 6, nylon) is the $0.50 alternative for the same four holes | [Mouser](https://www.mouser.com/c/?q=RM2X4MM%205701) | $5 |
| **M3 × 12 pan-head screw** (Keystone Electronics 29314) | 2 (buy 10) | kickstand peg to handle, from inside | [Mouser](https://www.mouser.com/c/?q=Keystone%2029314) | $1 for two, $4 for ten |
| **#4 flat washer** (Keystone Electronics 4692, 3.25 / 7.1 / 0.64 mm) | 8 (buy 10) | under the heads of the eight M2.5 × 35 through-bolts, so the heads do not embed in the printed plastic each time the case is opened | [Mouser](https://www.mouser.com/c/?q=Keystone%204692) | $1 |
| **M2.5 × 35 socket-head screw** (DIN 912 / ISO 4762, A2 stainless) | 8 | screen to back housing ×4, front to back housing ×4. Mouser's longest metal M2.5 is 16 mm, so this stays an Amazon part until the CAD moves the front-housing inserts to the flange (the design option in openflight-enclosure#4) | [Amazon (25 pc)](https://www.amazon.com/dp/B082GJTKTY) | ~$10 |
| **Press-fit ball plunger, 3 mm × 4 mm, Ø2.4 mm stainless ball** (Ganter GN 614-3-NI class; McMaster 6052N11 in the drawing) | 2 | one per kickstand peg, the detent that holds the kickstand at its 10° stop. Not on Mouser: J.W. Winco / Ganter GN 614-3-NI (about $2.30 each, EU too), or a generic 5-pack on Amazon | [J.W. Winco](https://www.jwwinco.com/en-us/products/3.1-Indexing-locking-blocking-with-pins-and-ball-shaped-elements/Spring-plungers/GN-614-Stainless-Steel-Short-Press-Fit-Ball-Plungers) / [Amazon (5 pc)](https://www.amazon.com/dp/B0BXT6TJBV) | $5 |

About $37 as listed, $31 at exact counts; the Cost Summary carries $35.
What does not need buying: the Touch Display 2 mounts on its own tapped pads
with the M2.5 screws in its box, the Pi mounts to the X1202 with Geekworm's
supplied spacers, and the two housings need no inserts for the display.

> **Before ordering the inserts:** Mouser sometimes sells PEM inserts in
> multiples of 100. If all three lines come that way the inserts are ~$60
> rather than ~$10, and the CNC Kitchen "Standard" set on Amazon (same hole
> sizes, per openflight-enclosure#4) is the cheaper route. The IUTB-M2.5 price
> is taken as its M3 sibling's ($0.21 at 1-249 from Bossard; the same body),
> and the through-bolt pack price is a typical 25-pack figure, unverified on
> Amazon. The through-bolt count and length follow the August drawing; the v2
> CAD may shorten them.

## Cost Summary

| Category | ~Price |
|----------|--------|
| Core (OPS243, Pi 5, Display) | $355 |
| Sound Trigger (SEN-14262 + resistor + wires) | $18 |
| Power & Accessories | $37 |
| **Subtotal, no angle radar** | **~$410** |
| Angle Radar (IWR6843LEVM + cable + wire) — **current** | $156 |
| **Total with angle radar** | **~$566** |
| Optional Enclosure Inclinometer (LIS3DH + Qwiic-to-Dupont cable) | $15 |
| Optional extras (X1202 UPS HAT, four 18650 cells, 16 mm power button + leads, OV9281 camera) | $108 |
| Enclosure filament (v2 case, ~600 g PETG, estimate) | $15 |
| Enclosure hardware (30 inserts, screws, washers, two ball plungers) | $35 |
| **Complete build (total with angle radar + inclinometer + optional extras + filament + hardware)** | **~$739** |
| Optional extras with the X1206 instead (X1206 UPS HAT, four 21700 cells, 16 mm power button + leads, OV9281 camera) | $120 |
| **Complete build with the X1206 instead** | **~$751** |
| Angle Radar (2× K-LD7 + FTDI adapters) — **deprecated** | $140 |

The complete-build line uses the X1202 as the UPS (not the X1206), estimates its
four flat-top 18650 cells at ~$6 each ($24; a Samsung 35E, Molicel P28A, or LG
MJ1 each sells for about that), and leaves out the deprecated K-LD7 path, the
untested DS3502 and its Qwiic-to-Qwiic link, and the 12V DC adapter, which
replaces the 27W USB-C supply already counted rather than adding to it.

The enclosure filament line is an estimate from the CAD, not a slicer figure,
and it assumes **PETG**, not PLA: the case lives outdoors in the sun, and PETG
holds up to UV and to a hot car far better than PLA (it softens at ~80 °C
against PLA's ~60 °C), while still being a stock spool everywhere and an easy
print on an enclosed printer such as the P1S. The v2 back housing is a
195 × 248 mm tray, 52 mm deep inside, with 3.5 mm walls, a 2.5 mm floor,
57 mm radar standoffs and four corner bolt columns: about 300 g at two walls
and 10 % infill. The front half (the display frame) is ~170 g, the kickstand
~70 g, the battery hatch and the two kickstand pegs ~30 g together, and
supports plus a purge line take the print to roughly 600 g of PETG (a few
percent more than the same parts in PLA), two-thirds of a 1 kg spool. At
$20-25/kg that is ~$15. Replace it with the sliced weight once the v2 files
are published. Heat-set inserts, screws and the kickstand ball plunger are in
the enclosure repository's own parts list
([openflight-enclosure#4](https://github.com/open-flight/openflight-enclosure/issues/4))
and are priced in the Enclosure Hardware table above.

The enclosure hardware line is that table at small-quantity distributor
prices: ~$31 at exact counts, ~$40 once the spare inserts and screws the packs
give you are counted, carried here as $35. Everything but the eight M2.5 × 35
through-bolts and the two ball plungers is a Mouser line, so it rides in the
same order as the radars.

With the X1206 instead, the extras are $52 for the HAT (Geekworm list price)
+ four flat-top 21700 cells at ~$8 each ($32; a Samsung 50E or Molicel P42A
sells for $6-9) + the same $6 button pair + the $30 camera = $120, and the
complete build comes to ~$751. Nothing else changes: the X1206 V2.0 carries its
four 21700 holders on the board, uses the same power-button header, and takes
the same 12V adapter, since its 9-18V input sits inside the X1202's range.

If the [PR #221](https://github.com/open-flight/openflight/pull/221) internal
trigger lands and your OPS243 firmware can be updated, the Sound Trigger line
($18) becomes optional and drops out of every total above.

OpenFlight works without any angle radar: you get ball speed, club speed, smash
factor, spin rate, and estimated carry. The angle radar adds measured launch
angle (vertical and horizontal) and is what club path is derived from.

If you are building new, buy the **IWR6843**, not the K-LD7s. It costs about the
same as the two K-LD7s plus their FTDI adapters ($156 vs $140) and replaces both
of them with one board. The K-LD7 path is **deprecated** and kept only so
existing builds keep working.
