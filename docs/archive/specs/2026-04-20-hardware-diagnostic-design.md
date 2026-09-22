# Hardware Diagnostic Script

!!! warning "ARCHIVED DOCUMENT"

    This is a historical design or implementation note, kept as a record of why
    the code is shaped the way it is. It describes the project as of the date in
    its filename and **is not a guide to follow** — commands, paths, and
    constants may no longer match the code. See the
    [Archive index](../index.md) for current alternatives.

**Date:** 2026-04-20
**Status:** Approved
**Scope:** A unified guided diagnostic script that verifies every hardware component of the OpenFlight launch monitor — OPS243-A radar, both K-LD7 angle radars, the sound trigger, and the rolling buffer hardware trigger path.

## Problem

The repo has 5+ separate hardware test scripts (`test_kld7.py`, `test_radar_raw.py`, `test_sound_trigger_hardware.py`, `test_rolling_buffer_persist.py`, `debug_hardware_trigger.py`). Each tests one thing. There's no single "is the device fully working?" check — a user setting up a new build or debugging a failure has to run each script, interpret its output independently, and remember which flags each accepts. A unified diagnostic reduces setup friction and provides a clear pass/fail signal for support.

## Approach

Single Python script at `scripts/hardware-test/diagnose.py` that runs 6 checks in sequence. Each check is a function returning a `CheckResult`. Shared state (open serial handles, detected ports) is passed between checks via a `DiagnosticState` dataclass. Reuses existing `OPS243Radar`, `KLD7Tracker`, and `RollingBufferProcessor` — no new dependencies.

Guided walkthrough model: the diagnostic prompts the user at moments that require physical interaction (sound trigger). Connectivity checks run automatically. Missing optional hardware skips cleanly with a clear reason.

## Architecture

**File:** `scripts/hardware-test/diagnose.py` (one file)

**Entry point:** `uv run python scripts/hardware-test/diagnose.py`

**Flags:**
- `--require-all` — skipped checks (e.g., only one K-LD7 detected) fail instead of pass
- `--no-interactive` — skip checks that require user action (Check 6)
- `--timeout-multiplier N` (default 1.0) — scale all per-check timeouts for slow environments

**Data structures:**

```python
@dataclass
class CheckResult:
    name: str
    status: Literal["pass", "fail", "skip"]
    detail: str = ""        # one-line primary message
    hint: str = ""          # remediation suggestion on fail
    elapsed_s: float = 0.0

@dataclass
class DiagnosticState:
    """Shared state between checks so later ones can skip cleanly."""
    ops243_port: Optional[str] = None
    ops243_radar: Optional[OPS243Radar] = None  # held open across checks
    kld7_vertical_port: Optional[str] = None
    kld7_horizontal_port: Optional[str] = None
```

**Top-level flow:**

```python
CHECKS = [
    check_ops243_connectivity,
    check_ops243_rolling_buffer_persisted,
    check_ops243_software_trigger,
    check_kld7_vertical,
    check_kld7_horizontal,
    check_sound_trigger_end_to_end,
]

def main():
    state = DiagnosticState()
    results = []
    try:
        for check in CHECKS:
            print_check_start(check.__name__)
            result = run_with_timeout(check, state)
            results.append(result)
            print_check_result(result)
    finally:
        if state.ops243_radar:
            state.ops243_radar.disconnect()
    print_summary(results)
    sys.exit(0 if all_pass(results) else 1)
```

## The 6 Checks

### Check 1 — OPS243 connectivity (~2s)
- Auto-detect by scanning `/dev/ttyACM*` and `/dev/ttyUSB*`, try `OPS243Radar.connect()` at 57600 baud.
- Query `get_info()` and `get_firmware_version()`; PASS if either returns a usable response.
- Detail on pass: `"{port} • firmware {version}"`
- Hint on fail: `"Check USB connection and permissions (dialout group)"`
- Stores `state.ops243_port` and `state.ops243_radar` (kept open for Checks 2–3 and 6).

### Check 2 — OPS243 rolling buffer persisted (~1s)
- Queries current boot mode via `get_info()`. Verifies the radar is already in rolling buffer mode (G1/GC) without us configuring it — this proves the `A!` persistence from the setup script is active.
- Detail on pass: `"Radar boots in rolling buffer mode (G1)"`
- Hint on fail: `"Run 'uv run python scripts/hardware-test/test_rolling_buffer_persist.py --setup' then power cycle the radar"`
- Skipped (cascade) if Check 1 failed.

### Check 3 — OPS243 software trigger (~3s)
- Calls `radar.trigger_capture(timeout=5)`. Parses response through `RollingBufferProcessor.parse_capture()`. Verifies 4096 I/Q samples.
- Detail on pass: `"Capture received: 4096 I/Q samples"`
- On fail, detail distinguishes "no response" vs "malformed response":
  - No response: `"Software trigger sent but no I/Q response received"`
  - Malformed: `"Response received but parse failed: {parse_error}"`
- Skipped if Check 2 failed.

### Check 4 — K-LD7 vertical connectivity (~3s)
- Scans serial ports for FTDI/CP210x devices matching K-LD7 descriptors (reusing logic from `test_kld7.py`). If two match, uses the first one; the second is reserved for Check 5.
- Instantiates `KLD7Tracker(orientation="vertical")`, connects, streams for 1 second, verifies at least 5 frames received.
- Detail on pass: `"{port} • {frame_count} frames in 1.0s (~{fps} fps)"`
- Hint on fail: distinguishes "no K-LD7 detected on any serial port" vs "detected but no frames streaming".
- Disconnects after check so Check 5 can claim the other unit.

