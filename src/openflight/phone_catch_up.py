"""Catch a reconnecting phone up on the session shots it missed.

Both phone transports share this rule. A client names the last shot it
has (``last_event_id`` in the BLE ``hello`` payload, ``Last-Event-ID`` on the
network stream) and receives that shot again plus every current-session shot
after it, oldest first. The named shot is resent because the client may hold
only its provisional version if it disconnected before the final one arrived.
A client that names no shot, or one the session no longer holds (cleared,
deleted, or from before a Pi restart), receives the whole session. Either way
the most recent ``CATCH_UP_LIMIT`` shots are sent, and clients upsert them by
``event_id`` so a shot they already have is harmless.

Deletions the client missed come first, as ``shot_deleted`` events: every
deletion made after the named shot was first sent, or every remembered one
when the client names no shot or one the Pi no longer knows. Deleting a shot
the phone no longer has is a no-op, so an extra deletion is harmless.

The session itself (``monitor.get_shots()``) decides which shots exist, so
clears, deletes and profile changes need no bookkeeping for the shots.
``PhoneShotCache`` remembers the exact bytes last published per ``event_id``,
so a replayed shot carries the same ``final`` and ``enrichment`` state the
phone would have received live, and the order in which shots were first sent
and shots were deleted, which places a client's anchor among the deletions.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Sequence

# Covers a long gap (a tester once missed 55 shots). Over BLE each shot is a
# few dozen 20-byte notifications, so this is about a minute of catch-up.
CATCH_UP_LIMIT = 100
# Far above any realistic session; bounds memory on a Pi left running for days.
DEFAULT_CACHE_ENTRIES = 512
# Deletions replayed to a phone whose anchor is unknown; one kiosk session's worth.
MAX_REMEMBERED_DELETIONS = 100
# Event ids are UUIDs (36 characters); anything much longer is not one of ours.
MAX_EVENT_ID_LENGTH = 64

CatchUpEntry = tuple[str, bytes]


def normalize_last_event_id(value) -> str | None:
    """Return a usable ``last_event_id``, or ``None`` when the client sent none.

    Invalid values are treated as "no anchor" rather than rejected: a client
    that sends garbage still gets the whole session instead of a failed
    ``hello`` that would push it back to version one.
    """
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > MAX_EVENT_ID_LENGTH:
        return None
    return value


def select_catch_up(
    entries: Sequence[CatchUpEntry],
    last_event_id: str | None,
    *,
    limit: int = CATCH_UP_LIMIT,
) -> list[CatchUpEntry]:
    """The ``last_event_id`` shot and those after it (all shots if it is unknown).

    ``entries`` are ``(event_id, payload)`` pairs in session order. The result
    keeps that order and holds at most the most recent ``limit`` shots.
    """
    if limit < 1:
        raise ValueError("Catch-up limit must be at least one")
    start = 0
    if last_event_id is not None:
        for index, (event_id, _payload) in enumerate(entries):
            if event_id == last_event_id:
                start = index
                break
    return list(entries[start:])[-limit:]


class PhoneShotCache:
    """The latest encoded shot per ``event_id`` and the recent deletions.

    Bounded and thread-safe. One counter orders shots (by their first send)
    and deletions, so ``deletions_since`` can tell which deletions came after
    the shot a reconnecting phone names.
    """

    def __init__(
        self,
        max_entries: int = DEFAULT_CACHE_ENTRIES,
        max_deletions: int = MAX_REMEMBERED_DELETIONS,
    ):
        if max_entries < 1:
            raise ValueError("Phone shot cache must hold at least one entry")
        if max_deletions < 1:
            raise ValueError("Phone shot cache must remember at least one deletion")
        self.max_entries = max_entries
        self.max_deletions = max_deletions
        self._lock = threading.Lock()
        self._counter = 0
        # event_id -> (latest payload, position of its first send)
        self._payloads: OrderedDict[str, tuple[bytes, int]] = OrderedDict()
        # timestamp -> position of the deletion, oldest first
        self._deletions: OrderedDict[str, int] = OrderedDict()

    def __len__(self) -> int:
        with self._lock:
            return len(self._payloads)

    def _next_position(self) -> int:
        self._counter += 1
        return self._counter

    def remember(self, event_id: str, payload: bytes) -> None:
        """Store the newest payload for a shot (a final replaces its provisional)."""
        with self._lock:
            known = self._payloads.get(event_id)
            # Keep the first send's position: a phone that got only the
            # provisional must still hear of deletions made before the final.
            position = known[1] if known is not None else self._next_position()
            self._payloads[event_id] = (payload, position)
            self._payloads.move_to_end(event_id)
            while len(self._payloads) > self.max_entries:
                self._payloads.popitem(last=False)

    def get(self, event_id: str) -> bytes | None:
        """The last published payload for ``event_id``, if still cached."""
        with self._lock:
            known = self._payloads.get(event_id)
            return known[0] if known is not None else None

    def remember_deletion(self, timestamp) -> None:
        """Record a deleted shot by its timestamp (the ``shot_deleted`` key)."""
        if not isinstance(timestamp, str) or not timestamp:
            return
        with self._lock:
            self._deletions.pop(timestamp, None)
            self._deletions[timestamp] = self._next_position()
            while len(self._deletions) > self.max_deletions:
                self._deletions.popitem(last=False)

    def deletions_since(self, last_event_id: str | None) -> list[str]:
        """Timestamps deleted after ``last_event_id`` was first sent, oldest first.

        Every remembered deletion when ``last_event_id`` is ``None`` or no
        longer cached (deleted, evicted, or from before a Pi restart).
        """
        with self._lock:
            known = self._payloads.get(last_event_id) if last_event_id is not None else None
            since = known[1] if known is not None else 0
            return [
                timestamp for timestamp, position in self._deletions.items() if position > since
            ]
