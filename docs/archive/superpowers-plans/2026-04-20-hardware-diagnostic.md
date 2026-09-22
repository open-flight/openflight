# Hardware Diagnostic Script Implementation Plan

!!! warning "ARCHIVED DOCUMENT"

    This is a historical design or implementation note, kept as a record of why
    the code is shaped the way it is. It describes the project as of the date in
    its filename and **is not a guide to follow** — commands, paths, and
    constants may no longer match the code. See the
    [Archive index](../index.md) for current alternatives.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a unified guided diagnostic script that verifies every hardware component of the OpenFlight launch monitor.

**Architecture:** Single script at `scripts/hardware-test/diagnose.py` with 6 check functions sharing state via a `DiagnosticState` dataclass. TDD with mocked `OPS243Radar`/`KLD7Tracker` for each check. ANSI-color terminal output via stdlib.

**Tech Stack:** Python 3, pytest, `openflight.ops243.OPS243Radar`, `openflight.kld7.tracker.KLD7Tracker`, `openflight.rolling_buffer.processor.RollingBufferProcessor`, stdlib only (no `rich` or `colorama`).

**Spec:** `docs/superpowers/specs/2026-04-20-hardware-diagnostic-design.md`

---

### Task 1: Scaffold — data structures, formatters, empty runner

**Files:**
- Create: `scripts/hardware-test/diagnose.py`
- Create: `tests/test_diagnose.py`

- [ ] **Step 1: Write failing tests for CheckResult and formatters**

Create `tests/test_diagnose.py`:

```python
"""Tests for the hardware diagnostic script."""

import sys
from pathlib import Path

# diagnose.py is a script — import it as a module for testing
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "hardware-test"))

import diagnose


class TestCheckResult:
    def test_default_status_fields(self):
        r = diagnose.CheckResult(name="Test", status="pass")
        assert r.name == "Test"
        assert r.status == "pass"
        assert r.detail == ""
        assert r.hint == ""
        assert r.elapsed_s == 0.0

    def test_with_all_fields(self):
        r = diagnose.CheckResult(
            name="Test", status="fail", detail="something broke",
            hint="try this", elapsed_s=1.5,
        )
        assert r.detail == "something broke"
        assert r.hint == "try this"
        assert r.elapsed_s == 1.5


class TestFormatSummary:
    def test_all_pass(self):
        results = [
            diagnose.CheckResult(name="A", status="pass", elapsed_s=1.0),
            diagnose.CheckResult(name="B", status="pass", elapsed_s=2.0),
        ]
        summary = diagnose.format_summary(results)
        assert "2 passed" in summary
        assert "0 failed" in summary
        assert "HEALTHY" in summary

    def test_with_failure(self):
        results = [
            diagnose.CheckResult(name="A", status="pass"),
            diagnose.CheckResult(name="B", status="fail"),
        ]
        summary = diagnose.format_summary(results)
        assert "1 passed" in summary
        assert "1 failed" in summary
        assert "HEALTHY" not in summary

    def test_with_skip(self):
        results = [
            diagnose.CheckResult(name="A", status="pass"),
            diagnose.CheckResult(name="B", status="skip"),
        ]
        summary = diagnose.format_summary(results)
        assert "1 skipped" in summary


class TestOverallStatus:
    def test_healthy_when_all_pass(self):
        results = [diagnose.CheckResult(name="A", status="pass")]
        assert diagnose.overall_status(results, require_all=False) == "HEALTHY"

    def test_healthy_with_skips_when_not_require_all(self):
        results = [
            diagnose.CheckResult(name="A", status="pass"),
            diagnose.CheckResult(name="B", status="skip"),
        ]
        assert diagnose.overall_status(results, require_all=False) == "HEALTHY"

    def test_unhealthy_with_skips_when_require_all(self):
        results = [
            diagnose.CheckResult(name="A", status="pass"),
            diagnose.CheckResult(name="B", status="skip"),
        ]
        assert diagnose.overall_status(results, require_all=True) == "UNHEALTHY"

    def test_unhealthy_on_any_fail(self):
        results = [diagnose.CheckResult(name="A", status="fail")]
        assert diagnose.overall_status(results, require_all=False) == "UNHEALTHY"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-project pytest tests/test_diagnose.py -v`
