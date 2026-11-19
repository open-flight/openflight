"""Client-side filtering — the raw-ADC strip.

This is the load-bearing privacy boundary. The FlightWeb server stores a
device upload **verbatim**; it does not re-filter raw radar data out of a
device upload. So the product promise "raw radar data never leaves your Pi"
is enforced *here*, by applying an allowlist before upload.

Use an allowlist, not a blocklist — any future heavy entry type the session
logger gains must never leak by default.

The one exception is the explicit per-device ``upload_raw`` opt-in (radar
testing): then every entry uploads except ``RAW_EXCLUDED_TYPES``, and the
session's ``iwr6843_capture`` entries name the L3 dump files to upload
alongside it (see ``CaptureRef``).
"""

import gzip
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .. import __version__

CLIENT_VERSION = __version__

# Allowlisted entry types — only these are uploaded. ``error`` and
# ``session_error`` are both kept: the session logger currently emits ``error``
# (see session_logger.py), while the server spec names ``session_error``;
# keeping both is privacy-safe (error entries carry only error strings/context,
# never raw ADC) and future-proofs a rename.
KEEP_ENTRY_TYPES = frozenset(
    {
        "session_start",
        "session_end",
        "shot_detected",
        "trigger_event",
        "session_error",
        "error",
    }
)

# Raw mode still drops these: K-LD7 hardware is deprecated and its ~860 KB
# buffers would push a normal session past the server's body caps.
RAW_EXCLUDED_TYPES = frozenset({"kld7_buffer"})

# The session entry that references a shot's IWR6843 L3 dump on disk.
IWR6843_CAPTURE_TYPE = "iwr6843_capture"
IWR6843_CAPTURE_KIND = "iwr6843"

MANIFEST_TYPE = "upload_manifest"
MANIFEST_FORMAT_VERSION = 1

# Per-line cap mirroring the server's per-line guard. Belt-and-suspenders.
MAX_LINE_BYTES = 32 * 1024
# Raw mode keeps raw radar lines (an OPS243 rolling_buffer_capture is ~52 KB)
# and only guards against pathological ones. The server stores them in the
# blob verbatim; its 32 KB cap only limits which lines it parses into shots.
MAX_RAW_LINE_BYTES = 1024 * 1024
# Body caps mirroring the server. A filtered session is normally tens of KB,
# so these are safety checks, not normal operating limits.
MAX_GZIP_BYTES = 20 * 1024 * 1024
MAX_INFLATED_BYTES = 64 * 1024 * 1024

# Fixed namespace for deterministic UUIDv5 of (device_id, session_filename),
# used for older sessions that predate the embedded session_uuid. A stable
# namespace makes the same file always map to the same id (dedupe + safe retry).
SESSION_NAMESPACE = uuid.UUID("8d8ac610-566d-4ef0-9c22-186b2a5ed793")


class BodyTooLargeError(Exception):
    """Raised when a filtered body exceeds the gzip/inflated caps."""


@dataclass
class CaptureRef:
    """A shot's raw capture file, matched to the shot by ``shot_number``."""

    shot_number: int
    path: str
    kind: str = IWR6843_CAPTURE_KIND

    def to_dict(self) -> Dict[str, Any]:
        """JSON-safe form, as queued in the session's ``.pushed`` marker."""
        return {"shot_number": self.shot_number, "path": self.path, "kind": self.kind}


@dataclass
class FilterResult:
    """Outcome of filtering one session file."""

    manifest: Dict[str, Any]
    kept_lines: List[str]
    dropped_oversize: int = 0
    kept_type_counts: Dict[str, int] = field(default_factory=dict)
    session_id: str = ""
    # Raw mode only: dump files on disk to upload after the session.
    captures: List[CaptureRef] = field(default_factory=list)


def _iter_entries(lines: Iterable[str]):
    """Yield (raw_line, parsed_dict) for parseable JSON lines, skipping junk."""
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            yield stripped, parsed


def _capture_ref(entry: Dict[str, Any], session_dir: Optional[Path]) -> Optional[CaptureRef]:
    """The dump file an ``iwr6843_capture`` entry points at, if it was saved."""
    capture_path = entry.get("capture_path")
    shot_number = entry.get("shot_number")
    if not capture_path or isinstance(shot_number, bool) or not isinstance(shot_number, int):
        return None
    if shot_number < 0:
        return None
    path = Path(str(capture_path)).expanduser()
    if not path.is_absolute() and session_dir is not None:
        path = session_dir / path
    return CaptureRef(shot_number=shot_number, path=str(path))


