"""
Rolling Buffer Mode for OpenFlight.

This module provides an alternative to streaming mode that captures raw I/Q data
from the OPS243-A radar for post-processing. Benefits include:

- Higher temporal resolution (~937 Hz vs ~56 Hz)
- Spin detection from speed oscillations (50-60% success rate)
- More precise impact timing
- Full control over signal processing

Usage:
    from openflight.rolling_buffer import RollingBufferMonitor

    monitor = RollingBufferMonitor(port="/dev/ttyACM0")
    monitor.connect()
    monitor.start(shot_callback=on_shot)

    # Or use via server.py:
    # openflight-server --mode rolling-buffer
"""

from .monitor import (
    RollingBufferMonitor,
    estimate_carry_with_spin,
    get_optimal_spin_for_ball_speed,
)
from .processor import RollingBufferProcessor
from .trigger import (
    HardwareTriggeredCapture,
    SoundTrigger,
    SpeedTriggeredCapture,
    TriggerStrategy,
    create_trigger,
)
from .types import (
    ImpactEstimate,
    IQCapture,
    ProcessedCapture,
    SpeedReading,
    SpeedTimeline,
    SpinCandidate,
    SpinResult,
)

__all__ = [
    # Types
    "IQCapture",
    "ImpactEstimate",
    "SpeedReading",
    "SpeedTimeline",
    "SpinCandidate",
    "SpinResult",
    "ProcessedCapture",
    # Processor
    "RollingBufferProcessor",
    # Triggers
    "TriggerStrategy",
    "HardwareTriggeredCapture",
    "SoundTrigger",
    "SpeedTriggeredCapture",
    "create_trigger",
    # Monitor
    "RollingBufferMonitor",
    "estimate_carry_with_spin",
    "get_optimal_spin_for_ball_speed",
]
