---
icon: lucide/cable
---

# Wiring

Power and data for the IWR6843, the Raspberry Pi UART, serial and GPIO
permissions, and the sound-trigger line.

!!! warning "Do the OPS243 UART migration first"

    The IWR6843 needs the USB bus, so the OPS243 has to move to the Pi GPIO
    UART before you start. See
    [Moving the OPS243 to the Pi GPIO UART](../build/ops243-uart.md).

## Connect The Hardware

Power the system off before changing GPIO wiring.

### Power And Data Layout

The supported connection depends on the OPS243-A variant. Do not power both
radars from an unpowered, bus-powered USB hub.

#### Option A: OPS Through The Pi GPIO UART (Non-WiFi OPS Only)

The validated layout keeps the TI board on USB and connects the OPS243 to the
Pi UART header for power and data.

If the OPS243 is currently on USB, migrate and validate it on its own before
adding the TI board — see
[Moving the OPS243 from USB to the Pi GPIO UART](../build/ops243-uart.md).
Doing both at once makes any failure ambiguous.

> [!WARNING]
> Do not use this option with a WiFi-equipped OPS243-A. The onboard WiFi module
> already drives the radar processor's UART receive line, so J3 pin 6 cannot
> accept API commands from the Pi. J3 pin 7 can expose transmit data, but
> receive-only UART is not sufficient for OpenFlight because the server must
> configure and rearm the OPS after every capture. Use Option B instead.

| Connection | Wiring | Purpose |
|---|---|---|
| IWR6843 | USB to Pi or stable powered hub | Power, CLI commands, and binary L3 dump transfer |
| OPS power | Pi 5V physical pin 2 or 4 to OPS J3 pin 9 (`5V`) | Powers the OPS without sharing the TI USB path |
| OPS ground | Pi GND to OPS J3 pin 10 (`GND`) | Establishes the shared electrical reference |
| OPS data to Pi | OPS J3 pin 7 (`TxD`) to Pi GPIO15 / physical pin 10 (`RXD0`) | OPS transmits readings into Pi RX |
| Pi commands to OPS | Pi GPIO14 / physical pin 8 (`TXD0`) to OPS J3 pin 6 (`RxD`) | Pi transmits commands into OPS RX |
| Sound trigger | Detector `GATE` to OPS J3 pin 3 (`HOST_INT`) and Pi BCM17 / physical pin 11 | Freezes OPS and notifies the Pi of the same impact |
| Trigger power | Pi 3.3V and GND to detector `VCC` and `GND` | Keeps the trigger at Pi-safe logic levels |

#### Option B: OPS Through USB

