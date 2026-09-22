# K-LD7 Timing and PRF Probe Implementation Plan

!!! warning "ARCHIVED DOCUMENT"

    This is a historical design or implementation note, kept as a record of why
    the code is shaped the way it is. It describes the project as of the date in
    its filename and **is not a guide to follow** — commands, paths, and
    constants may no longer match the code. See the
    [Archive index](../index.md) for current alternatives.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a guarded K-LD7 timing/protocol probe that measures real RADC frame cadence and optionally probes explicitly listed undocumented commands.

**Architecture:** Add a standalone hardware-test script with a small serial protocol layer, pure summarization helpers, and CLI safety gates. Tests exercise packet construction, split-read handling, frame-gap summaries, and unsafe-mode validation without requiring hardware.

**Tech Stack:** Python 3.11+, `uv run`, `pytest`, `pyserial`, standard-library `argparse`, `dataclasses`, `json`, `struct`, and `statistics`.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `scripts/hardware-test/probe_kld7_timing.py` | Standalone K-LD7 protocol probe, CLI, measurement loop, unsafe command probing, JSONL/summary output |
| `tests/test_probe_kld7_timing.py` | Unit tests for protocol helpers, fake serial reads, summarization, CLI safety validation |
| `docs/kld7-troubleshooting.md` | Short operator-facing section explaining when and how to run the timing probe |

No production tracker code changes are planned.

## Task 1: Protocol Primitives

**Files:**
- Create: `scripts/hardware-test/probe_kld7_timing.py`
- Test: `tests/test_probe_kld7_timing.py`

- [ ] **Step 1: Write failing tests for packet construction and command validation**

Add tests:

```python
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hardware-test" / "probe_kld7_timing.py"
spec = importlib.util.spec_from_file_location("probe_kld7_timing", SCRIPT)
probe = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(probe)


def test_build_packet_uppercases_command_and_packs_length():
    packet = probe.build_packet("gnfd", (0x21).to_bytes(4, "little"))

    assert packet == b"GNFD\x04\x00\x00\x00!\x00\x00\x00"


def test_validate_command_rejects_non_four_byte_command():
    assert probe.validate_probe_command("ABC", "") == "command must be exactly 4 ASCII characters"


def test_validate_command_rejects_non_uppercase_command():
    assert probe.validate_probe_command("test", "") == "command must be uppercase ASCII"


def test_validate_command_rejects_odd_hex_payload():
    assert probe.validate_probe_command("TST1", "abc") == "hex payload must have an even number of characters"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_probe_kld7_timing.py -v
```

Expected: FAIL because `scripts/hardware-test/probe_kld7_timing.py` does not exist.

- [ ] **Step 3: Implement packet helpers**

Create `scripts/hardware-test/probe_kld7_timing.py` with:

```python
#!/usr/bin/env python3
"""Guarded K-LD7 timing and protocol probe."""

from __future__ import annotations

import argparse
import json
import statistics
import struct
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import BinaryIO, Iterable, Optional

try:
    import serial
    from serial.tools.list_ports import comports
except ImportError:  # pragma: no cover - exercised by operator environment
    serial = None
    comports = None


DEFAULT_BAUD = 3_000_000
DEFAULT_START_BAUD = 115_200
SUPPORTED_BAUD_RATES = [115_200, 460_800, 921_600, 2_000_000, 3_000_000]
FRAME_CODES = {
    "RADC": 0x01,
    "RFFT": 0x02,
    "PDAT": 0x04,
    "TDAT": 0x08,
    "DDAT": 0x10,
    "DONE": 0x20,
}
DOCUMENTED_WRITE_COMMANDS = {
    "RBFR", "RSPI", "RRAI", "THOF", "TRFT", "VISU", "MIRA", "MARA",
    "MIAN", "MAAN", "MISP", "MASP", "DEDI", "RATH", "ANTH", "SPTH",
    "DIG1", "DIG2", "DIG3", "HOLD", "MIDE", "MIDS",
}
DESTRUCTIVE_COMMANDS = {"RFSE"}


def build_packet(command: str, payload: bytes = b"") -> bytes:
    cmd = command.upper().encode("ascii")
    if len(cmd) != 4:
        raise ValueError("command must be exactly 4 ASCII characters")
    return struct.pack("<4sI", cmd, len(payload)) + payload


def validate_probe_command(command: str, hex_payload: str) -> Optional[str]:
    try:
        raw = command.encode("ascii")
    except UnicodeEncodeError:
        return "command must be ASCII"
    if len(raw) != 4:
        return "command must be exactly 4 ASCII characters"
    if command != command.upper():
        return "command must be uppercase ASCII"
    if len(hex_payload) % 2:
        return "hex payload must have an even number of characters"
    try:
        bytes.fromhex(hex_payload)
    except ValueError:
        return "hex payload must be valid hexadecimal"
    return None
```

