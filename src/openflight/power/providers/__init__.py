"""Hardware-specific battery telemetry providers."""

from .geekworm import GeekwormPowerReader
from .linux import LinuxPowerReader
from .mock import MockPowerReader

__all__ = ["GeekwormPowerReader", "LinuxPowerReader", "MockPowerReader"]
