#!/usr/bin/env python3
"""Probe which BLE advertising properties this Pi's Bluetooth controller accepts.

BlueZ reports every advertising failure as the same D-Bus error, "Failed to
register advertisement", so the real cause has to come from elsewhere. This
script registers the OpenFlight advertisement one property group at a time and
reports exactly which group BlueZ or the kernel rejects.

    uv run python scripts/hardware-test/test_ble_advertise.py

Run ``journalctl -u bluetooth -n 20 --no-pager`` afterwards to see bluetoothd's
own reason for any failure. Two failures are common on Raspberry Pi hardware:

* ``Invalid Parameters (0x0d)`` after adding TxPower/MinInterval/MaxInterval:
  the controller has no LE Extended Advertising, so the kernel refuses the
  MGMT_ADV_PARAM_* flags BlueZ derives from those properties.
* ``Invalid Parameters (0x0d)`` after adding LocalName: advertising data does
  not fit in the legacy 31-byte budget alongside the 128-bit service UUID.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

# pylint: disable=wrong-import-position,invalid-name
from dbus_next import BusType  # noqa: E402
from dbus_next.aio import MessageBus  # noqa: E402
from dbus_next.constants import PropertyAccess  # noqa: E402
from dbus_next.errors import DBusError  # noqa: E402
from dbus_next.service import ServiceInterface, dbus_property, method  # noqa: E402

from openflight.ble.protocol import SERVICE_UUID  # noqa: E402

ADAPTER_PATH = "/org/bluez/hci0"
LOCAL_NAME = "OpenFlight"

# Advertising data budget for a legacy (non-extended) advertisement.
LEGACY_AD_BUDGET = 31
AD_ELEMENT_OVERHEAD = 2  # one length byte plus one AD type byte


class ProbeAdvertisement(ServiceInterface):
    """Minimal org.bluez.LEAdvertisement1 mirroring Bless 0.3.0's properties."""

    def __init__(self, path: str):
        self.path = path
        super().__init__("org.bluez.LEAdvertisement1")

    @method()
    def Release(self):  # noqa: N802
        """Called by BlueZ when it drops the advertisement."""

    @dbus_property(access=PropertyAccess.READ)
    def Type(self) -> "s":  # type: ignore[valid-type] # noqa: F821 N802
        return "peripheral"

    @dbus_property(access=PropertyAccess.READ)
    def ServiceUUIDs(self) -> "as":  # type: ignore[valid-type] # noqa: F722 N802
        return [SERVICE_UUID]

    @dbus_property(access=PropertyAccess.READ)
    def LocalName(self) -> "s":  # type: ignore[valid-type] # noqa: F821 N802
        return LOCAL_NAME

    @dbus_property(access=PropertyAccess.READ)
    def TxPower(self) -> "n":  # type: ignore[valid-type] # noqa: F821 N802
        return 20

    @dbus_property(access=PropertyAccess.READ)
    def MinInterval(self) -> "u":  # type: ignore[valid-type] # noqa: F821 N802
        return 100

    @dbus_property(access=PropertyAccess.READ)
    def MaxInterval(self) -> "u":  # type: ignore[valid-type] # noqa: F821 N802
        return 100


OPTIONAL_PROPERTIES = ("ServiceUUIDs", "LocalName", "TxPower", "MinInterval", "MaxInterval")

# Each variant lists the properties hidden from BlueZ. Later variants add back
# what earlier ones withheld, so the first failure names the culprit. The first
# variant carries no advertising data at all, which separates "this controller
# rejects everything" from "this payload is too large" from "this property is
# unsupported".
VARIANTS = (
    ("no advertising data", OPTIONAL_PROPERTIES),
    ("service UUID only", ("LocalName", "TxPower", "MinInterval", "MaxInterval")),
    ("service UUID + LocalName", ("TxPower", "MinInterval", "MaxInterval")),
    ("service UUID + LocalName + TxPower", ("MinInterval", "MaxInterval")),
    ("Bless 0.3.0 defaults (all properties)", ()),
)


