"""Persistent profile roster: the named contexts shots are attributed to.

A profile is deliberately untyped. It may denote a person ("Cormac") or a
place ("Home Range"), because both are things you want shots recorded
against. Later features attach data via the open ``settings`` dict, which
this module round-trips untouched and never interprets.

The store is the single source of truth for both the roster and which
profile is active; the UI mirrors it and holds no copy of its own.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

DEFAULT_PROFILES_PATH = Path.home() / ".config" / "openflight" / "profiles.json"
PROFILES_PATH_ENV = "OPENFLIGHT_PROFILES_PATH"
DEFAULT_PROFILE_NAME = "Profile 1"
MAX_PROFILES = 12
MAX_NAME_LENGTH = 40


def resolve_profiles_path(path: Union[str, Path, None] = None) -> Path:
    """Constructor argument, then ``OPENFLIGHT_PROFILES_PATH``, then the user default.

    Tests and CI point the env var at a temp file so they cannot create or
    rewrite ``~/.config/openflight/profiles.json``.
    """
    if path is not None and str(path).strip():
        return Path(path).expanduser()
    env_path = (os.environ.get(PROFILES_PATH_ENV) or "").strip()
    if env_path:
        return Path(env_path).expanduser()
    return DEFAULT_PROFILES_PATH


def clean_profile_name(raw: Any) -> str:
    """Trim and cap a candidate name. Returns "" when unusable."""
    if raw is None:
        return ""
    return str(raw).strip()[:MAX_NAME_LENGTH]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class Profile:
    """One named context that shots are attributed to."""

    id: str
    name: str
    created_at: str
    settings: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Wire/disk representation.

        ``settings`` is shallow-copied so external callers can't mutate store-owned
        state (and bypass the store's lock / persistence path).
        """
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "settings": dict(self.settings),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> Optional["Profile"]:
        """Parse one stored record, or None when it is unusable."""
        if not isinstance(raw, dict):
            return None
        profile_id = str(raw.get("id") or "").strip()
        name = clean_profile_name(raw.get("name"))
        if not profile_id or not name:
            return None
        settings = raw.get("settings")
        return cls(
            id=profile_id,
            name=name,
            created_at=str(raw.get("created_at") or _utc_now_iso()),
            settings=settings if isinstance(settings, dict) else {},
        )


class ProfileStore:
    """Load, mutate, and atomically persist the profile roster.

    Mutators return a falsy value and change nothing when they would break
    an invariant: at least one profile always exists, and
    ``active_profile_id`` always names a live profile.
    """

    def __init__(self, path: Union[str, Path, None] = None):
        self._path = resolve_profiles_path(path)
        # One kiosk, one writer -- an in-process lock is enough; no file locking.
        self._lock = threading.Lock()
        self._profiles: List[Profile] = []
        self._active_id: str = ""
        self._load()

    # -- reads ---------------------------------------------------------

    def list(self) -> List[Profile]:  # pylint: disable=redefined-builtin
        """All profiles, in insertion order."""
        return list(self._profiles)

    def get_active(self) -> Profile:
        """The active profile. Always present."""
        for profile in self._profiles:
            if profile.id == self._active_id:
                return profile
        return self._profiles[0]

    def snapshot(self) -> dict:
        """The authoritative payload broadcast on the socket."""
        with self._lock:
            return self._payload()

    # -- mutations -----------------------------------------------------

    def add(self, name: Any) -> Optional[Profile]:
        """Append a profile and make it active. None when rejected."""
        cleaned = clean_profile_name(name)
        if not cleaned or len(self._profiles) >= MAX_PROFILES:
            return None

        with self._lock:
            profile = Profile(id=uuid.uuid4().hex, name=cleaned, created_at=_utc_now_iso())
            self._profiles.append(profile)
            self._active_id = profile.id
        self.save()
        return profile

    def rename(self, profile_id: Any, name: Any) -> bool:
        """Change a profile's name. Its id and shots are unaffected."""
        cleaned = clean_profile_name(name)
        if not cleaned:
            return False

        with self._lock:
            profile = self._find(profile_id)
            if profile is None:
                return False
            profile.name = cleaned
        self.save()
        return True

    def remove(self, profile_id: Any) -> bool:
        """Delete a profile. Refused for the active or the last one."""
        with self._lock:
            profile = self._find(profile_id)
            if profile is None or profile.id == self._active_id or len(self._profiles) <= 1:
                return False
            self._profiles.remove(profile)
        self.save()
        return True

    def set_active(self, profile_id: Any) -> bool:
        """Change the active profile. Refused for an unknown id."""
        with self._lock:
            profile = self._find(profile_id)
            if profile is None:
                return False
            self._active_id = profile.id
        self.save()
        return True

    # -- persistence ---------------------------------------------------

    def save(self) -> None:
        """Write the roster atomically. Never raises into a caller."""
        with self._lock:
            payload = self._payload()
        temp_path = self._path.with_name(f"{self._path.name}.{uuid.uuid4().hex}.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self._path)
        except OSError as error:
            logger.error("[profiles] could not save profiles to %s: %s", self._path, error)
            try:
                temp_path.unlink()
            except OSError:
                pass

    def _payload(self) -> dict:
        """The disk/wire representation of the whole roster."""
        return {
            "profiles": [profile.to_dict() for profile in self._profiles],
            "active_profile_id": self.get_active().id,
        }

    def _find(self, profile_id: Any) -> Optional[Profile]:
        wanted = str(profile_id or "").strip()
        if not wanted:
            return None
        return next((profile for profile in self._profiles if profile.id == wanted), None)

    def _load(self) -> None:
        """Read the roster, seeding a default when absent or unusable."""
        raw: Any = None
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            raw = None
        except (OSError, json.JSONDecodeError) as error:
            logger.warning("[profiles] could not read %s: %s", self._path, error)
            raw = None

        entries = raw.get("profiles") if isinstance(raw, dict) else None
        parsed = (
            [Profile.from_dict(entry) for entry in entries] if isinstance(entries, list) else []
        )
        self._profiles = [profile for profile in parsed if profile is not None][:MAX_PROFILES]

        if not self._profiles:
            self._profiles = [
                Profile(id=uuid.uuid4().hex, name=DEFAULT_PROFILE_NAME, created_at=_utc_now_iso())
            ]
            self._active_id = self._profiles[0].id
            self.save()
            return

        stored_active = str(raw.get("active_profile_id") or "") if isinstance(raw, dict) else ""
        known = {profile.id for profile in self._profiles}
        self._active_id = stored_active if stored_active in known else self._profiles[0].id
