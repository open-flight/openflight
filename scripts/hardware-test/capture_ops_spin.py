#!/usr/bin/env python3
"""Capture lossless OPS243 evidence for spin diagnosis.

The output is append-only JSONL. Each radar response is flushed to disk before
it is parsed, so malformed dumps and processing failures remain inspectable.
Valid captures use the existing ``rolling_buffer_capture`` schema and can be
fed directly to the replay and spin plotting tools under ``scripts/analysis``.

The OPS243 must already boot in persisted rolling-buffer mode. This script does
not write flash or switch modes at runtime.

Examples:
    uv run python scripts/hardware-test/capture_ops_spin.py --club driver -n 20
    uv run python scripts/hardware-test/capture_ops_spin.py \
        --port /dev/ttyAMA0 --ops-baud 115200 --club 7-iron -n 30
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import logging
import os
import platform
import subprocess
import sys
import time
import traceback
from dataclasses import asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# pylint: disable=wrong-import-position
from openflight.launch_monitor import ClubType  # noqa: E402
from openflight.ops243 import OPS243Radar, is_uart_port  # noqa: E402
from openflight.rolling_buffer.processor import RollingBufferProcessor  # noqa: E402
from openflight.rolling_buffer.types import (  # noqa: E402
    IQCapture,
    ProcessedCapture,
    SpeedTimeline,
    SpinResult,
)

SCHEMA_VERSION = 1
SAMPLE_RATE_KSPS = 30


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def json_safe(value: Any) -> Any:
    """Convert diagnostic values to deterministic JSON-compatible values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(item) for item in value]
    return repr(value)


class JsonlWriter:
    """Crash-resistant JSONL writer that flushes every diagnostic event."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._handle = None

    def __enter__(self) -> "JsonlWriter":
        """Create a new output file without overwriting prior evidence."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("x", encoding="utf-8")
        return self

    def write(self, record: dict[str, Any]) -> None:
        """Write and fsync one complete diagnostic record."""
        if self._handle is None:
            raise RuntimeError("JSONL writer is not open")
        payload = dict(record)
        payload.setdefault("schema_version", SCHEMA_VERSION)
        payload.setdefault("timestamp", utc_now())
        self._handle.write(json.dumps(json_safe(payload), ensure_ascii=False) + "\n")
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def __exit__(self, exc_type, exc_value, exc_tb) -> None:
        """Close the output file when the capture session ends."""
        if self._handle is not None:
            self._handle.close()
            self._handle = None


def _channel_summary(samples: list[int]) -> dict[str, Any]:
    values = np.asarray(samples, dtype=np.float64)
    if values.size == 0:
        return {"samples": 0}
    centered = values - np.mean(values)
    return {
        "samples": int(values.size),
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "std": float(np.std(values)),
        "rms_ac": float(np.sqrt(np.mean(centered**2))),
        "min": int(np.min(values)),
        "max": int(np.max(values)),
        "peak_to_peak": int(np.max(values) - np.min(values)),
        "p01": float(np.percentile(values, 1)),
        "p05": float(np.percentile(values, 5)),
        "p95": float(np.percentile(values, 95)),
        "p99": float(np.percentile(values, 99)),
        "unique_values": int(np.unique(values).size),
        "clipped_low": int(np.count_nonzero(values <= 0)),
        "clipped_high": int(np.count_nonzero(values >= 4095)),
        "near_low_rail": int(np.count_nonzero(values <= 4)),
        "near_high_rail": int(np.count_nonzero(values >= 4091)),
    }


def summarize_adc(capture: IQCapture) -> dict[str, Any]:
    """Summarize clipping, dynamic range, and I/Q relationship."""
    i_values = np.asarray(capture.i_samples, dtype=np.float64)
    q_values = np.asarray(capture.q_samples, dtype=np.float64)
    correlation = None
    magnitude_summary = None
    if i_values.size and i_values.size == q_values.size:
        i_centered = i_values - np.mean(i_values)
        q_centered = q_values - np.mean(q_values)
        if np.std(i_centered) > 0 and np.std(q_centered) > 0:
            correlation = float(np.corrcoef(i_centered, q_centered)[0, 1])
        magnitude = np.abs(i_centered + 1j * q_centered)
        magnitude_summary = {
            "mean": float(np.mean(magnitude)),
            "std": float(np.std(magnitude)),
            "max": float(np.max(magnitude)),
        }
    return {
        "i": _channel_summary(capture.i_samples),
        "q": _channel_summary(capture.q_samples),
        "lengths_match": len(capture.i_samples) == len(capture.q_samples),
        "iq_correlation": correlation,
        "centered_complex_magnitude": magnitude_summary,
    }


