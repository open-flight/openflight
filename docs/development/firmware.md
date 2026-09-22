# IWR6843 Firmware Developer Guide

OpenFlight uses custom firmware on the TI IWR6843LEVM to preserve a short radar
movie around impact in the chip's on-board L3 RAM. The firmware continuously
processes chirps, stores selected complex range bins in a circular frame ring,
and streams that ring to the Raspberry Pi after the shared sound trigger.

Most builders do **not** need to compile firmware. A validated flashable image is
checked into the repository. Build the firmware only when changing capture
geometry, range windows, HWA/EDMA processing, or the binary dump contract.

For hardware wiring, mounting, geometry, calibration, and normal OpenFlight
startup, use the [IWR6843 Operator Guide](../iwr6843/index.md).

## Current Release

One firmware image supports two runtime capture profiles. Flash the image once,
then choose a profile by passing its `.cfg` to OpenFlight.

| Component | Current value |
|---|---|
| Flash image | `firmware/releases/l3_dump_configurable_capture_20260818.bin` |
| Default config | `config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg` |
| Dense config | `config/iwr6843_l3dump_dense_36f2ms_53bin_iq8.cfg` |
| Dense/wide-late config | `config/iwr6843_l3dump_dense_36f2ms_53bin_iq8_wide_late.cfg` |
| Reference calibration | `config/iwr6843_calibration_reference.json` |
| Native build | `make -C firmware build-native` |
| Container build | `make -C firmware docker-build` |
| Flash image size | 346,820 bytes |
| Flash SHA-256 | `823ddd18a231d0004020de6262160d6863384cccac6674bae6f7d0fcea58f955` |
| Dump format | Variable-width, timed complex range-FFT snapshots |

Verify the checked-in image before flashing:

```bash
sha256sum firmware/releases/l3_dump_configurable_capture_20260818.bin
```

## Choose A Capture Profile

| Profile | Wide/default | Dense/advanced | Dense/wide-late experimental |
|---|---:|---:|---:|
| Config | `iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg` | `iwr6843_l3dump_dense_36f2ms_53bin_iq8.cfg` | `iwr6843_l3dump_dense_36f2ms_53bin_iq8_wide_late.cfg` |
| Frames | 24 | 36 | 36 |
| Frame spacing | 3 ms | 2 ms | 2 ms |
| Movie duration | 72 ms | 72 ms | 72 ms |
| Saved bins per frame | 53 | 53 | 53 |
| Stored sample format | IQ16 | Fixed-scale IQ8 | Fixed-scale IQ8 |
| Payload bytes | 732,672 | 549,504 | 549,504 |
| Primary goal | Robust ball flight | Dense impact sampling | Isolate late-window coverage |

Use **wide/default** unless you are deliberately testing dense impact data. Its
53-bin windows tolerate more variation in tee distance, launch speed, and setup
geometry, while IQ16 retains the HWA output without quantization. Hardware tests
held the requested 3 ms cadence without RF or HWA faults. In an August 9
TrackMan session, its live inclinometer-adjusted LCMF output covered all 59
matched 9-iron and 7-iron shots with 0.86 degree MAE, 0.70 degree P50, and 1.75
degree P90 absolute error.

Use **dense/advanced** to test whether 2 ms temporal sampling improves impact
and launch measurements. It preserves the same 53-bin range span as the wide
profile and uses fixed-scale IQ8 so all 36 frames fit in L3. Its EDMA packing
path sustained 99.9911% HWA frame coverage in hardware cadence testing, with
zero IQ8 overruns or EDMA errors. The 53-bin dense profile still needs
source-of-truth TrackMan validation; horizontal launch and club metrics remain
experimental.

The dense/wide-late profile keeps the dense profile's cadence, precision, and
payload size, but keeps all ball-phase frames in bins 47-99. The standard dense
profile shifts its final six ball frames outward to bins 64-116. Comparing the
two profiles isolates late-window placement without changing IQ precision or
timing.

All profiles use 3 TX, 4 RX, 12 TDM loops, 128 acquired ADC samples, and the
same 72 ms capture duration. Changing profiles does not require reflashing.

