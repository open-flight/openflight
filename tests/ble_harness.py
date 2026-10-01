"""Loopback BLE harness: a fake Bless/BlueZ server plus virtual centrals.

``BleLoopback`` runs the real ``BleShotPublisher`` on its own thread and event
loop, exactly as ``--ble`` does, but with a fake ``bless`` module. The fake
server behaves like Bless 0.3.0 on BlueZ where it matters to the publisher:

* ``app.StartNotify``/``app.StopNotify`` are called *before* the
  characteristic is added to (or removed from) ``app.subscribed_characteristics``.
* ``update_value`` notifies the characteristic's current value, and BlueZ
  delivers it to every central subscribed to that characteristic (and only
  those), which ``VirtualCentral`` models.
* Writes arrive through ``write_request_func(characteristic, value)``.

A ``VirtualCentral`` subscribes, writes framed commands and reassembles the
frames it is notified with using the real protocol reassembler. No Bluetooth
stack, radio or ``bless`` install is needed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import threading
import time
import types
import uuid
from enum import IntFlag

from openflight.ble.protocol import (
    CONTROL_CHARACTERISTIC_UUID,
    SCHEMA_VERSION,
    SHOT_CHARACTERISTIC_UUID,
    FragmentReassembler,
    fragment_payload,
)
from openflight.ble.publisher import BleShotPublisher


def normalize(value: str) -> str:
    return str(uuid.UUID(str(value))).lower()


class _Properties(IntFlag):
    notify = 1
    write = 2


class _Permissions(IntFlag):
    readable = 1
    writeable = 2


class FakeCharacteristic:
    def __init__(self, char_uuid: str, properties, permissions):
        # Bless normalizes UUIDs to lowercase strings.
        self.uuid = normalize(char_uuid)
        self.properties = properties
        self.permissions = permissions
        self.value = bytearray()


class FakeBlueZApplication:
    """The slice of Bless's ``BlueZGattApplication`` the publisher touches."""

    def __init__(self):
        self.subscribed_characteristics: list[str] = []
        # Bless 0.3.0 installs no-ops here; the publisher replaces them.
        self.StartNotify = lambda _session: None  # pylint: disable=invalid-name
        self.StopNotify = lambda _session: None  # pylint: disable=invalid-name


class FakeBlessServer:
    """Fake ``bless.BlessServer`` that records notifications per characteristic."""

    instances: list["FakeBlessServer"] = []

    def __init__(self, *, name, loop, on_subscribe=None, on_unsubscribe=None, **_kwargs):
        self.name = name
        self.loop = loop
        self.on_subscribe = on_subscribe
        self.on_unsubscribe = on_unsubscribe
        self.app = FakeBlueZApplication()
        self.characteristics: dict[str, FakeCharacteristic] = {}
        self.write_request_func = None
        self.started = threading.Event()
        self.stopped = threading.Event()
        self.listeners: list = []
        FakeBlessServer.instances.append(self)

    async def add_new_service(self, _service_uuid):
        return None

    async def add_new_characteristic(
        self, _service_uuid, char_uuid, properties, _value, permissions
    ):
        characteristic = FakeCharacteristic(char_uuid, properties, permissions)
        self.characteristics[characteristic.uuid] = characteristic

    async def start(self):
        self.started.set()

    async def stop(self):
        self.stopped.set()

    def get_characteristic(self, char_uuid):
        return self.characteristics.get(normalize(char_uuid))

    def update_value(self, _service_uuid, char_uuid) -> bool:
        key = normalize(char_uuid)
        frame = bytes(self.characteristics[key].value)
        for listener in list(self.listeners):
            listener(key, frame)
        return True

    # -- driven from the central side, always on the publisher's loop ------

    def start_notify(self, char_uuid: str) -> None:
        """What BlueZ does when the first central enables a characteristic's CCCD."""
        self.app.StartNotify(None)
        self.app.subscribed_characteristics.append(normalize(char_uuid))

    def stop_notify(self, char_uuid: str) -> None:
        """What BlueZ does when the last central disables a characteristic's CCCD."""
        self.app.StopNotify(None)
        self.app.subscribed_characteristics.remove(normalize(char_uuid))

    def write(self, char_uuid: str, value: bytes) -> None:
        self.write_request_func(self.characteristics[normalize(char_uuid)], bytearray(value))


def fake_bless_module() -> types.ModuleType:
    module = types.ModuleType("bless")
    module.BlessServer = FakeBlessServer
    module.GATTCharacteristicProperties = _Properties
    module.GATTAttributePermissions = _Permissions
    return module