def _timeline_records(timeline: Optional[SpeedTimeline]) -> list[dict[str, Any]]:
    if timeline is None:
        return []
    return [asdict(reading) for reading in timeline.readings]


def _spin_record(spin: Optional[SpinResult]) -> Optional[dict[str, Any]]:
    return asdict(spin) if spin is not None else None


def _processing_record(processed: Optional[ProcessedCapture]) -> dict[str, Any]:
    if processed is None:
        return {
            "status": "no_shot",
            "ball_speed_mph": None,
            "ball_timestamp_ms": None,
            "club_speed_mph": None,
            "club_timestamp_ms": None,
            "smash_factor": None,
            "impact": None,
            "spin": None,
        }
    return {
        "status": "processed",
        "ball_speed_mph": processed.ball_speed_mph,
        "ball_timestamp_ms": processed.ball_timestamp_ms,
        "club_speed_mph": processed.club_speed_mph,
        "club_timestamp_ms": processed.club_timestamp_ms,
        "smash_factor": processed.smash_factor,
        "impact": asdict(processed.impact) if processed.impact is not None else None,
        "spin": _spin_record(processed.spin),
    }


def build_raw_capture_record(
    *,
    capture_number: int,
    response: str,
    wait_started_at: float,
    response_received_at: float,
    first_byte_timestamp: Optional[float],
) -> dict[str, Any]:
    """Build the lossless pre-parse record for one OPS response."""
    encoded = response.encode("utf-8")
    return {
        "type": "ops_spin_raw_capture",
        "capture_number": capture_number,
        "capture_id": f"ops-{capture_number:06d}",
        "wait_started_at": wait_started_at,
        "response_received_at": response_received_at,
        "wait_elapsed_ms": (response_received_at - wait_started_at) * 1000.0,
        "first_byte_timestamp": first_byte_timestamp,
        "response_characters": len(response),
        "response_bytes_utf8": len(encoded),
        "response_lines": len(response.splitlines()),
        "response_sha256": hashlib.sha256(encoded).hexdigest(),
        "markers": {
            "sample_time": '"sample_time"' in response,
            "trigger_time": '"trigger_time"' in response,
            "i": '"I"' in response,
            "q": '"Q"' in response,
            "q_closed": '"Q"' in response and "]}" in response[response.rfind('"Q"') :],
        },
        "raw_response": response,
    }