### Check 5 — K-LD7 horizontal connectivity (~3s)
- Same logic as Check 4, using the second detected K-LD7. Uses `orientation="horizontal"`.
- If only one K-LD7 was detected, SKIP with detail `"Only one K-LD7 detected — horizontal is optional"`.
- `--require-all` converts this skip to a fail.

### Check 6 — Sound trigger end-to-end (~20s, interactive)
- Calls `radar.rearm_rolling_buffer()` first (since Check 3's software trigger consumed the previous buffer).
- Prompts: `"Clap loudly or tap the SEN-14262 sensor. Waiting up to 15 seconds..."`
- Calls `radar.wait_for_hardware_trigger(timeout=15)`. On trigger, parses the capture, verifies 4096 samples.
- Detail on pass: `"Hardware trigger fired, capture valid (4096 samples)"`
- Hint on timeout: `"Check SEN-14262 wiring to OPS243 HOST_INT pin. Adjust R17 resistor if sensor too quiet."`
- On malformed response: distinguishes "trigger fired but capture bad" from no trigger.
- Skipped if Check 3 failed. Skipped with `--no-interactive`.

**Ordering rationale:** Later checks depend on earlier ones — connectivity → persistence → trigger. Failing a prerequisite cascade-skips dependent checks, producing a clear "OPS243 broken → these 4 checks skipped because of it" rather than a cascade of red.

## Output Format

Color-coded live checklist. Colors auto-disable when `stdout` isn't a TTY.

```
OpenFlight Hardware Diagnostic
========================================

[1/6] OPS243 connectivity ............... ✓ PASS (1.2s)
        /dev/ttyACM0 • firmware v1.2.3

[2/6] OPS243 rolling buffer persisted ... ✓ PASS (0.8s)
        Radar boots in rolling buffer mode (G1)

[3/6] OPS243 software trigger ........... ✓ PASS (2.1s)
        Capture received: 4096 I/Q samples

[4/6] K-LD7 vertical .................... ✓ PASS (2.5s)
        /dev/ttyUSB0 • 42 frames in 1.0s (~42 fps)

[5/6] K-LD7 horizontal .................. ⊘ SKIP
        Only one K-LD7 detected — horizontal is optional
        Pass --require-all to fail on skipped optional components

[6/6] Sound trigger + rolling buffer ....
        ► Clap loudly or tap the SEN-14262 sensor now.
          Waiting up to 15 seconds...
        ✓ Trigger received after 3.2s
[6/6] Sound trigger + rolling buffer .... ✓ PASS (3.4s)
        Hardware trigger fired, capture valid (4096 samples)

========================================
Summary: 5 passed, 0 failed, 1 skipped (10.0s total)
Overall: ✓ HEALTHY
```

**Color scheme:** ✓ PASS green, ✗ FAIL red, ⊘ SKIP yellow, ⧗ (running) dim, hints dim gray, headings bold.

**Failure example:**

```
[3/6] OPS243 software trigger ........... ✗ FAIL (5.1s)
        Software trigger sent but no I/Q response received
        → Radar may be stuck in a non-rolling-buffer mode despite Check 2
        → Try power-cycling the radar and re-running
```

## Exit Codes

- `0` — all checks passed (skips allowed unless `--require-all`)
- `1` — one or more checks failed
- `2` — script usage error (bad flags)
- `130` — interrupted by user (Ctrl+C)

## Error Handling

- Each check runs inside `try/except Exception as e` — any uncaught exception becomes `status=fail` with `detail=f"Unexpected error: {type(e).__name__}: {e}"`. The diagnostic never crashes; it reports.
- `SerialException` with `[Errno 13] Permission denied` maps to hint: `"Add your user to the dialout group: sudo usermod -aG dialout $USER"`.
- `KeyboardInterrupt` handled at top level for clean Ctrl+C exit (130).
- `finally` in `main()` ensures any open serial handles in `state` are closed on any exit path.

## Timeouts

- Checks 1, 2, 4, 5: 10s soft timeout per check
- Check 3: 8s
- Check 6: 15s wait + 5s processing = 20s
- `--timeout-multiplier N` scales all values
- Implementation: `signal.alarm()` wrapper (POSIX only — fine for Linux Pi, the target environment)

## Testing

**In scope (unit tests in `tests/test_diagnose.py`):**
- `CheckResult` formatting helpers (color, status symbols)
- Summary function with synthetic result sets (all pass, some fail, all skip)
- Each check function's decision logic, with `OPS243Radar` / `KLD7Tracker` mocked — verifies the correct `CheckResult` is produced for each observable radar/tracker response pattern

**Out of scope:**
- Live hardware interaction (that's what the script itself is for)
- Mocking the serial-level protocol — too brittle, no real verification value

## Out of Scope / YAGNI

- JSON output (can add later if a health dashboard needs it)
- Metrics export to Prometheus/Alloy (observability is already wired up elsewhere)
- CI integration beyond running the unit tests
- Web-based version of the diagnostic
- Fixing hardware problems detected (diagnostic is read-only; setup scripts already exist for fixes)

## Files Created / Modified

| File | Change |
|------|--------|
| `scripts/hardware-test/diagnose.py` | New |
| `tests/test_diagnose.py` | New |
| `README.md` | Add "Diagnostic" section pointing to the new script |
