# OPS243-A saved settings and accuracy controls

This is a **partial map inferred from firmware analysis**, for the XMC4500 AC /
1 MiB board and the pinned
[OPS243-A v1.3.3 HEX](https://github.com/omnipresense/OPS243-A_Firmware/blob/efaceefb8fa547071cb925c0d20d2e38363ace67/OPS243_CW_v1.3.3_20261016_1230.hex).
It explains the preservation checks in the [flashing guide](ops243-flashing.md).
It is not a manufacturer-issued calibration map, and the field offsets must
not be used to write settings directly.

## Storage layout

The factory-settings load/save routines use a 64-byte record at logical EEPROM
offset `0x000`. Persistent settings use a 200-byte record at offset `0x0A0`.
Both use the flash-backed store at `0x0C010000–0x0C01FFFF`, consistent with
[Infineon's emulated EEPROM documentation](https://infineon.github.io/mtb-xmc-emeeprom/xmc4_em_eeprom_api_reference_manual/html/index.html).

The store holds 4 KiB snapshots. Each has a four-byte CRC, a four-byte update
counter and the EEPROM payload. The tested original backup contains three
CRC-valid snapshots with counters 9, 10 and 11, at `0x0C018000`, `0x0C019000`
and `0x0C01A000`. The newest valid snapshot therefore has its payload at
`0x0C01A008`. Addresses below refer to that snapshot, not a fixed address that
every radar will use.

The firmware CRC routine uses the XMC FCE CRC32 kernel, seed zero, reflected
input bytes and 32-bit word writes. It reads the raw CRC register. Recomputing
that algorithm over each snapshot's final 4,092 bytes matched its stored CRC.

## Factory record

Offsets are relative to the start of the 64-byte factory record. These names
come from following its load/save code into runtime variables and reporting
functions. Board identity values are omitted.

| Offset | Size | Identified use | Tested backup |
| --- | ---: | --- | --- |
| `0x00` | 4 | Record marker | `OPS` followed by zero |
| `0x04` | 4 | Not identified | Zero |
| `0x08` | 16 | Serial number string | Present |
| `0x18` | 8 | Manufacturing date string | Present |
| `0x20` | 4 | Minimum magnitude, stored squared | 400 (reported magnitude 20) |
| `0x24` | 1 | RS-232 selected flag | 0 (UART selected) |
| `0x25` | 1 | RS-232 automatic-shutdown control | 1 |
| `0x26` | 1 | Application mode code | 7 |
| `0x27` | 1 | Reboot when RTC is not working | 1 |
| `0x28` | 4 | Minimum speed, float | 0 |
| `0x2C` | 4 | Maximum speed, float | Positive infinity |
| `0x30` | 4 | Maximum magnitude, stored squared | `0xFFFFFFFF` |
| `0x34` | 2 | Detection count to pass | 2 |
| `0x36` | 2 | Detection count to fail | 4 |
| `0x38` | 1 | UART available | 1 |
| `0x39` | 1 | RS-232 available | 1 |
| `0x3A` | 1 | Wi-Fi available | 0 |
| `0x3B` | 1 | Ethernet available | 0 |
| `0x3C` | 1 | Board/product configuration code | 4 |
| `0x3D` | 1 | Board revision code | 6 |
| `0x3E` | 1 | Process board-ID resistors | 1 |
| `0x3F` | 1 | Battery available | 0 |

This identified record contains identity and operating defaults. Analysis has
not identified a separate per-unit RF calibration coefficient table within it.
That is a limit of the investigation, not proof that no such data exists elsewhere.

## Identified accuracy-related persistent fields

Offsets here are relative to the persistent record at EEPROM offset `0x0A0`.
The firmware saves the frequency-tuning value using its tuning getter and
restores it through its tuning setter. The two cosine-correction values are
stored as speed multipliers, `1 / cos(angle)`; the reporting code converts
their reciprocals to angles.

| Field | Record offset | Address in snapshot 11 | Original and expected values |
| --- | --- | --- | --- |
| Frequency tuning `T` (signed 16-bit) | `0x7C` | `0x0C01A124` | 0 |
| Inbound cosine correction (float) | `0x80` | `0x0C01A128` | 1.0 (zero-angle correction) |
| Outbound cosine correction (float) | `0x84` | `0x0C01A12C` | 1.0 (zero-angle correction) |

OmniPreSense documents `T=0` as the normal 24.125 GHz target, and documents
separate inbound/outbound cosine correction in
[AN-010](https://omnipresense.com/wp-content/uploads/2025/09/AN-010-AC_API_Interface.pdf).
Its [OPS243 datasheet](https://omnipresense.com/wp-content/uploads/2019/03/OPS-DS-003-0.1_OPS243.pdf)
also describes a runtime algorithm that measures and adjusts the transmitter's
VCO frequency after power-up.

The periodic-calibration wait and limit are initialized RAM values in the
firmware images examined: the original v1.3.0 initializes a 60,000 ms wait;
v1.3.3 initializes 30,000 ms. Both initialize the limit to 3. These are startup
defaults, not a reading of the radar's current runtime state. The interval
change comes from the new firmware's initialized data.

## Evidence and limits

In the two supplied files, all 65,536 bytes of the settings store are identical,
including the factory record and the three persistent fields above. The
original manual update's final full-bank `contents match` establishes that the
radar matched the expected image immediately after programming.

Changes made by the running firmware after reboot were not checked. No
additional hardware was accessed during this analysis, and no complete
OmniPreSense calibration map was located.

### Firmware locations used to identify fields

These are code addresses for the pinned v1.3.3 image, using its `0x08000000`
cached flash alias:

- Factory record save/load: `0x0802AE68`, `0x0802B058`.
- Persistent record serialization/application: `0x08027FD4`, `0x08028340`.
- Emulated EEPROM initialization: `0x08025BC4`; read: `0x08025D50`.
- Frequency-tuning getter/setter: `0x080202E8`, `0x080202D8`.
- Periodic-calibration parameter report: `0x0802C900`.
- Cosine-angle report: code ending at `0x08030B0E`.
- Startup copy table: `0x08000248`; v1.3.3 initialized RAM source `0x0C063834`.