def build_capture_record(
    *,
    capture_number: int,
    capture: IQCapture,
    processed: Optional[ProcessedCapture],
    standard_timeline: SpeedTimeline,
    club: ClubType,
    clock_sync: Optional[dict[str, Any]],
    envelope_spin: Optional[SpinResult] = None,
) -> dict[str, Any]:
    """Build a replay-compatible structured record with all diagnostics."""
    processing = _processing_record(processed)
    spin = processed.spin if processed is not None else None
    impact = processed.impact if processed is not None else None
    record = {
        "type": "rolling_buffer_capture",
        "capture_number": capture_number,
        "capture_id": f"ops-{capture_number:06d}",
        "shot_number": capture_number,
        "club": club.value,
        "sample_time": capture.sample_time,
        "trigger_time": capture.trigger_time,
        "trigger_offset_ms": capture.trigger_offset_ms,
        "post_trigger_duration_ms": capture.post_trigger_duration_ms,
        "first_byte_timestamp": capture.first_byte_timestamp,
        "trigger_timestamp": capture.trigger_timestamp,
        "trigger_timestamp_source": capture.trigger_timestamp_source,
        "clock_sync_offset_s": capture.clock_sync_offset_s,
        "i_samples": list(capture.i_samples),
        "q_samples": list(capture.q_samples),
        "adc_summary": summarize_adc(capture),
        "clock_sync": clock_sync,
        "processing": processing,
        "standard_timeline": _timeline_records(standard_timeline),
        "overlapping_timeline": _timeline_records(
            processed.timeline if processed is not None else None
        ),
        "envelope_spin": _spin_record(envelope_spin),
        "ball_speed_mph": processing["ball_speed_mph"],
        "ball_timestamp_ms": processing["ball_timestamp_ms"],
        "club_speed_mph": processing["club_speed_mph"],
        "club_timestamp_ms": processing["club_timestamp_ms"],
        "smash_factor": processing["smash_factor"],
        "impact_timestamp_ms": impact.timestamp_ms if impact is not None else None,
        "impact_source": impact.source if impact is not None else None,
        "impact_reason": impact.reason if impact is not None else None,
        "spin_rpm": spin.spin_rpm if spin is not None else None,
        "spin_confidence": spin.confidence if spin is not None else None,
        "spin_method": spin.method if spin is not None else None,
        "spin_quality": spin.quality if spin is not None else None,
        "spin_multipath_fade_hz": spin.multipath_fade_hz if spin is not None else None,
        "spin_snr": spin.snr if spin is not None else None,
        "spin_modulation_depth": spin.modulation_depth if spin is not None else None,
        "spin_peak_freq_hz": spin.peak_freq_hz if spin is not None else None,
        "spin_seam_cycles": spin.seam_cycles if spin is not None else None,
        "spin_at_lower_rail": spin.at_lower_rail if spin is not None else None,
        "spin_at_upper_rail": spin.at_upper_rail if spin is not None else None,
        "spin_candidates": (
            [candidate.to_dict() for candidate in spin.candidates] if spin is not None else []
        ),
        "spin_phase_method": spin.phase_method if spin is not None else None,
        "spin_phase_rpm": spin.phase_rpm if spin is not None else None,
        "spin_phase_snr": spin.phase_snr if spin is not None else None,
        "spin_phase_agreement_pct": spin.phase_agreement_pct if spin is not None else None,
        "spin_phase_confirmed": spin.phase_confirmed if spin is not None else False,
        "spin_rejection_reason": spin.rejection_reason if spin is not None else None,
    }
    return record


def build_shot_record(capture_record: dict[str, Any]) -> dict[str, Any]:
    """Build the shot row expected by truth-pairing spin replay tools."""
    peak_hz = capture_record.get("spin_peak_freq_hz")
    return {
        "type": "shot_detected",
        "capture_id": capture_record["capture_id"],
        "data": {
            "shot_number": capture_record["shot_number"],
            "club": capture_record["club"],
            "ball_speed_mph": capture_record.get("ball_speed_mph"),
            "club_speed_mph": capture_record.get("club_speed_mph"),
            "smash_factor": capture_record.get("smash_factor"),
            "spin_rpm": capture_record.get("spin_rpm"),
            "spin_confidence": capture_record.get("spin_confidence"),
            "spin_method": capture_record.get("spin_method"),
            "spin_quality": capture_record.get("spin_quality"),
            "spin_snr": capture_record.get("spin_snr"),
            "spin_candidate_rpm": peak_hz * 60 if peak_hz is not None else None,
            "spin_rejection_reason": capture_record.get("spin_rejection_reason"),
            "mode": "ops-spin-diagnostic",
            "capture_status": capture_record["processing"]["status"],
        },
    }


def _error_record(capture_number: int, reason: str, exc: Optional[BaseException] = None) -> dict:
    record = {
        "type": "ops_spin_capture_error",
        "capture_number": capture_number,
        "capture_id": f"ops-{capture_number:06d}",
        "reason": reason,
    }
    if exc is not None:
        record.update(
            {
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": "".join(traceback.format_exception(exc)),
            }
        )
    return record


def _read_capture_clock_sync(radar: OPS243Radar) -> Optional[dict[str, Any]]:
    try:
        return radar.read_clock_sync(store=False)
    except Exception as exc:  # Hardware evidence must survive a diagnostic failure.
        return {"error_type": type(exc).__name__, "error": str(exc)}