The OPS243 can remain connected over USB, but the hub must have its own external
power input and must be powered separately instead of drawing all radar power
from the Pi. One option is the
[Acer four-port powered USB hub](https://www.amazon.com/dp/B0CN3F9Y1Z).
This is the recommended connection for a WiFi-equipped OPS243-A.

With this layout, connect both radar USB cables to the externally powered hub.
Do not also connect the OPS 5V, RX, or TX pins to the Pi GPIO header. The shared
sound-trigger GATE connection to OPS `HOST_INT` and Pi BCM17 is still required.

#### Pi Header Reference

| Physical pin | BCM name | Use |
|---|---|---|
| Pin 2 or 4 | 5V | OPS power |
| Pin 6, 9, 14, 20, 25, 30, 34, or 39 | GND | Shared ground |
| Pin 8 | GPIO14 / TXD0 | Pi TX to OPS RX |
| Pin 10 | GPIO15 / RXD0 | Pi RX from OPS TX |
| Pin 11 | GPIO17 | Sound-trigger GATE input |

#### OPS243-A J3 Header Reference

Use the 10-pin header labeled `J3` on the OPS243-A. Confirm the pin-1 marker or
board silkscreen before connecting wires; do not infer pin numbering from which
side of the board is closest.

| J3 pin | OPS signal | Connect to |
|---|---|---|
| Pin 3 | `HOST_INT` / rolling-buffer trigger | Sound detector `GATE` and Pi BCM17 / physical pin 11 |
| Pin 6 | `RxD` (input to non-WiFi OPS only) | Pi GPIO14 / `TXD0` / physical pin 8 |
| Pin 7 | `TxD` (output from OPS) | Pi GPIO15 / `RXD0` / physical pin 10 |
| Pin 9 | `5V` | Pi 5V physical pin 2 or 4 |
| Pin 10 | `GND` | Any Pi GND pin used by the shared ground |

UART transmit and receive are intentionally crossed: the OPS `TxD` output goes
to the Pi `RXD0` input, and the Pi `TXD0` output goes to the OPS `RxD` input.
This bidirectional mapping applies only when the OPS does not contain the WiFi
module described above.
The pin assignments come from the
[OPS243 datasheet](https://omnipresense.com/wp-content/uploads/2019/03/OPS-DS-003-0.1_OPS243.pdf);
the use of J3 pin 3 as a trigger is defined by
[AN-027 OPS243-A Rolling Buffer](https://omnipresense.com/wp-content/uploads/2025/06/AN-027-A_Rolling-Buffer.pdf).

The GATE signal is a three-way electrical connection. Splice three jumper wires
together at one junction: one from the sound detector `GATE`, one to the OPS243
J3 pin 3 (`HOST_INT`), and one to Pi BCM17 / physical pin 11. Use a soldered and
insulated splice or a secure three-way connector; do not rely on loosely
twisted wires.

```text
Sound detector GATE
  +-- OPS243 J3 pin 3 (HOST_INT)
  +-- Pi BCM17 / physical pin 11

Sound detector VCC
  +-- Pi 3.3V

Sound detector GND
  +-- Pi GND, shared with OPS J3 pin 10 (GND)
```

Important electrical rules:

- With Option A, cross serial TX and RX. OPS `TX` connects to Pi `RX`; OPS `RX`
  connects to Pi `TX`.
- Never connect 5V to a Pi GPIO signal pin.
- Confirm that the OPS serial interface uses 3.3V TTL signaling. Do not connect
  RS-232 voltage levels to Pi GPIO.
- Power the sound detector from Pi 3.3V so its GATE output remains Pi-safe.
- Keep Pi, OPS, and trigger grounds connected.
- Treat intermittent USB disconnects and simultaneous radar failures as power
  problems first.

## Prepare The Raspberry Pi UART

Complete this section only when using Option A. If the OPS243 is connected over
USB through an externally powered hub, skip this section and continue to
**Prepare Serial And GPIO Permissions**.

Enable the Pi hardware UART and remove the Linux login console from it:

```bash
sudo raspi-config
```

Choose `Interface Options` -> `Serial Port`, then answer:

1. Disable the login shell over serial.
2. Enable the serial-port hardware.
3. Reboot the Pi.

On Raspberry Pi 5, physical pins 8 and 10 use UART0 at `/dev/ttyAMA0`. Verify
that device:

```bash
ls -l /dev/ttyAMA0
```

Do not use `/dev/serial0` for this wiring on Raspberry Pi 5: it normally points
to `/dev/ttyAMA10`, which is the separate debug-header UART rather than the
40-pin GPIO header.

If `/dev/ttyAMA0` is missing, confirm that UART0 is enabled:

```bash
grep -E "enable_uart|dtparam=uart0" /boot/firmware/config.txt /boot/config.txt 2>/dev/null
```

Note for Raspberry Pi 5 & Newer OS Versions:
Newer hardware and Debian Bookworm use dtparam=uart0=on instead of the legacy enable_uart=1 setting to enable the UART0 hardware block.

At least one boot configuration should contain:

```text
enable_uart=1
```
Or

```text
dtparam=uart0=on
```

## Prepare Serial And GPIO Permissions

Both connection options require serial-device access for the radars and GPIO
access for the shared trigger. Confirm that the OpenFlight user belongs to
`dialout` and `gpio`:

```bash
groups
```

If either group is missing:

```bash
sudo usermod -a -G dialout,gpio "$USER"
sudo reboot
```

## Prepare The Sound-Trigger GPIO

No `raspi-config` interface setting is required for the sound-trigger input.
OpenFlight uses BCM17 by default, which is physical pin 11 on the Pi header. The
checked-in Python dependencies include `gpiozero` and the Pi `lgpio` backend.

The launch command does not need `--iwr6843-trigger-pin` when the GATE splice is
wired to BCM17. If startup reports `GPIO busy`, another OpenFlight, calibration,
or shot-test process still owns the pin; stop that process before retrying.

OpenFlight selects the `lgpio` pin factory itself and names the gpiochip
explicitly, because gpiozero 2.0.1.post2 cannot auto-detect one on a Pi 5 — its
`pins/lgpio.py` calls `os.path.exists` without importing `os`, so every backend
falls back and startup dies with `BadPinFactory: Unable to load any default pin
factory!`. Setting `GPIOZERO_PIN_FACTORY=lgpio` does not help; it forces the
same broken call. If a kernel update moves the 40-pin header to a different
chip, override it:

```bash
OPENFLIGHT_GPIO_CHIP=0 scripts/start-kiosk.sh ...
```

## Identify The TI Serial Port

Connect the IWR6843LEVM to the Pi over USB and inspect the serial devices:

```bash
ls -l /dev/serial/by-id/
ls -l /dev/ttyUSB*
```

The board's CP2105 exposes two UART interfaces. OpenFlight firmware uses the
**Enhanced/UARTA** interface for both CLI commands and binary dumps. This is
normally USB interface `00` and `/dev/ttyUSB0`. Do not select the Standard/data
interface, which is normally interface `01` and `/dev/ttyUSB1`.

The exact `/dev/ttyUSB*` number can change after reconnecting hardware. Prefer
the corresponding `/dev/serial/by-id/...-if00-port0` path when available. The
examples below use `/dev/ttyUSB0`; replace it if your Enhanced interface has a
different path.

When using Option B, also identify the OPS243 USB serial path under
`/dev/serial/by-id/`. Use that stable path for `--radar-port` instead of relying
on a changing `/dev/ttyACM*` number.

