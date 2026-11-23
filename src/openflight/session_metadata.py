"""User-managed metadata for historical sessions."""

import json
from pathlib import Path


def metadata_path(log_dir: Path) -> Path:
    return Path(log_dir).expanduser() / "history_metadata.json"


def load_hidden_sessions(log_dir: Path) -> set[str]:
    path = metadata_path(log_dir)
    if not path.exists():
        return set()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data.get("hidden_sessions", []))
    except (OSError, ValueError, TypeError, AttributeError):
        return set()


def save_hidden_sessions(log_dir: Path, hidden: set[str]) -> None:
    path = metadata_path(log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"hidden_sessions": sorted(hidden)}
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp.replace(path)
