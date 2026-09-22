# K-LD7 Timing and PRF Probe

!!! warning "ARCHIVED DOCUMENT"

    This is a historical design or implementation note, kept as a record of why
    the code is shaped the way it is. It describes the project as of the date in
    its filename and **is not a guide to follow** — commands, paths, and
    constants may no longer match the code. See the
    [Archive index](../index.md) for current alternatives.

**Date:** 2026-06-09
**Status:** Approved
**Scope:** Build a guarded hardware exploration path for diagnosing unreliable K-LD7 launch-angle frames, measuring actual RADC frame cadence, and optionally probing explicitly listed undocumented serial commands.

## Problem

OpenFlight is not always getting enough reliable K-LD7 frames around impact to extract launch angle. The current production path streams RADC frames at 3 Mbaud and anchors angle extraction to the OPS243 impact timestamp, but a golf ball can cross the useful detection zone in only a few tens of milliseconds. More frames near impact would improve the odds of a clean launch-angle estimate.

The hypothesis is that a higher PRF and slightly lower pulse width could improve frame capture. The K-LD7 public documentation, however, does not expose PRF or pulse width directly. The module is documented as an FSK Doppler radar. The exposed controls are maximum speed (`RSPI`), maximum range (`RRAI`), base frequency (`RBFR`), threshold/tracking settings, and data-frame request commands. That means the first question is whether the bottleneck is true RF acquisition cadence, serial readout/request strategy, or undocumented firmware capability.

## Approach

Create a standalone hardware-test probe that talks to the K-LD7 binary serial protocol directly, but defaults to non-destructive measurement.

The exploration has two phases:

1. **Safe measurement:** use documented commands only. Measure actual frame cadence, dropped `DONE` frame numbers, RADC payload read time, and serial errors under current and swept documented settings.
2. **Unsafe probing:** only with an explicit opt-in flag, send explicitly listed candidate undocumented command packets and record response codes plus before/after parameter diffs.

This is not a broad brute-force scanner. The unsafe phase must only send command names and payloads deliberately supplied on the CLI or in a small allowlisted fixture. The goal is to discover whether a plausible engineering command exists, not to fuzz the firmware.

## Evidence From Current Code And Docs

- `src/openflight/kld7/tracker.py` configures `RRAI`, `RSPI`, `RBFR`, `DEDI`, `THOF`, `TRFT`, `MIAN`, `MAAN`, `MIRA`, `MARA`, `MISP`, `MASP`, and `VISU`.
- `src/openflight/kld7/serial_io.py` connects through the existing `kld7.KLD7` package at 3 Mbaud and patches packet reads for robust RADC streaming.
- The installed `kld7` package is a thin wrapper around binary packets: 4-byte command, 4-byte length, optional payload, then `RESP` plus data packets.
- `docs/K-LD7_Datasheet.pdf` lists documented commands: `INIT`, `GNFD`, `GRPS`, `SRPS`, `RFSE`, `GBYE`, and individual parameter setters including `RBFR`, `RSPI`, `RRAI`, `THOF`, `TRFT`, `VISU`, detection bounds, output routing, hold time, and micro-detection settings.
- The datasheet notes RADC is 3072 bytes and recommends the highest baud rate. It also states real-time readout is not possible if requested data readout time exceeds the typical frame duration.

## Architecture

**File:** `scripts/hardware-test/probe_kld7_timing.py`

**Entry point:** `uv run python scripts/hardware-test/probe_kld7_timing.py`

The script should not depend on the production `KLD7Tracker`. It should use pyserial directly or a small local protocol helper so we can observe raw packet timings without tracker-side buffering or selection logic.

### Components

**`KLD7Protocol`**

Low-level serial protocol wrapper:

- Opens the port at 115200 even parity.
- Sends `INIT` to negotiate the requested baud rate, usually 3 Mbaud.
- If `INIT` fails, optionally sends `GBYE` at 3 Mbaud and retries, matching the production recovery path for radars left streaming by a crashed process.
- Sends binary packets with `struct.pack("<4sI", cmd, length) + payload`.
- Reads exact packet headers and payloads with the same short-read tolerance used by `src/openflight/kld7/serial_io.py`.
- Exposes `send_command(cmd, payload=b"")`, `read_packet()`, `get_response()`, `read_params()`, `set_param()`, `request_frame(frame_mask)`, and `close()`.

**`TimingRecorder`**

Records one row per packet/frame:

- Host monotonic timestamp at command send.
- First-byte arrival timestamp.
- Header complete timestamp.
- Payload complete timestamp.
- Packet code.
- Payload length.
- Response code, if applicable.
- `DONE` frame number, if requested.
- Short-read or timeout errors.

**`ProbeResult`**

Aggregates each run:

- Effective RADC Hz.
- Effective `DONE` Hz.
- Mean/p50/p95 RADC payload read duration.
- Missing `DONE` frame numbers.
- Number of timeouts, short reads, invalid headers, and sensor-busy responses.
- Parameter snapshot before and after the run.

## CLI

```bash
uv run python scripts/hardware-test/probe_kld7_timing.py \
  --port /dev/kld7_vertical \
  --duration 10 \
  --frame-mask RADC,DONE
```

### Safe Flags

