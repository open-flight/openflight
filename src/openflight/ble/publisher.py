"""Non-blocking BLE GATT publisher backed by Bless/BlueZ."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from collections.abc import Callable, Sequence
from typing import Any, Mapping

from .protocol import (
    CONTROL_CHARACTERISTIC_UUID,
    CONTROL_V2_CHARACTERISTIC_UUID,
    SCHEMA_VERSION,
    SCHEMA_VERSION_V2,
    SERVICE_UUID,
    SHOT_CHARACTERISTIC_UUID,
    SHOT_V2_CHARACTERISTIC_UUID,
    FragmentReassembler,
    build_club_event_v2,
    build_control_response,
    build_hello_result,
    encode_club_event,
    encode_message,
    encode_message_v2,
    encode_shot_event,
    encode_shot_event_v2,
    fragment_payload,
)

logger = logging.getLogger(__name__)

CommandHandler = Callable[[str, Mapping], tuple[dict, int]]
# Given a client's ``last_event_id`` (or ``None``), the ``(event_id, v2 shot
# payload)`` pairs it missed, oldest first. See ``openflight.phone_catch_up``.
CatchUpProvider = Callable[[Any], Sequence[tuple[str, bytes]]]

_V1_CHARACTERISTICS = frozenset(
    {SHOT_CHARACTERISTIC_UUID.lower(), CONTROL_CHARACTERISTIC_UUID.lower()}
)
_V2_CHARACTERISTICS = frozenset(
    {SHOT_V2_CHARACTERISTIC_UUID.lower(), CONTROL_V2_CHARACTERISTIC_UUID.lower()}
)
_ALL_CHARACTERISTICS = _V1_CHARACTERISTICS | _V2_CHARACTERISTICS

# (response schema, accepted request envelope schemas) per control
# characteristic. The version-one characteristic is unchanged; the v2 one also
# accepts version-one envelopes so a client can reuse one encoder for ``hello``.
_CONTROL_SCHEMAS = {
    CONTROL_CHARACTERISTIC_UUID.lower(): (SCHEMA_VERSION, (SCHEMA_VERSION,)),
    CONTROL_V2_CHARACTERISTIC_UUID.lower(): (
        SCHEMA_VERSION_V2,
        (SCHEMA_VERSION, SCHEMA_VERSION_V2),
    ),
}


def _normalize_uuid(value) -> str | None:
    try:
        return str(uuid.UUID(str(value))).lower()
    except (TypeError, ValueError):
        return None


def _characteristic_uuid(characteristic) -> str | None:
    """The normalized UUID of a Bless characteristic object, when it has one."""
    if characteristic is None:
        return None
    return _normalize_uuid(getattr(characteristic, "uuid", None))


class BleShotPublisher:
    """Publish completed shots without coupling the radar thread to Bluetooth.

    Version-one traffic uses the original shot and control characteristics and
    is byte-for-byte what jake-fishtech's iOS app expects. Schema v2 traffic
    (provisional and final shots, profile, power and processing events) uses a
    second shot/control pair, so a v1 central never receives a v2 message.
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        *,
        name: str = "OpenFlight",
        queue_size: int = 8,
        fragment_interval_s: float = 0.01,
        command_handler: CommandHandler | None = None,
        command_handler_v2: CommandHandler | None = None,
        catch_up_provider: CatchUpProvider | None = None,
    ):
        if queue_size < 1:
            raise ValueError("BLE queue size must be at least one")
        self.name = name
        self.queue_size = queue_size
        self.fragment_interval_s = fragment_interval_s
        self.command_handler = command_handler
        self.command_handler_v2 = command_handler_v2
        self.catch_up_provider = catch_up_provider

        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[bytes] | None = None
        self._v2_queue: asyncio.Queue[bytes] | None = None
        self._stop_event: asyncio.Event | None = None
        self._stop_requested = threading.Event()
        self._server = None
        self._latest_payload: bytes | None = None
        self._latest_v2_payload: bytes | None = None
        # ``_subscribed`` gates version-one traffic and ``_v2_subscribed`` gates
        # schema v2 traffic. Both follow BlueZ's per-characteristic
        # subscriptions when those are visible.
        self._subscribed = False
        self._v2_subscribed = False
        self._subscriptions: set[str] = set()
        self._sequence = 0
        self._v2_sequence = 0
        self._control_sequences = {key: 0 for key in _CONTROL_SCHEMAS}
        self._control_reassemblers = {key: FragmentReassembler() for key in _CONTROL_SCHEMAS}
        self._control_send_locks: dict[str, asyncio.Lock] = {}
        # Missed shots computed at a v2 ``hello`` that arrived before the phone
        # subscribed to the v2 shot characteristic; sent when it does.
        self._pending_v2_catch_up: list[bytes] | None = None

    @property
    def subscribed(self) -> bool:
        """Whether a central is subscribed to version-one notifications."""
        with self._state_lock:
            return self._subscribed

    @property
    def v2_subscribed(self) -> bool:
        """Whether a central is subscribed to schema v2 notifications."""
        with self._state_lock:
            return self._v2_subscribed

    def start(self) -> None:
        """Start advertising in a daemon thread; startup failures remain isolated."""
        with self._state_lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_requested.clear()
            self._thread = threading.Thread(
                target=self._run_thread,
                name="openflight-ble",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop advertising and join the BLE thread."""
        self._stop_requested.set()
        with self._state_lock:
            loop = self._loop
            stop_event = self._stop_event
            thread = self._thread
        if loop and stop_event:
            loop.call_soon_threadsafe(stop_event.set)
        if thread and thread is not threading.current_thread():
            thread.join(timeout=5.0)
            if thread.is_alive():
                logger.warning("[BLE] Publisher thread did not stop within 5 seconds")

    def publish(self, shot_data: Mapping) -> bool:
        """Store the latest v1 shot and enqueue it when a central is subscribed."""
        try:
            payload = encode_shot_event(shot_data)
        except (KeyError, TypeError, ValueError):
            logger.warning("[BLE] Failed to encode shot payload", exc_info=True)
            return False

        with self._state_lock:
            self._latest_payload = payload
            loop = self._loop
            subscribed = self._subscribed
        if loop and subscribed:
            loop.call_soon_threadsafe(self._enqueue_payload, payload)
        return True

    def publish_v2_shot(
        self,
        shot_data: Mapping,
        *,
        final: bool,
        enrichment: Mapping | None = None,
    ) -> bool:
        """Store the latest v2 shot (provisional or final) and notify v2 centrals."""
        try:
            payload = encode_shot_event_v2(shot_data, final=final, enrichment=enrichment)
            fragment_payload(payload, sequence=0)
        except (KeyError, TypeError, ValueError):
            logger.warning("[BLE] Failed to encode v2 shot payload", exc_info=True)
            return False

        with self._state_lock:
            self._latest_v2_payload = payload
            loop = self._loop
            subscribed = self._v2_subscribed
        if loop and subscribed:
            loop.call_soon_threadsafe(self._enqueue_v2_payload, payload)
        return True

    def publish_club(self, club: str) -> bool:
        """Notify connected centrals that the authoritative club changed."""
        try:
            payload = encode_club_event(club)
            v2_payload = encode_message_v2(build_club_event_v2(club))
        except (TypeError, ValueError):
            logger.warning("[BLE] Failed to encode club payload", exc_info=True)
            return False

        with self._state_lock:
            loop = self._loop
            subscribed = self._subscribed
            v2_subscribed = self._v2_subscribed
        if loop and subscribed:
            asyncio.run_coroutine_threadsafe(self._send_control_response(payload), loop)
        if loop and v2_subscribed:
            asyncio.run_coroutine_threadsafe(
                self._send_control_response(v2_payload, CONTROL_V2_CHARACTERISTIC_UUID),
                loop,
            )
        return True

    def publish_event_v2(self, event: Mapping) -> bool:
        """Notify v2 centrals of one schema v2 event on the v2 control characteristic."""
        try:
            payload = encode_message_v2(event)
            fragment_payload(payload, sequence=0)
        except (TypeError, ValueError):
            logger.warning("[BLE] Failed to encode v2 event", exc_info=True)
            return False

        with self._state_lock:
            loop = self._loop
            subscribed = self._v2_subscribed
        if loop and subscribed:
            asyncio.run_coroutine_threadsafe(
                self._send_control_response(payload, CONTROL_V2_CHARACTERISTIC_UUID),
                loop,
            )
        return True

    def _run_thread(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning(
                "[BLE] Bluetooth unavailable; shot recording will continue without BLE",
                exc_info=True,
            )
        finally:
            with self._state_lock:
                self._loop = None
                self._queue = None
                self._v2_queue = None
                self._stop_event = None
                self._server = None
                self._subscribed = False
                self._v2_subscribed = False
                self._subscriptions = set()
                self._control_send_locks = {}
                self._pending_v2_catch_up = None

    async def _run(self) -> None:
        # Bless is an optional dependency and must not affect non-BLE installs.
        from bless import (  # pylint: disable=import-error,import-outside-toplevel
            BlessServer,
            GATTAttributePermissions,
            GATTCharacteristicProperties,
        )

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=self.queue_size)
        v2_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=self.queue_size)
        stop_event = asyncio.Event()
        server = BlessServer(
            name=self.name,
            loop=loop,
            on_subscribe=self._on_subscribe,
            on_unsubscribe=self._on_unsubscribe,
        )
        await server.add_new_service(SERVICE_UUID)
        for shot_uuid in (SHOT_CHARACTERISTIC_UUID, SHOT_V2_CHARACTERISTIC_UUID):
            await server.add_new_characteristic(
                SERVICE_UUID,
                shot_uuid,
                GATTCharacteristicProperties.notify,
                bytearray(),
                GATTAttributePermissions.readable,
            )
        for control_uuid in (CONTROL_CHARACTERISTIC_UUID, CONTROL_V2_CHARACTERISTIC_UUID):
            await server.add_new_characteristic(
                SERVICE_UUID,
                control_uuid,
                GATTCharacteristicProperties.write | GATTCharacteristicProperties.notify,
                bytearray(),
                GATTAttributePermissions.readable | GATTAttributePermissions.writeable,
            )
        server.write_request_func = self._on_write_request
        self._install_bluez_subscription_hooks(server)

        with self._state_lock:
            self._loop = loop
            self._queue = queue
            self._v2_queue = v2_queue
            self._stop_event = stop_event
            self._server = server

        await server.start()
        logger.info("[BLE] Advertising %s", self.name)
        if self._stop_requested.is_set():
            stop_event.set()

        workers = [
            asyncio.create_task(self._delivery_worker()),
            asyncio.create_task(self._v2_delivery_worker()),
        ]
        try:
            await stop_event.wait()
        finally:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            await server.stop()
            logger.info("[BLE] Advertising stopped")

    def _install_bluez_subscription_hooks(self, server) -> None:
        """Wire callbacks that Bless 0.3.0 leaves disconnected on BlueZ.

        Bless's Linux backend accepts ``on_subscribe`` and ``on_unsubscribe``
        constructor keywords but replaces the underlying BlueZ ``StartNotify``
        and ``StopNotify`` handlers with no-ops. Hook the application object
        after asynchronous server setup so delivery state follows the iOS
        notification subscription.

        Bless calls the hook *before* it records the characteristic in
        ``app.subscribed_characteristics``, so when that list exists the
        per-characteristic state is re-read on the next loop iteration.
        """
        app = getattr(server, "app", None)
        if app is None:
            return
        app.StartNotify = lambda session: self._on_bluez_notify_change(app, session, True)
        app.StopNotify = lambda session: self._on_bluez_notify_change(app, session, False)

    def _on_bluez_notify_change(self, app, session, started: bool) -> None:
        tracked = getattr(app, "subscribed_characteristics", None)
        with self._state_lock:
            loop = self._loop
        if isinstance(tracked, list) and loop is not None:
            loop.call_soon(self._sync_bluez_subscriptions, app)
            return
        if started:
            self._on_subscribe(None, session)
        else:
            self._on_unsubscribe(None, session)

    def _sync_bluez_subscriptions(self, app) -> None:
        tracked = getattr(app, "subscribed_characteristics", None) or []
        subscriptions = {_normalize_uuid(item) for item in tracked} & _ALL_CHARACTERISTICS
        self._set_subscriptions(subscriptions)

    def _on_subscribe(self, characteristic, _session) -> None:
        characteristic_uuid = _characteristic_uuid(characteristic)
        with self._state_lock:
            subscriptions = set(self._subscriptions)
        if characteristic_uuid is None:
            # The backend did not say which characteristic; assume all of them,
            # as the version-one publisher did.
            subscriptions |= _ALL_CHARACTERISTICS
        else:
            subscriptions.add(characteristic_uuid)
        self._set_subscriptions(subscriptions)

    def _on_unsubscribe(self, characteristic, _session) -> None:
        characteristic_uuid = _characteristic_uuid(characteristic)
        with self._state_lock:
            subscriptions = set(self._subscriptions)
        if characteristic_uuid is None:
            subscriptions.clear()
        else:
            subscriptions.discard(characteristic_uuid)
        self._set_subscriptions(subscriptions)

    def _set_subscriptions(self, subscriptions: set[str]) -> None:
        v1 = bool(subscriptions & _V1_CHARACTERISTICS)
        v2 = bool(subscriptions & _V2_CHARACTERISTICS)
        shot_v1 = SHOT_CHARACTERISTIC_UUID.lower()
        shot_v2 = SHOT_V2_CHARACTERISTIC_UUID.lower()
        with self._state_lock:
            previous = self._subscriptions
            was_v1 = self._subscribed
            was_v2 = self._v2_subscribed
            self._subscriptions = set(subscriptions)
            self._subscribed = v1
            self._v2_subscribed = v2
            latest_payload = self._latest_payload
            latest_v2_payload = self._latest_v2_payload
            v1_queue = self._queue
            v2_queue = self._v2_queue
            v2_shot_started = shot_v2 in subscriptions and shot_v2 not in previous
            pending_catch_up = None
            if v2_shot_started or not v2:
                # Consumed by the subscription it was waiting for, or discarded
                # with the connection that asked for it.
                pending_catch_up = self._pending_v2_catch_up if v2_shot_started else None
                self._pending_v2_catch_up = None

        if v1 != was_v1:
            logger.info("[BLE] Client %s (v1)", "subscribed" if v1 else "unsubscribed")
        if v2 != was_v2:
            logger.info("[BLE] Client %s (schema v2)", "subscribed" if v2 else "unsubscribed")
        if was_v1 and not v1:
            self._clear_queue(v1_queue)
        if was_v2 and not v2:
            self._clear_queue(v2_queue)

        # Replay the latest shot when its shot characteristic gains a
        # subscriber, so a phone that subscribes to control first still gets it.
        # A v2 phone that already sent ``hello`` gets its catch-up instead,
        # which holds the latest shot unless the phone already had it.
        if shot_v1 in subscriptions and shot_v1 not in previous and latest_payload is not None:
            self._enqueue_payload(latest_payload)
        if v2_shot_started:
            if pending_catch_up is not None:
                self._start_v2_catch_up(pending_catch_up)
            elif latest_v2_payload is not None:
                self._enqueue_v2_payload(latest_v2_payload)

    def _enqueue_payload(self, payload: bytes) -> None:
        with self._state_lock:
            queue = self._queue
            subscribed = self._subscribed
        if subscribed:
            self._offer(queue, payload)

    def _enqueue_v2_payload(self, payload: bytes) -> None:
        with self._state_lock:
            queue = self._v2_queue
            subscribed = self._v2_subscribed
        if subscribed:
            self._offer(queue, payload)

    async def _load_v2_catch_up(self, last_event_id) -> list[bytes] | None:
        """The shots a v2 client missed, or ``None`` when catch-up is unavailable."""
        provider = self.catch_up_provider
        if provider is None:
            return None
        try:
            entries = await asyncio.to_thread(provider, last_event_id)
            return [payload for _event_id, payload in entries]
        except Exception:  # pylint: disable=broad-exception-caught
            # Never fail ``hello`` over catch-up: the phone keeps the
            # latest-shot replay it had before catch-up existed.
            logger.warning("[BLE] Could not load missed shots for catch-up", exc_info=True)
            return None

    def _schedule_v2_catch_up(self, payloads: list[bytes] | None) -> None:
        """Send catch-up now if the v2 shot characteristic is subscribed, else on subscribe.

        ``None`` (no catch-up for this command) does nothing.
        """
        if payloads is None:
            return
        shot_v2 = SHOT_V2_CHARACTERISTIC_UUID.lower()
        with self._state_lock:
            ready = shot_v2 in self._subscriptions
            if not ready:
                self._pending_v2_catch_up = payloads
        logger.info(
            "[BLE] Catch-up: %d missed shot(s) for schema v2 client%s",
            len(payloads),
            "" if ready else " (sent when it subscribes to shots)",
        )
        if ready:
            self._start_v2_catch_up(payloads)

    def _start_v2_catch_up(self, payloads: list[bytes]) -> None:
        with self._state_lock:
            loop = self._loop
        if loop is not None and payloads:
            asyncio.run_coroutine_threadsafe(self._deliver_v2_catch_up(payloads), loop)

    async def _deliver_v2_catch_up(self, payloads: list[bytes]) -> None:
        """Queue catch-up shots in order, waiting for room instead of dropping any."""
        for payload in payloads:
            with self._state_lock:
                queue = self._v2_queue
                subscribed = self._v2_subscribed
            if queue is None or not subscribed:
                return
            await queue.put(payload)

    @staticmethod
    def _offer(queue: asyncio.Queue[bytes] | None, payload: bytes) -> None:
        if queue is None:
            return
        if queue.full():
            try:
                queue.get_nowait()
                queue.task_done()
                logger.warning("[BLE] Delivery queue full; dropped oldest unsent shot")
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(payload)

    @staticmethod
    def _clear_queue(queue: asyncio.Queue[bytes] | None) -> None:
        if queue is None:
            return
        while True:
            try:
                queue.get_nowait()
                queue.task_done()
            except asyncio.QueueEmpty:
                return

    async def _delivery_worker(self) -> None:
        with self._state_lock:
            queue = self._queue
        await self._drain(queue, self._send_payload)

    async def _v2_delivery_worker(self) -> None:
        with self._state_lock:
            queue = self._v2_queue
        await self._drain(queue, self._send_v2_payload)

    @staticmethod
    async def _drain(queue: asyncio.Queue[bytes] | None, send) -> None:
        if queue is None:
            return
        while True:
            payload = await queue.get()
            try:
                await send(payload)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("[BLE] Failed to notify shot payload", exc_info=True)
            finally:
                queue.task_done()

    async def _send_payload(self, payload: bytes) -> None:
        with self._state_lock:
            sequence = self._sequence
            self._sequence = (self._sequence + 1) & 0xFFFF
        await self._notify_frames(SHOT_CHARACTERISTIC_UUID, payload, sequence, v2=False)

    async def _send_v2_payload(self, payload: bytes) -> None:
        with self._state_lock:
            sequence = self._v2_sequence
            self._v2_sequence = (self._v2_sequence + 1) & 0xFFFF
        await self._notify_frames(SHOT_V2_CHARACTERISTIC_UUID, payload, sequence, v2=True)

    def _gate_open(self, v2: bool) -> bool:
        with self._state_lock:
            return self._v2_subscribed if v2 else self._subscribed

    async def _notify_frames(
        self,
        characteristic_uuid: str,
        payload: bytes,
        sequence: int,
        *,
        v2: bool,
    ) -> None:
        with self._state_lock:
            server = self._server
        if server is None or not self._gate_open(v2):
            return

        characteristic = server.get_characteristic(characteristic_uuid)
        if characteristic is None:
            raise RuntimeError(f"BLE characteristic {characteristic_uuid} is unavailable")

        for frame in fragment_payload(payload, sequence=sequence):
            if not self._gate_open(v2):
                return
            characteristic.value = bytearray(frame)
            if not server.update_value(SERVICE_UUID, characteristic_uuid):
                raise RuntimeError("BLE notification update failed")
            await asyncio.sleep(self.fragment_interval_s)

    def _on_write_request(self, characteristic, value, **_kwargs) -> None:
        """Receive one framed phone control command from a writable GATT value."""
        characteristic_uuid = _characteristic_uuid(characteristic)
        reassembler = self._control_reassemblers.get(characteristic_uuid)
        if reassembler is None:
            return
        characteristic.value = bytearray(value)
        try:
            payload = reassembler.append(bytes(value))
        except ValueError:
            logger.warning("[BLE] Rejected malformed control frame", exc_info=True)
            reassembler.reset()
            return
        if payload is None:
            return

        with self._state_lock:
            loop = self._loop
        if loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self._process_control_payload(payload, characteristic_uuid),
            loop,
        )

    async def _process_control_payload(
        self,
        payload: bytes,
        characteristic_uuid: str = CONTROL_CHARACTERISTIC_UUID,
    ) -> None:
        characteristic_uuid = characteristic_uuid.lower()
        response_schema, accepted_schemas = _CONTROL_SCHEMAS[characteristic_uuid]
        v2 = response_schema == SCHEMA_VERSION_V2
        handler = self.command_handler_v2 if v2 else self.command_handler
        request_id = "unknown"
        catch_up: list[bytes] | None = None
        try:
            command = json.loads(payload)
            if not isinstance(command, dict):
                raise ValueError("Control command must be a JSON object")
            request_id = command.get("request_id")
            command_type = command.get("type")
            command_payload = command.get("payload")
            if command.get("schema_version") not in accepted_schemas:
                raise ValueError("Unsupported control schema version")
            if not isinstance(request_id, str) or not request_id:
                raise ValueError("Control command requires a request_id")
            if not isinstance(command_type, str) or not command_type:
                raise ValueError("Control command requires a type")
            if not isinstance(command_payload, dict):
                raise ValueError("Control command payload must be an object")

            if command_type == "hello":
                response, catch_up = await self._answer_hello(
                    request_id, command_payload, response_schema
                )
            else:
                if handler is None:
                    raise ValueError("Phone controls are not configured on this OpenFlight server")
                result, status = await asyncio.to_thread(handler, command_type, command_payload)
                if status < 200 or status >= 300:
                    error = result.get("error", f"Control command failed with status {status}")
                    response = self._control_response(
                        request_id, error=str(error), schema_version=response_schema
                    )
                else:
                    response = self._control_response(
                        request_id, result=result, schema_version=response_schema
                    )
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            response = self._control_response(
                str(request_id or "unknown"), error=str(error), schema_version=response_schema
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("[BLE] Phone control command failed")
            response = self._control_response(
                str(request_id or "unknown"),
                error="OpenFlight could not apply the phone command",
                schema_version=response_schema,
            )

        encoded = encode_message_v2(response) if v2 else encode_message(response)
        await self._send_control_response(encoded, characteristic_uuid)
        # After the answer, so a client sees ``hello`` succeed before shots arrive.
        self._schedule_v2_catch_up(catch_up)

    async def _answer_hello(
        self,
        request_id: str,
        command_payload: Mapping,
        response_schema: int,
    ) -> tuple[dict, list[bytes] | None]:
        """Negotiate the transport and load a v2 client's catch-up shots.

        Transport negotiation is answered by the publisher itself: it is about
        which characteristics exist, not server state.
        """
        result = build_hello_result(command_payload.get("client_schema_max"))
        response = self._control_response(request_id, result=result, schema_version=response_schema)
        if result["schema_version"] < SCHEMA_VERSION_V2:
            return response, None
        return response, await self._load_v2_catch_up(command_payload.get("last_event_id"))

    @staticmethod
    def _control_response(
        request_id: str,
        *,
        result: Mapping[str, Any] | None = None,
        error: str | None = None,
        schema_version: int = SCHEMA_VERSION,
    ) -> dict:
        return build_control_response(
            request_id, result=result, error=error, schema_version=schema_version
        )

    async def _send_control_response(
        self,
        payload: bytes,
        characteristic_uuid: str = CONTROL_CHARACTERISTIC_UUID,
    ) -> None:
        key = characteristic_uuid.lower()
        lock = self._control_send_locks.get(key)
        if lock is None:
            lock = self._control_send_locks[key] = asyncio.Lock()
        async with lock:
            await self._send_control_payload(payload, key)

    async def _send_control_payload(
        self,
        payload: bytes,
        characteristic_uuid: str = CONTROL_CHARACTERISTIC_UUID,
    ) -> None:
        """Send one complete control message without interleaving fragments."""
        key = characteristic_uuid.lower()
        v2 = key in _V2_CHARACTERISTICS
        # Notify with the spelling the characteristic was registered with.
        canonical = CONTROL_V2_CHARACTERISTIC_UUID if v2 else CONTROL_CHARACTERISTIC_UUID
        with self._state_lock:
            server = self._server
            sequence = self._control_sequences[key]
            self._control_sequences[key] = (sequence + 1) & 0xFFFF
        if server is None or not self._gate_open(v2):
            return

        characteristic = server.get_characteristic(canonical)
        if characteristic is None:
            raise RuntimeError("BLE control characteristic is unavailable")
        for frame in fragment_payload(payload, sequence=sequence):
            characteristic.value = bytearray(frame)
            if not server.update_value(SERVICE_UUID, canonical):
                raise RuntimeError("BLE control notification update failed")
            await asyncio.sleep(self.fragment_interval_s)