- [ ] **Step 4: Run tests to verify Task 1 passes**

Run:

```bash
uv run pytest tests/test_probe_kld7_timing.py -v
```

Expected: PASS for the four packet-helper tests.

## Task 2: Packet Reading and Summary Logic

**Files:**
- Modify: `scripts/hardware-test/probe_kld7_timing.py`
- Modify: `tests/test_probe_kld7_timing.py`

- [ ] **Step 1: Write failing tests for split reads and `DONE` gap summaries**

Append tests:

```python
class FakeSerial:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.writes = []
        self.timeout = 0.2
        self.baudrate = 115200

    def read(self, n):
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if len(chunk) > n:
            self.chunks.insert(0, chunk[n:])
            return chunk[:n]
        return chunk

    def write(self, data):
        self.writes.append(data)
        return len(data)

    def flush(self):
        return None

    def reset_input_buffer(self):
        return None

    def close(self):
        return None


def test_read_packet_handles_split_header_and_payload():
    payload = b"\x00\x01\x02\x03"
    header = b"DONE" + len(payload).to_bytes(4, "little")
    fake = FakeSerial([header[:3], header[3:8], payload[:1], payload[1:]])
    protocol = probe.KLD7Protocol.__new__(probe.KLD7Protocol)
    protocol.port = fake

    packet = protocol.read_packet()

    assert packet.code == "DONE"
    assert packet.payload == payload
    assert packet.payload_bytes == 4


def test_summarize_measurements_counts_done_gaps():
    packets = [
        probe.PacketRecord(code="DONE", payload_bytes=4, complete_monotonic=1.0, done_frame=10),
        probe.PacketRecord(code="RADC", payload_bytes=3072, complete_monotonic=1.1, read_duration_ms=10.0),
        probe.PacketRecord(code="DONE", payload_bytes=4, complete_monotonic=2.0, done_frame=12),
        probe.PacketRecord(code="RADC", payload_bytes=3072, complete_monotonic=2.1, read_duration_ms=20.0),
    ]

    summary = probe.summarize_packets(packets, duration_s=2.0)

    assert summary["radc_frames"] == 2
    assert summary["done_frames"] == 2
    assert summary["done_frame_gaps"] == 1
    assert summary["effective_radc_hz"] == 1.0
    assert summary["read_duration_ms_p95"] == 20.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_probe_kld7_timing.py -v
```

Expected: FAIL because `KLD7Protocol`, `PacketRecord`, and `summarize_packets` are not implemented.

- [ ] **Step 3: Implement protocol records, exact reads, and summaries**

Add to the script:

```python
@dataclass
class PacketRecord:
    code: str
    payload_bytes: int
    command: Optional[str] = None
    response_code: Optional[int] = None
    send_monotonic: Optional[float] = None
    first_byte_monotonic: Optional[float] = None
    header_complete_monotonic: Optional[float] = None
    complete_monotonic: Optional[float] = None
    read_duration_ms: Optional[float] = None
    done_frame: Optional[int] = None
    error: Optional[str] = None
    payload: bytes = field(default=b"", repr=False)


class KLD7Protocol:
    def __init__(self, port_path: str, baud: int = DEFAULT_BAUD, timeout: float = 0.2):
        if serial is None:
            raise RuntimeError("pyserial is required for hardware probing")
        self.port_path = port_path
        self.baud = baud
        self.port = serial.Serial(
            port=port_path,
            baudrate=DEFAULT_START_BAUD,
            parity=serial.PARITY_EVEN,
            stopbits=1,
            timeout=timeout,
        )

    def _read_exact(self, n: int) -> tuple[bytes, Optional[float]]:
        buf = b""
        first_byte_at = None
        deadline = time.monotonic() + max(float(getattr(self.port, "timeout", 0.2) or 0.2), 0.2)
        while len(buf) < n:
            chunk = self.port.read(n - len(buf))
            if chunk:
                if first_byte_at is None:
                    first_byte_at = time.monotonic()
                buf += chunk
                continue
            if time.monotonic() >= deadline:
                break
            time.sleep(0.001)
        return buf, first_byte_at

    def read_packet(self) -> PacketRecord:
        started = time.monotonic()
        header, first_byte_at = self._read_exact(8)
        header_complete = time.monotonic()
        if len(header) != 8:
            return PacketRecord(
                code="",
                payload_bytes=0,
                first_byte_monotonic=first_byte_at,
                header_complete_monotonic=header_complete,
                complete_monotonic=header_complete,
                error=f"short header read: got {len(header)} of 8 bytes",
            )
        raw_code, length = struct.unpack("<4sI", header)
        code = raw_code.decode("ascii", errors="replace")
        payload = b""
        payload_first = None
        if length:
            payload, payload_first = self._read_exact(length)
        complete = time.monotonic()
        error = None
        if len(payload) != length:
            error = f"short payload read: got {len(payload)} of {length} bytes"
        done_frame = None
        if code == "DONE" and len(payload) == 4:
            done_frame = int.from_bytes(payload, "little", signed=False)
        return PacketRecord(
            code=code,
            payload_bytes=length,
            first_byte_monotonic=first_byte_at or payload_first,
            header_complete_monotonic=header_complete,
            complete_monotonic=complete,
            read_duration_ms=(complete - (first_byte_at or started)) * 1000.0,
            done_frame=done_frame,
            error=error,
            payload=payload,
        )


def _percentile(values: list[float], percentile: float) -> Optional[float]:
    if not values:
        return None
    values = sorted(values)
    index = min(len(values) - 1, max(0, round((percentile / 100.0) * (len(values) - 1))))
    return values[index]


def summarize_packets(packets: list[PacketRecord], duration_s: float) -> dict:
    radc_packets = [p for p in packets if p.code == "RADC" and not p.error]
    done_packets = [p for p in packets if p.code == "DONE" and not p.error]
    done_frames = [p.done_frame for p in done_packets if p.done_frame is not None]
    gaps = 0
    for previous, current in zip(done_frames, done_frames[1:]):
        if current > previous + 1:
            gaps += current - previous - 1
    read_durations = [p.read_duration_ms for p in radc_packets if p.read_duration_ms is not None]
    errors: dict[str, int] = {}
    for packet in packets:
        if packet.error:
            errors[packet.error] = errors.get(packet.error, 0) + 1
    return {
        "duration_s": duration_s,
        "radc_frames": len(radc_packets),
        "done_frames": len(done_packets),
        "effective_radc_hz": round(len(radc_packets) / duration_s, 3) if duration_s > 0 else 0.0,
        "effective_done_hz": round(len(done_packets) / duration_s, 3) if duration_s > 0 else 0.0,
        "done_frame_gaps": gaps,
        "read_duration_ms_mean": statistics.mean(read_durations) if read_durations else None,
        "read_duration_ms_p50": statistics.median(read_durations) if read_durations else None,
        "read_duration_ms_p95": _percentile(read_durations, 95),
        "errors": errors,
    }
```

- [ ] **Step 4: Run tests to verify Task 2 passes**

Run:

```bash
uv run pytest tests/test_probe_kld7_timing.py -v
```

Expected: PASS for packet construction, split reads, and summary tests.

## Task 3: CLI Safety Gates and Measurement Loop

**Files:**
- Modify: `scripts/hardware-test/probe_kld7_timing.py`
- Modify: `tests/test_probe_kld7_timing.py`

- [ ] **Step 1: Write failing tests for CLI validation**

Append tests:

```python
def test_unsafe_probe_requires_output():
    parser = probe.build_parser()
    args = parser.parse_args(["--port", "/dev/null", "--unsafe-probe", "--probe-command", "TEST"])

    assert probe.validate_args(args) == ["--unsafe-probe requires --output so probe activity is auditable"]


def test_rfse_requires_factory_reset_flag():
    parser = probe.build_parser()
    args = parser.parse_args([
        "--port", "/dev/null",
        "--output", "/tmp/probe.jsonl",
        "--unsafe-probe",
        "--probe-command", "RFSE",
    ])

    assert probe.validate_args(args) == ["RFSE is refused unless --allow-factory-reset is set"]


def test_parse_frame_mask_combines_known_flags():
    assert probe.parse_frame_mask("RADC,DONE") == 0x21
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_probe_kld7_timing.py -v
```

Expected: FAIL because parser, arg validation, and frame-mask parsing are missing.

- [ ] **Step 3: Implement parser, validation, and measurement flow**

Add:

```python
def parse_frame_mask(value: str) -> int:
    mask = 0
    for raw_name in value.split(","):
        name = raw_name.strip().upper()
        if not name:
            continue
        if name not in FRAME_CODES:
            raise argparse.ArgumentTypeError(f"unknown frame type {name!r}")
        mask |= FRAME_CODES[name]
    return mask


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe K-LD7 RADC timing and guarded commands.")
    parser.add_argument("--port")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, choices=SUPPORTED_BAUD_RATES)
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--frame-mask", default="RADC,DONE")
    parser.add_argument("--rspi-sweep", action="store_true")
    parser.add_argument("--rrai", type=int)
    parser.add_argument("--rbfr", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--unsafe-probe", action="store_true")
    parser.add_argument("--probe-command", action="append", default=[])
    parser.add_argument("--allow-factory-reset", action="store_true")
    parser.add_argument("--no-restore-params", action="store_true")
    return parser


def validate_args(args: argparse.Namespace) -> list[str]:
    errors = []
    if args.unsafe_probe and not args.output:
        errors.append("--unsafe-probe requires --output so probe activity is auditable")
    if args.probe_command and not args.unsafe_probe:
        errors.append("--probe-command requires --unsafe-probe")
    for command_spec in args.probe_command:
        command, _, hex_payload = command_spec.partition(":")
        error = validate_probe_command(command, hex_payload)
        if error:
            errors.append(f"{command_spec}: {error}")
        if command.upper() in DESTRUCTIVE_COMMANDS and not args.allow_factory_reset:
            errors.append("RFSE is refused unless --allow-factory-reset is set")
    return errors
```