- `--port PATH`: serial port or udev alias. Required unless auto-detection finds exactly one K-LD7.
- `--baud 3000000`: target post-`INIT` baud rate.
- `--duration SECONDS`: measurement duration for each configuration.
- `--frame-mask LIST`: comma-separated frame types. Default `RADC,DONE`.
- `--rspi-sweep`: run all documented `RSPI` values.
- `--rrai VALUE`: documented range code or meters.
- `--rbfr VALUE`: documented base frequency code.
- `--output PATH`: write JSONL packet log plus summary JSON.
- `--restore-params`: restore the initial `GRPS` parameter snapshot before exit. Default on. Restore should use individual documented parameter setters, not an opaque `SRPS` write that includes the software-version bytes.

### Unsafe Flags

- `--unsafe-probe`: enables undocumented command probing.
- `--probe-command CMD[:HEX_PAYLOAD]`: one explicitly listed 4-byte uppercase command packet to send. Repeatable. Hex payload must have an even number of characters.
- `--allow-factory-reset`: permits `RFSE`. Default refuses `RFSE` even in unsafe mode.
- `--no-restore-params`: leaves changed parameters in place, but only after printing a warning and requiring `--unsafe-probe`.

Unsafe mode is unavailable unless `--output` is set, so every probe leaves an audit trail.

## Safe Measurement Flow

1. Resolve the serial port.
2. Connect at 115200, send `INIT`, switch to target baud.
3. Send `GRPS` and store the initial parameter snapshot.
4. Apply documented settings for the current run.
5. Re-read `GRPS` and record the active settings.
6. Loop until duration expires:
   - Send `GNFD` with the requested frame mask.
   - Read `RESP`.
   - Read expected data packets, including `DONE` when requested.
   - Record exact timing and packet sizes.
7. Summarize effective frame cadence and gaps.
8. Restore the initial parameter snapshot unless disabled.
9. Send `GBYE` and close the port.

## Unsafe Probe Flow

1. Run the safe connection and initial `GRPS` snapshot.
2. For each explicit `--probe-command`:
   - Refuse known destructive commands unless separately allowed.
   - Validate the command is exactly four ASCII uppercase bytes.
   - Send the command with its exact payload.
   - Read and record `RESP`.
   - Drain and record any follow-up packets.
   - Re-read `GRPS`.
   - Emit a before/after parameter diff.
3. If the command returns `OK` or changes parameters, run a short `RADC,DONE` cadence sample.
4. Restore the original parameter snapshot before exit by default.

## Safety Rules

- No random command generation.
- No wildcard payload sweeps.
- No persistent production integration until a command is understood and reproducible.
- Refuse `RFSE` by default.
- Always snapshot parameters before writes.
- Restore parameters by default.
- Always send `GBYE` on exit.
- Treat any command that appears to alter RF timing as lab-only until RFbeam confirms regulatory implications. The K-LD7 has modular RF approvals; changing waveform timing or duty cycle outside documented settings could invalidate those assumptions.

## Data Products

**JSONL packet log:** one row per command/packet with timing and raw metadata.

**Summary JSON:** one object per run:

```json
{
  "port": "/dev/kld7_vertical",
  "baud": 3000000,
  "frame_mask": ["RADC", "DONE"],
  "params_before": {"RSPI": 3, "RRAI": 0, "RBFR": 0},
  "params_active": {"RSPI": 3, "RRAI": 0, "RBFR": 0},
  "duration_s": 10.0,
  "radc_frames": 340,
  "done_frames": 340,
  "effective_radc_hz": 34.0,
  "done_frame_gaps": 0,
  "read_duration_ms_p95": 12.4,
  "errors": {}
}
```

## Interpretation

The first decision point is whether the measured RADC stream is close to the documented `RSPI=3` cadence.

- If RADC is near 34 Hz with low gaps, the launch-angle issue is probably frame selection, timing alignment, geometry, SNR, or target ambiguity rather than serial acquisition.
- If RADC is well below 34 Hz or has large `DONE` gaps, the next target is readout/request strategy, USB scheduling, or reducing requested packet volume.
- If a documented `RSPI` sweep changes effective cadence as expected, `RSPI=3` remains the highest documented cadence and true higher PRF is not exposed.
- If an undocumented command returns `OK` and changes cadence, it must stay experimental until validated against RF behavior, data quality, and module stability.

## Testing

Unit tests should cover protocol and summarization without hardware:

- Packet builder creates correct command headers and payload lengths.
- Packet reader handles split headers and split payloads.
- `DONE` frame gap detection works across wrap-free monotonically increasing frame numbers.
- Summary statistics are correct for synthetic packet logs.
- Unsafe mode refuses undocumented probes unless `--unsafe-probe` is present.
- `RFSE` is refused unless `--allow-factory-reset` is present.
- Parameter restore runs in `finally` when initial parameters were captured.

Hardware verification is manual and explicit:

```bash
uv run python scripts/hardware-test/probe_kld7_timing.py \
  --port /dev/kld7_vertical \
  --duration 10 \
  --frame-mask RADC,DONE \
  --output /tmp/kld7_vertical_timing.jsonl
```

## Out Of Scope

- Production use of undocumented commands.
- Automatic fuzzing of the K-LD7 firmware.
- Firmware extraction or binary reverse engineering.
- RF lab measurement of actual waveform timing.
- Changes to live launch-angle selection.
- UI changes.
- Replacing the `kld7` Python package in production.

## Files Created Or Modified

| File | Change |
|------|--------|
| `scripts/hardware-test/probe_kld7_timing.py` | New guarded timing/protocol probe |
| `tests/test_probe_kld7_timing.py` | New protocol, summary, and safety tests |
| `docs/kld7-troubleshooting.md` | Add a short section linking the probe and explaining interpretation |
