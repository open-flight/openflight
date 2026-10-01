"""Fan out completed shots to HTTP clients as Server-Sent Events.

This is the network sibling of the BLE publisher: same versioned payloads, same
bounded-queue delivery policy, same isolation from shot recording. Clients get
version one by default and schema v2 (provisional and final shots plus
profile, power, processing and club events) with ``?schema=2``. It exists so
a phone can receive shots over the network on hardware where BLE advertising is
unavailable, and so the payload contract can be exercised with nothing but
``curl``.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Iterable, Iterator, Mapping, Sequence

# The wire payload is the same versioned V1 shot event the BLE transport sends,
# so both transports are validated against one contract and one test fixture.
from .ble.protocol import (
    SCHEMA_VERSION,
    SCHEMA_VERSION_V2,
    build_club_event_v2,
    build_shot_event_v2,
    encode_club_event,
    encode_message_v2,
    encode_shot_event,
)

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

    v2 shots also carry their ``event_id`` as the SSE ``id``, so a client's
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

    ``encode_shot_event`` emits compact single-line JSON, so the payload never
    needs to be split across multiple ``data:`` lines.
    """
    name = event.name if isinstance(event, StreamEvent) else "shot"
    event_id = event.event_id if isinstance(event, StreamEvent) else None
    id_line = f"id: {event_id}\n" if event_id else ""
    return f"event: {name}\n{id_line}data: {event.decode('utf-8')}\n\n"


class ShotStreamBroker:
    """Deliver encoded shot events to every connected SSE subscriber."""

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
        # Subscribers that opted into schema v2 with ``?schema=2``. Everyone
        # else keeps receiving the unchanged version-one stream.
        self._v2_subscribers: set[int] = set()
        self._latest_payload: bytes | None = None
        self._latest_v2_event: StreamEvent | None = None

    @property
    def subscriber_count(self) -> int:
        """How many clients are currently streaming."""
        with self._lock:
            return len(self._subscribers)

    def publish(self, shot_data: Mapping) -> bool:
        """Encode a shot and hand it to every subscriber; never raises upward."""
        try:
            payload = encode_shot_event(shot_data)
        except (KeyError, TypeError, ValueError):
            logger.warning("[STREAM] Failed to encode shot payload", exc_info=True)
            return False

        with self._lock:
            self._latest_payload = payload
            subscribers = self._subscribers_for(SCHEMA_VERSION)
        event = StreamEvent("shot", payload)
        for subscriber in subscribers:
            self._offer(subscriber, event)
        return True

    def publish_v2_shot(
        self,
        shot_data: Mapping,
        *,
        final: bool,
        enrichment: Mapping | None = None,
    ) -> bool:
        """Send a provisional or final v2 shot to schema v2 subscribers."""
        try:
            shot_event = build_shot_event_v2(shot_data, final=final, enrichment=enrichment)
            event = StreamEvent("shot", encode_message_v2(shot_event), shot_event["event_id"])
        except (KeyError, TypeError, ValueError):
            logger.warning("[STREAM] Failed to encode v2 shot payload", exc_info=True)
            return False

        with self._lock:
            self._latest_v2_event = event
            subscribers = self._subscribers_for(SCHEMA_VERSION_V2)
        for subscriber in subscribers:
            self._offer(subscriber, event)
        return True

    def publish_event_v2(self, event: Mapping) -> bool:
        """Send one schema v2 event, named by its ``type``, to v2 subscribers."""
        try:
            stream_event = StreamEvent(str(event["type"]), encode_message_v2(event))
        except (KeyError, TypeError, ValueError):
            logger.warning("[STREAM] Failed to encode v2 event", exc_info=True)
            return False

        with self._lock:
            subscribers = self._subscribers_for(SCHEMA_VERSION_V2)
        for subscriber in subscribers:
            self._offer(subscriber, stream_event)
        return True

    def publish_club(self, club: str) -> bool:
        """Broadcast an authoritative club change to every network subscriber."""
        try:
            event = StreamEvent("club_changed", encode_club_event(club))
            v2_event = StreamEvent("club_changed", encode_message_v2(build_club_event_v2(club)))
        except (TypeError, ValueError):
            logger.warning("[STREAM] Failed to encode club payload", exc_info=True)
            return False

        with self._lock:
            subscribers = self._subscribers_for(SCHEMA_VERSION)
            v2_subscribers = self._subscribers_for(SCHEMA_VERSION_V2)
        for subscriber in subscribers:
            self._offer(subscriber, event)
        for subscriber in v2_subscribers:
            self._offer(subscriber, v2_event)
        return True

    def subscribe(
        self,
        *,
        schema: int = SCHEMA_VERSION,
        initial_events: Iterable[Mapping] = (),
        catch_up: Sequence[tuple[str, bytes]] | None = None,
    ) -> queue.Queue[StreamEvent]:
        """Register a subscriber, seeded with the latest shot for replay.

        A schema v2 subscriber is first seeded with ``initial_events`` (current
        state such as club and profiles), then its ``catch_up`` shots
        (``(event_id, payload)`` pairs, oldest first) or, when catch-up is
        unavailable (``None``), the latest v2 shot. Version one ignores
        ``catch_up``: its shots carry no stable id to resume from.
        """
        if schema not in (SCHEMA_VERSION, SCHEMA_VERSION_V2):
            raise ValueError(f"Unsupported shot stream schema: {schema}")
        seed = []
        if schema == SCHEMA_VERSION_V2:
            for event in initial_events:
                try:
                    seed.append(StreamEvent(str(event["type"]), encode_message_v2(event)))
                except (KeyError, TypeError, ValueError):
                    logger.warning("[STREAM] Failed to encode initial v2 event", exc_info=True)
        with self._lock:
            if len(self._subscribers) >= self.max_subscribers:
                raise ShotStreamFull(f"Shot stream already has {self.max_subscribers} clients")
            if schema == SCHEMA_VERSION_V2 and catch_up is not None:
                seed.extend(
                    StreamEvent("shot", payload, event_id) for event_id, payload in catch_up
                )
            elif schema == SCHEMA_VERSION_V2:
                if self._latest_v2_event is not None:
                    seed.append(self._latest_v2_event)
            elif self._latest_payload is not None:
                seed.append(StreamEvent("shot", self._latest_payload))
            subscriber: queue.Queue[StreamEvent] = queue.Queue(
                maxsize=max(self.queue_size, len(seed))
            )
            for event in seed:
                subscriber.put_nowait(event)
            self._subscribers.append(subscriber)
            if schema == SCHEMA_VERSION_V2:
                self._v2_subscribers.add(id(subscriber))
            count = len(self._subscribers)
        logger.info("[STREAM] Client subscribed (%d streaming, schema %d)", count, schema)
        return subscriber

    def _subscribers_for(self, schema: int) -> list[queue.Queue[StreamEvent]]:
        """Subscribers of one schema; the caller holds ``_lock``."""
        v2 = schema == SCHEMA_VERSION_V2
        return [item for item in self._subscribers if (id(item) in self._v2_subscribers) == v2]

    def unsubscribe(self, subscriber: queue.Queue[StreamEvent]) -> None:
        """Drop a subscriber. Unsubscribing twice is not an error."""
        with self._lock:
            if subscriber not in self._subscribers:
                return
            self._subscribers.remove(subscriber)
            self._v2_subscribers.discard(id(subscriber))
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
