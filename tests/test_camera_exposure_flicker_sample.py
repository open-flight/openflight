"""The lighting check judges the brightest flicker phase, not one random frame."""

from types import SimpleNamespace

import numpy as np

from openflight.camera.auto_exposure import measure_exposure
from openflight.camera.capture_runtime import CameraCaptureRuntime


class _Ring:
    def __init__(self, images):
        self._frames = [SimpleNamespace(image=i) for i in images]
        self._n = 0

    @property
    def latest_frame(self):
        frame = self._frames[min(self._n, len(self._frames) - 1)]
        self._n += 1
        return frame


def _scene(level):
    rng = np.random.default_rng(1)
    img = np.clip(rng.normal(level, level * 0.4, (400, 640)), 0, 255).astype(np.uint8)
    return img


def test_dim_flicker_phase_no_longer_fails_the_check():
    dim, bright = _scene(30), _scene(56)
    assert measure_exposure(dim).status == "too_dark"  # one unlucky frame used to decide
    runtime = CameraCaptureRuntime.__new__(CameraCaptureRuntime)
    runtime._ring = _Ring([dim, bright, dim, bright, dim, bright, dim, bright])
    sample = runtime._flicker_robust_sample()
    assert measure_exposure(sample).status != "too_dark"


def test_no_frames_returns_empty():
    runtime = CameraCaptureRuntime.__new__(CameraCaptureRuntime)
    runtime._ring = SimpleNamespace(latest_frame=None)
    assert runtime._flicker_robust_sample().size == 0
