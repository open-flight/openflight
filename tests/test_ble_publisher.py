"""Tests for BLE delivery isolation, buffering, and fragmentation."""

import asyncio
import json
import logging
import sys
import types
from enum import IntFlag

from openflight.ble.protocol import (
    CONTROL_CHARACTERISTIC_UUID,
    SHOT_CHARACTERISTIC_UUID,
    fragment_payload,
    reassemble_fragments,
)
from openflight.ble.publisher import BleShotPublisher


def _shot_data(ball_speed=150.0):
    return {
        "timestamp": "2026-07-29T19:42:10",
        "club": "driver",
        "ball_speed_mph": ball_speed,
        "club_speed_mph": None,
        "smash_factor": None,
        "estimated_carry_yards": 250,
        "launch_angle_vertical": None,
        "launch_angle_horizontal": None,
        "spin_rpm": None,
        "club_path_deg": None,
        "spin_axis_deg": None,
    }


class _Characteristic:
    value = bytearray()


class _Server:
    def __init__(self):
        self.characteristic = _Characteristic()
        self.notifications = []

    def get_characteristic(self, uuid):
        assert uuid == SHOT_CHARACTERISTIC_UUID
        return self.characteristic

    def update_value(self, _service_uuid, _characteristic_uuid):
        self.notifications.append(bytes(self.characteristic.value))
        return True


class _BlueZApplication:
    """Match the subscription hooks exposed by Bless's Linux backend."""

    def __init__(self):
        self.StartNotify = lambda _session: None
        self.StopNotify = lambda _session: None


class _BlueZServer:
    def __init__(self):
        self.app = _BlueZApplication()


class _ControlCharacteristic:
    uuid = CONTROL_CHARACTERISTIC_UUID
    value = bytearray()


class _ControlServer:
    def __init__(self):
        self.characteristic = _ControlCharacteristic()
        self.notifications = []

    def get_characteristic(self, uuid):
        assert uuid == CONTROL_CHARACTERISTIC_UUID
        return self.characteristic

    def update_value(self, service_uuid, characteristic_uuid):
        self.notifications.append(
            (service_uuid, characteristic_uuid, bytes(self.characteristic.value))
        )
        return True


def test_linux_runtime_uses_bless_writeable_permission(monkeypatch):
    """Bless 0.3.0 spells the attribute permission ``writeable``."""

    class Properties(IntFlag):
        notify = 1
        write = 2

    class Permissions(IntFlag):
        readable = 1
        writeable = 2

    class FakeBlessServer:
        instance = None

        def __init__(self, **_kwargs):
            self.characteristics = []
            self.app = None
            FakeBlessServer.instance = self

        async def add_new_service(self, _uuid):
            return None

        async def add_new_characteristic(
            self, service_uuid, characteristic_uuid, properties, value, permissions
        ):
            self.characteristics.append(
                (service_uuid, characteristic_uuid, properties, value, permissions)
            )

        async def start(self):
            return None

        async def stop(self):
            return None

    fake_bless = types.ModuleType("bless")
    fake_bless.BlessServer = FakeBlessServer
    fake_bless.GATTCharacteristicProperties = Properties
    fake_bless.GATTAttributePermissions = Permissions
    monkeypatch.setitem(sys.modules, "bless", fake_bless)

    async def run():
        publisher = BleShotPublisher()
        publisher._stop_requested.set()  # pylint: disable=protected-access
        await publisher._run()  # pylint: disable=protected-access

    asyncio.run(run())

    control = next(
        item
        for item in FakeBlessServer.instance.characteristics
        if item[1] == CONTROL_CHARACTERISTIC_UUID
    )
    assert control[4] == Permissions.readable | Permissions.writeable


def test_disconnected_publish_retains_only_latest_payload():
    publisher = BleShotPublisher()

    assert publisher.publish(_shot_data(140.0))
    assert publisher.publish(_shot_data(151.0))

    latest = json.loads(publisher._latest_payload)  # pylint: disable=protected-access
    assert latest["ball_speed_mph"] == 151.0


def test_invalid_shot_does_not_raise_into_caller():
    publisher = BleShotPublisher()

    assert publisher.publish({}) is False


def test_delivery_uses_bounded_frames_and_round_trips():
    async def run():
        publisher = BleShotPublisher(fragment_interval_s=0)
        server = _Server()
        publisher._server = server  # pylint: disable=protected-access
        publisher._subscribed = True  # pylint: disable=protected-access

        payload = b'{"schema_version":1,"event_id":"shot-1","ball_speed_mph":150.0}'
        await publisher._send_payload(payload)  # pylint: disable=protected-access

        assert reassemble_fragments(server.notifications) == payload
        assert max(map(len, server.notifications)) <= 20

    asyncio.run(run())


def test_queue_overflow_drops_oldest_unsent_payload():
    async def run():
        publisher = BleShotPublisher(queue_size=2)
        publisher._queue = asyncio.Queue(maxsize=2)  # pylint: disable=protected-access
        publisher._subscribed = True  # pylint: disable=protected-access

        publisher._enqueue_payload(b"first")  # pylint: disable=protected-access
        publisher._enqueue_payload(b"second")  # pylint: disable=protected-access
        publisher._enqueue_payload(b"third")  # pylint: disable=protected-access

        assert publisher._queue.qsize() == 2  # pylint: disable=protected-access
        assert publisher._queue.get_nowait() == b"second"  # pylint: disable=protected-access
        assert publisher._queue.get_nowait() == b"third"  # pylint: disable=protected-access

    asyncio.run(run())


