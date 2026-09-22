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
| **Raspberry Pi Display Cable, Standard–Mini, 200 mm (SC1131)** | Only with the Touch Display 2 below: the 22-way (Pi 5 "mini") to 15-way (display "standard") DSI ribbon. Buy the 200 mm length; 300 and 500 mm also fit but leave a loop to stow. The ribbon in the Display 2 box is about 100 mm and does not reach the Pi in the v3 case, see the note below and [Cable lengths](#cable-lengths-enclosure-v3) | [Raspberry Pi](https://www.raspberrypi.com/products/display-cable/) / [Mouser](https://www.mouser.com/ProductDetail/Raspberry-Pi/SC1131?qs=HoCaDK9Nz5eSyEpyddOkmQ%3D%3D) / [Amazon](https://www.amazon.com/dp/B0GX33S2C6) (Raspberry Pi's own listing; pick 200 mm) | ~$2 |

> **NOTE on OPS243-A-W (WiFi version):** The standard **OPS243-A** (USB only) is strongly recommended. The WiFi module on the OPS243-A-W drives the internal UART receive line, preventing direct connection to the Raspberry Pi GPIO UART (Layout A). However, if you already have the WiFi version, it can still be used over USB with a powered USB hub (Layout B) when paired with the IWR6843 angle radar.

> **Display alternative:** The [Raspberry Pi Touch Display 2](https://www.raspberrypi.com/products/touch-display-2/) (7" 720x1280, MIPI DSI) also works with the Pi 5. Print the `Screen-RPI-Display-2.stl` bezel from the [openflight-enclosure v3 case](https://github.com/open-flight/openflight-enclosure) for it. **It also needs the 200 mm display cable in the row above:** the case mounts the Pi and UPS to the shell rather than to the screen, which makes the install easier, and the roughly 100 mm 22-way to 15-way ribbon that ships in the Display 2 box does not reach the Pi from there.

## Sound Trigger (for Rolling Buffer Mode)

The sound trigger detects club impact to precisely time radar captures. Essential for spin detection via rolling buffer mode.

> **Optional internal-trigger path:** Hardware mode lets the OPS243 fire the rolling-buffer dump from its own internal speed trigger, with no SEN-14262 in the loop. It requires OPS243-A firmware 1.3.2 or newer in the 1.3 release train; firmware 1.3.1 is rejected because of a vendor data-sequence bug. See [Internal Hardware Trigger](#internal-hardware-trigger) below.

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **SparkFun SEN-14262** | Sound Detector with envelope/gate outputs | [SparkFun](https://www.sparkfun.com/products/14262) | $12 |
| **Through-hole resistor** | For R17 pad on SEN-14262 to reduce sensitivity (see note) | Any electronics supplier | $1 |
| **Jumper wires (female/female, 300 mm)** | 8 wires out of one pack: `GATE` → `HOST_INT`, `VCC` → 3.3V and `GND` → `GND` from the sound detector, the OPS243 → Pi ground run, the OPS243 `TxD`/`RxD`/5V wires of Layout A, and the `GATE` → Pi BCM17 wire the angle radar needs. Female on both ends — the Pi GPIO header, the OPS243 J3 header, and headers soldered to the SEN-14262 are all male pins. 300 mm, not 150, because the sound detector sits on the camera strip at the front of the v3 case while the Pi is on the rear wall and the OPS243 on the radar front, and the strip has to come off with the detector still wired for service: a 150 mm wire reaches with the case closed but not opened (see [Cable lengths](#cable-lengths-enclosure-v3)). SparkFun PRT-09389: 10 wires, 12 in / 305 mm, $4.95 at SparkFun list; Mouser's listing (474-PRT-09389) is unverified. The 150 mm PRT-12796 pack in the shared Mouser project covers only the OPS243 ↔ Pi runs | [Mouser](https://www.mouser.com/c/?q=PRT-09389) / [SparkFun](https://www.sparkfun.com/jumper-wires-premium-12-f-f-pack-of-10.html) | $5 |

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

### Internal Hardware Trigger

Hardware mode lets the OPS243-A start the rolling-buffer capture from its own speed trigger, so the sound detector, its resistor and its wiring are not needed. The supported firmware is OPS243-A 1.3.2 or newer in the 1.3 release train. Check before buying anything: plug the radar into USB, open a serial terminal, send `?V`, and read the version it prints back.

- **It reports 1.3.2 or later in the 1.3 train.** Nothing to buy; use `scripts/start-kiosk.sh --trigger hardware`. OmniPreSense [told the project on 2026-09-10](https://github.com/open-flight/openflight/pull/221#issuecomment-5619646576) that 1.3.2 went onto the sensors shipping from that build on (1.3.1 had gone to some earlier customers with a late bug), so a new order should arrive like this. Skip the Sound Trigger table above if you choose hardware mode.
- **It reports 1.3.1 or older.** You flash it yourself, which is where the debugger cost comes in. OmniPreSense's [AN-013 code-update note](https://omnipresense.com/wp-content/uploads/2019/06/AN-013-D_OPS241-Code-Update.pdf) is the procedure: a SEGGER J-Link on the radar's keyed `J2` JTAG header (a 10-pin 1.27 mm Cortex debug header, not the `J3` UART header OpenFlight wires to), Infineon's free XMCFlasher in Serial Wire Debug mode with the XMC4500-1024 target selected, and the 1.3.2 hex file, which is not a public download: email customerservice@omnipresense.com for it, and they will also confirm which J-Link model to get. Send `?P` first and pick the XMC4700 in XMCFlasher instead if the board reports that part ([note on the PR](https://github.com/open-flight/openflight/pull/221#issuecomment-5463503457)). Do not press Erase in XMCFlasher: it clears the factory settings some sensors carry and anything you saved to persistent memory. On Windows run the J-Link driver installer as administrator and tick the legacy J-Link USB driver, or XMCFlasher will not find the probe ([upgrade report](https://github.com/open-flight/openflight/pull/221#issuecomment-5756563718)).

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **SEGGER J-Link EDU Mini (Adafruit 3571)** | Only if you go the internal-trigger route and your OPS243-A reports firmware older than 1.3.2. This is the low-cost programmer AN-013 points at; the 9-pin 0.05" (1.27 mm) Cortex target cable that fits `J2` and a USB-C cable are in the box, so nothing else is needed. Licensed for non-commercial use only. In the shared Mouser project | [Mouser](https://www.mouser.se/en/ProductDetail/Adafruit/3571?qs=YCa%2FAAYMW03SrXLinBpZFw%3D%3D) / [Amazon](https://www.amazon.com/dp/B0758XRMTF) / [Adafruit](https://www.adafruit.com/product/3571) | $76 |

That is about four times the sound trigger's $18, and it is a one-off tool rather than a part of the monitor, so it is a trade you make for the wiring and the R17 soldering the internal trigger removes, not for the price.

## Angle Radar (TI IWR6843) — CURRENT

This is the supported angle radar. It measures vertical and horizontal launch
angle, and supplies the pre-impact frames club path is derived from.

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **TI IWR6843LEVM** | 60 GHz mmWave evaluation board, 4 RX × 3 TX | [TI](https://www.ti.com/tool/IWR6843LEVM) | $150 |
| **Micro-USB cable (data-capable), 250-300 mm** | Connects the LEVM's CP2105 serial bridge to the Pi — the LEVM's USB port is micro-USB. Charge-only cables will not enumerate. Length: the LEVM's micro-USB (`J5`) sits at the top edge of the board on the radar front and the Pi's USB-A ports at the top of the rear wall, so 150 mm is the shortest that still leaves room for the two plug bodies and the bends; 250-500 mm is comfortable, and the shared Mouser project carries a 50 cm StarTech cable (see [Cable lengths](#cable-lengths-enclosure-v3)) | Any | $5 |
| **Jumper wire** | 1 wire: detector `GATE` → Pi BCM17 / physical pin 11, alongside the existing `GATE` → OPS `HOST_INT`. Female/female again — comes out of the same 300 mm SparkFun PRT-09389 pack as the sound-trigger wires above; 150 mm reaches with the case closed but not with the camera strip lifted off, which is why the pack is 300 mm | [Mouser](https://www.mouser.com/c/?q=PRT-09389) | $1 |

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
| **Qwiic-to-Dupont cable (single)** | Mouser-stocked equivalent of the kit above: one JST-SH 4-pin to female Dupont sockets cable, 150 mm (SparkFun CAB-17261, Mouser 474-CAB-17261, the line in the shared Mouser project; Adafruit 4397 is the same cable from Adafruit direct). Enough on its own for the LIS3DH → Pi header run, and it keeps the whole inclinometer orderable from Mouser. 150 mm is the only length either maker offers in this JST-SH-to-female-socket configuration; the shorter 50-100 mm Qwiic cables are Qwiic-to-Qwiic and have no Dupont end. Reaches the floor bay nearest the Pi with about 15 mm to spare as routed; plug it into the LIS3DH socket nearer the Pi (see [Cable lengths](#cable-lengths-enclosure-v3)) | [Mouser](https://www.mouser.com/ProductDetail/SparkFun-Electronics/CAB-17261?qs=DRkmTr78QAQLJE%2FDhtP97Q%3D%3D) / [Amazon](https://www.amazon.com/dp/B0992PHLBC) / [Adafruit 4397](https://www.adafruit.com/product/4397) | ~$2 |

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

## Power & Accessories

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **27W USB-C Power Supply** | Official Pi 5 power supply (5.1V 5A). **It must be this supply, or one that negotiates 5V at 5A over USB PD.** The Pi 5 only releases its full downstream USB power budget when the supply reports 5A, and the two radars need that budget; a standard USB-C PD charger tops out at 3A at 5V, the Pi then caps the USB ports, and the radars brown out or fail to enumerate. Plug it straight into the Pi: do not route it through a USB-C extension or panel-mount pass-through in the case, because the extra contact resistance causes voltage sag and can make the 5V 5A negotiation fail, and a panel-mount USB-C pass-through rated for 5A is hard to find in the first place. Not needed if you power the Pi from the UPS HAT (see below) | [Adafruit](https://www.adafruit.com/product/5814) | $14 |
| **Raspberry Pi Active Cooler** | Clip-on heatsink + fan for the Pi 5 (SC1148). Recommended: the kiosk runs the UI, radar capture, and FFT processing continuously, and a passively cooled Pi 5 throttles under sustained load | [Mouser](https://www.mouser.com/ProductDetail/Raspberry-Pi/SC1148?qs=HoCaDK9Nz5fqo0izK2taew%3D%3D) | $8 |
| **Jumper wires (female/male, 75 mm)** | Header-pin extensions: the female end goes onto a Pi GPIO pin and the male end re-presents that pin for a second connector. Used here to keep the 5V rail reachable for the OPS243 when the Touch Display 2 is also wired to the header, instead of one connector covering the whole rail. 75 mm is the shortest female/male length Mouser stocks (Adafruit 1953, Mouser 485-1953, 20-wire ribbon). $1.95 at Adafruit list; Mouser's price for 485-1953 is unverified | [Mouser](https://www.mouser.com/ProductDetail/Adafruit/1953?qs=GURawfaeGuBbX2LiaCDbnA%3D%3D) | $2 |
| MicroSD Card (32GB+) | For Pi OS and software | Any Class 10 | $10 |
| USB-A to Micro-USB Cable | For OPS243 radar connection | Any | $5 |

> **Cheaper and simpler with the UPS HAT:** if you fit the Geekworm X1202/X1206 from the Optional table, skip the 27W USB-C supply. Any barrel-jack supply that gives the UPS enough power feeds it (Geekworm asks for 3A or more anywhere in its 6-18V range; at 12V that also charges the cells at full rate while the Pi runs flat out, at lower voltages it does not; see the adapter row in the Optional table), and the UPS delivers 5.1V 5A to the Pi over its pogo pins; the Geekworm setup script sets `PSU_MAX_CURRENT=5000` so the Pi treats it as a 5A supply and keeps the full USB budget. For a device that lives in a case, the DC barrel jack is the better input either way: there is no USB PD negotiation to fail and no USB-C extension to sag, and a 12V adapter you already own will do.

> **UPS safety, read before the first charge:** **Never charge the 18650 cells below 0 °C (32 °F).** Lithium-ion cells charged below freezing plate lithium onto the anode, which permanently damages them and can make them unsafe; bring a cold rig indoors or let it warm up before connecting power. **Never connect the UPS's USB-C input and its barrel jack at the same time.** If you do power the UPS from the USB-C supply, plug it into the **UPS board's** USB-C socket, never into the Pi's own USB-C port while the Pi sits on the UPS. Details in the [Geekworm operator guide](../build/battery.md).

> **Power input on the v3 case:** the [openflight-enclosure v3](https://github.com/open-flight/openflight-enclosure) shell has no USB-C opening. A USB-C supply can only be run into the case through one of the rear cutouts, so it stays captive to the case: there is no detachable USB-C option. For a supply you can unplug at the case, go the DC route: a panel-mount 5.5 × 2.1 mm DC jack in the shell's Ø12.5 mm rear DC hole, wired to the UPS without soldering. Buy a panel jack that comes with leads (the DC-route rows at the end of the Optional table), then either **(a)** join its leads to a 2-pin JST XH lead with two Wago 221 lever connectors and plug that into the X1202/X1206's `XH2.54-2P` DC input header, or **(b)** screw them into a 5.5 × 2.1 mm barrel plug that ends in a screw-terminal block (Adafruit 369, on Mouser; no Wagos needed) and plug that into the UPS's own barrel jack, which takes the same 6-18 V; any standard plug fits it (5.5 mm outer sleeve, 2.1 mm pin, 9.5-14 mm long, centre positive). Either way feed it **12 V**: the XH input is rated for about 3 A, so 12-18 V at 3 A is what carries the full Pi load plus charging, and the same 12 V adapters in the Optional table apply. Check polarity against the `+` mark at the header; the jack's centre pin is positive, and so is the plug's.

## Optional

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **Geekworm X1202 UPS HAT** | Rechargeable Pi 5 power using four matching flat-top 18650 Li-ion cells. Cells are not included | [Geekworm](https://geekworm.com/products/x1202) / [Amazon](https://www.amazon.com/dp/B0CRZ4ZXQW) | ~$48 + cells |
| **Geekworm X1206 UPS HAT** | Larger rechargeable Pi 5 power option using four matching flat-top 21700 Li-ion cells (unprotected only, per Geekworm), advertised up to 20,000mAh total. The four 21700 holders are on the board, like the X1202's 18650 holders, so no separate holder is needed; cells are not included. Same XH2.54 power-button header as the X1202, so the button row below fits it too. Power it the same way as the X1202: 12V or higher on the DC input (the adapter row), or 5V 5A on USB-C | [Geekworm](https://geekworm.com/products/x1206) | $52 + cells |
| **X1202 power button: Adafruit 16 mm momentary button (1445) + XH quick-connect leads (1152)** | The X1202/X1206 expose their external power button on an XH2.54 2-pin header and need a momentary (spring-back) switch: the board reads press length the way the Pi 5 power button does, so a latching or toggle switch will not work. The 1445 is a 16 mm panel-mount momentary push button (normally open, two 0.11" tabs). The 1152 pack holds ten 20 cm wire pairs, each ending in a 2-pin JST XH plug for the header and two pre-crimped 0.11" quick-connects that push onto the button's tabs, so nothing is soldered or crimped; Adafruit's 1445 page names the 1152 pairs as its wiring. Needs a 16 mm panel hole. **The v3 case does not have one yet:** both rear holes in the openflight-enclosure v3 shell are Ø12.5 mm, sized for a 12 mm button, so the 1445 fits only once the CAD opens the button hole to Ø16.5 mm, or you switch to a 12 mm momentary button with quick-connect tabs. The 1152's 200 mm leads reach the X1202's `PSW` header from the middle rear hole with slack, and from the right-hand hole only pulled straight across the Pi stack (see [Cable lengths](#cable-lengths-enclosure-v3)). Adafruit showed the 1152 out of stock when checked and its Amazon listing (B00SK6M36U) as unavailable; Mouser's stock is unverified | [Mouser (1445)](https://www.mouser.com/ProductDetail/Adafruit/1445?qs=GURawfaeGuAOIArgy7Ph4w%3D%3D) / [Mouser (1152)](https://www.mouser.com/ProductDetail/Adafruit/1152?qs=GURawfaeGuAkPRIbdozo3A%3D%3D) / [Adafruit](https://www.adafruit.com/product/1445) | ~$6 |
| **DC adapter for the X1202 (5.5 × 2.1 mm barrel, center positive, 6-18V, 3A or more)** | Feeds the X1202 through its barrel jack instead of USB-C, which is the better input for a cased build: no USB PD negotiation to fail and no USB-C extension to sag. The X1202 accepts 6-18V DC on that jack and converts it to the 5.1V 5A the Pi 5 needs while also charging the cells. Geekworm's stated requirement is a current, not a wattage: "6-18Vdc, ≥3A", with charging at up to 3.2A into the cells; it publishes no watt figure. Where the power goes: the Pi 5 can draw up to 25.5W (5.1V × 5A) with both radars on its USB budget, and charging adds up to about 12W when the cells are low, plus converter losses. So the same 3A buys different things at different voltages: at 12V (36W) it runs the Pi at full load and charges at the same time, which is why Geekworm's own adapters are 12V; at 9V (27W) it runs the Pi but charging slows under load; at 6V (18W) it cannot carry a full Pi load and the cells drain while plugged in. **Check the amps against the voltage** rather than treating "6-18V" as "any adapter": a little over 25W is enough to run the Pi, not to run it and charge at full rate, and 12-18V at 3A or more covers both. Examples: MEAN WELL GST36 (12V 3A; GST36U12-P1J US plug, GST36E12-P1J EU plug, both at Mouser) or Geekworm's own PSU60 (12V 5A, also sold as an Amazon bundle with the X1202). Never connect the DC jack and the USB-C input at the same time | [Mouser (EU plug)](https://www.mouser.com/c/?q=GST36E12-P1J) / [Mouser (US plug)](https://www.mouser.com/c/?q=GST36U12-P1J) / [Amazon (PSU60)](https://www.amazon.com/dp/B0BDF89DCB) | ~$15 |
| **InnoMaker OV9281 global-shutter camera** | High-speed monochrome camera for experimental vision work. Camera software is not enabled in the production kiosk path | [Amazon](https://www.amazon.com/dp/B09WTP5GZH?th=1) | ~$30 |
| **Panel DC jack with leads, 5.5 × 2.1 mm (DC route)** | The socket in the shell's rear DC hole. Mouser: Tensility 10-03609, an overmoulded panel jack on a 305 mm 18 AWG lead, rated 7.5 A, M11 × 1.0 thread with the nut and lock washer fitted from inside, for panels 1.5-4.5 mm thick. The shell's Ø12.5 mm hole is 1.3 mm over its thread and its Ø13.5 mm head has flats at 9.8 mm, so it clamps but shows a sliver of hole at the flats. Amazon: the pre-wired DC-099 style kits with a 12 mm thread fit the hole as drawn, such as the [6-set](https://www.amazon.com/dp/B0DP6MNQQB) the enclosure's reference model was made from (150 mm 20 AWG leads) or a [10-pack rated 5 A](https://www.amazon.com/dp/B08F26JJKM) with 150 mm 18 AWG leads; the [DaierTek set](https://www.amazon.com/dp/B0BD46CP5Y) adds pre-wired 5.5 × 2.1 mm plugs for the barrel-jack option. Tensility's other 2.1 mm lead, 10-02878, has a Ø10.8 mm thread but only a Ø12.5 mm flange, the same as the hole, so it is not the one to buy. Mouser's Tensility listing is unverified from here; Tensility sells direct at $6.74 | [Mouser (search)](https://www.mouser.com/c/?q=10-03609) / [Tensility](https://www.tensility.com/products/10-03609) / [Amazon (6-set)](https://www.amazon.com/dp/B0DP6MNQQB) | $7-10 |
| **Wago 221-412 lever connectors, ×2 (DC route, header option)** | Two-conductor lever splices for 24-12 AWG that join the jack leads to the XH lead without tools, one per conductor. Any two-way 221 does; the inline 221-2411 is the same clamp in a straight-through body. The barrel option below needs none | [Mouser (search)](https://www.mouser.com/c/?q=WAGO%20221-412) / [Amazon (bag of 10)](https://www.amazon.com/dp/B072PT3JNL) | $1 |
| **UPS end of the DC route: JST XH 2-pin lead (header option) or screw-terminal barrel plug (barrel option)** | Header option: the plug for the X1202/X1206's `XH2.54-2P` DC input. One pair from the Adafruit 1152 pack in the button row is exactly this (XH plug, 20 cm of 22 AWG, quick-connects cut off), so nothing extra is needed if you have that pack; otherwise Adafruit 4872 is a matching XH-compatible plug-and-socket pair with 20 cm leads, but its 26 AWG wire is thin for the 3 A the input can draw, so prefer the 1152 pair. Barrel option: Adafruit 369, a 5.5 × 2.1 mm plug on a two-way screw-terminal block marked + and −; the jack leads screw straight in, so no Wagos, and it goes into the UPS's own barrel jack. Mouser hosts its datasheet but the listing is unverified from here | [Mouser (4872)](https://www.mouser.com/ProductDetail/Adafruit/4872?qs=sGAEpiMZZMsvnOgGvSjZeHfx0dldyM%2FtbKuuneru8OfHveSm083OQA%3D%3D) / [Amazon (XH 2.54 pre-crimped kit; check it is 22 AWG or heavier)](https://www.amazon.com/dp/B08G17QHSD) / [Mouser (369, search)](https://www.mouser.com/c/?q=485-369) / [Adafruit 369](https://www.adafruit.com/product/369) | $1-2 |

See [Camera and YOLO Experiments](../development/camera-yolo.md) before buying the
camera; the standard setup does not install its optional software dependencies.

---

## Cable Lengths (Enclosure v3)

The parts rows above already say which length to buy; this section is the
measurement behind them, for anyone changing the enclosure or the wiring.
Ordinary builders can skip it.

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
| IWR6843 USB | LEVM `J5` (top edge of the board, radar front) → Pi USB-A | ~50 mm | ~130 mm | 250-500 mm micro-USB; 150 mm is the floor |
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

The heat-set inserts, screws and the one tool the case needs are listed with
the case, not here: see **[Required hardware](https://github.com/open-flight/openflight-enclosure#documentation)**
in the [openflight-enclosure](https://github.com/open-flight/openflight-enclosure)
repository, which gives the insert size and count per printed part, the
screw lengths, and the long-reach hex key the case screws need. The insert
family and where to order it are being settled in
[openflight-enclosure#4](https://github.com/open-flight/openflight-enclosure/issues/4);
until that lands, buy what the enclosure page says for the parts you print.

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
| Enclosure filament (v3 case, ~750 g PETG, estimate) | $17 |
| Enclosure hardware (heat-set inserts, screws, hex key; listed in the enclosure repo) | $35 |
| **Complete build (total with angle radar + inclinometer + optional extras + filament + hardware)** | **~$741** |
| Optional extras with the X1206 instead (X1206 UPS HAT, four 21700 cells, 16 mm power button + leads, OV9281 camera) | $120 |
| **Complete build with the X1206 instead** | **~$753** |
| Angle Radar (2× K-LD7 + FTDI adapters) — **deprecated** | $140 |

<details markdown="1">
<summary>How the filament estimate was made</summary>

The enclosure filament line is an estimate from the CAD, not a slicer figure,
and it assumes **PETG**, not PLA: the case lives outdoors in the sun, and PETG
holds up to UV and to a hot car far better than PLA (it softens at ~80 °C
against PLA's ~60 °C), while still being a stock spool everywhere and an easy
print on an enclosed printer such as the P1S. The figure is the mesh volume of
the v3 `v1` print set at PETG's 1.27 g/cm³: the x1202 shell is 416 cm³
(~530 g; a 200 × 214 × 111 mm body with 3 mm walls prints close to solid at
the recommended 3-4 walls, so infill saves little), the no-fill radar front
60 cm³ (~75 g), the camera + sound-detector strip and its retainer 31 cm³
(~40 g), the 1024×600 screen bezel 36 cm³ (~45 g) and four solid feet 5 cm³
(~7 g): about 700 g of parts, and the tree supports the shell, camera strip
and no-fill radar front need plus a purge line take the print to roughly
750 g, three-quarters of a 1 kg spool. At $20-25/kg that is ~$17. The Touch
Display 2 bezel is 69 cm³ (~85 g), 40 g more than the 1024×600 one, and the
no-UPS Pi adapter adds 17 cm³ (~20 g). Replace these with sliced weights when
the enclosure repository publishes them. Heat-set inserts, screws and the hex
key are listed on the enclosure repository's Required hardware page (see
Enclosure Hardware above).

</details>

The enclosure hardware line is an allowance for the v3 set (short heat-set
inserts in three sizes, the M3 case screws and the board screws, and a
long-reach 2.5 mm hex key) at pack prices. It was $35 for the v2 set and is
carried unchanged until the v3 set is priced against the insert decision in
[openflight-enclosure#4](https://github.com/open-flight/openflight-enclosure/issues/4).

With the X1206 instead, the extras are $52 for the HAT (Geekworm list price)
+ four flat-top 21700 cells at ~$8 each ($32; a Samsung 50E or Molicel P42A
sells for $6-9) + the same $6 button pair + the $30 camera = $120, and the
complete build comes to ~$753. Nothing else changes: the X1206 V2.0 carries its
four 21700 holders on the board, uses the same power-button header, and takes
the same 12V adapter.

If the [PR #221](https://github.com/open-flight/openflight/pull/221) internal
trigger lands, the Sound Trigger line ($18) becomes optional and drops out of
every total above for a radar that already reports firmware 1.3.2. For one that
arrived with 1.3.1 or older, the swap instead costs the ~$76 J-Link EDU Mini
listed under [Internal Trigger Instead](#internal-trigger-instead-pr-221), a
one-off tool that flashes the 1.3.2 firmware.

OpenFlight works without any angle radar: you get ball speed, club speed, smash
factor, spin rate, and estimated carry. The angle radar adds measured launch
angle (vertical and horizontal) and is what club path is derived from.

If you are building new, buy the **IWR6843**, not the K-LD7s. It costs about the
same as the two K-LD7s plus their FTDI adapters ($156 vs $140) and replaces both
of them with one board. The K-LD7 path is **deprecated** and kept only so
existing builds keep working.