class BleLoopback:
    """Run a real ``BleShotPublisher`` against ``FakeBlessServer`` in-process."""

    def __init__(self, monkeypatch, **publisher_kwargs):
        monkeypatch.setitem(sys.modules, "bless", fake_bless_module())
        FakeBlessServer.instances.clear()
        publisher_kwargs.setdefault("fragment_interval_s", 0)
        self.publisher = BleShotPublisher(**publisher_kwargs)
        self.server: FakeBlessServer | None = None
        self.centrals: list[VirtualCentral] = []
        self._subscribers: dict[str, int] = {}
        self._lock = threading.Lock()

    def __enter__(self) -> "BleLoopback":
        self.publisher.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if FakeBlessServer.instances and FakeBlessServer.instances[0].started.is_set():
                break
            time.sleep(0.005)
        else:
            raise AssertionError("fake BLE server never started")
        self.server = FakeBlessServer.instances[0]
        self.server.listeners.append(self._deliver)
        # Let the publisher finish its own post-start bookkeeping.
        self.run_on_loop(lambda: None)
        return self

    def __exit__(self, *_exc):
        self.publisher.stop()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        return self.server.loop

    def run_on_loop(self, callback) -> None:
        """Run ``callback`` on the publisher's loop and wait for queued follow-ups."""
        done = threading.Event()

        def call():
            callback()
            # One more turn so ``call_soon`` work scheduled by the callback runs.
            self.loop.call_soon(done.set)

        self.loop.call_soon_threadsafe(call)
        assert done.wait(5), "publisher loop did not run the callback"

    def central(self, name: str = "central") -> "VirtualCentral":
        central = VirtualCentral(self, name)
        self.centrals.append(central)
        return central

    def _deliver(self, char_uuid: str, frame: bytes) -> None:
        for central in list(self.centrals):
            central.receive(char_uuid, frame)

    def _subscribe(self, char_uuid: str) -> None:
        key = normalize(char_uuid)
        with self._lock:
            count = self._subscribers.get(key, 0)
            self._subscribers[key] = count + 1
        if count == 0:
            self.run_on_loop(lambda: self.server.start_notify(key))

    def _unsubscribe(self, char_uuid: str) -> None:
        key = normalize(char_uuid)
        with self._lock:
            count = self._subscribers.get(key, 0) - 1
            self._subscribers[key] = max(count, 0)
        if count == 0:
            self.run_on_loop(lambda: self.server.stop_notify(key))

    def write(self, char_uuid: str, frame: bytes) -> None:
        self.run_on_loop(lambda: self.server.write(char_uuid, frame))


