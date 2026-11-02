"""Read historical OpenFlight sessions without modifying their logs."""

import json
import re
from pathlib import Path

from .session_logger import SessionLogger


SESSION_FILENAME = re.compile(
    r"^session_(\d{8}_\d{6})_(.+)\.jsonl$"
)


def session_camera_root(
    session_id: str,
    log_dir: Path | None = None,
) -> Path | None:
    """Return the camera directory a session's captures are filed under.

    Captures are written to ``<log_dir>/<session location>/camera``, and the
    location is the filename's suffix. Returns None for an invalid ID or a
    location that would resolve outside the log directory.
    """
    root = Path(log_dir or SessionLogger.DEFAULT_LOG_DIR).resolve()
    match = SESSION_FILENAME.fullmatch(f"{session_id}.jsonl")
    if not match:
        return None

    camera_root = (root / match.group(2) / "camera").resolve()
    if not camera_root.is_relative_to(root):
        return None
    return camera_root


def list_sessions(
    log_dir: Path | None = None,
    *,
    include_empty: bool = False,
) -> list[dict]:
    """Return saved sessions, newest first."""
    root = Path(log_dir or SessionLogger.DEFAULT_LOG_DIR)
    if not root.is_dir():
        return []

    sessions = []

    for path in root.glob("session_*.jsonl"):
        match = SESSION_FILENAME.fullmatch(path.name)
        if not match or not path.is_file():
            continue

        shot_count = 0
        camera_count = 0
        start_time = None

        try:
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    try:
                        event = json.loads(line)
                    except (json.JSONDecodeError, UnicodeError):
                        continue

                    if not isinstance(event, dict):
                        continue

                    event_type = event.get("type")

                    if event_type == "session_start":
                        start_time = event.get("start_time") or event.get("ts")
                    elif event_type == "shot_detected":
                        shot_count += 1
                    elif event_type == "camera_capture":
                        if event.get("capture_path") and not event.get("capture_error"):
                            camera_count += 1

        except OSError:
            continue

        if shot_count == 0 and not include_empty:
            continue

        sessions.append({
            "id": path.stem,
            "filename": path.name,
            "started_at": start_time or match.group(1),
            "location": match.group(2),
            "shot_count": shot_count,
            "camera_capture_count": camera_count,
        })

    return sorted(
        sessions,
        key=lambda session: session["filename"],
        reverse=True,
    )


def get_session_details(
    session_id: str,
    log_dir: Path | None = None,
) -> dict | None:
    """Read shots and associate camera captures within one saved session."""
    root = Path(log_dir or SessionLogger.DEFAULT_LOG_DIR).resolve()

    # Only accept valid session IDs, never arbitrary filesystem paths.
    filename = f"{session_id}.jsonl"
    if not SESSION_FILENAME.fullmatch(filename):
        return None

    path = root / filename
    if not path.is_file():
        return None

    shots = []
    captures = {}
    started_at = None

    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    continue

                if not isinstance(event, dict):
                    continue

                kind = event.get("type")

                if kind == "session_start":
                    started_at = event.get("start_time") or event.get("ts")

                elif kind == "shot_detected":
                    shots.append(event)

                elif kind == "camera_capture":
                    number = event.get("shot_number")
                    if number is not None:
                        captures.setdefault(number, []).append(event)
    except OSError:
        return None

    output = []
    camera_root = session_camera_root(session_id, root)

    for shot in shots:
        number = shot.get("shot_number")
        candidates = captures.get(number, [])

        # Use the recorded impact timestamp to distinguish repeated
        # shot numbers when possible.
        impact = shot.get("impact_timestamp")
        capture = None

        for candidate in candidates:
            if (
                impact is not None
                and candidate.get("shot_timestamp") == impact
            ):
                capture = candidate
                break

        if capture is None and len(candidates) == 1:
            capture = candidates[0]

        camera_available = False
        if capture and not capture.get("capture_error"):
            capture_path = capture.get("capture_path")
            if isinstance(capture_path, str) and camera_root is not None:
                folder = Path(capture_path).resolve()
                if (
                    folder.is_relative_to(camera_root)
                    and (folder / "frames.npz").is_file()
                    and (folder / "frames.npz").stat().st_size > 0
                ):
                    camera_available = True

        output.append({
            "shot_number": number,
            "timestamp": shot.get("timestamp") or shot.get("ts"),
            "club": shot.get("club"),
            "ball_speed_mph": shot.get("ball_speed_mph"),
            "club_speed_mph": shot.get("club_speed_mph"),
            "smash_factor": shot.get("smash_factor"),
            "estimated_carry_yards": shot.get("estimated_carry_yards"),
            "spin_rpm": shot.get("spin_rpm"),
            "spin_quality": shot.get("spin_quality"),
            "launch_angle_vertical": shot.get("launch_angle_vertical"),
            "launch_angle_horizontal": shot.get("launch_angle_horizontal"),
            "camera_available": camera_available,
            "recorded_data": shot,
        })

    return {
        "id": session_id,
        "started_at": started_at,
        "shot_count": len(output),
        "shots": output,
    }


def find_shot_capture(
    session_id: str,
    shot_number: int,
    log_dir: Path | None = None,
) -> dict | None:
    """Find a camera capture belonging to a shot in one historical session."""
    root = Path(log_dir or SessionLogger.DEFAULT_LOG_DIR).resolve()

    if not SESSION_FILENAME.fullmatch(f"{session_id}.jsonl"):
        return None

    path = root / f"{session_id}.jsonl"
    if not path.is_file():
        return None

    shots = []
    captures = []

    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    continue

                if not isinstance(event, dict):
                    continue
                if event.get("shot_number") != shot_number:
                    continue

                if event.get("type") == "shot_detected":
                    shots.append(event)
                elif (
                    event.get("type") == "camera_capture"
                    and not event.get("capture_error")
                ):
                    captures.append(event)
    except OSError:
        return None

    # Refuse ambiguous matches rather than replaying the wrong shot.
    if len(shots) != 1:
        return None

    impact = shots[0].get("impact_timestamp")
    matching = [
        capture for capture in captures
        if impact is not None
        and capture.get("shot_timestamp") == impact
    ]

    if len(matching) != 1:
        return None

    capture = matching[0]
    if not isinstance(capture.get("capture_path"), str):
        return None
    if not isinstance(capture.get("metadata"), dict):
        return None

    camera_root = session_camera_root(session_id, root)
    if camera_root is None:
        return None
    if not Path(capture["capture_path"]).resolve().is_relative_to(camera_root):
        return None

    return capture
