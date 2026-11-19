"""Spool-and-retry mechanics — the session directory *is* the queue.

State lives in sidecar files next to each ``session_*.jsonl`` so it survives
crashes with no database:

- ``<session>.jsonl.pushed`` — present once accepted by the server (terminal).
- ``<session>.jsonl.parked`` — present once given up on (terminal).
- ``<session>.jsonl.state`` — JSON attempt counter + last error for in-flight
  retries; removed on success.

With raw uploads on, the ``.pushed`` marker also carries the session's raw
capture queue (``captures_pending`` / ``captures_parked``): each shot's L3 dump
uploads after the session itself, so a pushed session may still have dumps
waiting. Keeping that queue in the marker means the (large) session file is
never re-read just to find its dumps.

Originals are never moved or modified.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PUSHED_SUFFIX = ".pushed"
PARKED_SUFFIX = ".parked"
STATE_SUFFIX = ".state"

SESSION_GLOB = "session_*.jsonl"

# After this many failures, park the file and report via ``status`` instead of
# retrying forever.
MAX_ATTEMPTS = 20

# How long to defer a session after a quota (402) rejection — retry daily
# rather than every timer tick.
QUOTA_COOLDOWN_S = 24 * 60 * 60


def _sidecar(path: Path, suffix: str) -> Path:
    # Append (not replace) so "session_x.jsonl" -> "session_x.jsonl.pushed".
    return path.with_name(path.name + suffix)


def _now() -> str:
    return datetime.now().isoformat()


def session_files(log_dir: Path) -> List[Path]:
    """All session JSONL files in ``log_dir`` (sorted); empty if dir missing."""
    log_dir = Path(log_dir)
    if not log_dir.is_dir():
        return []
    return sorted(log_dir.glob(SESSION_GLOB))


def is_pushed(path: Path) -> bool:
    """True if a ``.pushed`` marker exists for this session."""
    return _sidecar(path, PUSHED_SUFFIX).exists()


def is_parked(path: Path) -> bool:
    """True if a ``.parked`` marker exists for this session."""
    return _sidecar(path, PARKED_SUFFIX).exists()


def pending_sessions(log_dir: Path) -> List[Path]:
    """Session files that are neither pushed nor parked."""
    return [p for p in session_files(log_dir) if not is_pushed(p) and not is_parked(p)]


def read_attempts(path: Path) -> int:
    """Number of recorded failed attempts for this session (0 if none)."""
    state_path = _sidecar(path, STATE_SUFFIX)
    if not state_path.exists():
        return 0
    try:
        return int(json.loads(state_path.read_text()).get("attempts", 0))
    except (json.JSONDecodeError, ValueError):
        return 0


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


def record_failure(path: Path, error: str) -> int:
    """Increment the attempt counter, store the error, and park if maxed out.

    Returns the new attempt count.
    """
    attempts = read_attempts(path) + 1
    _write_json(
        _sidecar(path, STATE_SUFFIX),
        {"attempts": attempts, "last_error": error, "last_attempt_at": _now()},
    )
    if attempts >= MAX_ATTEMPTS:
        mark_parked(path, reason="max_attempts", attempts=attempts, last_error=error)
    return attempts


def record_cooldown(path: Path, reason: str, seconds: float, now: Optional[float] = None) -> None:
    """Defer retries for this session until ``seconds`` from now (e.g. quota).

    Does not increment the failure counter or park — quota is not a client bug.
    """
    now = time.time() if now is None else now
    state_path = _sidecar(path, STATE_SUFFIX)
    state: Dict[str, Any] = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
        except (json.JSONDecodeError, ValueError):
            state = {}
    state.update(
        {"cooldown_until": now + seconds, "cooldown_reason": reason, "last_attempt_at": _now()}
    )
    _write_json(state_path, state)


def in_cooldown(path: Path, now: Optional[float] = None) -> bool:
    """True if this session is deferred (cooldown not yet elapsed)."""
    now = time.time() if now is None else now
    state_path = _sidecar(path, STATE_SUFFIX)
    if not state_path.exists():
        return False
    try:
        until = json.loads(state_path.read_text()).get("cooldown_until")
    except (json.JSONDecodeError, ValueError):
        return False
    return until is not None and now < until


def mark_pushed(
    path: Path,
    session_id: str,
    shot_count: Optional[int],
    captures: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Mark a session as successfully uploaded and clear retry state.

    ``captures`` (raw mode) queues the session's capture files for upload.
    """
    marker: Dict[str, Any] = {
        "session_id": session_id,
        "shot_count": shot_count,
        "pushed_at": _now(),
    }
    if captures:
        marker["captures_pending"] = [dict(c, attempts=0) for c in captures]
        marker["captures_parked"] = []
    _write_json(_sidecar(path, PUSHED_SUFFIX), marker)
    state_path = _sidecar(path, STATE_SUFFIX)
    if state_path.exists():
        state_path.unlink()


def mark_parked(path: Path, reason: str, attempts: int, last_error: Optional[str]) -> None:
    """Mark a session as parked (given up on); reported via ``status``."""
    _write_json(
        _sidecar(path, PARKED_SUFFIX),
        {
            "reason": reason,
            "attempts": attempts,
            "last_error": last_error,
            "parked_at": _now(),
        },
    )


def clear_markers(path: Path, include_pushed: bool = False) -> List[str]:
    """Remove terminal/retry markers so a session becomes pending again.

    Clears ``.parked`` and ``.state`` (un-park + reset attempts/cooldown). With
    ``include_pushed`` also clears ``.pushed`` to force re-upload of a session
    the server already stored (idempotent server-side). Returns the suffixes
    actually removed.
    """
    suffixes = [PARKED_SUFFIX, STATE_SUFFIX]
    if include_pushed:
        suffixes.append(PUSHED_SUFFIX)
    cleared: List[str] = []
    for suffix in suffixes:
        marker = _sidecar(path, suffix)
        if marker.exists():
            marker.unlink()
            cleared.append(suffix)
    return cleared


def read_pushed(path: Path) -> Dict[str, Any]:
    """The ``.pushed`` marker contents ({} if absent or unreadable)."""
    try:
        data = json.loads(_sidecar(path, PUSHED_SUFFIX).read_text())
    except (json.JSONDecodeError, ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def pending_captures(path: Path) -> List[Dict[str, Any]]:
    """Capture files still waiting to upload for a pushed session."""
    return list(read_pushed(path).get("captures_pending") or [])


def save_capture_queue(
    path: Path, pending: List[Dict[str, Any]], newly_parked: List[Dict[str, Any]]
) -> None:
    """Rewrite a pushed session's capture queue after an upload pass."""
    marker = read_pushed(path)
    if not marker:
        return
    marker["captures_pending"] = pending
    marker["captures_parked"] = list(marker.get("captures_parked") or []) + newly_parked
    _write_json(_sidecar(path, PUSHED_SUFFIX), marker)


def summarize(log_dir: Path) -> Dict[str, int]:
    """Count pushed / parked / pending sessions for ``status``."""
    files = session_files(log_dir)
    pushed = sum(1 for p in files if is_pushed(p))
    parked = sum(1 for p in files if is_parked(p))
    pending = sum(1 for p in files if not is_pushed(p) and not is_parked(p))
    captures_pending = sum(len(pending_captures(p)) for p in files if is_pushed(p))
    return {
        "total": len(files),
        "pushed": pushed,
        "parked": parked,
        "pending": pending,
        "captures_pending": captures_pending,
    }