def test_unsubscribe_clears_backlog_but_keeps_latest_payload():
    async def run():
        publisher = BleShotPublisher(queue_size=2)
        publisher._queue = asyncio.Queue(maxsize=2)  # pylint: disable=protected-access
        publisher._subscribed = True  # pylint: disable=protected-access
        publisher.publish(_shot_data())
        publisher._enqueue_payload(b"queued")  # pylint: disable=protected-access

        publisher._on_unsubscribe(None, None)  # pylint: disable=protected-access

        assert not publisher.subscribed
        assert publisher._queue.empty()  # pylint: disable=protected-access
        assert publisher._latest_payload is not None  # pylint: disable=protected-access

    asyncio.run(run())


def test_bluez_subscription_hooks_update_publisher_state_and_replay_latest():
    """Bless 0.3.0 ignores constructor subscription callbacks on Linux."""

    async def run():
        publisher = BleShotPublisher()
        publisher._queue = asyncio.Queue(maxsize=2)  # pylint: disable=protected-access
        publisher.publish(_shot_data(151.0))
        server = _BlueZServer()

        publisher._install_bluez_subscription_hooks(server)  # pylint: disable=protected-access
        server.app.StartNotify(None)

        assert publisher.subscribed
        assert publisher._queue.get_nowait() == publisher._latest_payload  # pylint: disable=protected-access

        server.app.StopNotify(None)

        assert not publisher.subscribed

    asyncio.run(run())


def test_background_startup_failure_is_isolated(caplog):
    class FailingPublisher(BleShotPublisher):
        async def _run(self):
            raise RuntimeError("no BlueZ adapter")

    publisher = FailingPublisher()
    with caplog.at_level(logging.WARNING):
        publisher.start()
        publisher._thread.join(timeout=2)  # pylint: disable=protected-access

    assert not publisher._thread.is_alive()  # pylint: disable=protected-access
    assert "shot recording will continue without BLE" in caplog.text


def test_control_write_reassembles_command_and_notifies_response():
    async def run():
        received = []

        def handler(command_type, payload):
            received.append((command_type, payload))
            return {
                "status": "applied",
                "persistent": True,
                "configured_iwr_tilt_deg": 12.25,
            }, 200

        publisher = BleShotPublisher(command_handler=handler, fragment_interval_s=0)
        publisher._loop = asyncio.get_running_loop()  # pylint: disable=protected-access
        publisher._server = _ControlServer()  # pylint: disable=protected-access
        publisher._subscribed = True  # pylint: disable=protected-access
        command = {
            "schema_version": 1,
            "type": "iwr6843_orientation_calibration",
            "request_id": "request-1",
            "payload": {"mount_tilt_deg": 12.25},
        }

        for frame in fragment_payload(json.dumps(command).encode(), sequence=7):
            publisher._on_write_request(  # pylint: disable=protected-access
                publisher._server.characteristic,  # pylint: disable=protected-access
                bytearray(frame),
            )

        for _ in range(100):
            if publisher._server.notifications:  # pylint: disable=protected-access
                break
            await asyncio.sleep(0.01)

        assert received == [("iwr6843_orientation_calibration", {"mount_tilt_deg": 12.25})]
        updates = publisher._server.notifications  # pylint: disable=protected-access
        assert {item[1] for item in updates} == {CONTROL_CHARACTERISTIC_UUID}
        response = json.loads(reassemble_fragments(item[2] for item in updates))
        assert response == {
            "schema_version": 1,
            "request_id": "request-1",
            "ok": True,
            "result": {
                "status": "applied",
                "persistent": True,
                "configured_iwr_tilt_deg": 12.25,
            },
        }

    asyncio.run(run())


def test_control_write_dispatches_club_selection_command():
    async def run():
        received = []

        def handler(command_type, payload):
            received.append((command_type, payload))
            return {"status": "applied", "club": payload["club"]}, 200

        publisher = BleShotPublisher(command_handler=handler, fragment_interval_s=0)
        publisher._loop = asyncio.get_running_loop()  # pylint: disable=protected-access
        publisher._server = _ControlServer()  # pylint: disable=protected-access
        publisher._subscribed = True  # pylint: disable=protected-access
        command = {
            "schema_version": 1,
            "type": "set_club",
            "request_id": "club-request-1",
            "payload": {"club": "7-iron"},
        }

        for frame in fragment_payload(json.dumps(command).encode(), sequence=8):
            publisher._on_write_request(  # pylint: disable=protected-access
                publisher._server.characteristic,  # pylint: disable=protected-access
                bytearray(frame),
            )

        for _ in range(100):
            if publisher._server.notifications:  # pylint: disable=protected-access
                break
            await asyncio.sleep(0.01)

        assert received == [("set_club", {"club": "7-iron"})]
        updates = publisher._server.notifications  # pylint: disable=protected-access
        response = json.loads(reassemble_fragments(item[2] for item in updates))
        assert response == {
            "schema_version": 1,
            "request_id": "club-request-1",
            "ok": True,
            "result": {"status": "applied", "club": "7-iron"},
        }

    asyncio.run(run())


def test_publish_club_notifies_connected_clients_as_unsolicited_state():
    async def run():
        publisher = BleShotPublisher(fragment_interval_s=0)
        publisher._loop = asyncio.get_running_loop()  # pylint: disable=protected-access
        publisher._server = _ControlServer()  # pylint: disable=protected-access
        publisher._subscribed = True  # pylint: disable=protected-access

        assert publisher.publish_club("3-wood")

        for _ in range(100):
            if publisher._server.notifications:  # pylint: disable=protected-access
                break
            await asyncio.sleep(0.01)

        updates = publisher._server.notifications  # pylint: disable=protected-access
        payload = json.loads(reassemble_fragments(item[2] for item in updates))
        assert payload == {
            "schema_version": 1,
            "type": "club_changed",
            "club": "3-wood",
        }

    asyncio.run(run())