The supported normal-TX profiles use a fixed positive TDM sign. This physical
registration keeps the full eight-element vertical channel aligned with OPS
radial speed. Automatic sign selection is useful for offline diagnostics, but
it is not the production policy because multipath can select the mirrored sign
and collapse that channel while leaving the range track apparently healthy.

## On-Chip Data Path

```text
RF chirp
  -> ADCBUF
  -> HWA 128-point range FFT
  -> EDMA copies the configured moving range window
  -> IQ16 is stored directly, or EDMA compacts HWA-scaled IQ8 into L3
  -> circular frame ring in L3 RAM
  -> sound trigger freezes the completed pre/post-impact movie
  -> header, timing/window metadata, scale table, and IQ payload stream to Pi
  -> firmware rearms the ring for the next shot
```

The saved bins remain complex I/Q so the host retains phase for vertical and
horizontal direction of arrival. Every frame carries its absolute range-window
start, bin count, and measured time delta. IQ8 frames also carry their scale,
allowing the host to restore the physical sample amplitude before processing.

For IQ8, `iq8Scale` selects a fixed power-of-two scale before `sensorStart`.
The HWA applies that scale, then EDMA copies the compact byte from each signed
component into L3 without a CPU packing loop. Firmware `stats` report completed
EDMA packs, waits, errors, clipped components, and missed HWA starts rather than
silently hiding cadence failures.

## Firmware And Host Contract

The wire format is defined in two places that must stay synchronized:

- Firmware: [`iwr6843/dump_format.h`](https://github.com/jewbetcha/openflight/blob/main/firmware/iwr6843/dump_format.h)
- Host parser: [`../src/openflight/iwr6843/dump.py`](https://github.com/jewbetcha/openflight/blob/main/src/openflight/iwr6843/dump.py)

The configurable version 7 transfer contains:

1. A packed 20-byte little-endian `l3_dump_header_t`.
2. A packed 24-byte `l3_temperature_report_t` captured immediately before streaming.
3. A `(start bin, valid bins, elapsed microseconds)` descriptor per frame.
4. A per-frame scale table when `sample_fmt` is IQ8.
5. Complex IQ16 or IQ8 samples ordered by frame, chirp, RX, and local range bin.
6. Each complex sample in TI's native imaginary-then-real order.

The header carries:

| Field | Meaning |
|---|---|
| `magic` | `ILD1` synchronization marker |
| `version` | Dump contract version |
| `n_frames` | Number of ring frames |
| `chirps_per_frame` | `n_tx x loops` |
| `n_tx`, `n_rx` | Virtual-array geometry |
| `n_samples` | Stored bins per chirp/RX for snapshot formats |
| `sample_fmt` | IQ16 or scaled IQ8 variable-width timed range snapshots |
| `trigger_frame` | Oldest circular-ring slot for chronological rotation |
| `frame_period_us` | Frame spacing used by trajectory fitting |

Changing the header, sample order, frame metadata, or sample format requires a
matching host-parser change and regression tests in the same commit.

## Repository Layout

| Path | Responsibility |
|---|---|
| `firmware/iwr6843/l3_dump.c` | RF control, HWA/EDMA pipeline, circular ring, freeze/rearm, CLI, and dump streaming |
| `firmware/iwr6843/dump_format.h` | Packed firmware-side wire contract |
| `firmware/iwr6843/makefile` | TI mmWave SDK application build and meta-image generation |
| `firmware/iwr6843/mss.cfg` | SYS/BIOS configuration |
| `firmware/iwr6843/mss_linker.cmd` | Places the ring and optional scratch buffers in L3 RAM |
| `firmware/Makefile` | Toolchain setup and production firmware build target |
| `firmware/releases/` | The single checked-in, validated flash image |
| `firmware/flash_iwr6843.py` | Pi-compatible IWR6843 ROM bootloader client |
| `config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg` | Default wide IQ16 capture profile |
| `config/iwr6843_l3dump_dense_36f2ms_53bin_iq8.cfg` | Dense IQ8 capture profile |
| `config/iwr6843_l3dump_dense_36f2ms_53bin_iq8_wide_late.cfg` | Experimental dense IQ8 capture with the wide late-flight window |
| `src/openflight/iwr6843/dump.py` | Python decoder and executable format reference |

## Where To Build, Flash, And Run

| Operation | Supported environment |
|---|---|
| Build | Native x86_64 Linux or the provided Docker image |
| Build on Apple Silicon | Docker Desktop emulating the x86_64 build image; UTM is a fallback |
| Build on Raspberry Pi 5 | Not currently reliable because TI's x86/i386 installer stubs can fail under QEMU and a 16 KiB host page size |
| Flash | Raspberry Pi using `flash_iwr6843.py`, or TI UniFlash as a fallback |
| Run | Raspberry Pi through OpenFlight |

The Pi can flash and run the image, but it should not be treated as the
canonical compiler host.

## Build On Apple Silicon With Docker

Install Docker Desktop, start its engine, and place the five TI installers
listed below in `firmware/ti_installers/`. The installers are license-gated and
are intentionally excluded from Git.

Build the reusable x86_64 toolchain image once:

```bash
make -C firmware docker-image
```

Build the supported firmware after any source change:

```bash
make -C firmware docker-build
```

Docker runs the same `build-native` recipe under `linux/amd64` and writes the
release artifact back into the host worktree at:

```text
firmware/releases/l3_dump_configurable_capture_20260818.bin
```

Use the UTM workflow below only when Docker emulation is unavailable.

## Build On Apple Silicon With UTM

### 1. Create An x86_64 Debian VM

In UTM:

1. Select **Create a New Virtual Machine**.
2. Select **Emulate**, not Virtualize.
3. Select **Linux** and an amd64 Debian netinst ISO.
4. Use `Intel ICH9 based PC (2009, x86_64)`.
5. Allocate at least 4 GB RAM and 30 GB storage.
6. Install `SSH server` and `standard system utilities`; a desktop is optional.
7. Eject the installer ISO before the first reboot into the installed system.

Confirm the guest architecture and page size:

```bash
uname -m
getconf PAGE_SIZE
```

Expected output is `x86_64` and `4096`.

### 2. Put OpenFlight In The VM

Clone the repository inside the VM or copy your existing worktree with `rsync`:

```bash
sudo apt-get update
sudo apt-get install -y git rsync openssh-server
git clone https://github.com/jewbetcha/openflight.git
cd openflight
```

To copy an existing worktree from the Mac instead:

```bash
rsync -av --exclude '.venv' ~/Projects/openflight/ \
  openflight@VM_ADDRESS:~/openflight/
```

Find the VM address with `ip addr` inside Debian.

### 3. Supply The TI Installers

TI's installers are large and license-gated, so they are intentionally ignored
by git. Download them from TI and place these exact files under
`firmware/ti_installers/` inside the VM:

```text
mmwave_sdk_03_06_02_00-LTS-Linux-x86-Install.bin
ti_cgt_tms470_20.2.7.LTS_linux-x64_installer.bin
bios_6_73_01_01.run
sysconfig-1.10.0_2163-setup.run
xdctools_3_61_00_16_core_linux.zip
```

The application is MSS/R4F-only; it does not require the C674x DSP compiler or
DSP libraries.

Verify the installer set:

```bash
make -C firmware check-installers
```

### 4. Install The Build Environment

Install Debian packages, probe every installer stub, and install the TI tools
under `/opt/ti`:

```bash
make -C firmware install-ti-deps-native
make -C firmware probe-installers-native
make -C firmware install-ti-tools-native
```

The resulting layout is:

```text
/opt/ti/sdk/mmwave_sdk_03_06_02_00-LTS
/opt/ti/cgt-arm/ti-cgt-arm_20.2.7.LTS
/opt/ti/bios/bios_6_73_01_01
/opt/ti/xdc/xdctools_3_61_00_16_core
/opt/ti/sysconfig
```

### 5. Build The Current Firmware

From the repository root inside the VM:

```bash
make -C firmware build-native
```

The target performs the application build, generates the flashable TI
meta-image, and copies the production image into `firmware/releases/`:

```text
firmware/releases/l3_dump_configurable_capture_20260818.bin
```

Generated `.xer4f`, `.map`, and intermediate `.bin` files stay under
`firmware/iwr6843/` and are ignored by Git. Current production images and
intentional rollback images live under `releases/`.

### 6. Copy Artifacts Out Of The VM

From the Mac:

```bash
mkdir -p artifacts/firmware_build
rsync -av \
  openflight@VM_ADDRESS:~/openflight/firmware/releases/ \
  artifacts/firmware_build/
```

## Build On Native x86_64 Linux

Use the same installer files and Make targets as the UTM VM. Confirm `uname -m`
reports `x86_64`, then start at **Supply The TI Installers** above.

The tool paths can be overridden when a machine does not use `/opt/ti`:

```bash
make -C firmware build-native \
  TI_ROOT=/custom/ti
```

## Supported Build Target

`make -C firmware build-native` and `make -C firmware docker-build` produce the
same configurable image. Capture timing, frame plan, moving windows, and IQ16
or IQ8 storage are selected by the runtime config. Use Git history for earlier
experiments rather than distributing those images or targets as installation
choices.

## Flash From The Raspberry Pi

The checked-in Python flasher uses the IWR6843 ROM UART bootloader and does not
require TI Cloud Agent. Flash over the CP2105 **Enhanced/UARTA** interface,
normally interface `00` and `/dev/ttyUSB0`. Do not use the Standard interface,
normally `/dev/ttyUSB1`.

### 1. Stop Serial Users

Stop OpenFlight and any calibration or test process using the TI port:

```bash
pgrep -af 'openflight|calibrate|shot_test'
sudo fuser -v /dev/ttyUSB0
```

### 2. Enter Flash Mode And Probe

Set the IWR6843LEVM switches to:

```text
S1.1 ON, S1.2 OFF, S1.3 ON, S1.4 ON, S1.5 OFF
```

Start the non-destructive probe:

```bash
uv run python firmware/flash_iwr6843.py \
  --probe \
  --port /dev/ttyUSB0
```

Follow the prompts exactly:

1. Type `READY` so the script opens UART and settles the control lines.
2. Press and release RESET only when requested.
3. Wait one second.
4. Type `PROBE`.

Do not continue until the ROM bootloader handshake passes.

### 3. Flash The Current Image

Leave the board in flash mode and run:

```bash
uv run python firmware/flash_iwr6843.py \
  firmware/releases/l3_dump_configurable_capture_20260818.bin \
  --port /dev/ttyUSB0
```

Type `READY`, press RESET when prompted, wait one second, and type `FLASH`. The
default workflow erases SFLASH, writes acknowledged chunks, closes the image,
and verifies the final ROM bootloader status.

Expected completion:

```text
Erasing existing SFLASH...
Opening firmware image...
Writing firmware...
Writing: 100% (346,820/346,820 bytes)
Closing and verifying firmware...

Flash verified by the IWR6843 ROM bootloader.
```

Do not reset, disconnect, or remove power while erase or write is active. A
failed write is recoverable because the ROM bootloader is not stored in SFLASH.
Leave the board in flash mode and rerun the complete command.

### 4. Return To Functional Mode

Set the switches to:

```text
S1.1 OFF, S1.2 OFF, S1.3 ON, S1.4 ON, S1.5 OFF
```

Press and release RESET. The firmware CLI and binary dumps now share the
Enhanced UART at 1,041,667 baud. Flashing itself always uses the ROM
bootloader's 115,200-baud protocol.

The flasher follows TI application note
[SWRA627, IWR6843 Bootloader Flow](https://www.ti.com/lit/an/swra627/swra627.pdf).

## Verify The Installed Firmware

Run OpenFlight with the matching config as described in the
[Operator Guide](../iwr6843/verify.md#start-openflight). With `--debug`, a
healthy capture reports:

```text
[IWR6843] Trigger #1: dumping firmware-frozen L3 ring
[IWR6843] Capture #1 complete: 732812 bytes
```

The firmware/config geometry is checked at `sensorStart`. A mismatch in TX
masks, loop count, frame count, or ADC samples is rejected rather than silently
capturing a differently shaped cube.

## Changing Capture Geometry

The runtime config controls the capture without rebuilding firmware:

| Config command | Purpose |
|---|---|
| `frameCfg` | TDM loop count and RF frame period |
| `captureFormat iq16\|iq8` | L3 sample representation |
| `iq8Scale 16\|32\|64\|128\|256` | Fixed power-of-two IQ8 quantization scale used by EDMA packing |
| `phaseCaptureCfg` | Pre/impact/ball window starts, widths, counts, and stride |

Before increasing loops, frames, transmitters, or bins, calculate the ring:

```text
IQ16 bytes = TX x loops x frames x RX x saved bins x 4
IQ8 bytes  = TX x loops x frames x RX x saved bins x 2
```

The result must fit within 786,432 L3 bytes along with any variant-specific L3
scratch sections. The linker places `.l3ring` and `.l3scratch` in `L3_RAM` and
fails the build if they overflow.

The firmware rejects invalid windows, frame plans, and L3 budgets at
`sensorStart`. The dense IQ8 profile also has only about 380 microseconds
between its 1.62 ms RF burst and the next 2 ms frame. Its EDMA packer moves the
low byte of each HWA-scaled IQ16 component into the compact ring without a CPU
copy loop. Cadence testing at scale `128` reduced the observed HWA miss rate
from 28.2% with CPU packing to 0.0089% with EDMA packing, with no IQ8 overruns
or EDMA errors. That proves scheduling headroom, but ball-signal fidelity and
launch-angle accuracy must still be validated against the IQ16 baseline.

## Validation Before Flashing A New Variant

Run the firmware contract and host-pipeline tests:

```bash
uv run pytest \
  tests/test_iwr6843_firmware_rearm.py \
  tests/test_iwr6843_pipeline.py \
  tests/test_iwr6843_driver.py \
  tests/test_iwr6843_monitor.py \
  tests/test_iwr6843_bootloader.py
```

Also check:

1. The `.cfg` matches all compile-time capture geometry.
2. The map file keeps `.l3ring` and `.l3scratch` inside L3.
3. The first static capture has the expected version, dimensions, frame period,
   per-frame window table, and total byte count.
4. Repeated dump/rearm cycles work without resetting the board.
5. Vertical and horizontal estimators can replay the new format offline.
6. Source-of-truth testing is repeated if timing, loops, frame spacing, TX
   schedule, or saved range coverage changed.

## Troubleshooting

| Symptom | Cause | Action |
|---|---|---|
| TI installer exits immediately | Build host is ARM, installer lacks execute permission, or i386 compatibility is missing | Use x86_64 Debian, run `install-ti-deps-native`, then `probe-installers-native` |
| VM returns to the Debian installer | ISO remains attached | Eject the ISO from the UTM CD/DVD drive and reboot |
| `check-installers` reports missing files | Installer name or location differs | Use the exact filenames under `firmware/ti_installers/` |
| Build cannot find `/opt/ti/...` | Tool installation did not complete or uses a custom root | Run `install-ti-tools-native` or pass `TI_ROOT=/custom/ti` |
| Link fails with L3 overflow | Ring or scratch allocation exceeds 768 KiB | Reduce frames, loops, TX count, or saved bins and inspect the map file |
| Probe receives no ROM response | Wrong CP2105 interface or RESET timing | Use Enhanced/UARTA, type `READY`, then RESET only when prompted |
| Flash fails after erase | Image transfer was interrupted | Leave flash mode enabled and rerun the full flash command; the ROM bootloader remains available |
| No CLI after flashing | Board remains in flash mode or was not reset | Restore functional switches and press RESET |
| Server rejects `captureFormat`, `iq8Scale`, or `phaseCaptureCfg` | Older firmware is flashed | Flash `l3_dump_configurable_capture_20260818.bin`, reset in functional mode, and retry |
| Dump length differs from the selected profile | Wrong config, interrupted UART transfer, or stale process | Verify firmware SHA-256, use Enhanced/UARTA, stop serial owners, reset, and retry |
| Dense profile reports sustained `hwa_missed`, `iq8_overrun`, or `iq8_edma_err` | The requested cadence exceeds processing time or EDMA packing failed | Return to the wide profile and inspect `stats`; do not trust descriptor cadence from a missed-frame run |
| First run works but restart hangs | Retired v1 image or incomplete shutdown | Flash the current release image and reset in functional mode |

## Historical Context

The [IWR6843 field report](../how-it-works/launch-angle.md) explains
why the project moved capture into on-chip L3 and how the estimator evolved.
The implementation has since advanced from full raw ADC rings to HWA-generated,
dynamically windowed complex range snapshots; this README is the authoritative
description of the current firmware.
