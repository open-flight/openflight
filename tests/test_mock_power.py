"""The simulated battery (``--battery mock``) for testing power status without a UPS."""

import argparse

import pytest

from openflight import server as server_module
from openflight.power import (
    SUPPORTED_BATTERY_PROVIDERS,
    BatteryProvider,
    PowerMonitor,
    PowerState,
    create_power_reader,
)
from openflight.power.providers import MockPowerReader


def _samples(reader, count):
    return [reader.read() for _ in range(count)]


def _fake_linux_battery(root):
    """A sysfs tree that LinuxPowerReader would happily read."""
    battery = root / "battery"
    battery.mkdir()
    (battery / "type").write_text("Battery\n", encoding="ascii")
    (battery / "capacity").write_text("47\n", encoding="ascii")
    (battery / "voltage_now").write_text("3787500\n", encoding="ascii")
    mains = root / "charger@0"
    mains.mkdir()
    (mains / "type").write_text("Mains\n", encoding="ascii")
    (mains / "online").write_text("1\n", encoding="ascii")


def test_mock_is_a_supported_provider():
    assert "mock" in SUPPORTED_BATTERY_PROVIDERS
    assert BatteryProvider("mock") is BatteryProvider.MOCK


def test_cycle_starts_full_on_battery():
    first = MockPowerReader().read()

    assert first.battery_percent == 100.0
    assert first.external_power is False


def test_cycle_reaches_every_available_state():
    reader = MockPowerReader()

    states = {PowerMonitor._state_for(sample) for sample in _samples(reader, reader.cycle_length)}

    assert states == {
        PowerState.ON_BATTERY,
        PowerState.LOW,
        PowerState.CRITICAL,
        PowerState.PLUGGED_IN,
    }


def test_discharges_on_battery_then_charges_when_plugged_in():
    reader = MockPowerReader()
    cycle = _samples(reader, reader.cycle_length)
    unplugged = [sample for sample in cycle if not sample.external_power]
    plugged = [sample for sample in cycle if sample.external_power]

    assert unplugged and plugged
    # One unplugged stretch, then one plugged-in stretch.
    assert cycle == unplugged + plugged
    unplugged_levels = [sample.battery_percent for sample in unplugged]
    plugged_levels = [sample.battery_percent for sample in plugged]
    assert unplugged_levels == sorted(unplugged_levels, reverse=True)
    assert plugged_levels == sorted(plugged_levels)
    assert plugged_levels[-1] == 100.0


def test_cycle_repeats_exactly():
    reader = MockPowerReader()

    first = _samples(reader, reader.cycle_length)
    second = _samples(reader, reader.cycle_length)

    assert first == second


def test_readings_stay_plausible():
    reader = MockPowerReader()

    for sample in _samples(reader, reader.cycle_length):
        assert 0.0 <= sample.battery_percent <= 100.0
        assert 3.3 <= sample.battery_voltage_v <= 4.2


def test_voltage_tracks_charge():
    reader = MockPowerReader()
    unplugged = [
        sample for sample in _samples(reader, reader.cycle_length) if not sample.external_power
    ]

    voltages = [sample.battery_voltage_v for sample in unplugged]
    assert voltages == sorted(voltages, reverse=True)


def test_factory_uses_mock_even_when_a_real_battery_exists(tmp_path):
    """A laptop's own battery must not silently replace the simulated one."""
    _fake_linux_battery(tmp_path)

    reader = create_power_reader("mock", power_supply_path=tmp_path)

    assert isinstance(reader, MockPowerReader)


def test_power_monitor_publishes_mock_statuses():
    published = []
    power = PowerMonitor(provider="mock", on_status=published.append)

    for _ in range(3):
        power.poll_once()

    assert [status.available for status in published] == [True, True, True]
    assert {status.provider for status in published} == {"mock"}
    assert published[0].battery_percent == 100.0
    assert published[1].battery_percent < published[0].battery_percent
    assert power.status == published[-1]


def test_cli_accepts_mock_provider():
    parser = argparse.ArgumentParser()
    server_module._add_battery_arguments(parser)

    assert parser.parse_args(["--battery", "mock"]).battery == "mock"


def test_rejects_non_positive_steps():
    with pytest.raises(ValueError):
        MockPowerReader(discharge_step=0)
    with pytest.raises(ValueError):
        MockPowerReader(charge_step=-1)