class VirtualCentral:
    """A phone: subscribes, writes framed commands, reassembles notifications."""

    def __init__(self, loopback: BleLoopback, name: str):
        self.loopback = loopback
        self.name = name
        self.subscriptions: set[str] = set()
        self._reassemblers: dict[str, FragmentReassembler] = {}
        self.frames: dict[str, list[bytes]] = {}
        self.messages: dict[str, list[bytes]] = {}
        self._condition = threading.Condition()
        self._write_sequence = 0

    # -- GATT operations ----------------------------------------------------

    def subscribe(self, *char_uuids: str) -> None:
        for char_uuid in char_uuids:
            key = normalize(char_uuid)
            if key not in self.subscriptions:
                self.subscriptions.add(key)
                self.loopback._subscribe(key)  # pylint: disable=protected-access

    def unsubscribe(self, *char_uuids: str) -> None:
        for char_uuid in char_uuids:
            key = normalize(char_uuid)
            if key in self.subscriptions:
                self.subscriptions.discard(key)
                self.loopback._unsubscribe(key)  # pylint: disable=protected-access

    def disconnect(self) -> None:
        self.unsubscribe(*list(self.subscriptions))

    def write_payload(self, char_uuid: str, payload: bytes) -> None:
        for frame in fragment_payload(payload, sequence=self._write_sequence):
            self.loopback.write(char_uuid, frame)
        self._write_sequence = (self._write_sequence + 1) & 0xFFFF

    def command(
        self,
        command_type: str,
        payload: dict | None = None,
        *,
        control: str = CONTROL_CHARACTERISTIC_UUID,
        schema_version: int = SCHEMA_VERSION,
        request_id: str | None = None,
    ) -> str:
        request_id = request_id or str(uuid.uuid4())
        message = {
            "schema_version": schema_version,
            "type": command_type,
            "request_id": request_id,
            "payload": payload if payload is not None else {},
        }
        self.write_payload(control, json.dumps(message).encode("utf-8"))
        return request_id

    def request(self, command_type: str, payload: dict | None = None, **kwargs) -> dict:
        """Send a command and wait for the response carrying its request id."""
        control = kwargs.get("control", CONTROL_CHARACTERISTIC_UUID)
        request_id = self.command(command_type, payload, **kwargs)
        return self.wait_for(
            control,
            lambda message: message.get("request_id") == request_id,
        )

    # -- notifications --------------------------------------------------------

    def receive(self, char_uuid: str, frame: bytes) -> None:
        if char_uuid not in self.subscriptions:
            return  # BlueZ only notifies centrals that enabled this CCCD.
        with self._condition:
            self.frames.setdefault(char_uuid, []).append(frame)
            reassembler = self._reassemblers.setdefault(char_uuid, FragmentReassembler())
            message = reassembler.append(frame)
            if message is not None:
                self.messages.setdefault(char_uuid, []).append(message)
                self._condition.notify_all()

    def decoded(self, char_uuid: str) -> list[dict]:
        with self._condition:
            raw = list(self.messages.get(normalize(char_uuid), []))
        return [json.loads(item.decode("utf-8")) for item in raw]

    def raw(self, char_uuid: str) -> list[bytes]:
        with self._condition:
            return list(self.messages.get(normalize(char_uuid), []))

    def wait_for(self, char_uuid: str, predicate=lambda _message: True, timeout=5.0) -> dict:
        key = normalize(char_uuid)
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                for raw in self.messages.get(key, []):
                    message = json.loads(raw.decode("utf-8"))
                    if predicate(message):
                        return message
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(
                        f"{self.name} got no matching message on {key}; "
                        f"saw {self.messages.get(key, [])!r}"
                    )
                self._condition.wait(remaining)

    def wait_for_count(self, char_uuid: str, count: int, timeout=5.0) -> list[dict]:
        key = normalize(char_uuid)
        deadline = time.monotonic() + timeout
        with self._condition:
            while len(self.messages.get(key, [])) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(
                        f"{self.name} expected {count} messages on {key}; "
                        f"saw {self.messages.get(key, [])!r}"
                    )
                self._condition.wait(remaining)
        return self.decoded(key)


def settle(loopback: BleLoopback, rounds: int = 3) -> None:
    """Let queued notifications drain before asserting on their absence."""
    for _ in range(rounds):
        loopback.run_on_loop(lambda: None)
        time.sleep(0.02)


@contextlib.contextmanager
def server_loopback(monkeypatch, tmp_path):
    """A monitor-less OpenFlight server state with BLE on the loopback harness.

    The real server command dispatch answers commands; Socket.IO emits are
    recorded on ``loopback.emitted`` instead of sent.
    """
    from openflight import server as server_module  # pylint: disable=import-outside-toplevel
    from openflight.launch_monitor import ClubType  # pylint: disable=import-outside-toplevel
    from openflight.profiles import ProfileStore  # pylint: disable=import-outside-toplevel

    emitted = []
    lock = threading.Lock()

    def emit(event, payload=None, **_kwargs):
        with lock:
            emitted.append((event, payload))

    monkeypatch.setattr(server_module.socketio, "emit", emit)
    monkeypatch.setattr(server_module, "monitor", None)
    monkeypatch.setattr(server_module, "profile_store", ProfileStore(tmp_path / "profiles.json"))
    monkeypatch.setattr(server_module, "active_club", ClubType.DRIVER)
    monkeypatch.setattr(server_module, "iwr6843_runtime", None)
    monkeypatch.setattr(server_module, "power_monitor", None)
    monkeypatch.setattr(server_module, "kld7_vertical", None)
    monkeypatch.setattr(server_module, "kld7_horizontal", None)
    monkeypatch.setattr(server_module, "camera_capture_runtime", None)
    monkeypatch.setattr(server_module, "ball_speed_correction_enabled", False)
    monkeypatch.setattr(server_module, "calculated_spin_enabled", False)
    monkeypatch.setattr(server_module, "ballistics_enabled", False)
    monkeypatch.setattr(server_module, "debug_mode", False)
    monkeypatch.setattr(server_module, "sim_connectors", [])
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)

    loopback = BleLoopback(
        monkeypatch,
        command_handler=server_module.dispatch_phone_control_command,
    )
    with loopback:
        monkeypatch.setattr(server_module, "ble_publisher", loopback.publisher)
        loopback.emitted = emitted
        yield loopback


PHONE_UUIDS = (SHOT_CHARACTERISTIC_UUID, CONTROL_CHARACTERISTIC_UUID)
