---
icon: lucide/file-text
---

# Datasheets

Vendor documentation, checked into the repository so builds stay reproducible
when vendor URLs move.

## OPS243-A Doppler radar

| Document | Contents |
| --- | --- |
| [OPS243 datasheet](../radar/OPS-DS-003-01_OPS243-Datasheet.pdf) | Electrical specs, J3 pinout, operating modes |
| [AN-010-AD: API interface](../radar/AN-010-AD_API_Interface.pdf) | Serial command set. Note that enumerating USB disables UART reporting — the cause of a common wiring false alarm. |
| [AN-027-A: Rolling buffer](<../radar/AN-027-A_Rolling Buffer.pdf>) | Rolling-buffer mode, `HOST_INT` behaviour, dump format |
| [Sports ball detection](../radar/OmniPreSense_Sports_Ball_Detect_2507.pdf) | Vendor application note for ball-speed use |

`AN-027-A` is the one to read if you want to understand why the
[one-time flash-persist step](../setup/rolling-buffer.md) exists.

## K-LD7 (deprecated)

Retained for existing builds. See [Legacy (K-LD7)](../legacy/index.md).

| Document | Contents |
| --- | --- |
| [K-LD7 datasheet](../K-LD7_Datasheet.pdf) | Module specs |
| [K-LD7 EVAL datasheet](../K-LD7-EVAL_Datasheet.pdf) | Evaluation board |

## TI IWR6843

TI does not permit redistribution of its documents, so these are links rather
than checked-in copies:

- [IWR6843 product page](https://www.ti.com/product/IWR6843)
- [IWR6843ISK / LEVM evaluation module](https://www.ti.com/tool/IWR6843ISK)
- [mmWave SDK](https://www.ti.com/tool/MMWAVE-SDK)

For what OpenFlight actually does with the device, the
[firmware developer guide](../development/firmware.md) and the
[launch angle field report](../how-it-works/launch-angle.md) are more useful than
the TI documents.

## Other hardware

- [SparkFun SEN-14262 sound detector](https://www.sparkfun.com/products/14262) —
  see [sound trigger wiring](../build/sound-trigger.md) for the R17 modification
- [ST LIS3DH accelerometer](https://www.st.com/en/mems-and-sensors/lis3dh.html) —
  see [inclinometer](../build/inclinometer.md)
- [Geekworm X1202 / X1206](https://wiki.geekworm.com/X1202) —
  see [battery](../build/battery.md)