def capture_once(
    *,
    radar: OPS243Radar,
    processor: RollingBufferProcessor,
    writer: JsonlWriter,
    capture_number: int,
    timeout: float,
    pre_trigger_segments: int,
    club: ClubType,
    sync_clock: bool = True,
) -> str:
    """Capture, persist, parse, and process one hardware-triggered dump."""
    wait_started_at = time.time()
    try:
        response = radar.wait_for_hardware_trigger(timeout=timeout)
    except Exception as exc:
        writer.write(_error_record(capture_number, "acquisition_failed", exc))
        return "acquisition_failed"
    response_received_at = time.time()

    if not response:
        writer.write(
            {
                "type": "ops_spin_timeout",
                "capture_number": capture_number,
                "timeout_s": timeout,
                "wait_started_at": wait_started_at,
                "wait_ended_at": response_received_at,
            }
        )
        print(f"  #{capture_number}: timeout after {timeout:.0f}s")
        return "timeout"

    first_byte_timestamp = radar.last_hardware_trigger_first_byte_timestamp
    writer.write(
        build_raw_capture_record(
            capture_number=capture_number,
            response=response,
            wait_started_at=wait_started_at,
            response_received_at=response_received_at,
            first_byte_timestamp=first_byte_timestamp,
        )
    )

    outcome = "processing_failed"
    try:
        capture = processor.parse_capture(
            response,
            first_byte_timestamp=first_byte_timestamp,
        )
        if capture is None:
            writer.write(_error_record(capture_number, "parse_failed"))
            print(f"  #{capture_number}: parse failed ({len(response)} characters; raw saved)")
            outcome = "parse_failed"
            return outcome

        clock_sync = _read_capture_clock_sync(radar) if sync_clock else None
        if clock_sync and clock_sync.get("usable_for_trigger_timestamps"):
            offset = clock_sync.get("best_offset_s")
            if offset is not None:
                capture.apply_trigger_timestamp_from_clock_sync(float(offset))

        standard_timeline = processor.process_standard(capture)
        processed = processor.process_capture(capture, club_type=club)
        envelope_spin = None
        if processed is not None:
            envelope_spin = processor.detect_spin(
                capture,
                processed.ball_speed_mph,
                processed.ball_timestamp_ms,
            )
        capture_record = build_capture_record(
            capture_number=capture_number,
            capture=capture,
            processed=processed,
            standard_timeline=standard_timeline,
            club=club,
            clock_sync=clock_sync,
            envelope_spin=envelope_spin,
        )
        writer.write(capture_record)
        writer.write(build_shot_record(capture_record))
        if processed is None:
            print(f"  #{capture_number}: no shot found (raw I/Q and timeline saved)")
            outcome = "no_shot"
        else:
            spin = processed.spin
            spin_text = "none"
            if spin is not None:
                spin_text = f"{spin.spin_rpm:.0f} rpm ({spin.method}, evidence={spin.snr:.2f})"
            print(
                f"  #{capture_number}: ball={processed.ball_speed_mph:.1f} mph "
                f"club={processed.club_speed_mph or 0:.1f} mph spin={spin_text}"
            )
            outcome = "processed"
        return outcome
    except Exception as exc:  # Preserve the next capture instead of losing the session.
        writer.write(_error_record(capture_number, "processing_failed", exc))
        print(f"  #{capture_number}: processing failed: {type(exc).__name__}: {exc}")
        return outcome
    finally:
        try:
            radar.rearm_rolling_buffer(pre_trigger_segments=pre_trigger_segments)
        except Exception as exc:
            writer.write(_error_record(capture_number, "rearm_failed", exc))
            raise


def _git_output(*args: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()


def collect_host_metadata() -> dict[str, Any]:
    """Collect host/software facts that can change signal processing results."""
    pi_model = None
    model_path = Path("/proc/device-tree/model")
    if model_path.is_file():
        pi_model = model_path.read_text(encoding="utf-8", errors="replace").rstrip("\x00\n")
    versions = {}
    for distribution in ("openflight", "numpy", "scipy", "pyserial"):
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = None
    status = _git_output("status", "--porcelain")
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "raspberry_pi_model": pi_model,
        "packages": versions,
        "git_commit": _git_output("rev-parse", "HEAD"),
        "git_dirty": bool(status) if status is not None else None,
    }


