"""Triggered frame ring buffer for high-speed camera captures."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Sequence

import numpy as np


@dataclass(frozen=True)
class CameraFrame:
    """One monochrome camera frame and its timing metadata."""

    image: np.ndarray
    sensor_timestamp_ns: int
    host_timestamp_ns: int
    exposure_us: int
    analogue_gain: float


@dataclass(frozen=True)
class TriggeredCapture:
    """Frames surrounding one trigger edge."""

    frames: tuple[CameraFrame, ...]
    pre_trigger_count: int
    trigger_host_timestamp_ns: int

    @property
    def post_trigger_count(self) -> int:
        """Number of frames captured after the trigger."""
        return len(self.frames) - self.pre_trigger_count


class TriggeredFrameBuffer:
    """Continuously retain pre-trigger frames and freeze a post-trigger tail."""

    def __init__(self, pre_trigger_frames: int, post_trigger_frames: int):
        if pre_trigger_frames < 1:
            raise ValueError("pre_trigger_frames must be at least 1")
        if post_trigger_frames < 1:
            raise ValueError("post_trigger_frames must be at least 1")

        self.pre_trigger_frames = pre_trigger_frames
        self.post_trigger_frames = post_trigger_frames
        self._pre: Deque[CameraFrame] = deque(maxlen=pre_trigger_frames)
        self._post: list[CameraFrame] = []
        self._frozen_pre: tuple[CameraFrame, ...] = ()
        self._trigger_ns = 0
        self._ready: Optional[TriggeredCapture] = None
        self._capturing = False
        self._condition = threading.Condition()

    @property
    def buffered_frames(self) -> int:
        """Current number of frames available before a trigger."""
        with self._condition:
            return len(self._pre)

    @property
    def capture_busy(self) -> bool:
        """Whether a triggered post-impact tail is still being collected."""
        with self._condition:
            return self._capturing

    @property
    def latest_frame(self) -> Optional[CameraFrame]:
        """Most recent frame available without disturbing capture state."""
        with self._condition:
            if self._post:
                return self._post[-1]
            if self._pre:
                return self._pre[-1]
            if self._frozen_pre:
                return self._frozen_pre[-1]
            if self._ready is not None and self._ready.frames:
                return self._ready.frames[-1]
            return None

    def add_frame(self, frame: CameraFrame) -> None:
        """Add one frame from the camera callback."""
        with self._condition:
            if not self._capturing:
                self._pre.append(frame)
                return

            self._post.append(frame)
            if len(self._post) < self.post_trigger_frames:
                return

            frames = self._frozen_pre + tuple(self._post)
            self._ready = TriggeredCapture(
                frames=frames,
                pre_trigger_count=len(self._frozen_pre),
                trigger_host_timestamp_ns=self._trigger_ns,
            )
            self._capturing = False
            self._pre.clear()
            self._post = []
            self._frozen_pre = ()
            self._condition.notify_all()

    def trigger(self, host_timestamp_ns: Optional[int] = None) -> bool:
        """Freeze the current pre-trigger ring and begin collecting the tail."""
        with self._condition:
            if (
                self._capturing
                or self._ready is not None
                or len(self._pre) < self.pre_trigger_frames
            ):
                return False
            self._frozen_pre = tuple(self._pre)
            self._pre.clear()
            self._post = []
            self._trigger_ns = host_timestamp_ns or time.monotonic_ns()
            self._capturing = True
            return True

    def wait_for_capture(self, timeout_s: Optional[float] = None) -> Optional[TriggeredCapture]:
        """Wait for and consume the next completed capture."""
        with self._condition:
            ready = self._condition.wait_for(lambda: self._ready is not None, timeout_s)
            if not ready:
                return None
            capture = self._ready
            self._ready = None
            return capture

    def pop_capture(self) -> Optional[TriggeredCapture]:
        """Consume a completed capture without blocking."""
        with self._condition:
            capture = self._ready
            self._ready = None
            return capture


def unpack_r8_frame(
    raw: np.ndarray,
    width: int,
    height: int,
    rotate_180: bool,
    mirror_horizontal: bool = False,
) -> np.ndarray:
    """Convert the Pi's unpacked OV9281 R8 buffer into a compact image."""
    if raw.ndim != 2 or raw.shape[0] < height:
        raise ValueError(f"unexpected raw frame shape {raw.shape}")

    rows = raw[:height]
    if rows.dtype == np.uint16:
        image = (rows[:, :width] >> 8).astype(np.uint8)
    elif rows.dtype == np.uint8 and rows.shape[1] >= width * 2:
        # PiSP exposes R8 as an R16 stream; the luminance byte is the high byte.
        image = rows[:, 1 : width * 2 : 2].copy()
    elif rows.dtype == np.uint8 and rows.shape[1] >= width:
        image = rows[:, :width].copy()
    else:
        raise ValueError(f"unexpected raw frame layout {raw.shape} {raw.dtype}")

    if rotate_180:
        image = np.ascontiguousarray(image[::-1, ::-1])
    if mirror_horizontal:
        image = np.ascontiguousarray(image[:, ::-1])
    return image


def unpack_yuv420_y_plane(
    main: np.ndarray,
    width: int,
    height: int,
    rotate_180: bool,
    mirror_horizontal: bool = False,
) -> np.ndarray:
    """Extract the luma plane from Picamera2's YUV420 main stream."""
    if main.ndim == 2:
        if main.shape[0] < height or main.shape[1] < width:
            raise ValueError(f"unexpected YUV420 frame shape {main.shape}")
        image = main[:height, :width].copy()
    elif main.ndim == 3:
        if main.shape[0] < height or main.shape[1] < width:
            raise ValueError(f"unexpected YUV420 frame shape {main.shape}")
        image = main[:height, :width, 0].copy()
    else:
        raise ValueError(f"unexpected YUV420 frame shape {main.shape}")

    if rotate_180:
        image = np.ascontiguousarray(image[::-1, ::-1])
    if mirror_horizontal:
        image = np.ascontiguousarray(image[:, ::-1])
    return image


def timing_summary(frames: Sequence[CameraFrame]) -> dict[str, float | int]:
    """Summarize delivered cadence and discontinuities for a capture."""
    if len(frames) < 2:
        return {"frame_count": len(frames), "delivered_fps": 0.0, "gap_count": 0}

    timestamps = np.asarray([frame.sensor_timestamp_ns for frame in frames], dtype=np.int64)
    intervals_ms = np.diff(timestamps) / 1_000_000.0
    median_ms = float(np.median(intervals_ms))
    duration_s = float((timestamps[-1] - timestamps[0]) / 1_000_000_000.0)
    delivered_fps = (len(frames) - 1) / duration_s if duration_s > 0 else 0.0
    return {
        "frame_count": len(frames),
        "delivered_fps": delivered_fps,
        "median_interval_ms": median_ms,
        "p95_interval_ms": float(np.percentile(intervals_ms, 95)),
        "max_interval_ms": float(intervals_ms.max()),
        "gap_count": int(np.sum(intervals_ms > median_ms * 1.5)),
    }
