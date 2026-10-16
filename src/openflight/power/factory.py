"""Battery provider selection and reader construction."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from pathlib import Path

from .providers.geekworm import GeekwormPowerReader
from .providers.linux import LinuxPowerReader
from .providers.mock import MockPowerReader
from .reader import PowerReader


class BatteryProvider(str, Enum):
    """Battery hardware integrations supported by OpenFlight."""

    GEEKWORM = "geekworm"
    # A simulated battery for testing the UI and phone apps without a UPS.
    MOCK = "mock"


SUPPORTED_BATTERY_PROVIDERS = tuple(provider.value for provider in BatteryProvider)


def normalize_battery_provider(provider: BatteryProvider | str) -> BatteryProvider:
    """Validate and normalize a provider name."""
    try:
        return BatteryProvider(provider)
    except ValueError as error:
        supported = ", ".join(SUPPORTED_BATTERY_PROVIDERS)
        raise ValueError(
            f"Unsupported battery provider {provider!r}; expected one of: {supported}"
        ) from error


def create_power_reader(
    provider: BatteryProvider | str,
    *,
    power_supply_path: Path = LinuxPowerReader.DEFAULT_POWER_SUPPLY_PATH,
    provider_factory: Callable[[], PowerReader] | None = None,
) -> PowerReader:
    """Prefer standard Linux telemetry and fall back to the selected provider."""
    normalized = normalize_battery_provider(provider)
    if normalized is BatteryProvider.MOCK:
        # Never let a real battery (e.g. a laptop's) stand in for the simulation.
        return provider_factory() if provider_factory is not None else MockPowerReader()
    try:
        return LinuxPowerReader(power_supply_path=power_supply_path)
    except OSError:
        pass

    if provider_factory is not None:
        return provider_factory()
    if normalized is BatteryProvider.GEEKWORM:
        return GeekwormPowerReader()

    raise AssertionError(f"No reader factory registered for {normalized.value}")
