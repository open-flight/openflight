"""The batched FFT window iterator must match the per-block reference exactly."""

import glob
import pickle
from pathlib import Path

import numpy as np
import pytest

from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.rolling_buffer.types import IQCapture

CAPTURE_GLOB = str(Path(__file__).parent.parent / "session_logs" / "capture_*.pkl")


def _tracked_captures() -> list[IQCapture]:
    captures = []
    for path in sorted(glob.glob(CAPTURE_GLOB)):
        with open(path, "rb") as handle:
            data = pickle.load(handle)
        for entry in data["captures"]:
            captures.append(
                IQCapture(
                    sample_time=entry["radar_sample_time"],
                    trigger_time=entry["radar_trigger_time"],
                    i_samples=[int(v) for v in entry["i_samples"]],
                    q_samples=[int(v) for v in entry["q_samples"]],
                )
            )
    return captures


def _synthetic_capture(n_samples: int, seed: int = 0) -> IQCapture:
    rng = np.random.default_rng(seed)
    t = np.arange(n_samples)
    tone = 600.0 * np.exp(2j * np.pi * 0.2 * t)
    noise = rng.normal(0, 20, n_samples) + 1j * rng.normal(0, 20, n_samples)
    signal = tone + noise
    return IQCapture(
        sample_time=0.0,
        trigger_time=0.0,
        i_samples=[int(2048 + v) for v in signal.real],
        q_samples=[int(2048 + v) for v in signal.imag],
    )


def _reference_windows(processor, capture, step_size):
    """Per-window path the batched iterator replaced: _process_block per slice."""
    i_data = np.asarray(capture.i_samples)
    q_data = np.asarray(capture.q_samples)
    start = 0
    while start + processor.WINDOW_SIZE <= len(i_data):
        peaks = processor._process_block(
            i_data[start : start + processor.WINDOW_SIZE],
            q_data[start : start + processor.WINDOW_SIZE],
        )
        yield start, peaks
        start += step_size


def _assert_equal(processor, capture, step_size):
    batched = list(processor._iter_capture_windows(capture, step_size))
    reference = list(_reference_windows(processor, capture, step_size))
    assert [start for start, _ in batched] == [start for start, _ in reference]
    for (start, readings), (_, peaks) in zip(batched, reference):
        expected_ts = (start / processor.SAMPLE_RATE) * 1000
        assert [(r.speed_mph, r.magnitude, r.direction) for r in readings] == peaks
        assert all(r.timestamp_ms == expected_ts for r in readings)


@pytest.mark.parametrize("step_size", [32, 128])
def test_tracked_captures_identical(step_size):
    captures = _tracked_captures()
    if not captures:
        pytest.skip("no tracked captures in session_logs/")
    processor = RollingBufferProcessor()
    for capture in captures:
        _assert_equal(processor, capture, step_size)


@pytest.mark.parametrize("n_samples", [100, 128, 129, 1000, 4096])
@pytest.mark.parametrize("step_size", [32, 128])
def test_synthetic_lengths_identical(n_samples, step_size):
    _assert_equal(RollingBufferProcessor(), _synthetic_capture(n_samples), step_size)


def test_capture_shorter_than_window_yields_nothing():
    processor = RollingBufferProcessor()
    assert list(processor._iter_capture_windows(_synthetic_capture(100), 32)) == []