def _apply_variant(hidden: tuple[str, ...]) -> None:
    for name in OPTIONAL_PROPERTIES:
        getattr(ProbeAdvertisement, name).disabled = name in hidden


def _estimate_ad_length(hidden: tuple[str, ...]) -> int:
    length = 0
    if "ServiceUUIDs" not in hidden:
        length += AD_ELEMENT_OVERHEAD + 16  # complete list of 128-bit service UUIDs
    if "LocalName" not in hidden:
        length += AD_ELEMENT_OVERHEAD + len(LOCAL_NAME)
    return length


async def _describe_adapter(bus: MessageBus) -> bool:
    introspection = await bus.introspect("org.bluez", ADAPTER_PATH)
    proxy = bus.get_proxy_object("org.bluez", ADAPTER_PATH, introspection)
    adapter = proxy.get_interface("org.bluez.Adapter1")
    powered = await adapter.get_powered()
    print(f"Adapter {ADAPTER_PATH}: powered={powered}")

    manager = proxy.get_interface("org.bluez.LEAdvertisingManager1")
    supported = await manager.get_supported_instances()
    active = await manager.get_active_instances()
    print(f"Advertising instances: {active} active, {supported} free")
    if not powered:
        print("\nAdapter is powered off. Run 'bluetoothctl power on' and retry.")
    return bool(powered)


async def _try_variant(bus: MessageBus, index: int, label: str, hidden: tuple[str, ...]) -> bool:
    _apply_variant(hidden)
    advertisement = ProbeAdvertisement(f"/org/openflight/probe/advertisement{index}")
    bus.export(advertisement.path, advertisement)

    introspection = await bus.introspect("org.bluez", ADAPTER_PATH)
    proxy = bus.get_proxy_object("org.bluez", ADAPTER_PATH, introspection)
    manager = proxy.get_interface("org.bluez.LEAdvertisingManager1")

    estimate = _estimate_ad_length(hidden)
    budget = f"~{estimate}/{LEGACY_AD_BUDGET} bytes of advertising data"
    try:
        await manager.call_register_advertisement(advertisement.path, {})
    except DBusError as exc:
        print(f"  [FAIL] {label} ({budget}): {exc.text}")
        bus.unexport(advertisement.path, advertisement)
        return False

    print(f"  [ OK ] {label} ({budget})")
    await asyncio.sleep(0.5)
    try:
        await manager.call_unregister_advertisement(advertisement.path)
    except DBusError as exc:
        print(f"         (could not unregister: {exc.text})")
    bus.unexport(advertisement.path, advertisement)
    return True


async def main() -> int:
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        if not await _describe_adapter(bus):
            return 1

        print("\nRegistering advertisement variants:")
        results = []
        for index, (label, hidden) in enumerate(VARIANTS, start=1):
            results.append((label, await _try_variant(bus, index, label, hidden)))

        print()
        if all(ok for _, ok in results):
            print("All variants accepted. Bless should advertise as-is on this Pi.")
            return 0

        first_failure = next(label for label, ok in results if not ok)
        print(f"First rejected variant: {first_failure}")
        if first_failure == "no advertising data":
            print("An advertisement carrying no data was rejected, so neither the")
            print("payload size nor any single property is the cause. Suspect the")
            print("adapter, BlueZ, or the kernel's advertising path itself.")
        elif first_failure == "service UUID only":
            print("18 bytes of advertising data were rejected while an empty")
            print("advertisement was accepted, which points at data-length")
            print("validation rather than at an unsupported property.")
        elif first_failure == "service UUID + LocalName":
            print("The service UUID and the name do not both fit; shorten the name")
            print("passed to BleShotPublisher in server.py.")
        else:
            print("An advertising parameter this controller cannot honor is the")
            print("cause. OpenFlight already hides TxPower/MinInterval/MaxInterval.")
        print("\nCompare against bluetoothd's own reason:")
        print("  journalctl -u bluetooth -n 20 --no-pager")
        return 1
    finally:
        bus.disconnect()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