def collect_processor_config(processor: RollingBufferProcessor) -> dict[str, Any]:
    """Snapshot every uppercase processor constant for exact replay context."""
    return {
        name: json_safe(getattr(processor, name))
        for name in dir(processor)
        if name.isupper() and not name.startswith("_")
    }


def _safe_radar_call(name: str, call) -> dict[str, Any]:
    try:
        return {"name": name, "ok": True, "value": call()}
    except Exception as exc:
        return {
            "name": name,
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def collect_radar_metadata(radar: OPS243Radar) -> dict[str, Any]:
    """Collect identity, transport, and non-mutating state queries."""
    query = radar._send_command  # pylint: disable=protected-access
    calls = [
        _safe_radar_call("info", radar.get_info),
        _safe_radar_call("serial_number", radar.get_serial_number),
        _safe_radar_call("units", radar.get_current_units),
        _safe_radar_call("speed_filter", radar.get_speed_filter),
        _safe_radar_call("mode_query", lambda: query("G?")),
        _safe_radar_call("sample_query", lambda: query("S?")),
        _safe_radar_call("power_query", lambda: query("P?")),
    ]
    if is_uart_port(radar.port):
        calls.append(_safe_radar_call("uart_baud_query", radar.query_uart_baud))
    return {
        "port": radar.port,
        "transport": "uart" if is_uart_port(radar.port) else "usb",
        "baud": radar.baud,
        "bytes_per_second": radar.bytes_per_second,
        "dump_transfer_budget_s": radar.transfer_budget_s(floor=0),
        "queries": calls,
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the hardware capture command-line interface."""
    parser = argparse.ArgumentParser(
        description="Capture complete OPS243 evidence for offline spin diagnosis",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--port", help="OPS243 serial port; USB is auto-detected")
    parser.add_argument(
        "--ops-baud",
        type=int,
        choices=sorted(set(OPS243Radar.BAUD_PROBE_ORDER)),
        help="Target GPIO UART baud; ignored for USB",
    )
    parser.add_argument("-o", "--output", type=Path, help="New append-only JSONL output file")
    parser.add_argument("-n", "--max-captures", type=int, default=0, help="0 captures until Ctrl+C")
    parser.add_argument("--timeout", type=float, default=60.0, help="Seconds per trigger wait")
    parser.add_argument(
        "--pre-trigger",
        type=int,
        choices=range(0, 33),
        default=12,
        help="128-sample blocks retained before HOST_INT",
    )
    parser.add_argument(
        "--club",
        choices=[club.value for club in ClubType],
        default=ClubType.UNKNOWN.value,
        help="Club used for transition diagnostics",
    )
    parser.add_argument("--ball-label", help="Ball model or marking used in this session")
    parser.add_argument(
        "--environment",
        choices=("outdoor-range", "indoor-net", "other"),
        default="outdoor-range",
    )
    parser.add_argument("--radar-to-ball-ft", type=float)
    parser.add_argument("--ball-to-net-ft", type=float)
    parser.add_argument("--notes", help="Free-form setup notes stored in the session header")
    parser.add_argument(
        "--no-clock-sync",
        action="store_true",
        help="Skip the per-capture OPS-to-host clock mapping",
    )
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING"), default="INFO")
    return parser


def _default_output() -> Path:
    """Return a collision-resistant default path in the session directory."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path.home() / "openflight_sessions" / f"ops_spin_capture_{stamp}.jsonl"


def _session_config(args: argparse.Namespace) -> dict[str, Any]:
    """Return physical setup and capture settings for the session header."""
    return {
        "sample_rate_hz": SAMPLE_RATE_KSPS * 1000,
        "sample_rate_ksps": SAMPLE_RATE_KSPS,
        "pre_trigger_segments": args.pre_trigger,
        "pre_trigger_ms": args.pre_trigger * 128 / SAMPLE_RATE_KSPS,
        "post_trigger_ms": (32 - args.pre_trigger) * 128 / SAMPLE_RATE_KSPS,
        "timeout_s": args.timeout,
        "max_captures": args.max_captures,
        "club": args.club,
        "ball_label": args.ball_label,
        "environment": args.environment,
        "radar_to_ball_ft": args.radar_to_ball_ft,
        "ball_to_net_ft": args.ball_to_net_ft,
        "notes": args.notes,
        "clock_sync_each_capture": not args.no_clock_sync,
    }


def run_session(args: argparse.Namespace) -> int:
    """Connect to the OPS243 and record captures until the requested limit."""
    output = (args.output or _default_output()).expanduser().resolve()
    club = ClubType(args.club)
    processor = RollingBufferProcessor(sample_rate=SAMPLE_RATE_KSPS * 1000)
    radar_kwargs = {} if args.ops_baud is None else {"uart_baud": args.ops_baud}
    radar = OPS243Radar(port=args.port, **radar_kwargs)
    counts: dict[str, int] = {}
    capture_number = 1

    print("OPS spin diagnostic capture")
    print(f"  Output: {output}")
    print(f"  Club: {club.value}")
    print(
        f"  Buffer: S#{args.pre_trigger} "
        f"({args.pre_trigger * 128 / SAMPLE_RATE_KSPS:.1f} ms pre, "
        f"{(32 - args.pre_trigger) * 128 / SAMPLE_RATE_KSPS:.1f} ms post)"
    )
    print()

    with JsonlWriter(output) as writer:
        writer.write(
            {
                "type": "ops_spin_session_start",
                "config": _session_config(args),
                "host": collect_host_metadata(),
                "processor": collect_processor_config(processor),
            }
        )
        try:
            radar.connect()
            boot_radar = collect_radar_metadata(radar)
            radar.prepare_persisted_rolling_buffer(
                pre_trigger_segments=args.pre_trigger,
                sample_rate_ksps=SAMPLE_RATE_KSPS,
            )
            configured_radar = collect_radar_metadata(radar)
            initial_clock_sync = _read_capture_clock_sync(radar) if not args.no_clock_sync else None
            radar.rearm_rolling_buffer(pre_trigger_segments=args.pre_trigger)
            writer.write(
                {
                    "type": "ops_spin_radar_ready",
                    "boot_radar": boot_radar,
                    "configured_radar": configured_radar,
                    "initial_clock_sync": initial_clock_sync,
                }
            )
            print(f"Connected to {radar.port}; waiting for HOST_INT triggers.")
            print("Hit shots normally. Press Ctrl+C after the final shot.\n")

            while args.max_captures == 0 or capture_number <= args.max_captures:
                outcome = capture_once(
                    radar=radar,
                    processor=processor,
                    writer=writer,
                    capture_number=capture_number,
                    timeout=args.timeout,
                    pre_trigger_segments=args.pre_trigger,
                    club=club,
                    sync_clock=not args.no_clock_sync,
                )
                counts[outcome] = counts.get(outcome, 0) + 1
                if outcome != "timeout":
                    capture_number += 1
                if outcome == "acquisition_failed":
                    print("Stopping after radar acquisition failure.", file=sys.stderr)
                    break
        except KeyboardInterrupt:
            print("\nStopping capture cleanly...")
        except Exception as exc:
            writer.write(
                {
                    "type": "ops_spin_session_error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": "".join(traceback.format_exception(exc)),
                }
            )
            print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        finally:
            try:
                radar.disconnect()
            except Exception as exc:
                writer.write(
                    {
                        "type": "ops_spin_disconnect_error",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
            writer.write(
                {
                    "type": "ops_spin_session_end",
                    "captures_attempted": capture_number - 1,
                    "outcomes": counts,
                }
            )

    print(f"Saved diagnostic capture: {output}")
    print("Replay all captures:")
    print(f"  uv run --no-sync python scripts/analysis/replay_captures.py {output}")
    print("Plot one shot:")
    print(f"  uv run --no-sync python scripts/analysis/plot_spin_debug.py --log {output} --shot 1")
    return 0


def main() -> int:
    """Run an OPS spin diagnostic session from command-line arguments."""
    args = build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.max_captures < 0:
        raise SystemExit("--max-captures must be zero or positive")
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")
    return run_session(args)


if __name__ == "__main__":
    raise SystemExit(main())
