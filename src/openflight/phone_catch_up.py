"""Catch a reconnecting phone up on the session shots it missed.

Both phone transports share this rule. A schema v2 client names the last shot it
has (``last_event_id`` in the BLE ``hello`` payload, ``Last-Event-ID`` on the
network stream) and receives that shot again plus every current-session shot
after it, oldest first. The named shot is resent because the client may hold
only its provisional version if it disconnected before the final one arrived.
A client that names no shot, or one the session no longer holds (cleared,
deleted, or from before a Pi restart), receives the whole session. Either way
the most recent ``CATCH_UP_LIMIT`` shots are sent, and clients upsert them by
``event_id`` so a shot they already have is harmless.

The session itself (``monitor.get_shots()``) decides which shots exist, so
clears, deletes and profile changes need no bookkeeping here. ``PhoneShotCache``
only remembers the exact v2 bytes last published per ``event_id``, so a replayed
shot carries the same ``final`` and ``enrichment`` state the phone would have
received live.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Sequence

CATCH_UP_LIMIT = 20
# Far above any realistic session; bounds memory on a Pi left running for days.
DEFAULT_CACHE_ENTRIES = 512
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
    """The latest encoded v2 shot per ``event_id``, bounded and thread-safe."""

    def __init__(self, max_entries: int = DEFAULT_CACHE_ENTRIES):
        if max_entries < 1:
            raise ValueError("Phone shot cache must hold at least one entry")
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._payloads: OrderedDict[str, bytes] = OrderedDict()

    def __len__(self) -> int:
        with self._lock:
            return len(self._payloads)

    def remember(self, event_id: str, payload: bytes) -> None:
        """Store the newest payload for a shot (a final replaces its provisional)."""
        with self._lock:
            self._payloads[event_id] = payload
            self._payloads.move_to_end(event_id)
            while len(self._payloads) > self.max_entries:
                self._payloads.popitem(last=False)

    def get(self, event_id: str) -> bytes | None:
        """The last published payload for ``event_id``, if still cached."""
        with self._lock:
            return self._payloads.get(event_id)
