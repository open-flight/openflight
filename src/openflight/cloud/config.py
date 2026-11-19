"""Persistent config for the openflight-cloud uploader.

Stored at ``~/.config/openflight/cloud.json`` with mode ``0600``. The
``device_token`` is a bearer credential and must never be logged. The file is
written by ``link`` on success and read by ``push``/``status``. When the file
is absent (or ``enabled`` is false) the uploader is a no-op.

``upload_raw`` is an explicit per-device opt-in (``openflight-cloud raw on``):
sessions upload unfiltered (raw radar data included) and each shot's IWR6843
L3 dump uploads alongside, for radar testing. Off by default.
"""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

# Current deployment. The production domain is undecided (openflight vs.
# flightweb), so it lives in config and may move.
DEFAULT_ENDPOINT = "https://flightweb.fly.dev"

CONFIG_PATH = Path.home() / ".config" / "openflight" / "cloud.json"


@dataclass
class CloudConfig:
    """Uploader configuration persisted to disk."""

    endpoint: str = DEFAULT_ENDPOINT
    device_token: str = ""
    device_id: str = ""
    enabled: bool = True
    upload_raw: bool = False

    def is_linked(self) -> bool:
        """True when a device token and id are both present."""
        return bool(self.device_token and self.device_id)

    def is_active(self) -> bool:
        """True when the uploader should actually push (linked and enabled)."""
        return self.enabled and self.is_linked()


def load_config(path: Path = CONFIG_PATH) -> Optional[CloudConfig]:
    """Load config from ``path``; return None if the file is absent."""
    path = Path(path)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return CloudConfig(
        endpoint=data.get("endpoint", DEFAULT_ENDPOINT),
        device_token=data.get("device_token", ""),
        device_id=data.get("device_id", ""),
        enabled=data.get("enabled", True),
        upload_raw=data.get("upload_raw") is True,
    )


def save_config(config: CloudConfig, path: Path = CONFIG_PATH) -> None:
    """Write config to ``path`` with mode 0600, creating parent dirs."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create with restrictive permissions from the start so the bearer token is
    # never briefly world-readable.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(asdict(config), handle, indent=2)
        handle.write("\n")
    # Re-assert mode in case the file already existed with looser permissions.
    os.chmod(path, 0o600)