Extend `KLD7Protocol`:

```python
    def send_command(self, command: str, payload: bytes = b"") -> PacketRecord:
        sent = time.monotonic()
        self.port.reset_input_buffer()
        self.port.write(build_packet(command, payload))
        self.port.flush()
        packet = self.read_packet()
        packet.command = command.upper()
        packet.send_monotonic = sent
        if packet.code == "RESP" and packet.payload:
            packet.response_code = packet.payload[0]
        return packet

    def request_frame(self, frame_mask: int) -> list[PacketRecord]:
        records = [self.send_command("GNFD", int(frame_mask).to_bytes(4, "little", signed=True))]
        expected = bin(frame_mask).count("1")
        for _ in range(expected):
            record = self.read_packet()
            records.append(record)
            if record.code == "DONE":
                break
        return records

    def close(self) -> None:
        try:
            self.port.write(build_packet("GBYE"))
            self.port.flush()
        finally:
            self.port.close()
```

Add `measure()` and `main()`:

```python
def measure(protocol: KLD7Protocol, frame_mask: int, duration_s: float) -> list[PacketRecord]:
    records = []
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        records.extend(protocol.request_frame(frame_mask))
    return records


def write_jsonl(path: Path, records: Iterable[PacketRecord], summary: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            data = asdict(record)
            data.pop("payload", None)
            handle.write(json.dumps({"type": "packet", **data}, sort_keys=True) + "\n")
        handle.write(json.dumps({"type": "summary", **summary}, sort_keys=True) + "\n")


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    errors = validate_args(args)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 2
    frame_mask = parse_frame_mask(args.frame_mask)
    if not args.port:
        print("error: --port is required for the first implementation", file=sys.stderr)
        return 2
    protocol = KLD7Protocol(args.port, baud=args.baud)
    records = []
    try:
        records = measure(protocol, frame_mask, args.duration)
    finally:
        protocol.close()
    summary = summarize_packets(records, args.duration)
    print(json.dumps(summary, indent=2, sort_keys=True))
    if args.output:
        write_jsonl(args.output, records, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/test_probe_kld7_timing.py -v
```

Expected: PASS.

## Task 4: Docs and Verification

**Files:**
- Modify: `docs/kld7-troubleshooting.md`
- Test: `tests/test_probe_kld7_timing.py`

- [ ] **Step 1: Add troubleshooting docs**

Add a section after "RADC Streaming Issues":

```markdown
### Measuring Real K-LD7 RADC Cadence

Use the guarded timing probe when launch-angle extraction is missing frames or when one K-LD7 orientation appears slower than the other:

```bash
uv run python scripts/hardware-test/probe_kld7_timing.py \
  --port /dev/kld7_vertical \
  --duration 10 \
  --frame-mask RADC,DONE \
  --output /tmp/kld7_vertical_timing.jsonl
```

At the production `RSPI=3` setting, expect roughly 34 RADC frames per second with low `done_frame_gaps`. If cadence is much lower or gaps are high, investigate USB scheduling, serial read duration, or requested packet volume before changing launch-angle selection logic.

Undocumented command probing is available only through `--unsafe-probe` and requires `--output`. Do not use it in production sessions.
```

- [ ] **Step 2: Run unit tests**

Run:

```bash
uv run pytest tests/test_probe_kld7_timing.py -v
```

Expected: PASS.

- [ ] **Step 3: Run lint/format checks for touched Python files**

Run:

```bash
uv run ruff check scripts/hardware-test/probe_kld7_timing.py tests/test_probe_kld7_timing.py
uv run ruff format --check scripts/hardware-test/probe_kld7_timing.py tests/test_probe_kld7_timing.py
```

Expected: PASS.

- [ ] **Step 4: Run a no-hardware CLI validation check**

Run:

```bash
uv run python scripts/hardware-test/probe_kld7_timing.py --unsafe-probe --probe-command TEST
```

Expected: exit code `2` and stderr containing `--unsafe-probe requires --output`.

