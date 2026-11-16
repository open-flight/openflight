"""A simulated battery for exercising power status without UPS hardware."""

from __future__ import annotations

from ..models import PowerSample

# Li-ion cell voltage mapped linearly from empty to full; only needs to look
# plausible to the UI and phones.
_EMPTY_VOLTAGE_V = 3.3
_FULL_VOLTAGE_V = 4.2


class MockPowerReader:
    """Cycle through a deterministic discharge and charge, one step per read.

    Starting full on battery, each read drops ``discharge_step`` percent down
    to ``floor_percent``, passing the low (<=20%) and critical (<=10%) states;
    then external power comes on and each read adds ``charge_step`` percent back
    to 100%. The cycle then repeats. At the default 5 s poll it takes about
    100 s, so every power state is seen within two minutes.
    """

    def __init__(
        self,
        *,
        discharge_step: float = 8.0,
        charge_step: float = 12.0,
        floor_percent: float = 5.0,
    ):
        if discharge_step <= 0 or charge_step <= 0:
            raise ValueError("Mock battery steps must be positive")
        if not 0.0 < floor_percent < 100.0:
            raise ValueError("Mock battery floor must be between 0 and 100 percent")
        self._cycle = self._build_cycle(discharge_step, charge_step, floor_percent)
        self._index = 0

    @property
    def cycle_length(self) -> int:
        """Number of reads before the simulated cycle repeats."""
        return len(self._cycle)

    @staticmethod
    def _build_cycle(
        discharge_step: float, charge_step: float, floor_percent: float
    ) -> list[PowerSample]:
        levels: list[tuple[float, bool]] = []
        percent = 100.0
        while percent > floor_percent:
            levels.append((percent, False))
            percent -= discharge_step
        levels.append((floor_percent, False))
        percent = floor_percent
        while percent < 100.0:
            percent = min(100.0, percent + charge_step)
            levels.append((percent, True))
        return [
            PowerSample(
                battery_percent=round(level, 1),
                battery_voltage_v=round(
                    _EMPTY_VOLTAGE_V + (_FULL_VOLTAGE_V - _EMPTY_VOLTAGE_V) * level / 100.0, 3
                ),
                external_power=plugged_in,
            )
            for level, plugged_in in levels
        ]

    def read(self) -> PowerSample:
        """Return the next simulated sample."""
        sample = self._cycle[self._index]
        self._index = (self._index + 1) % len(self._cycle)
        return sample

    def close(self) -> None:
        """The simulation holds no resources."""
