"""Fan out shots and phone events to HTTP clients as Server-Sent Events.

This is the network sibling of the BLE publisher: the same payloads, the same
bounded-queue delivery policy, the same isolation from shot recording. It
exists so a phone can receive shots over the network on hardware where BLE
advertising is unavailable, and so the payload contract can be exercised with
nothing but ``curl``.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Iterable, Iterator, Mapping, Sequence

# The wire payloads are the BLE transport's, so both transports are validated
# against one contract and one set of fixtures.
from .ble.protocol import build_club_event, build_shot_event, encode_message

logger = logging.getLogger(__name__)

SSE_MIMETYPE = "text/event-stream"
DEFAULT_HEARTBEAT_INTERVAL_S = 15.0
DEFAULT_MAX_SUBSCRIBERS = 8

# An SSE comment. Clients ignore it, but it keeps idle connections from being
# reaped and lets the server notice a vanished client between shots.
HEARTBEAT_FRAME = ": ping\n\n"


class ShotStreamFull(RuntimeError):
    """Raised when the broker already serves the maximum number of clients."""


class StreamEvent(bytes):
    """Encoded JSON carrying its SSE name while remaining bytes-compatible.

    Shots also carry their ``event_id`` as the SSE ``id``, so a client's
    ``Last-Event-ID`` names the last shot it received (see ``phone_catch_up``).
    """

    name: str
    event_id: str | None

    def __new__(cls, name: str, payload: bytes, event_id: str | None = None):
        event = super().__new__(cls, payload)
        event.name = name
        event.event_id = event_id
        return event


def format_event(event: StreamEvent | bytes) -> str:
    """Frame one encoded event for the SSE wire protocol.

    ``encode_message`` emits compact single-line JSON, so the payload never
    needs to be split across multiple ``data:`` lines.
    """
    name = event.name if isinstance(event, StreamEvent) else "shot"
    event_id = event.event_id if isinstance(event, StreamEvent) else None
    id_line = f"id: {event_id}\n" if event_id else ""
    return f"event: {name}\n{id_line}data: {event.decode('utf-8')}\n\n"


class ShotStreamBroker:
    """Deliver encoded shots and events to every connected SSE subscriber."""

    def __init__(
        self,
        *,
        queue_size: int = 8,
        heartbeat_interval_s: float = DEFAULT_HEARTBEAT_INTERVAL_S,
        max_subscribers: int = DEFAULT_MAX_SUBSCRIBERS,
    ):
        if queue_size < 1:
            raise ValueError("Shot stream queue size must be at least one")
        if heartbeat_interval_s <= 0:
            raise ValueError("Shot stream heartbeat interval must be positive")
        if max_subscribers < 1:
            raise ValueError("Shot stream must allow at least one subscriber")

        self.queue_size = queue_size
        self.heartbeat_interval_s = heartbeat_interval_s
        self.max_subscribers = max_subscribers

        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue[StreamEvent]] = []
        self._latest_shot: StreamEvent | None = None

    @property
    def subscriber_count(self) -> int:
        """How many clients are currently streaming."""
        with self._lock:
            return len(self._subscribers)

    def publish_shot(
        self,
        shot_data: Mapping,
        *,
        final: bool,
        enrichment: Mapping | None = None,
    ) -> bool:
        """Send a provisional or final shot to every subscriber; never raises upward."""
        try:
            shot_event = build_shot_event(shot_data, final=final, enrichment=enrichment)
            event = StreamEvent("shot", encode_message(shot_event), shot_event["event_id"])
        except (KeyError, TypeError, ValueError):
            logger.warning("[STREAM] Failed to encode shot payload", exc_info=True)
            return False

        with self._lock:
            self._latest_shot = event
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            self._offer(subscriber, event)
        return True

    def publish_event(self, event: Mapping) -> bool:
        """Send one event, named by its ``type``, to every subscriber."""
        try:
            stream_event = StreamEvent(str(event["type"]), encode_message(event))
        except (KeyError, TypeError, ValueError):
            logger.warning("[STREAM] Failed to encode event", exc_info=True)
            return False

        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            self._offer(subscriber, stream_event)
        return True

    def publish_club(self, club: str) -> bool:
        """Broadcast an authoritative club change to every subscriber."""
        try:
            event = build_club_event(club)
        except (TypeError, ValueError):
            logger.warning("[STREAM] Failed to encode club payload", exc_info=True)
            return False
        return self.publish_event(event)

    def subscribe(
        self,
        *,
        initial_events: Iterable[Mapping] = (),
        catch_up: Sequence[tuple[str, bytes]] | None = None,
    ) -> queue.Queue[StreamEvent]:
        """Register a subscriber, seeded with current state and missed shots.

        The seed is ``initial_events`` (current state such as club and
        profiles), then the ``catch_up`` shots (``(event_id, payload)`` pairs,
        oldest first) or, when catch-up is unavailable (``None``), the latest
        shot.
        """
        seed = []
        for event in initial_events:
            try:
                seed.append(StreamEvent(str(event["type"]), encode_message(event)))
            except (KeyError, TypeError, ValueError):
                logger.warning("[STREAM] Failed to encode initial event", exc_info=True)
        with self._lock:
            if len(self._subscribers) >= self.max_subscribers:
                raise ShotStreamFull(f"Shot stream already has {self.max_subscribers} clients")
            if catch_up is not None:
                seed.extend(
                    StreamEvent("shot", payload, event_id) for event_id, payload in catch_up
                )
            elif self._latest_shot is not None:
                seed.append(self._latest_shot)
            subscriber: queue.Queue[StreamEvent] = queue.Queue(
                maxsize=max(self.queue_size, len(seed))
            )
            for event in seed:
                subscriber.put_nowait(event)
            self._subscribers.append(subscriber)
            count = len(self._subscribers)
        logger.info("[STREAM] Client subscribed (%d streaming)", count)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[StreamEvent]) -> None:
        """Drop a subscriber. Unsubscribing twice is not an error."""
        with self._lock:
            if subscriber not in self._subscribers:
                return
            self._subscribers.remove(subscriber)
            count = len(self._subscribers)
        logger.info("[STREAM] Client unsubscribed (%d streaming)", count)

    def frames(self, subscriber: queue.Queue[StreamEvent]) -> Iterator[str]:
        """Yield SSE frames for an already-registered subscriber.

        Registration is deliberately not folded in here: a generator body does
        not run until first iteration, so a caller that needs to reject a client
        before sending response headers has to call ``subscribe`` itself.
        """
        try:
            # Open with a heartbeat so the WSGI server flushes response headers
            # at once. Without it a client learns nothing -- not even that it
            # connected -- until the first shot or heartbeat, because headers
            # are not written until the first chunk of the body.
            yield HEARTBEAT_FRAME
            while True:
                try:
                    event = subscriber.get(timeout=self.heartbeat_interval_s)
                except queue.Empty:
                    yield HEARTBEAT_FRAME
                    continue
                yield format_event(event)
        finally:
            self.unsubscribe(subscriber)

    def _offer(self, subscriber: queue.Queue[StreamEvent], event: StreamEvent) -> None:
        """Queue an event, dropping the oldest unsent event when full."""
        try:
            subscriber.put_nowait(event)
            return
        except queue.Full:
            pass

        try:
            subscriber.get_nowait()
            logger.warning("[STREAM] Delivery queue full; dropped oldest unsent shot")
        except queue.Empty:
            pass

        try:
            subscriber.put_nowait(event)
        except queue.Full:
            logger.warning("[STREAM] Delivery queue still full; dropped shot")
