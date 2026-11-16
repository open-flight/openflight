# OpenFlight Parts List

Hardware components for building the OpenFlight golf launch monitor.

> [!TIP]
> **Ordering shortcut:** A shared **[OpenFlight Mouser project](https://www.mouser.com/en/Tools/Project/Share?AccessID=4c97a00bbc)** is available for the parts Mouser stocks. Check it against the tables below before you order: anything Mouser does not carry has a direct vendor link here.


Below is the bill of materials (BOM) for OpenFlight. You can mix and match components to suit your budget and desired features. Use the lists below to determine what you'll need before moving on to building.

> [!IMPORTANT]
> **Before purchasing:** Prices are estimates and may change. Check the linked product pages for current pricing, compatibility, and availability.


## Tools and Extras

The assumption is that you already have these tools and supplies. If not, the following options are recommended.

 Item | Link | Approx. Price |
|---|---|---:|
| Soldering Iron | Your choice | — |
| Long Hex Keys | [Harbor Freight](https://www.harborfreight.com/sae-and-metric-t-handle-ball-end-hex-key-set-18-piece-63167.html) | $20 |
| Resistor Kit | [Amazon](https://a.co/d/0hUAJ7W2) | $10 |
|**Choose only one set**| — | — |
| JST-XH and Dupont Crimping Kit with Wires* | [Amazon](https://a.co/d/03rr8do3) | $30 |
|**OR**| — | — |
|Dupont-Terminated Wires | [Amazon](https://a.co/d/0bHWM9HL) | $7 |
|JST-XH-Terminated Wires | [Amazon](https://a.co/d/090Ktn7A) | $15 |

## Enclosure

| Item | Link | Approx. Price |
|---|---|---:|
| Screws | [Amazon](https://a.co/d/04J6FB9L) | $10 |
| Heat-Set Inserts | [Amazon](https://a.co/d/0iKCORNK) | $9 |
| Panel-Mount Ethernet Extension Cable | [Adafruit](https://www.adafruit.com/product/909) | $5 |
| PLA+, ASA, or PETG Filament | Your choice | $30 |

Download and print the files from the [OpenFlight Enclosure repository](https://github.com/open-flight/openflight-enclosure)

**Estimated enclosure materials total: $55** (hardware: $25 + filament: $30).


## Components

| Part | Description | Link | Approx. Price |
| ---- | ----------- | ---- | ------ |
| **Raspberry Pi 5 4GB** | Main computer that runs OpenFlight | [Adafruit](https://www.adafruit.com/product/5812) | $130 |
| **MicroSD Card** | Stores the operating system and OpenFlight software | [Amazon](https://a.co/d/09wPLlDT) | $35 |
| **3D-Printed Enclosure** | Houses and protects the electronics | [OpenFlight Enclosure](https://github.com/open-flight/openflight-enclosure) | $60 |
| **OPS243-A-CW-RP Doppler Radar** [^1] | Measures golf ball speed using Doppler radar | [OmniPreSense](https://omnipresense.com/product/ops243-doppler-radar-sensor/) | $249 |
| SparkFun SEN-14262 Sound Detector | **Optional**: Detects impact sounds for shot detection | [SparkFun](https://www.sparkfun.com/products/14262) | $12 |
| TI IWR6843LEVM Radar Module [^2] | **Optional**: Provides additional radar tracking capabilities | [Texas Instruments](https://www.ti.com/tool/IWR6843LEVM) | $150 |
| InnoMaker OV9281 Global-Shutter Camera | **Optional**: Captures high-speed images for computer vision | [Amazon](https://www.amazon.com/dp/B09WTP5GZH) | $30 |
| **Choose Only One Screen** | | | |
| Hosyond 7" Touchscreen Display | Touchscreen interface for OpenFlight | [Amazon](https://www.amazon.com/dp/B0D3QB7X4Z) | $46 |
|**OR**|||
| Raspberry Pi 5" Touch Display 2 | Alternative touchscreen interface | [Pishop](https://www.pishop.us/product/raspberry-pi-5-touch-display-2-portrait/?src=raspberrypi) | $55 |
| Touch Display 2 Cable | Connects the Raspberry Pi to the official Touch Display 2 | [Canakit](https://www.canakit.com/raspberry-pi-5-display-cable.html?cid=USD&src=raspberrypi) | $1 |


## Optional Components
### Battery Power

If you want to run OpenFlight on battery power, you'll need the following additional components. For more information see [Powering OpenFlight](power.md)
>[!NOTE]
>If you plan to add a battery, decide during your during the initial build. Adding one later may require completely disassembling your OpenFlight.


| Item | Link | Approx. Price |
|---|---|---:|
| Batteries | Depends on UPS | $30 |
| DC Power Plug | [Geekworm](https://geekworm.com/products/psu60) | $15 |
| DC Barrel Jack and Lead | [Amazon](https://a.co/d/083VNz4q) | $10 |
| 12 mm Power Button | [Geekworm](https://geekworm.com/products/psw12) | $2 |
|**Choose only one UPS** | — | — |
| Geekworm X1202 UPS HAT | [Geekworm](https://geekworm.com/products/x1202) | $51 |
| Geekworm X1206 UPS HAT | [Geekworm](https://geekworm.com/products/x1206) | $52 |

>[!NOTE]
>Choose either the X1202 or X1206 UPS HAT, not both.

Estimated battery-power hardware total: approximately $110, depending on your selected UPS HAT and wiring options.


### Enclosure Inclinometer

An LIS3DH mounted to the enclosure base lets OpenFlight compensate the IWR6843
tilt when the rig is placed on uneven ground.

| Part | Description | Link | Approx. Price |
|------|-------------|------|--------|
| **Adafruit LIS3DH breakout** | Triple-axis accelerometer with STEMMA QT connectors | [Adafruit product 2809](https://www.adafruit.com/product/2809) | $5 |
| **JST-SH cable kit (Qwiic-to-Dupont)** | Qwiic/STEMMA QT to female Dupont jumpers, used in the validated build. The LIS3DH plugs into its STEMMA QT socket and the Dupont ends push straight onto the Pi GPIO header, so no soldering is needed — the alternative is soldering a header onto the breakout and wiring that by hand | [Amazon](https://www.amazon.com/Connector-Compatible-Development-Sensors-Drivers/dp/B0GJPRX4YT) | ~$10 |
| **Qwiic-to-Dupont cable (single)** | Mouser-stocked equivalent of the kit above: one JST-SH 4-pin to female Dupont cable, 150 mm (SparkFun CAB-17261 / Mouser 474-CAB-17261, in the shared Mouser project; Adafruit 4397 is the same cable direct). Enough on its own for the LIS3DH → Pi header run, and it keeps the inclinometer orderable from Mouser. 150 mm is the only length made in this configuration — the shorter Qwiic cables have no Dupont end. Plug it into the LIS3DH socket nearer the Pi ([Cable lengths](#cable-lengths-enclosure-v3)) | [Mouser](https://www.mouser.com/ProductDetail/SparkFun-Electronics/CAB-17261?qs=DRkmTr78QAQLJE%2FDhtP97Q%3D%3D) / [Amazon](https://www.amazon.com/dp/B0992PHLBC) / [Adafruit 4397](https://www.adafruit.com/product/4397) | ~$2 |

>[!NOTE]
>See the [LIS3DH Inclinometer Setup Guide](../build/inclinometer.md) for wiring,
mounting, calibration, startup flags, and troubleshooting.



**Next step after gathering parts:** See the [Raspberry Pi Setup Guide](../setup/raspberry-pi.md) for assembly and software installation.

[^1]: OPS243-A-CW-WB (WiFi version): The standard OPS243-A-CW-RP (USB only) is strongly recommended. The WiFi module on the OPS243-A-CW-WB drives the internal UART receive line, preventing direct connection to the Raspberry Pi GPIO UART.

[^2]: The TI board needs custom firmware — it does not work out of the box. The stock TI demo does not expose the raw radar cube OpenFlight needs. A validated prebuilt image ships in firmware/releases/, so you do not need the TI toolchain to flash it.