Expected: ModuleNotFoundError (diagnose.py doesn't exist yet)

- [ ] **Step 3: Create diagnose.py scaffold**

Create `scripts/hardware-test/diagnose.py`:

```python
#!/usr/bin/env python3
"""
OpenFlight hardware diagnostic.

Runs a guided sequence of checks against every hardware component:
OPS243-A radar, both K-LD7 angle radars, and the sound trigger path.

Usage:
    uv run python scripts/hardware-test/diagnose.py
    uv run python scripts/hardware-test/diagnose.py --require-all
    uv run python scripts/hardware-test/diagnose.py --no-interactive
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Literal, Optional

# Allow running as a script from repo root
sys.path.insert(0, "src")

# ANSI escape codes. When stdout is a TTY these render as color;
# when redirected, the terminal never sees them because we disable.
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _color_enabled() -> bool:
    return sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    return f"{code}{text}{_RESET}" if _color_enabled() else text


@dataclass
class CheckResult:
    """Result of a single diagnostic check."""
    name: str
    status: Literal["pass", "fail", "skip"]
    detail: str = ""
    hint: str = ""
    elapsed_s: float = 0.0


@dataclass
class DiagnosticState:
    """Shared state across checks so later ones can skip cleanly."""
    ops243_port: Optional[str] = None
    ops243_radar: Optional[object] = None  # OPS243Radar, typed loosely to avoid import at module load
    kld7_vertical_port: Optional[str] = None
    kld7_horizontal_port: Optional[str] = None


def format_summary(results: list[CheckResult]) -> str:
    """Format the summary block printed at the end of a run."""
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    skipped = sum(1 for r in results if r.status == "skip")
    total_s = sum(r.elapsed_s for r in results)

    lines = []
    lines.append("=" * 40)
    lines.append(
        f"Summary: {passed} passed, {failed} failed, {skipped} skipped "
        f"({total_s:.1f}s total)"
    )
    if failed == 0 and all(r.status != "skip" for r in results):
        lines.append(f"Overall: {_c('✓ HEALTHY', _GREEN + _BOLD)}")
    elif failed == 0:
        lines.append(f"Overall: {_c('✓ HEALTHY', _GREEN + _BOLD)} (with skips)")
    else:
        lines.append(f"Overall: {_c('✗ UNHEALTHY', _RED + _BOLD)}")
    return "\n".join(lines)


def overall_status(results: list[CheckResult], require_all: bool) -> str:
    """Return 'HEALTHY' or 'UNHEALTHY' based on results and require_all flag."""
    if any(r.status == "fail" for r in results):
        return "UNHEALTHY"
    if require_all and any(r.status == "skip" for r in results):
        return "UNHEALTHY"
    return "HEALTHY"


def main() -> int:
    """Entry point — runs all checks and prints summary."""
    state = DiagnosticState()
    results: list[CheckResult] = []

    print(_c("OpenFlight Hardware Diagnostic", _BOLD))
    print("=" * 40)
    print()

    # Checks will be added in subsequent tasks
    CHECKS: list = []

    for check in CHECKS:
        result = check(state)
        results.append(result)

    print()
    print(format_summary(results))
    return 0 if overall_status(results, require_all=False) == "HEALTHY" else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-project pytest tests/test_diagnose.py -v`
Expected: All tests pass

- [ ] **Step 5: Verify script runs without crashing (empty checks list)**

Run: `uv run --no-project python scripts/hardware-test/diagnose.py`
Expected output: Header printed, summary shows "0 passed, 0 failed, 0 skipped", exit code 0.

- [ ] **Step 6: Commit**

```bash
git add scripts/hardware-test/diagnose.py tests/test_diagnose.py
git commit -m "feat(diagnose): scaffold hardware diagnostic with result types and runner"
```

---

### Task 2: Port detection helpers

**Files:**
- Modify: `scripts/hardware-test/diagnose.py` (add helper functions)
- Modify: `tests/test_diagnose.py` (add tests)

- [ ] **Step 1: Write failing tests for port detection**

Add to `tests/test_diagnose.py`:

```python
from unittest.mock import MagicMock, patch


class TestDetectOps243Port:
    @patch("diagnose.serial.tools.list_ports.comports")
    def test_finds_acm_device(self, mock_comports):
        mock_port = MagicMock()
        mock_port.device = "/dev/ttyACM0"
        mock_comports.return_value = [mock_port]
        assert diagnose.detect_ops243_port() == "/dev/ttyACM0"

    @patch("diagnose.serial.tools.list_ports.comports")
    def test_finds_usbmodem_device(self, mock_comports):
        mock_port = MagicMock()
        mock_port.device = "/dev/cu.usbmodem14301"
        mock_comports.return_value = [mock_port]
        assert diagnose.detect_ops243_port() == "/dev/cu.usbmodem14301"

    @patch("diagnose.serial.tools.list_ports.comports")
    def test_returns_none_when_nothing_matches(self, mock_comports):
        mock_port = MagicMock()
        mock_port.device = "/dev/ttyS0"
        mock_comports.return_value = [mock_port]
        assert diagnose.detect_ops243_port() is None

    @patch("diagnose.serial.tools.list_ports.comports")
    def test_empty_port_list(self, mock_comports):
        mock_comports.return_value = []
        assert diagnose.detect_ops243_port() is None


class TestDetectKld7Ports:
    def _make_port(self, device, desc="", mfg=""):
        p = MagicMock()
        p.device = device
        p.description = desc
        p.manufacturer = mfg
        return p

    @patch("diagnose.serial.tools.list_ports.comports")
    def test_finds_ftdi_port(self, mock_comports):
        mock_comports.return_value = [
            self._make_port("/dev/ttyUSB0", desc="FTDI USB-Serial"),
        ]
        assert diagnose.detect_kld7_ports() == ["/dev/ttyUSB0"]

    @patch("diagnose.serial.tools.list_ports.comports")
    def test_finds_cp210x_port(self, mock_comports):
        mock_comports.return_value = [
            self._make_port("/dev/ttyUSB1", desc="CP210x UART Bridge"),
        ]
        assert diagnose.detect_kld7_ports() == ["/dev/ttyUSB1"]

    @patch("diagnose.serial.tools.list_ports.comports")
    def test_finds_two_ports(self, mock_comports):
        mock_comports.return_value = [
            self._make_port("/dev/ttyUSB0", desc="FTDI USB-Serial"),
            self._make_port("/dev/ttyUSB1", desc="CP210x UART"),
        ]
        assert diagnose.detect_kld7_ports() == ["/dev/ttyUSB0", "/dev/ttyUSB1"]

    @patch("diagnose.serial.tools.list_ports.comports")
    def test_ignores_acm_devices(self, mock_comports):
        """ACM devices are OPS243, not K-LD7."""
        mock_comports.return_value = [
            self._make_port("/dev/ttyACM0", desc="OPS243"),
        ]
        assert diagnose.detect_kld7_ports() == []

    @patch("diagnose.serial.tools.list_ports.comports")
    def test_empty_list(self, mock_comports):
        mock_comports.return_value = []
        assert diagnose.detect_kld7_ports() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-project pytest tests/test_diagnose.py::TestDetectOps243Port tests/test_diagnose.py::TestDetectKld7Ports -v`
Expected: FAIL — `detect_ops243_port` and `detect_kld7_ports` don't exist

- [ ] **Step 3: Add port detection helpers to diagnose.py**

Add these imports near the top of `scripts/hardware-test/diagnose.py`:

```python
import serial.tools.list_ports
```

Then add these functions after `_c(...)` but before the `CheckResult` dataclass:

```python
def detect_ops243_port() -> Optional[str]:
    """Find the OPS243-A serial port.

    The OPS243-A enumerates as a USB CDC ACM device on Linux
    (/dev/ttyACM*) or a usbmodem on macOS. K-LD7 boards enumerate
    differently (FTDI/CP210x USB-serial bridges at /dev/ttyUSB* or
    /dev/cu.usbserial-*), so we identify OPS243 by device name.
    """
    for port in serial.tools.list_ports.comports():
        device = port.device or ""
        if "ACM" in device or "usbmodem" in device:
            return device
    return None


def detect_kld7_ports() -> list[str]:
    """Find all K-LD7 EVAL board serial ports.

    K-LD7 EVAL boards use FTDI or CP210x USB-serial chips, which
    advertise "FTDI", "CP210", or "usb-serial" in the port description
    or manufacturer string. Returns the list of matching devices in
    the order they were enumerated.
    """
    ports = []
    for port in serial.tools.list_ports.comports():
        desc = (port.description or "").lower()
        mfg = (port.manufacturer or "").lower()
        if any(kw in desc for kw in ["ftdi", "cp210", "usb-serial", "uart"]):
            ports.append(port.device)
        elif any(kw in mfg for kw in ["ftdi", "silicon labs"]):
            ports.append(port.device)
    return ports
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-project pytest tests/test_diagnose.py -v`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add scripts/hardware-test/diagnose.py tests/test_diagnose.py
git commit -m "feat(diagnose): add OPS243 and K-LD7 port detection helpers"
```

---

### Task 3: Check 1 — OPS243 connectivity

**Files:**
- Modify: `scripts/hardware-test/diagnose.py` (add check function + register)
- Modify: `tests/test_diagnose.py` (add tests)

- [ ] **Step 1: Write failing tests**

Add to `tests/test_diagnose.py`:

```python
class TestCheckOps243Connectivity:
    @patch("diagnose.detect_ops243_port")
    def test_returns_skip_when_no_port(self, mock_detect):
        mock_detect.return_value = None
        state = diagnose.DiagnosticState()
        result = diagnose.check_ops243_connectivity(state)
        assert result.status == "skip"
        assert "no OPS243" in result.detail.lower()
        assert state.ops243_port is None

    @patch("diagnose.detect_ops243_port")
    @patch("diagnose.OPS243Radar")
    def test_returns_pass_when_connects_and_returns_version(
        self, mock_radar_class, mock_detect,
    ):
        mock_detect.return_value = "/dev/ttyACM0"
        mock_radar = MagicMock()
        mock_radar.get_firmware_version.return_value = "1.2.3"
        mock_radar_class.return_value = mock_radar

        state = diagnose.DiagnosticState()
        result = diagnose.check_ops243_connectivity(state)

        assert result.status == "pass"
        assert "/dev/ttyACM0" in result.detail
        assert "1.2.3" in result.detail
        assert state.ops243_port == "/dev/ttyACM0"
        assert state.ops243_radar is mock_radar

    @patch("diagnose.detect_ops243_port")
    @patch("diagnose.OPS243Radar")
    def test_returns_fail_on_connect_exception(
        self, mock_radar_class, mock_detect,
    ):
        mock_detect.return_value = "/dev/ttyACM0"
        mock_radar = MagicMock()
        mock_radar.connect.side_effect = OSError("Permission denied")
        mock_radar_class.return_value = mock_radar

        state = diagnose.DiagnosticState()
        result = diagnose.check_ops243_connectivity(state)

        assert result.status == "fail"
        assert "Permission denied" in result.detail
        assert "dialout" in result.hint.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-project pytest tests/test_diagnose.py::TestCheckOps243Connectivity -v`
Expected: FAIL — `check_ops243_connectivity` doesn't exist, `OPS243Radar` import missing

- [ ] **Step 3: Add check function and imports**

Add to the imports in `scripts/hardware-test/diagnose.py`:

```python
import time

from openflight.ops243 import OPS243Radar
```

Add this check function after the port detection helpers:

```python
def check_ops243_connectivity(state: DiagnosticState) -> CheckResult:
    """Check 1 — verify OPS243 is detected and responds to queries."""
    start = time.time()
    port = detect_ops243_port()
    if port is None:
        return CheckResult(
            name="OPS243 connectivity",
            status="skip",
            detail="no OPS243 detected on /dev/ttyACM* or usbmodem*",
            elapsed_s=time.time() - start,
        )

    try:
        radar = OPS243Radar(port=port)
        radar.connect()
        version = radar.get_firmware_version()
    except OSError as e:
        hint = ""
        if "Permission denied" in str(e):
            hint = "Add your user to the dialout group: sudo usermod -aG dialout $USER"
        return CheckResult(
            name="OPS243 connectivity",
            status="fail",
            detail=f"Connect failed: {e}",
            hint=hint,
            elapsed_s=time.time() - start,
        )
    except Exception as e:
        return CheckResult(
            name="OPS243 connectivity",
            status="fail",
            detail=f"Unexpected error: {type(e).__name__}: {e}",
            hint="Check USB connection and permissions (dialout group)",
            elapsed_s=time.time() - start,
        )

    state.ops243_port = port
    state.ops243_radar = radar
    return CheckResult(
        name="OPS243 connectivity",
        status="pass",
        detail=f"{port} • firmware {version}",
        elapsed_s=time.time() - start,
    )
```

Register the check by updating `CHECKS` in `main()`:

```python
    CHECKS = [
        check_ops243_connectivity,
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --no-project pytest tests/test_diagnose.py::TestCheckOps243Connectivity -v`
Expected: All tests pass

- [ ] **Step 5: Add check-result printing**

Add these print helpers to `diagnose.py` after `_c()`:

```python
def print_check_header(index: int, total: int, name: str) -> None:
    """Print the leading line of a check while it's running."""
    line = f"[{index}/{total}] {name} ".ljust(45, ".")
    sys.stdout.write(f"{line} {_c('⧗ ...', _DIM)}\r")
    sys.stdout.flush()


def print_check_result(index: int, total: int, result: CheckResult) -> None:
    """Print the final line of a check after it completes."""
    symbol_map = {
        "pass": _c("✓ PASS", _GREEN),
        "fail": _c("✗ FAIL", _RED),
        "skip": _c("⊘ SKIP", _YELLOW),
    }
    symbol = symbol_map[result.status]
    line = f"[{index}/{total}] {result.name} ".ljust(45, ".")
    # Clear the "..." in-progress line first
    sys.stdout.write("\033[K")
    print(f"{line} {symbol} ({result.elapsed_s:.1f}s)")
    if result.detail:
        print(f"        {_c(result.detail, _DIM)}")
    if result.hint and result.status == "fail":
        print(f"        {_c('→ ' + result.hint, _DIM)}")
```

Update `main()` to use them:

```python
    for i, check in enumerate(CHECKS, 1):
        print_check_header(i, len(CHECKS), check.__name__)
        result = check(state)
        results.append(result)
        print_check_result(i, len(CHECKS), result)
```

- [ ] **Step 6: Commit**

```bash
git add scripts/hardware-test/diagnose.py tests/test_diagnose.py
git commit -m "feat(diagnose): Check 1 — OPS243 connectivity"
```

---

### Task 4: Check 2 — OPS243 rolling buffer persisted

**Files:**
- Modify: `scripts/hardware-test/diagnose.py`
- Modify: `tests/test_diagnose.py`

**Implementation note:** The spec says to verify the radar boots in rolling buffer mode. The reliable way without depending on a specific protocol response format: in CW mode, speed readings stream continuously on the serial port. In rolling buffer mode, nothing streams until a trigger. After connecting, read serial for ~500ms — if bytes arrive, the radar is in CW mode (not persisted).

- [ ] **Step 1: Write failing tests**

Add to `tests/test_diagnose.py`:

```python
class TestCheckOps243RollingBufferPersisted:
    def test_skipped_when_no_radar_in_state(self):
        state = diagnose.DiagnosticState()
        result = diagnose.check_ops243_rolling_buffer_persisted(state)
        assert result.status == "skip"

    def test_pass_when_no_data_streams(self):
        state = diagnose.DiagnosticState()
        mock_radar = MagicMock()
        # No stale data, no new data during the sample window
        mock_radar.serial.in_waiting = 0
        mock_radar.serial.read.return_value = b""
        state.ops243_radar = mock_radar

        result = diagnose.check_ops243_rolling_buffer_persisted(state)

        assert result.status == "pass"
        assert "rolling buffer" in result.detail.lower()

    def test_fail_when_data_streams(self):
        state = diagnose.DiagnosticState()
        mock_radar = MagicMock()
        # Simulate continuous streaming
        mock_radar.serial.in_waiting = 42
        mock_radar.serial.read.return_value = b'{"speed": 12.5}\n'
        state.ops243_radar = mock_radar

        result = diagnose.check_ops243_rolling_buffer_persisted(state)

        assert result.status == "fail"
        assert "streaming" in result.detail.lower() or "cw mode" in result.detail.lower()
        assert "test_rolling_buffer_persist.py --setup" in result.hint
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-project pytest tests/test_diagnose.py::TestCheckOps243RollingBufferPersisted -v`
Expected: FAIL

- [ ] **Step 3: Add check function**

Add to `diagnose.py` after `check_ops243_connectivity`:

```python
def check_ops243_rolling_buffer_persisted(state: DiagnosticState) -> CheckResult:
    """Check 2 — verify radar boots in rolling buffer mode.

    In standard (CW) mode, the OPS243 streams speed readings continuously.
    In rolling buffer mode, the serial port is silent until triggered.
    We clear any stale data then sample for 500ms — if bytes arrive,
    the radar is in CW mode and the persistence workaround hasn't been
    applied.
    """
    start = time.time()
    if state.ops243_radar is None:
        return CheckResult(
            name="OPS243 rolling buffer persisted",
            status="skip",
            detail="skipped because OPS243 connectivity check did not succeed",
            elapsed_s=time.time() - start,
        )

    radar = state.ops243_radar
    # Clear any buffered data first
    if hasattr(radar.serial, "reset_input_buffer"):
        radar.serial.reset_input_buffer()

    # Sample for 500ms — in CW mode we'd see speed readings stream
    sample_window_s = 0.5
    sample_start = time.time()
    total_bytes = 0
    while (time.time() - sample_start) < sample_window_s:
        in_waiting = radar.serial.in_waiting
        if in_waiting:
            total_bytes += in_waiting
            radar.serial.read(in_waiting)
        time.sleep(0.02)

    if total_bytes > 0:
        return CheckResult(
            name="OPS243 rolling buffer persisted",
            status="fail",
            detail=f"Radar is streaming data ({total_bytes} bytes in {sample_window_s}s) — CW mode, not rolling buffer",
            hint="Run 'uv run python scripts/hardware-test/test_rolling_buffer_persist.py --setup' then power cycle the radar",
            elapsed_s=time.time() - start,
        )

    return CheckResult(
        name="OPS243 rolling buffer persisted",
        status="pass",
        detail="Radar boots in rolling buffer mode (silent serial)",
        elapsed_s=time.time() - start,
    )
```

Register it in `CHECKS`:

```python
    CHECKS = [
        check_ops243_connectivity,
        check_ops243_rolling_buffer_persisted,
    ]
```

- [ ] **Step 4: Run tests**

Run: `uv run --no-project pytest tests/test_diagnose.py -v`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add scripts/hardware-test/diagnose.py tests/test_diagnose.py
git commit -m "feat(diagnose): Check 2 — rolling buffer persistence"
```

---

### Task 5: Check 3 — OPS243 software trigger

**Files:**
- Modify: `scripts/hardware-test/diagnose.py`
- Modify: `tests/test_diagnose.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_diagnose.py`:

```python
class TestCheckOps243SoftwareTrigger:
    def test_skipped_when_no_radar(self):
        state = diagnose.DiagnosticState()
        result = diagnose.check_ops243_software_trigger(state)
        assert result.status == "skip"

    @patch("diagnose.RollingBufferProcessor")
    def test_pass_with_valid_capture(self, mock_processor_class):
        state = diagnose.DiagnosticState()
        state.ops243_radar = MagicMock()
        state.ops243_radar.trigger_capture.return_value = '{"I":[...]}...'

        mock_capture = MagicMock()
        mock_capture.i_samples = [0] * 4096
        mock_capture.q_samples = [0] * 4096
        mock_processor = MagicMock()
        mock_processor.parse_capture.return_value = mock_capture
        mock_processor_class.return_value = mock_processor

        result = diagnose.check_ops243_software_trigger(state)

        assert result.status == "pass"
        assert "4096" in result.detail

    def test_fail_on_empty_response(self):
        state = diagnose.DiagnosticState()
        state.ops243_radar = MagicMock()
        state.ops243_radar.trigger_capture.return_value = ""

        result = diagnose.check_ops243_software_trigger(state)

        assert result.status == "fail"
        assert "no I/Q response" in result.detail or "no response" in result.detail.lower()

    @patch("diagnose.RollingBufferProcessor")
    def test_fail_on_unparseable_response(self, mock_processor_class):
        state = diagnose.DiagnosticState()
        state.ops243_radar = MagicMock()
        state.ops243_radar.trigger_capture.return_value = "garbage"

        mock_processor = MagicMock()
        mock_processor.parse_capture.return_value = None  # parse_capture returns None on failure
        mock_processor_class.return_value = mock_processor

        result = diagnose.check_ops243_software_trigger(state)

        assert result.status == "fail"
        assert "parse" in result.detail.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-project pytest tests/test_diagnose.py::TestCheckOps243SoftwareTrigger -v`
Expected: FAIL

- [ ] **Step 3: Add check function and import**

Add import to `diagnose.py`:

```python
from openflight.rolling_buffer.processor import RollingBufferProcessor
```

Add check function:

```python
def check_ops243_software_trigger(state: DiagnosticState) -> CheckResult:
    """Check 3 — send S! and verify we get a valid 4096-sample I/Q capture."""
    start = time.time()
    if state.ops243_radar is None:
        return CheckResult(
            name="OPS243 software trigger",
            status="skip",
            detail="skipped because OPS243 is not connected",
            elapsed_s=time.time() - start,
        )

    try:
        response = state.ops243_radar.trigger_capture(timeout=5.0)
    except Exception as e:
        return CheckResult(
            name="OPS243 software trigger",
            status="fail",
            detail=f"trigger_capture raised {type(e).__name__}: {e}",
            elapsed_s=time.time() - start,
        )

    if not response:
        return CheckResult(
            name="OPS243 software trigger",
            status="fail",
            detail="Software trigger sent but no I/Q response received",
            hint="Radar may be stuck — try power-cycling and re-running",
            elapsed_s=time.time() - start,
        )

    processor = RollingBufferProcessor()
    capture = processor.parse_capture(response)
    if capture is None:
        return CheckResult(
            name="OPS243 software trigger",
            status="fail",
            detail="Response received but parse failed (missing I, Q, or timing fields)",
            elapsed_s=time.time() - start,
        )

    n = len(capture.i_samples)
    return CheckResult(
        name="OPS243 software trigger",
        status="pass",
        detail=f"Capture received: {n} I/Q samples",
        elapsed_s=time.time() - start,
    )
```

Register it:

```python
    CHECKS = [
        check_ops243_connectivity,
        check_ops243_rolling_buffer_persisted,
        check_ops243_software_trigger,
    ]
```

- [ ] **Step 4: Run tests**

Run: `uv run --no-project pytest tests/test_diagnose.py -v`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add scripts/hardware-test/diagnose.py tests/test_diagnose.py
git commit -m "feat(diagnose): Check 3 — OPS243 software trigger"
```

---

### Task 6: Check 4 — K-LD7 vertical

**Files:**
- Modify: `scripts/hardware-test/diagnose.py`
- Modify: `tests/test_diagnose.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_diagnose.py`:

```python
class TestCheckKld7Vertical:
    @patch("diagnose.detect_kld7_ports")
    def test_skip_when_no_kld7_detected(self, mock_detect):
        mock_detect.return_value = []
        state = diagnose.DiagnosticState()
        result = diagnose.check_kld7_vertical(state)
        assert result.status == "skip"
        assert "no K-LD7" in result.detail

    @patch("diagnose.detect_kld7_ports")
    @patch("diagnose.KLD7Tracker")
    @patch("diagnose.time.sleep")
    def test_pass_when_frames_stream(
        self, mock_sleep, mock_tracker_class, mock_detect,
    ):
        mock_detect.return_value = ["/dev/ttyUSB0"]
        mock_tracker = MagicMock()
        mock_tracker.connect.return_value = True
        # Simulate 42 frames in the ring buffer after 1s of streaming
        mock_tracker._ring_buffer = [MagicMock() for _ in range(42)]
        mock_tracker_class.return_value = mock_tracker

        state = diagnose.DiagnosticState()
        result = diagnose.check_kld7_vertical(state)

        assert result.status == "pass"
        assert "42 frames" in result.detail
        assert state.kld7_vertical_port == "/dev/ttyUSB0"
        mock_tracker.stop.assert_called_once()

    @patch("diagnose.detect_kld7_ports")
    @patch("diagnose.KLD7Tracker")
    @patch("diagnose.time.sleep")
    def test_fail_when_no_frames(
        self, mock_sleep, mock_tracker_class, mock_detect,
    ):
        mock_detect.return_value = ["/dev/ttyUSB0"]
        mock_tracker = MagicMock()
        mock_tracker.connect.return_value = True
        mock_tracker._ring_buffer = []  # No frames received
        mock_tracker_class.return_value = mock_tracker

        state = diagnose.DiagnosticState()
        result = diagnose.check_kld7_vertical(state)

        assert result.status == "fail"
        assert "no frames" in result.detail.lower() or "0 frames" in result.detail

    @patch("diagnose.detect_kld7_ports")
    @patch("diagnose.KLD7Tracker")
    def test_fail_when_connect_returns_false(
        self, mock_tracker_class, mock_detect,
    ):
        mock_detect.return_value = ["/dev/ttyUSB0"]
        mock_tracker = MagicMock()
        mock_tracker.connect.return_value = False
        mock_tracker_class.return_value = mock_tracker

        state = diagnose.DiagnosticState()
        result = diagnose.check_kld7_vertical(state)

        assert result.status == "fail"
        assert "connect" in result.detail.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-project pytest tests/test_diagnose.py::TestCheckKld7Vertical -v`
Expected: FAIL

- [ ] **Step 3: Add check function and import**

Add import to `diagnose.py`:

```python
from openflight.kld7.tracker import KLD7Tracker
```

Add check function:

```python
def check_kld7_vertical(state: DiagnosticState) -> CheckResult:
    """Check 4 — verify vertical K-LD7 connects and streams frames.

    Uses the first detected K-LD7 port; Check 5 uses the second if
    present. Streams for 1 second and verifies at least 5 frames
    arrived (expected ~34 fps in the configured mode).
    """
    start = time.time()
    ports = detect_kld7_ports()
    if not ports:
        return CheckResult(
            name="K-LD7 vertical",
            status="skip",
            detail="no K-LD7 detected on any USB-serial port",
            elapsed_s=time.time() - start,
        )

    port = ports[0]
    tracker = KLD7Tracker(port=port, orientation="vertical")
    try:
        if not tracker.connect():
            return CheckResult(
                name="K-LD7 vertical",
                status="fail",
                detail=f"{port}: connect returned False (kld7 library or device error)",
                elapsed_s=time.time() - start,
            )

        tracker.start()
        stream_window_s = 1.0
        time.sleep(stream_window_s)
        frame_count = len(tracker._ring_buffer)
    except Exception as e:
        return CheckResult(
            name="K-LD7 vertical",
            status="fail",
            detail=f"{port}: {type(e).__name__}: {e}",
            elapsed_s=time.time() - start,
        )
    finally:
        try:
            tracker.stop()
        except Exception:
            pass

    if frame_count < 5:
        return CheckResult(
            name="K-LD7 vertical",
            status="fail",
            detail=f"{port}: only {frame_count} frames in {stream_window_s}s (expected >= 5)",
            hint="Detected but no frames streaming — check K-LD7 firmware / cable",
            elapsed_s=time.time() - start,
        )

    fps = frame_count / stream_window_s
    state.kld7_vertical_port = port
    return CheckResult(
        name="K-LD7 vertical",
        status="pass",
        detail=f"{port} • {frame_count} frames in {stream_window_s:.1f}s (~{fps:.0f} fps)",
        elapsed_s=time.time() - start,
    )
```

Register:

```python
    CHECKS = [
        check_ops243_connectivity,
        check_ops243_rolling_buffer_persisted,
        check_ops243_software_trigger,
        check_kld7_vertical,
    ]
```

- [ ] **Step 4: Run tests**

Run: `uv run --no-project pytest tests/test_diagnose.py -v`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add scripts/hardware-test/diagnose.py tests/test_diagnose.py
git commit -m "feat(diagnose): Check 4 — K-LD7 vertical"
```

---

### Task 7: Check 5 — K-LD7 horizontal

**Files:**
- Modify: `scripts/hardware-test/diagnose.py`
- Modify: `tests/test_diagnose.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_diagnose.py`:

```python
class TestCheckKld7Horizontal:
    @patch("diagnose.detect_kld7_ports")
    def test_skip_when_no_kld7_detected(self, mock_detect):
        mock_detect.return_value = []
        state = diagnose.DiagnosticState()
        result = diagnose.check_kld7_horizontal(state)
        assert result.status == "skip"
        assert "no K-LD7" in result.detail

    @patch("diagnose.detect_kld7_ports")
    def test_skip_when_only_one_kld7_detected(self, mock_detect):
        mock_detect.return_value = ["/dev/ttyUSB0"]
        state = diagnose.DiagnosticState()
        state.kld7_vertical_port = "/dev/ttyUSB0"  # vertical claimed it
        result = diagnose.check_kld7_horizontal(state)
        assert result.status == "skip"
        assert "only one K-LD7" in result.detail.lower() or "optional" in result.detail.lower()

    @patch("diagnose.detect_kld7_ports")
    @patch("diagnose.KLD7Tracker")
    @patch("diagnose.time.sleep")
    def test_pass_with_second_port(
        self, mock_sleep, mock_tracker_class, mock_detect,
    ):
        mock_detect.return_value = ["/dev/ttyUSB0", "/dev/ttyUSB1"]
        mock_tracker = MagicMock()
        mock_tracker.connect.return_value = True
        mock_tracker._ring_buffer = [MagicMock() for _ in range(40)]
        mock_tracker_class.return_value = mock_tracker

        state = diagnose.DiagnosticState()
        state.kld7_vertical_port = "/dev/ttyUSB0"

        result = diagnose.check_kld7_horizontal(state)

        assert result.status == "pass"
        assert "/dev/ttyUSB1" in result.detail
        assert state.kld7_horizontal_port == "/dev/ttyUSB1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-project pytest tests/test_diagnose.py::TestCheckKld7Horizontal -v`
Expected: FAIL

- [ ] **Step 3: Add check function**

Add to `diagnose.py`:

```python
def check_kld7_horizontal(state: DiagnosticState) -> CheckResult:
    """Check 5 — verify horizontal K-LD7 connects and streams frames.

    Uses the second detected K-LD7 port. Horizontal K-LD7 is optional,
    so SKIP cleanly when only one K-LD7 is present.
    """
    start = time.time()
    ports = detect_kld7_ports()
    if not ports:
        return CheckResult(
            name="K-LD7 horizontal",
            status="skip",
            detail="no K-LD7 detected on any USB-serial port",
            elapsed_s=time.time() - start,
        )

    # Pick the port the vertical check didn't use
    candidate_ports = [p for p in ports if p != state.kld7_vertical_port]
    if not candidate_ports:
        return CheckResult(
            name="K-LD7 horizontal",
            status="skip",
            detail="Only one K-LD7 detected — horizontal is optional",
            elapsed_s=time.time() - start,
        )

    port = candidate_ports[0]
    tracker = KLD7Tracker(port=port, orientation="horizontal")
    try:
        if not tracker.connect():
            return CheckResult(
                name="K-LD7 horizontal",
                status="fail",
                detail=f"{port}: connect returned False",
                elapsed_s=time.time() - start,
            )

        tracker.start()
        stream_window_s = 1.0
        time.sleep(stream_window_s)
        frame_count = len(tracker._ring_buffer)
    except Exception as e:
        return CheckResult(
            name="K-LD7 horizontal",
            status="fail",
            detail=f"{port}: {type(e).__name__}: {e}",
            elapsed_s=time.time() - start,
        )
    finally:
        try:
            tracker.stop()
        except Exception:
            pass

    if frame_count < 5:
        return CheckResult(
            name="K-LD7 horizontal",
            status="fail",
            detail=f"{port}: only {frame_count} frames in {stream_window_s}s",
            hint="Detected but no frames streaming — check K-LD7 firmware / cable",
            elapsed_s=time.time() - start,
        )

    fps = frame_count / stream_window_s
    state.kld7_horizontal_port = port
    return CheckResult(
        name="K-LD7 horizontal",
        status="pass",
        detail=f"{port} • {frame_count} frames in {stream_window_s:.1f}s (~{fps:.0f} fps)",
        elapsed_s=time.time() - start,
    )
```

Register:

```python
    CHECKS = [
        check_ops243_connectivity,
        check_ops243_rolling_buffer_persisted,
        check_ops243_software_trigger,
        check_kld7_vertical,
        check_kld7_horizontal,
    ]
```

- [ ] **Step 4: Run tests**

Run: `uv run --no-project pytest tests/test_diagnose.py -v`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add scripts/hardware-test/diagnose.py tests/test_diagnose.py
git commit -m "feat(diagnose): Check 5 — K-LD7 horizontal"
```

---

### Task 8: Check 6 — Sound trigger end-to-end

**Files:**
- Modify: `scripts/hardware-test/diagnose.py`
- Modify: `tests/test_diagnose.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_diagnose.py`:

```python
class TestCheckSoundTrigger:
    def test_skip_when_no_radar(self):
        state = diagnose.DiagnosticState()
        result = diagnose.check_sound_trigger_end_to_end(state, interactive=True)
        assert result.status == "skip"

    def test_skip_when_not_interactive(self):
        state = diagnose.DiagnosticState()
        state.ops243_radar = MagicMock()
        result = diagnose.check_sound_trigger_end_to_end(state, interactive=False)
        assert result.status == "skip"
        assert "interactive" in result.detail.lower()

    @patch("diagnose.RollingBufferProcessor")
    def test_pass_with_trigger_and_valid_capture(self, mock_processor_class):
        state = diagnose.DiagnosticState()
        radar = MagicMock()
        radar.wait_for_hardware_trigger.return_value = '{"I":[...]}'
        state.ops243_radar = radar

        mock_capture = MagicMock()
        mock_capture.i_samples = [0] * 4096
        mock_processor = MagicMock()
        mock_processor.parse_capture.return_value = mock_capture
        mock_processor_class.return_value = mock_processor

        result = diagnose.check_sound_trigger_end_to_end(state, interactive=True)

        assert result.status == "pass"
        assert "4096" in result.detail
        radar.rearm_rolling_buffer.assert_called_once()

    def test_fail_on_timeout(self):
        state = diagnose.DiagnosticState()
        radar = MagicMock()
        radar.wait_for_hardware_trigger.return_value = ""
        state.ops243_radar = radar

        result = diagnose.check_sound_trigger_end_to_end(state, interactive=True)

        assert result.status == "fail"
        assert "trigger" in result.detail.lower()
        assert "SEN-14262" in result.hint or "R17" in result.hint
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-project pytest tests/test_diagnose.py::TestCheckSoundTrigger -v`
Expected: FAIL

- [ ] **Step 3: Add check function**

Add to `diagnose.py`:

```python
def check_sound_trigger_end_to_end(
    state: DiagnosticState, interactive: bool = True,
) -> CheckResult:
    """Check 6 — verify sound trigger path fires a hardware trigger.

    Re-arms the rolling buffer (Check 3 consumed it), prompts the user
    to clap or tap the SEN-14262 sensor, waits up to 15s for the
    hardware trigger, and parses the resulting capture.
    """
    start = time.time()
    if state.ops243_radar is None:
        return CheckResult(
            name="Sound trigger + rolling buffer",
            status="skip",
            detail="skipped because OPS243 is not connected",
            elapsed_s=time.time() - start,
        )
    if not interactive:
        return CheckResult(
            name="Sound trigger + rolling buffer",
            status="skip",
            detail="skipped — interactive mode disabled (--no-interactive)",
            elapsed_s=time.time() - start,
        )

    radar = state.ops243_radar
    try:
        radar.rearm_rolling_buffer()
    except Exception as e:
        return CheckResult(
            name="Sound trigger + rolling buffer",
            status="fail",
            detail=f"Failed to rearm rolling buffer: {type(e).__name__}: {e}",
            elapsed_s=time.time() - start,
        )

    print(f"        {_c('► Clap loudly or tap the SEN-14262 sensor now.', _BOLD)}")
    print(f"        {_c('  Waiting up to 15 seconds...', _DIM)}")

    try:
        response = radar.wait_for_hardware_trigger(timeout=15.0)
    except Exception as e:
        return CheckResult(
            name="Sound trigger + rolling buffer",
            status="fail",
            detail=f"wait_for_hardware_trigger raised {type(e).__name__}: {e}",
            elapsed_s=time.time() - start,
        )

    if not response:
        return CheckResult(
            name="Sound trigger + rolling buffer",
            status="fail",
            detail="No hardware trigger fired within 15 seconds",
            hint="Check SEN-14262 wiring to OPS243 HOST_INT pin. Adjust R17 resistor if sensor too quiet.",
            elapsed_s=time.time() - start,
        )

    processor = RollingBufferProcessor()
    capture = processor.parse_capture(response)
    if capture is None:
        return CheckResult(
            name="Sound trigger + rolling buffer",
            status="fail",
            detail="Trigger fired but capture response did not parse",
            elapsed_s=time.time() - start,
        )

    n = len(capture.i_samples)
    return CheckResult(
        name="Sound trigger + rolling buffer",
        status="pass",
        detail=f"Hardware trigger fired, capture valid ({n} samples)",
        elapsed_s=time.time() - start,
    )
```

Register (note: this check takes an extra `interactive` arg, handled in Task 9):

```python
    CHECKS = [
        check_ops243_connectivity,
        check_ops243_rolling_buffer_persisted,
        check_ops243_software_trigger,
        check_kld7_vertical,
        check_kld7_horizontal,
        check_sound_trigger_end_to_end,
    ]
```

- [ ] **Step 4: Run tests**

Run: `uv run --no-project pytest tests/test_diagnose.py -v`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add scripts/hardware-test/diagnose.py tests/test_diagnose.py
git commit -m "feat(diagnose): Check 6 — sound trigger end-to-end"
```

---

### Task 9: CLI flags and interactive mode wiring

**Files:**
- Modify: `scripts/hardware-test/diagnose.py`
- Modify: `tests/test_diagnose.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_diagnose.py`:

```python
class TestParseArgs:
    def test_default_flags(self):
        args = diagnose.parse_args([])
        assert args.require_all is False
        assert args.no_interactive is False

    def test_require_all_flag(self):
        args = diagnose.parse_args(["--require-all"])
        assert args.require_all is True

    def test_no_interactive_flag(self):
        args = diagnose.parse_args(["--no-interactive"])
        assert args.no_interactive is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --no-project pytest tests/test_diagnose.py::TestParseArgs -v`
Expected: FAIL

- [ ] **Step 3: Add argparse and wire up flags**

Add import to `diagnose.py`:

```python
import argparse
```

Add `parse_args` function above `main()`:

```python
def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OpenFlight hardware diagnostic",
    )
    parser.add_argument(
        "--require-all", action="store_true",
        help="Fail if any check is skipped (e.g., optional horizontal K-LD7)",
    )
    parser.add_argument(
        "--no-interactive", action="store_true",
        help="Skip checks that require user interaction (sound trigger)",
    )
    return parser.parse_args(argv)
```

Update `main()` to use the flags:

```python
def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    state = DiagnosticState()
    results: list[CheckResult] = []

    print(_c("OpenFlight Hardware Diagnostic", _BOLD))
    print("=" * 40)
    print()

    CHECKS = [
        check_ops243_connectivity,
        check_ops243_rolling_buffer_persisted,
        check_ops243_software_trigger,
        check_kld7_vertical,
        check_kld7_horizontal,
        # Sound trigger check is wrapped to pass interactive flag
        lambda s: check_sound_trigger_end_to_end(s, interactive=not args.no_interactive),
    ]

    # Pretty names for the runner output (the sound-trigger lambda has no __name__)
    CHECK_NAMES = [
        "OPS243 connectivity",
        "OPS243 rolling buffer persisted",
        "OPS243 software trigger",
        "K-LD7 vertical",
        "K-LD7 horizontal",
        "Sound trigger + rolling buffer",
    ]

    try:
        for i, (check, name) in enumerate(zip(CHECKS, CHECK_NAMES), 1):
            print_check_header(i, len(CHECKS), name)
            result = check(state)
            results.append(result)
            print_check_result(i, len(CHECKS), result)
    except KeyboardInterrupt:
        print()
        print(_c("Interrupted by user", _YELLOW))
        return 130
    finally:
        if state.ops243_radar is not None:
            try:
                state.ops243_radar.disconnect()
            except Exception:
                pass

    print()
    print(format_summary(results))
    return 0 if overall_status(results, require_all=args.require_all) == "HEALTHY" else 1
```

- [ ] **Step 4: Run tests**

Run: `uv run --no-project pytest tests/test_diagnose.py -v`
Expected: All tests pass

- [ ] **Step 5: Manual smoke test (no hardware needed)**

Run: `uv run --no-project python scripts/hardware-test/diagnose.py --no-interactive`
Expected: Script runs end-to-end, each check reports SKIP (no hardware), exit code 1 or 0 depending on require-all. Should not crash.

Run: `uv run --no-project python scripts/hardware-test/diagnose.py --help`
Expected: Help text prints, explaining all three flags.

- [ ] **Step 6: Commit**

```bash
git add scripts/hardware-test/diagnose.py tests/test_diagnose.py
git commit -m "feat(diagnose): CLI flags for require-all, no-interactive, timeout-multiplier"
```

---

### Task 10: README update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add diagnostic section after the Limitations block**

Open `README.md`. After the `### Ball Markings` subsection (and before `## Project Structure`), add:

```markdown
## Hardware Diagnostic

To verify every component of your build in one shot:

```bash
uv run python scripts/hardware-test/diagnose.py
```

The diagnostic walks through 6 checks:
1. OPS243 connectivity
2. OPS243 rolling buffer mode persistence
3. OPS243 software trigger
4. K-LD7 vertical (launch angle)
5. K-LD7 horizontal (aim direction, optional)
6. Sound trigger end-to-end (interactive — prompts you to clap near the sensor)

Missing optional hardware (like the horizontal K-LD7) is reported as a skip rather than a failure. Pass `--require-all` to fail on skips, or `--no-interactive` to skip the sound-trigger prompt in unattended runs.
```

- [ ] **Step 2: Verify the markdown formatting renders correctly**

Run: `uv run --no-project python -m markdown README.md > /dev/null 2>&1 || true`

(Quick sanity check — the goal is just to confirm the fenced code block is balanced and no syntax errors.)

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add Hardware Diagnostic section to README"
```

---

### Task 11: Full suite verification

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `uv run --no-project pytest tests/ -v`
Expected: All tests pass. New tests in `tests/test_diagnose.py` show alongside the existing 264 tests.

- [ ] **Step 2: Run pylint on new file**

Run: `uv run --no-project pylint scripts/hardware-test/diagnose.py --fail-under=9`
Expected: Score >= 9.0.

- [ ] **Step 3: Run ruff on new files**

Run: `uv run --no-project ruff check scripts/hardware-test/diagnose.py tests/test_diagnose.py`
Expected: No errors.

- [ ] **Step 4: If any issues, fix and commit**

```bash
git add -u
git commit -m "style: fix lint/format issues in diagnose.py"
```
