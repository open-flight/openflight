"""Non-blocking BLE GATT publisher backed by Bless/BlueZ."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from collections.abc import Callable
from typing import Mapping

from .protocol import (
    ACCEPTED_REQUEST_SCHEMAS,
    CONTROL_CHARACTERISTIC_UUID,
    SERVICE_UUID,
    SHOT_CHARACTERISTIC_UUID,
    FragmentReassembler,
    build_club_event,
    build_control_response,
    build_hello_result,
    encode_message,
    encode_shot_event,
    fragment_payload,
)

logger = logging.getLogger(__name__)

CommandHandler = Callable[[str, Mapping], tuple[dict, int]]

_SHOT = SHOT_CHARACTERISTIC_UUID.lower()
_CONTROL = CONTROL_CHARACTERISTIC_UUID.lower()
_ALL_CHARACTERISTICS = frozenset({_SHOT, _CONTROL})


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
    """Publish shots and phone events without coupling the radar thread to Bluetooth.

    One GATT service holds a notify-only shot characteristic and a
    write-and-notify control characteristic for commands, responses and
    events. BlueZ notifies every subscribed central from one characteristic
    value, so every connected phone receives every message.
    """

    def __init__(
        self,
        *,
        name: str = "OpenFlight",
        queue_size: int = 8,
        fragment_interval_s: float = 0.01,
        command_handler: CommandHandler | None = None,
    ):
        if queue_size < 1:
            raise ValueError("BLE queue size must be at least one")
        self.name = name
        self.queue_size = queue_size
        self.fragment_interval_s = fragment_interval_s
        self.command_handler = command_handler

        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[bytes] | None = None
        self._stop_event: asyncio.Event | None = None
        self._stop_requested = threading.Event()
        self._server = None
        self._latest_payload: bytes | None = None
        # Follows BlueZ's per-characteristic subscriptions when those are
        # visible; any subscription opens delivery.
        self._subscribed = False
        self._subscriptions: set[str] = set()
        self._sequence = 0
        self._control_sequence = 0
        self._control_reassembler = FragmentReassembler()
        self._control_send_lock: asyncio.Lock | None = None

    @property
    def subscribed(self) -> bool:
        """Whether a central is subscribed to notifications."""
        with self._state_lock:
            return self._subscribed

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

    def publish_shot(
        self,
        shot_data: Mapping,
        *,
        final: bool,
        enrichment: Mapping | None = None,
    ) -> bool:
        """Store the latest shot (provisional or final) and notify subscribed centrals."""
        try:
            payload = encode_shot_event(shot_data, final=final, enrichment=enrichment)
            fragment_payload(payload, sequence=0)
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

    def publish_club(self, club: str) -> bool:
        """Notify connected centrals that the authoritative club changed."""
        try:
            event = build_club_event(club)
        except (TypeError, ValueError):
            logger.warning("[BLE] Failed to encode club payload", exc_info=True)
            return False
        return self.publish_event(event)

    def publish_event(self, event: Mapping) -> bool:
        """Notify subscribed centrals of one event on the control characteristic."""
        try:
            payload = encode_message(event)
            fragment_payload(payload, sequence=0)
        except (TypeError, ValueError):
            logger.warning("[BLE] Failed to encode event", exc_info=True)
            return False

        with self._state_lock:
            loop = self._loop
            subscribed = self._subscribed
        if loop and subscribed:
            asyncio.run_coroutine_threadsafe(self._send_control_response(payload), loop)
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
                self._stop_event = None
                self._server = None
                self._subscribed = False
                self._subscriptions = set()
                self._control_send_lock = None

    async def _run(self) -> None:
        # Bless is an optional dependency and must not affect non-BLE installs.
        from bless import (  # pylint: disable=import-error,import-outside-toplevel
            BlessServer,
            GATTAttributePermissions,
            GATTCharacteristicProperties,
        )

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=self.queue_size)
        stop_event = asyncio.Event()
        server = BlessServer(
            name=self.name,
            loop=loop,
            on_subscribe=self._on_subscribe,
            on_unsubscribe=self._on_unsubscribe,
        )
        await server.add_new_service(SERVICE_UUID)
        await server.add_new_characteristic(
            SERVICE_UUID,
            SHOT_CHARACTERISTIC_UUID,
            GATTCharacteristicProperties.notify,
            bytearray(),
            GATTAttributePermissions.readable,
        )
        await server.add_new_characteristic(
            SERVICE_UUID,
            CONTROL_CHARACTERISTIC_UUID,
            GATTCharacteristicProperties.write | GATTCharacteristicProperties.notify,
            bytearray(),
            GATTAttributePermissions.readable | GATTAttributePermissions.writeable,
        )
        server.write_request_func = self._on_write_request
        self._install_bluez_subscription_hooks(server)

        with self._state_lock:
            self._loop = loop
            self._queue = queue
            self._stop_event = stop_event
            self._server = server

        await server.start()
        logger.info("[BLE] Advertising %s", self.name)
        if self._stop_requested.is_set():
            stop_event.set()

        worker = asyncio.create_task(self._delivery_worker())
        try:
            await stop_event.wait()
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
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
            # The backend did not say which characteristic; assume both.
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
        subscribed = bool(subscriptions & _ALL_CHARACTERISTICS)
        with self._state_lock:
            previous = self._subscriptions
            was_subscribed = self._subscribed
            self._subscriptions = set(subscriptions)
            self._subscribed = subscribed
            latest_payload = self._latest_payload
            queue = self._queue
            shot_started = _SHOT in subscriptions and _SHOT not in previous

        if subscribed != was_subscribed:
            logger.info("[BLE] Client %s", "subscribed" if subscribed else "unsubscribed")
        if was_subscribed and not subscribed:
            self._clear_queue(queue)

        # Replay the latest shot when the shot characteristic gains a
        # subscriber, so a phone that subscribes to control first still gets it.
        if shot_started and latest_payload is not None:
            self._enqueue_payload(latest_payload)

    def _enqueue_payload(self, payload: bytes) -> None:
        with self._state_lock:
            queue = self._queue
            subscribed = self._subscribed
        if subscribed:
            self._offer(queue, payload)

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
        if queue is None:
            return
        while True:
            payload = await queue.get()
            try:
                await self._send_payload(payload)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("[BLE] Failed to notify shot payload", exc_info=True)
            finally:
                queue.task_done()

    async def _send_payload(self, payload: bytes) -> None:
        with self._state_lock:
            sequence = self._sequence
            self._sequence = (self._sequence + 1) & 0xFFFF
        await self._notify_frames(SHOT_CHARACTERISTIC_UUID, payload, sequence)

    async def _notify_frames(
        self,
        characteristic_uuid: str,
        payload: bytes,
        sequence: int,
    ) -> None:
        with self._state_lock:
            server = self._server
        if server is None or not self.subscribed:
            return

        characteristic = server.get_characteristic(characteristic_uuid)
        if characteristic is None:
            raise RuntimeError(f"BLE characteristic {characteristic_uuid} is unavailable")

        for frame in fragment_payload(payload, sequence=sequence):
            if not self.subscribed:
                return
            characteristic.value = bytearray(frame)
            if not server.update_value(SERVICE_UUID, characteristic_uuid):
                raise RuntimeError("BLE notification update failed")
            await asyncio.sleep(self.fragment_interval_s)

    def _on_write_request(self, characteristic, value, **_kwargs) -> None:
        """Receive one framed phone control command from a writable GATT value."""
        if _characteristic_uuid(characteristic) != _CONTROL:
            return
        characteristic.value = bytearray(value)
        try:
            payload = self._control_reassembler.append(bytes(value))
        except ValueError:
            logger.warning("[BLE] Rejected malformed control frame", exc_info=True)
            self._control_reassembler.reset()
            return
        if payload is None:
            return

        with self._state_lock:
            loop = self._loop
        if loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._process_control_payload(payload), loop)

    async def _process_control_payload(self, payload: bytes) -> None:
        request_id = "unknown"
        try:
            command = json.loads(payload)
            if not isinstance(command, dict):
                raise ValueError("Control command must be a JSON object")
            request_id = command.get("request_id")
            command_type = command.get("type")
            command_payload = command.get("payload")
            if command.get("schema_version") not in ACCEPTED_REQUEST_SCHEMAS:
                raise ValueError("Unsupported control schema version")
            if not isinstance(request_id, str) or not request_id:
                raise ValueError("Control command requires a request_id")
            if not isinstance(command_type, str) or not command_type:
                raise ValueError("Control command requires a type")
            if not isinstance(command_payload, dict):
                raise ValueError("Control command payload must be an object")

            if command_type == "hello":
                # Answered by the publisher itself: ``hello`` is about which
                # characteristics exist, not server state.
                result = build_hello_result(command_payload.get("client_schema_max"))
                response = build_control_response(request_id, result=result)
            else:
                if self.command_handler is None:
                    raise ValueError("Phone controls are not configured on this OpenFlight server")
                result, status = await asyncio.to_thread(
                    self.command_handler, command_type, command_payload
                )
                if status < 200 or status >= 300:
                    error = result.get("error", f"Control command failed with status {status}")
                    response = build_control_response(request_id, error=str(error))
                else:
                    response = build_control_response(request_id, result=result)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            response = build_control_response(str(request_id or "unknown"), error=str(error))
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("[BLE] Phone control command failed")
            response = build_control_response(
                str(request_id or "unknown"),
                error="OpenFlight could not apply the phone command",
            )

        await self._send_control_response(encode_message(response))

    async def _send_control_response(self, payload: bytes) -> None:
        """Send one complete control message without interleaving fragments."""
        # Created lazily on the publisher's event loop, where every send runs.
        if self._control_send_lock is None:
            self._control_send_lock = asyncio.Lock()
        async with self._control_send_lock:
            await self._send_control_payload(payload)

    async def _send_control_payload(self, payload: bytes) -> None:
        with self._state_lock:
            server = self._server
            sequence = self._control_sequence
            self._control_sequence = (sequence + 1) & 0xFFFF
        if server is None or not self.subscribed:
            return

        characteristic = server.get_characteristic(CONTROL_CHARACTERISTIC_UUID)
        if characteristic is None:
            raise RuntimeError("BLE control characteristic is unavailable")
        for frame in fragment_payload(payload, sequence=sequence):
            characteristic.value = bytearray(frame)
            if not server.update_value(SERVICE_UUID, CONTROL_CHARACTERISTIC_UUID):
                raise RuntimeError("BLE control notification update failed")
            await asyncio.sleep(self.fragment_interval_s)