def _filter_entries(
    lines: Iterable[str],
    device_id: str,
    filename: str,
    client_version: str,
    raw_mode: bool = False,
    session_dir: Optional[Path] = None,
) -> FilterResult:
    """Single-pass core: consume ``lines`` once, applying the allowlist.

    Works on any line iterable — including an open file object, which streams
    line by line so a huge raw-ADC session is never held in memory at once.
    Captures the ``session_uuid`` from ``session_start`` during the same pass
    so we don't need a second read to resolve the upload id.

    In ``raw_mode`` every type except ``RAW_EXCLUDED_TYPES`` is kept, and the
    ``iwr6843_capture`` entries are collected as the shot's dump files.
    """
    kept_lines: List[str] = []
    kept_type_counts: Dict[str, int] = {}
    dropped_oversize = 0
    embedded_uuid = ""
    captures: Dict[int, CaptureRef] = {}
    max_line_bytes = MAX_RAW_LINE_BYTES if raw_mode else MAX_LINE_BYTES

    for raw, entry in _iter_entries(lines):
        entry_type = entry.get("type")
        if entry_type == "session_start" and not embedded_uuid:
            session_uuid = entry.get("session_uuid")
            if session_uuid:
                embedded_uuid = str(session_uuid).lower()
        if raw_mode:
            if not isinstance(entry_type, str) or entry_type in RAW_EXCLUDED_TYPES:
                continue
            if entry_type == IWR6843_CAPTURE_TYPE:
                ref = _capture_ref(entry, session_dir)
                if ref is not None:
                    captures[ref.shot_number] = ref  # last capture for a shot wins
        elif entry_type not in KEEP_ENTRY_TYPES:
            continue
        if len(raw.encode("utf-8")) > max_line_bytes:
            dropped_oversize += 1
            continue
        kept_lines.append(raw)
        kept_type_counts[entry_type] = kept_type_counts.get(entry_type, 0) + 1

    session_id = embedded_uuid or str(uuid.uuid5(SESSION_NAMESPACE, f"{device_id}:{filename}"))
    manifest = {
        "type": MANIFEST_TYPE,
        "format_version": MANIFEST_FORMAT_VERSION,
        "client_version": client_version,
        "device_id": device_id,
        "filtered": not raw_mode,
        "raw": raw_mode,
        "kept_entry_types": sorted(kept_type_counts),
    }
    return FilterResult(
        manifest=manifest,
        kept_lines=kept_lines,
        dropped_oversize=dropped_oversize,
        kept_type_counts=kept_type_counts,
        session_id=session_id,
        captures=sorted(captures.values(), key=lambda c: c.shot_number),
    )


def filter_session_lines(
    lines: Iterable[str],
    device_id: str,
    client_version: str = CLIENT_VERSION,
    filename: str = "",
    raw_mode: bool = False,
) -> FilterResult:
    """Filter raw session lines to the allowlist and build the manifest.

    Drops non-allowlisted types, drops any kept line over ``MAX_LINE_BYTES``
    (counting them), and skips blank/unparseable lines. ``filename`` is only
    used for the UUIDv5 session-id fallback when no ``session_uuid`` is present.
    ``raw_mode`` keeps raw radar entries (see module docstring).
    """
    return _filter_entries(lines, device_id, filename, client_version, raw_mode=raw_mode)


def filter_session_file(
    path,
    device_id: str,
    client_version: str = CLIENT_VERSION,
    raw_mode: bool = False,
) -> FilterResult:
    """Stream-filter a session file by path without loading it into memory.

    The raw ADC (rolling_buffer_capture / kld7_buffer / iq_blocks) is dropped as
    each line is read, so peak memory stays near a single line regardless of how
    large the raw-ADC file is. This is the production path for ``push``. In
    ``raw_mode`` the raw entries are kept (minus ``RAW_EXCLUDED_TYPES``).
    """
    path = Path(path)
    with path.open(encoding="utf-8", errors="replace") as handle:
        return _filter_entries(
            handle,
            device_id,
            path.name,
            client_version,
            raw_mode=raw_mode,
            session_dir=path.parent,
        )


def build_upload_body(
    result: FilterResult,
    max_gzip_bytes: Optional[int] = None,
    max_inflated_bytes: Optional[int] = None,
) -> bytes:
    """Build the gzipped NDJSON upload body (manifest first), enforcing caps.

    Raises BodyTooLargeError if the body would exceed either cap — the caller
    should park the session and report it rather than upload raw.
    """
    # Resolve at call time so tests (and config) can adjust the module caps.
    max_gzip_bytes = MAX_GZIP_BYTES if max_gzip_bytes is None else max_gzip_bytes
    max_inflated_bytes = MAX_INFLATED_BYTES if max_inflated_bytes is None else max_inflated_bytes
    out_lines = [json.dumps(result.manifest)]
    out_lines.extend(result.kept_lines)
    ndjson = ("\n".join(out_lines) + "\n").encode("utf-8")

    if len(ndjson) > max_inflated_bytes:
        raise BodyTooLargeError(
            f"inflated body {len(ndjson)} bytes exceeds cap {max_inflated_bytes}"
        )

    # mtime=0 keeps the gzip output deterministic (stable retries/dedupe).
    body = gzip.compress(ndjson, mtime=0)
    if len(body) > max_gzip_bytes:
        raise BodyTooLargeError(f"gzip body {len(body)} bytes exceeds cap {max_gzip_bytes}")
    return body
