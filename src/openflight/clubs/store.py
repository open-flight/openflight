"""Persistent custom clubs backed by an atomic, versioned JSON file.

Based on weawer's ``ClubStore`` prototype (feat/custom-club-foundation-v2):
same file layout, schema version, atomic write and rollback when a write
fails. Clubs carry a ``bag`` and are listed and capped per bag.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Optional, Union

from .club import ClubValidationError, CustomClub, clean_id

logger = logging.getLogger(__name__)

DEFAULT_CLUBS_PATH = Path.home() / ".config" / "openflight" / "clubs.json"
CLUBS_PATH_ENV = "OPENFLIGHT_CLUBS_PATH"
CLUBS_SCHEMA_VERSION = 1
MAX_CLUBS_PER_BAG = 30


class ClubStoreError(ValueError):
    """A change the store refused, with a message a player can act on."""


def resolve_clubs_path(path: Union[str, Path, None] = None) -> Path:
    """Constructor argument, then ``OPENFLIGHT_CLUBS_PATH``, then the user default.

    Tests and CI point the env var at a temp file so they cannot create or
    rewrite ``~/.config/openflight/clubs.json``.
    """
    if path is not None and str(path).strip():
        return Path(path).expanduser()
    env_path = (os.environ.get(CLUBS_PATH_ENV) or "").strip()
    if env_path:
        return Path(env_path).expanduser()
    return DEFAULT_CLUBS_PATH


class ClubStore:
    """Load, mutate, and atomically persist custom clubs.

    Ids are generated here and never reused: an edit keeps the id, and a
    removed club's id is gone for good, so shots that recorded it can never
    be filed under a different club.
    """

    def __init__(self, path: Union[str, Path, None] = None):
        self._path = resolve_clubs_path(path)
        self._lock = threading.Lock()
        self._clubs: list[CustomClub] = []
        # False after reading a file this version does not understand, so it
        # is never overwritten with an empty catalog.
        self._writable = True
        self._load()

    # -- reads ---------------------------------------------------------

    def list(self, bag: Any = None) -> list[CustomClub]:  # pylint: disable=redefined-builtin
        """Clubs in the order they were added; only one bag's when ``bag`` is given."""
        with self._lock:
            if bag is None:
                return list(self._clubs)
            wanted = clean_id(bag)
            return [club for club in self._clubs if club.bag == wanted]

    def get(self, club_id: Any) -> Optional[CustomClub]:
        """One club by id, or None."""
        with self._lock:
            return self._find(club_id)

    def snapshot(self) -> dict:
        """The versioned disk/wire representation."""
        with self._lock:
            return self._payload()

    # -- mutations -----------------------------------------------------

    def add(self, bag: Any, fields: Any) -> CustomClub:
        """Validate, add and persist a club to ``bag`` under a new id.

        Raises:
            ClubStoreError: the fields are invalid, the bag is full, or the
                file could not be written (nothing is changed).
        """
        bag_id = clean_id(bag)
        if not bag_id:
            raise ClubStoreError("Choose a bag for the club")
        club = _build(fields, club_id=uuid.uuid4().hex, bag=bag_id)
        with self._lock:
            if sum(1 for entry in self._clubs if entry.bag == bag_id) >= MAX_CLUBS_PER_BAG:
                raise ClubStoreError(f"A bag holds at most {MAX_CLUBS_PER_BAG} clubs")
            self._clubs.append(club)
            if not self._save_locked():
                self._clubs.remove(club)
                raise ClubStoreError("Could not save the club. Try again.")
        return club

    def update(self, club_id: Any, fields: Any) -> CustomClub:
        """Replace a club's recorded values, keeping its id and bag.

        Raises:
            ClubStoreError: no club has this id, the fields are invalid, or
                the file could not be written (nothing is changed).
        """
        with self._lock:
            current = self._find(club_id)
            if current is None:
                raise ClubStoreError("That club no longer exists")
            updated = _build(fields, club_id=current.id, bag=current.bag)
            index = self._clubs.index(current)
            self._clubs[index] = updated
            if not self._save_locked():
                self._clubs[index] = current
                raise ClubStoreError("Could not save the club. Try again.")
        return updated

    def remove(self, club_id: Any) -> bool:
        """Remove and persist one club. False when unknown or the write failed."""
        with self._lock:
            club = self._find(club_id)
            if club is None:
                return False
            index = self._clubs.index(club)
            self._clubs.pop(index)
            if not self._save_locked():
                self._clubs.insert(index, club)
                return False
        return True

    def remove_bag(self, bag: Any) -> int:
        """Remove every club in a bag, e.g. when its profile is deleted. Returns the count."""
        wanted = clean_id(bag)
        with self._lock:
            kept = [club for club in self._clubs if club.bag != wanted]
            removed = len(self._clubs) - len(kept)
            if removed == 0:
                return 0
            previous, self._clubs = self._clubs, kept
            if not self._save_locked():
                self._clubs = previous
                return 0
        return removed

    def save(self) -> bool:
        """Write all clubs atomically and report whether it succeeded."""
        with self._lock:
            return self._save_locked()

    # -- persistence ---------------------------------------------------

    def _save_locked(self) -> bool:
        """Persist the catalog while the caller holds ``self._lock``."""
        if not self._writable:
            logger.error("[clubs] refusing to overwrite %s: unsupported schema", self._path)
            return False
        payload = self._payload()
        temp_path = self._path.with_name(f"{self._path.name}.{uuid.uuid4().hex}.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self._path)
            return True
        except OSError as error:
            logger.error("[clubs] could not save custom clubs to %s: %s", self._path, error)
            try:
                temp_path.unlink()
            except OSError:
                pass
            return False

    def _payload(self) -> dict:
        return {
            "version": CLUBS_SCHEMA_VERSION,
            "clubs": [club.to_dict() for club in self._clubs],
        }

    def _find(self, club_id: Any) -> Optional[CustomClub]:
        wanted = clean_id(club_id)
        if not wanted:
            return None
        return next((club for club in self._clubs if club.id == wanted), None)

    def _load(self) -> None:
        """Read the catalog. Bad entries are skipped so startup never fails."""
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as error:
            logger.warning("[clubs] could not read %s: %s", self._path, error)
            return

        version = raw.get("version") if isinstance(raw, dict) else None
        if version != CLUBS_SCHEMA_VERSION:
            logger.warning("[clubs] unsupported schema version %r in %s", version, self._path)
            self._writable = False
            return

        entries = raw.get("clubs")
        seen: set[str] = set()
        for entry in entries if isinstance(entries, list) else []:
            club = _load_entry(entry)
            if club is None or club.id in seen:
                continue
            seen.add(club.id)
            self._clubs.append(club)


def _build(fields: Any, *, club_id: str, bag: str) -> CustomClub:
    try:
        return CustomClub.from_dict(fields, club_id=club_id, bag=bag)
    except ClubValidationError as error:
        raise ClubStoreError(str(error)) from None


def _load_entry(entry: Any) -> Optional[CustomClub]:
    """One stored club, or None when it lacks an id or bag or fails validation."""
    if not isinstance(entry, dict):
        return None
    club_id, bag = clean_id(entry.get("id")), clean_id(entry.get("bag"))
    if not club_id or not bag:
        return None
    try:
        return CustomClub.from_dict(entry, club_id=club_id, bag=bag)
    except ClubValidationError:
        return None
