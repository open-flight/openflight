"""Fake camera runtimes for the child-process tests (imported inside the child)."""

import os
import threading
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

from openflight.camera.capture_runtime import CaptureInbox, SavedCameraCapture


class FakeRuntime:
    """Mimics CameraCaptureRuntime's public surface without hardware."""

    def __init__(self, *, output_dir, settings, use_gpio_trigger):
        self.output_dir = Path(output_dir)
        self.settings = settings
        self.use_gpio_trigger = use_gpio_trigger
        self._inbox = CaptureInbox()
        self._running = False
        self._sequence = 0

    def start(self):
        self._running = True
        # Auto exposure changes settings during start; the parent must see it.
        self.settings = replace(self.settings, exposure_us=777, gain=7.0)

    def stop(self):
        self._running = False

    def status(self):
        return {
            "running": self._running,
            "pid": os.getpid(),
            "affinity": sorted(os.sched_getaffinity(0)),
        }

    def vertical_crop_status(self):
        return {"raw_crop_adjustable": False, "vertical_offset_px": 0}

    def update_vertical_crop(self, offset_px):
        raise ValueError(f"vertical crop is unavailable ({offset_px})")

    def auto_exposure_status(self):
        return {"enabled": True, "exposure_us": self.settings.exposure_us}

    @property
    def camera_analysis_eligible(self):
        return True

    def exposure_quality(self):
        return {"status": "good"}

    def capture_preview_jpeg(self, quality=80):
        return b"\xff\xd8jpeg" + bytes([quality])

    def latest_frame_image(self):
        return np.full((self.settings.height, self.settings.width), 42, np.uint8)

    def notify_trigger(self, timestamp=None):
        self._sequence += 1
        sequence = self._sequence
        trigger = float(timestamp)

        def save():
            time.sleep(0.05)
            self._inbox.add(
                SavedCameraCapture(
                    sequence,
                    trigger,
                    time.time(),
                    self.output_dir / f"c{sequence}",
                    {"n": sequence},
                )
            )

        threading.Thread(target=save, daemon=True).start()
        return True

    def capture_for_shot(self, impact_timestamp, *, timeout_s=1.0):
        return self._inbox.take(
            impact_timestamp, tolerance_s=self.settings.match_tolerance_s, timeout_s=timeout_s
        )


class FailingRuntime(FakeRuntime):
    def start(self):
        raise RuntimeError("camera produced only 0/90 pre-trigger frames")


class DyingRuntime(FakeRuntime):
    def status(self):
        os._exit(3)


def _first_time(runtime, name):
    """True only the first time this fake hits ``name`` (marker file in output_dir)."""
    marker = runtime.output_dir / f".{name}"
    if marker.exists():
        return False
    marker.write_text("x")
    return True


class CrashOnFirstTriggerRuntime(FakeRuntime):
    def notify_trigger(self, timestamp=None):
        if _first_time(self, "crashed"):
            os._exit(3)
        return super().notify_trigger(timestamp)


class FailFirstStartRuntime(FakeRuntime):
    def start(self):
        if _first_time(self, "start_failed"):
            raise RuntimeError("camera produced only 0/90 pre-trigger frames")
        super().start()


class AlwaysFailingRuntime(FakeRuntime):
    def start(self):
        raise RuntimeError("picamera2 is required for --camera-capture")


class HangOnFirstStatusRuntime(FakeRuntime):
    def status(self):
        if _first_time(self, "hung"):
            time.sleep(3600)
        return super().status()
