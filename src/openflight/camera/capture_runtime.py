"""High-speed camera capture runtime for offline shot correlation."""

from __future__ import annotations

import json
import logging
import math
import queue
import sys
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal

import numpy as np

from openflight.camera.auto_exposure import (
    AutoExposureDecision,
    AutoExposurePolicy,
    ExposureObservation,
    measure_exposure,
    motion_blur_risk,
)
from openflight.camera.triggered_buffer import (
    CameraFrame,
    TriggeredCapture,
    TriggeredFrameBuffer,
    timing_summary,
    unpack_r8_frame,
    unpack_yuv420_y_plane,
)
from openflight.gpio_factory import ensure_lgpio_pin_factory

logger = logging.getLogger(__name__)

CameraCaptureStream = Literal["raw", "main-y"]

RASPBERRY_PI_DIST_PACKAGES = Path("/usr/lib/python3/dist-packages")
OV9281_VERTICAL_OFFSET_PATH = Path("/sys/module/ov9282/parameters/strip_y_offset")
AUTO_EXPOSURE_STARTUP_SETTLE_S = 0.3


def vertical_crop_limits(width: int, height: int) -> dict[str, int] | None:
    """Return safe output-pixel crop limits for a supported sensor mode."""
    if (width, height) == (320, 200):
        # Keep five pixels of margin around the driver's centered +/-75 limit.
        return {"min_px": -70, "max_px": 70, "step_px": 10}
    return None


@dataclass(frozen=True)
class CameraCaptureSettings:
    """Camera ring-buffer settings used for offline capture."""

    width: int = 640
    height: int = 400
    fps: float = 300.0
    pre_ms: float = 150.0
    post_ms: float = 50.0
    exposure_us: int = 1000
    gain: float = 4.0
    stream: CameraCaptureStream = "raw"
    rotate_180: bool = False
    mirror_horizontal: bool = False
    roll_correction_deg: float = 0.0
    scaler_crop: tuple[int, int, int, int] | None = None
    gpio_pin: int = 17
    match_tolerance_s: float = 0.75
    auto_exposure: bool = True
    auto_exposure_state_path: Path | None = None

    @property
    def pre_frames(self) -> int:
        """Frames retained before the sound trigger."""
        return math.ceil(self.pre_ms * self.fps / 1000.0)

    @property
    def post_frames(self) -> int:
        """Frames retained after the sound trigger."""
        return math.ceil(self.post_ms * self.fps / 1000.0)


@dataclass(frozen=True)
class SavedCameraCapture:
    """A persisted camera capture and its timing metadata."""

    sequence: int
    trigger_timestamp: float
    completed_timestamp: float
    path: Path
    metadata: dict
    error: str | None = None

    @property
    def valid(self) -> bool:
        """Whether the capture was saved successfully."""
        return self.error is None


def parse_scaler_crop(value: str | None) -> tuple[int, int, int, int] | None:
    """Parse a Picamera2 ScalerCrop tuple."""
    if value is None:
        return None
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 4:
        raise ValueError("ScalerCrop must be X,Y,W,H")
    try:
        x, y, width, height = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError("ScalerCrop must contain integers") from exc
    if width <= 0 or height <= 0:
        raise ValueError("ScalerCrop width and height must be positive")
    return x, y, width, height


def _save_pgm(path: Path, image: np.ndarray) -> None:
    """Save a dependency-free grayscale preview."""
    with path.open("wb") as handle:
        handle.write(f"P5\n{image.shape[1]} {image.shape[0]}\n255\n".encode("ascii"))
        handle.write(image.tobytes())


def ensure_picamera2_import_path() -> bool:
    """Expose Raspberry Pi OS camera packages when running inside uv's venv."""
    path = str(RASPBERRY_PI_DIST_PACKAGES)
    if path in sys.path:
        return True
    if not RASPBERRY_PI_DIST_PACKAGES.exists():
        return False
    sys.path.append(path)
    return True


class CameraCaptureRuntime:
    """Maintain a high-speed camera ring and save clips on sound-trigger edges."""

    def __init__(
        self,
        *,
        output_dir: str | Path,
        settings: CameraCaptureSettings | None = None,
        button_factory: Callable | None = None,
        use_gpio_trigger: bool = True,
        vertical_offset_path: str | Path = OV9281_VERTICAL_OFFSET_PATH,
    ):
        self.output_dir = Path(output_dir).expanduser()
        self.settings = settings or CameraCaptureSettings()
        self._button_factory = button_factory
        self._use_gpio_trigger = use_gpio_trigger
        self._vertical_offset_path = Path(vertical_offset_path)
        self._auto_exposure_state_path = (
            Path(self.settings.auto_exposure_state_path).expanduser()
            if self.settings.auto_exposure_state_path is not None
            else None
        )
        self._last_persisted_controls: tuple[int, float] | None = None
        self._restore_auto_exposure_controls()
        self._camera = None
        self._button = None
        self._ring = TriggeredFrameBuffer(self.settings.pre_frames, self.settings.post_frames)
        self._running = False
        self._sequence = 0
        self._worker: threading.Thread | None = None
        self._ready: queue.Queue[TriggeredCapture | None] = queue.Queue()
        self._captures: list[SavedCameraCapture] = []
        self._condition = threading.Condition()
        self._trigger_epochs: queue.Queue[float] = queue.Queue()
        self._trigger_auto_exposure: queue.Queue[dict] = queue.Queue()
        self._camera_control_lock = threading.Lock()
        self._trigger_exposure_lock = threading.Lock()
        self._reconfigure_lock = threading.Lock()
        self._auto_exposure_policy = AutoExposurePolicy(fps=self.settings.fps)
        self._auto_exposure_stop = threading.Event()
        self._auto_exposure_lock = threading.Lock()
        self._auto_exposure_decision = AutoExposureDecision(
            status="unavailable",
            analysis_eligible=not self.settings.auto_exposure,
            message="Waiting for automatic exposure calibration",
            observation=ExposureObservation(
                sample_available=False,
                status="unavailable",
                recommendation="hold",
                message="Waiting for a camera frame",
            ),
            motion_blur_risk=motion_blur_risk(self.settings.exposure_us),
        )
        self._auto_exposure_last_check_epoch: float | None = None
        self._auto_exposure_last_adjustment_epoch: float | None = None
        self._auto_exposure_capture_deferred = False

    def start(self) -> None:
        """Start the camera and, optionally, the GPIO edge listener."""
        if self._running:
            return
        ensure_picamera2_import_path()
        try:
            from picamera2 import Picamera2  # pylint: disable=import-error,import-outside-toplevel
        except ImportError as exc:
            raise RuntimeError("picamera2 is required for --camera-capture") from exc

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._camera = Picamera2()
        frame_duration_us = round(1_000_000 / self.settings.fps)
        config = self._camera.create_video_configuration(
            main={"size": (self.settings.width, self.settings.height), "format": "YUV420"},
            raw={"size": (self.settings.width, self.settings.height), "format": "R8"},
            controls={
                "AeEnable": False,
                "ExposureTime": self.settings.exposure_us,
                "AnalogueGain": self.settings.gain,
                "FrameDurationLimits": (frame_duration_us, frame_duration_us),
            },
            buffer_count=8,
            display=None,
            encode=None,
        )
        self._camera.configure(config)
        if self.settings.scaler_crop is not None:
            self._camera.set_controls({"ScalerCrop": self.settings.scaler_crop})
        self._camera.post_callback = self._on_frame
        self._running = True
        self._worker = threading.Thread(
            target=self._save_loop,
            name="camera-capture-save",
            daemon=True,
        )
        self._worker.start()
        try:
            self._camera.start()
            self._wait_for_prebuffer()
            if self.settings.auto_exposure:
                self._start_auto_exposure()
                self._refill_locked_exposure_prebuffer()
            if self._use_gpio_trigger:
                self._start_gpio_trigger()
        except Exception:
            self.stop()
            raise
        logger.info(
            "[CAMERA] Capture armed at %dx%d %.1ffps (%s, %d pre/%d post)",
            self.settings.width,
            self.settings.height,
            self.settings.fps,
            self.settings.stream,
            self.settings.pre_frames,
            self.settings.post_frames,
        )

    def stop(self) -> None:
        """Stop camera capture and release hardware resources."""
        self._running = False
        self._auto_exposure_stop.set()
        if self._button is not None:
            self._button.close()
            self._button = None
        if self._camera is not None:
            try:
                self._camera.stop()
                self._camera.close()
            finally:
                self._camera = None
        self._ready.put(None)
        if self._worker is not None:
            self._worker.join(timeout=3.0)
            self._worker = None

    def capture_preview_jpeg(self, quality: int = 80) -> bytes | None:
        """Encode the latest rolling-buffer frame as a preview JPEG.

        Reusing the compact raw frame avoids a second capture request and
        remains reliable in high-FPS modes where the processed YUV companion
        stream may not produce usable pixels.
        """
        if not self._running or self._camera is None:
            return None
        try:
            import cv2  # pylint: disable=import-error,import-outside-toplevel

            frame = self._ring.latest_frame
            if frame is None:
                return None
            image = np.ascontiguousarray(frame.image)
            if abs(self.settings.roll_correction_deg) > 1e-6:
                height, width = image.shape
                transform = cv2.getRotationMatrix2D(
                    (width / 2.0, height / 2.0),
                    -self.settings.roll_correction_deg,
                    1.0,
                )
                image = cv2.warpAffine(
                    image,
                    transform,
                    (width, height),
                    flags=cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_CONSTANT,
                    borderValue=0,
                )
            ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
            return encoded.tobytes() if ok else None
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("[CAMERA] Preview capture failed", exc_info=True)
            return None

    def exposure_quality(self) -> dict:
        """Rate exposure in the center-lower hitting zone of the latest frame."""
        frame = self._ring.latest_frame
        image = frame.image if frame is not None else np.asarray([])
        return measure_exposure(image).to_dict()

    @property
    def camera_analysis_eligible(self) -> bool:
        """Whether current lighting permits camera-derived shot metrics."""
        if not self.settings.auto_exposure:
            return True
        with self._auto_exposure_lock:
            return self._auto_exposure_decision.analysis_eligible

    def auto_exposure_status(self) -> dict:
        """Return controller state for diagnostics and the operator UI."""
        with self._auto_exposure_lock:
            payload = self._auto_exposure_decision.to_dict()
            payload.update(
                {
                    "enabled": self.settings.auto_exposure,
                    "capture_deferred": self._auto_exposure_capture_deferred,
                    "last_check_timestamp": self._auto_exposure_last_check_epoch,
                    "last_adjustment_timestamp": self._auto_exposure_last_adjustment_epoch,
                }
            )
        payload["exposure_us"] = self.settings.exposure_us
        payload["gain"] = self.settings.gain
        return payload

    def update_image_controls(self, *, exposure_us: int, gain: float) -> dict:
        """Apply exposure and gain without stopping the rolling buffer."""
        exposure_us = int(exposure_us)
        gain = float(gain)
        frame_period_us = round(1_000_000 / self.settings.fps)
        if exposure_us <= 0:
            raise ValueError("camera exposure must be positive")
        if exposure_us >= frame_period_us:
            raise ValueError(
                f"camera exposure must be shorter than the {frame_period_us}us frame period"
            )
        if gain <= 0:
            raise ValueError("camera gain must be positive")
        if not self._running or self._camera is None:
            raise RuntimeError("camera capture is not running")

        with self._camera_control_lock:
            self._camera.set_controls(
                {
                    "ExposureTime": exposure_us,
                    "AnalogueGain": gain,
                }
            )
        self.settings = replace(
            self.settings,
            exposure_us=exposure_us,
            gain=gain,
        )
        logger.info(
            "[CAMERA] Live controls updated: exposure=%dus gain=%.2f",
            exposure_us,
            gain,
        )
        return {"exposure_us": exposure_us, "gain": gain}

    def vertical_crop_status(self) -> dict:
        """Describe the live sensor-window adjustment available to the UI."""
        limits = vertical_crop_limits(self.settings.width, self.settings.height)
        adjustable = limits is not None and self._vertical_offset_path.exists()
        offset = 0
        if adjustable:
            try:
                offset = int(self._vertical_offset_path.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                logger.warning("[CAMERA] Could not read vertical crop parameter", exc_info=True)
                adjustable = False
        payload = {
            "raw_crop_adjustable": adjustable,
            "vertical_offset_px": offset,
        }
        if limits is not None:
            payload.update(
                {
                    "vertical_offset_min_px": limits["min_px"],
                    "vertical_offset_max_px": limits["max_px"],
                    "vertical_offset_step_px": limits["step_px"],
                }
            )
        return payload

    def update_vertical_crop(self, offset_px: int) -> dict:
        """Move the hardware sensor window and restart the rolling capture."""
        limits = vertical_crop_limits(self.settings.width, self.settings.height)
        if limits is None:
            raise ValueError(
                f"vertical crop is unavailable for {self.settings.width}x{self.settings.height}"
            )
        offset_px = int(offset_px)
        if not limits["min_px"] <= offset_px <= limits["max_px"]:
            raise ValueError(
                f"vertical crop must be between {limits['min_px']} and {limits['max_px']} pixels"
            )
        if offset_px % limits["step_px"]:
            raise ValueError(f"vertical crop must use {limits['step_px']}-pixel steps")
        if not self._running:
            raise RuntimeError("camera capture is not running")

        with self._reconfigure_lock:
            status = self.vertical_crop_status()
            if not status["raw_crop_adjustable"]:
                raise RuntimeError(
                    "OV9281 vertical crop is unavailable; install the OpenFlight driver "
                    "and make strip_y_offset writable"
                )
            previous = int(status["vertical_offset_px"])
            if offset_px == previous:
                return status

            self.stop()
            try:
                self._vertical_offset_path.write_text(f"{offset_px}\n", encoding="ascii")
            except OSError as exc:
                self._reset_capture_state()
                self.start()
                raise RuntimeError(f"could not update OV9281 vertical crop: {exc}") from exc

            self._reset_capture_state()
            try:
                self.start()
            except Exception:
                logger.exception("[CAMERA] Crop restart failed; restoring %+d px", previous)
                self._vertical_offset_path.write_text(f"{previous}\n", encoding="ascii")
                self._reset_capture_state()
                self.start()
                raise
            logger.info("[CAMERA] Sensor view moved to %+d output pixels", offset_px)
            return self.vertical_crop_status()

    def _reset_capture_state(self) -> None:
        """Prepare one runtime instance to start again after a controlled stop."""
        self._ring = TriggeredFrameBuffer(self.settings.pre_frames, self.settings.post_frames)
        self._ready = queue.Queue()
        self._trigger_epochs = queue.Queue()
        self._trigger_auto_exposure = queue.Queue()
        self._auto_exposure_policy.reset()

    def status(self) -> dict:
        """Return lightweight state for the operator UI."""
        buffered_frames = self._ring.buffered_frames
        return {
            "running": self._running,
            "armed": self._running and buffered_frames >= self.settings.pre_frames,
            "buffered_frames": buffered_frames,
            "required_pre_frames": self.settings.pre_frames,
            "auto_exposure": self.auto_exposure_status(),
        }

    def notify_trigger(self, timestamp: float | None = None) -> bool:
        """Freeze the camera ring on a sound-trigger edge."""
        if not self._running:
            return False
        trigger_epoch = time.time() if timestamp is None else float(timestamp)
        with self._trigger_exposure_lock:
            accepted = self._ring.trigger(time.monotonic_ns())
            if not accepted:
                logger.debug("[CAMERA] Ignoring trigger edge while capture is busy")
                return False
            self._trigger_epochs.put(trigger_epoch)
            self._trigger_auto_exposure.put(self.auto_exposure_status())
            return True

    def capture_for_shot(
        self,
        impact_timestamp: float | None,
        *,
        timeout_s: float = 1.0,
    ) -> SavedCameraCapture | None:
        """Consume the saved capture nearest the OPS impact/trigger timestamp."""
        deadline = time.monotonic() + timeout_s
        with self._condition:
            while True:
                if impact_timestamp is None and self._captures:
                    return self._captures.pop(0)

                if impact_timestamp is not None:
                    cutoff = impact_timestamp - self.settings.match_tolerance_s
                    while self._captures and self._captures[0].trigger_timestamp < cutoff:
                        stale = self._captures.pop(0)
                        logger.warning(
                            "[CAMERA] Discarding unmatched capture #%d (edge %.3f, shot %.3f)",
                            stale.sequence,
                            stale.trigger_timestamp,
                            impact_timestamp,
                        )
                    if (
                        self._captures
                        and abs(self._captures[0].trigger_timestamp - impact_timestamp)
                        <= self.settings.match_tolerance_s
                    ):
                        return self._captures.pop(0)

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=remaining)

    def _start_gpio_trigger(self) -> None:
        button_factory = self._button_factory
        if button_factory is None:
            ensure_lgpio_pin_factory()
            from gpiozero import Button  # pylint: disable=import-error,import-outside-toplevel

            button_factory = Button
        self._button = button_factory(
            self.settings.gpio_pin,
            pull_up=False,
            bounce_time=None,
        )
        self._button.when_pressed = self.notify_trigger

    def _wait_for_prebuffer(self) -> None:
        deadline = time.monotonic() + 5.0
        while self._ring.buffered_frames < self.settings.pre_frames and time.monotonic() < deadline:
            time.sleep(0.01)
        if self._ring.buffered_frames < self.settings.pre_frames:
            raise RuntimeError(
                "camera produced only "
                f"{self._ring.buffered_frames}/{self.settings.pre_frames} pre-trigger frames"
            )

    def _refill_locked_exposure_prebuffer(self) -> None:
        """Discard calibration frames and refill the ring at locked controls."""
        self._ring = TriggeredFrameBuffer(
            self.settings.pre_frames,
            self.settings.post_frames,
        )
        self._wait_for_prebuffer()

    def _start_auto_exposure(self) -> None:
        """Calibrate once before arming shot capture, then lock the controls."""
        self._auto_exposure_policy.reset()
        self._auto_exposure_stop.clear()
        self._auto_exposure_loop()

    def _auto_exposure_loop(self) -> None:
        """Converge startup controls and return without steady-state monitoring."""
        while self._running and not self._auto_exposure_stop.is_set():
            try:
                decision = self._run_auto_exposure_cycle()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("[CAMERA] Startup exposure calibration failed", exc_info=True)
                return

            if decision is None:
                if self._auto_exposure_stop.wait(0.1):
                    return
                continue
            if not decision.should_apply:
                logger.info(
                    "[CAMERA] Startup exposure locked: %dus gain %.1f (%s)",
                    self.settings.exposure_us,
                    self.settings.gain,
                    decision.status,
                )
                return
            if self._auto_exposure_stop.wait(AUTO_EXPOSURE_STARTUP_SETTLE_S):
                return

    def _run_auto_exposure_cycle(self) -> AutoExposureDecision | None:
        """Measure one stable frame and apply the policy's requested control step."""
        with self._trigger_exposure_lock:
            if self._ring.capture_busy:
                with self._auto_exposure_lock:
                    self._auto_exposure_capture_deferred = True
                return None

            frame = self._ring.latest_frame
            observation = measure_exposure(
                frame.image if frame is not None else np.asarray([]),
            )
            decision = self._auto_exposure_policy.evaluate(
                observation,
                exposure_us=self.settings.exposure_us,
                gain=self.settings.gain,
            )
            if decision.should_apply:
                self.update_image_controls(
                    exposure_us=decision.target.exposure_us,
                    gain=decision.target.gain,
                )
                logger.info(
                    "[CAMERA] Auto exposure: %s -> %dus gain %.1f (%s)",
                    observation.status,
                    decision.target.exposure_us,
                    decision.target.gain,
                    decision.message,
                )
            elif decision.status == "lighting_required":
                logger.warning("[CAMERA] %s", decision.message)

            checked_at = time.time()
            with self._auto_exposure_lock:
                self._auto_exposure_decision = decision
                self._auto_exposure_capture_deferred = False
                self._auto_exposure_last_check_epoch = checked_at
                if decision.should_apply:
                    self._auto_exposure_last_adjustment_epoch = checked_at
        if decision.status == "ready" and observation.status == "good":
            self._persist_auto_exposure_controls()
        return decision

    def _restore_auto_exposure_controls(self) -> None:
        """Use the last good setting as the next startup seed for this mode."""
        path = self._auto_exposure_state_path
        if not self.settings.auto_exposure or path is None or not path.exists():
            return
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            same_mode = (
                saved.get("version") == 1
                and int(saved["width"]) == self.settings.width
                and int(saved["height"]) == self.settings.height
                and math.isclose(float(saved["fps"]), self.settings.fps, rel_tol=0.001)
            )
            exposure_us = int(saved["exposure_us"])
            gain = float(saved["gain"])
            frame_period_us = round(1_000_000 / self.settings.fps)
            if same_mode and 0 < exposure_us < frame_period_us and gain > 0:
                self.settings = replace(
                    self.settings,
                    exposure_us=exposure_us,
                    gain=gain,
                )
                self._last_persisted_controls = (exposure_us, gain)
                logger.info(
                    "[CAMERA] Restored auto exposure seed: %dus gain %.1f",
                    exposure_us,
                    gain,
                )
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
            logger.warning("[CAMERA] Ignoring invalid auto exposure state: %s", path)

    def _persist_auto_exposure_controls(self) -> None:
        """Atomically remember a validated setting without delaying capture."""
        path = self._auto_exposure_state_path
        controls = (self.settings.exposure_us, self.settings.gain)
        if path is None or controls == self._last_persisted_controls:
            return
        payload = {
            "version": 1,
            "width": self.settings.width,
            "height": self.settings.height,
            "fps": self.settings.fps,
            "exposure_us": controls[0],
            "gain": controls[1],
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(f"{path.suffix}.tmp")
            temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            temporary.replace(path)
            self._last_persisted_controls = controls
        except OSError:
            logger.warning("[CAMERA] Could not persist auto exposure state", exc_info=True)

    def _on_frame(self, request) -> None:
        try:
            metadata = request.get_metadata()
            if self.settings.stream == "main-y":
                image = unpack_yuv420_y_plane(
                    request.make_array("main"),
                    self.settings.width,
                    self.settings.height,
                    self.settings.rotate_180,
                    self.settings.mirror_horizontal,
                )
            else:
                image = unpack_r8_frame(
                    request.make_array("raw"),
                    self.settings.width,
                    self.settings.height,
                    self.settings.rotate_180,
                    self.settings.mirror_horizontal,
                )
            self._ring.add_frame(
                CameraFrame(
                    image=image,
                    sensor_timestamp_ns=int(metadata["SensorTimestamp"]),
                    host_timestamp_ns=time.monotonic_ns(),
                    exposure_us=int(metadata.get("ExposureTime", 0)),
                    analogue_gain=float(metadata.get("AnalogueGain", 0.0)),
                )
            )
            capture = self._ring.pop_capture()
            if capture is not None:
                self._ready.put(capture)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("[CAMERA] Frame callback failed: %s", exc, exc_info=True)

    def _save_loop(self) -> None:
        while self._running:
            capture = self._ready.get()
            if capture is None:
                break
            trigger_epoch = self._trigger_epochs.get() if not self._trigger_epochs.empty() else 0.0
            auto_exposure = (
                self._trigger_auto_exposure.get()
                if not self._trigger_auto_exposure.empty()
                else None
            )
            self._sequence += 1
            sequence = self._sequence
            try:
                saved = self._save_capture(
                    sequence,
                    trigger_epoch,
                    capture,
                    auto_exposure=auto_exposure,
                )
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("[CAMERA] Capture #%d save failed: %s", sequence, exc, exc_info=True)
                saved = SavedCameraCapture(
                    sequence=sequence,
                    trigger_timestamp=trigger_epoch,
                    completed_timestamp=time.time(),
                    path=self.output_dir,
                    metadata={},
                    error=str(exc),
                )
            with self._condition:
                self._captures.append(saved)
                self._condition.notify_all()

    def _save_capture(
        self,
        sequence: int,
        trigger_epoch: float,
        capture: TriggeredCapture,
        *,
        auto_exposure: dict | None = None,
    ) -> SavedCameraCapture:
        timestamp = datetime.fromtimestamp(trigger_epoch or time.time()).strftime(
            "%Y%m%d_%H%M%S_%f"
        )[:-3]
        shot_dir = self.output_dir / f"camera_{timestamp}_{sequence:03d}"
        shot_dir.mkdir(parents=True, exist_ok=False)
        started = time.monotonic()

        images = np.stack([frame.image for frame in capture.frames])
        sensor_ns = np.asarray(
            [frame.sensor_timestamp_ns for frame in capture.frames],
            dtype=np.int64,
        )
        host_ns = np.asarray(
            [frame.host_timestamp_ns for frame in capture.frames],
            dtype=np.int64,
        )
        exposure_us = np.asarray([frame.exposure_us for frame in capture.frames], dtype=np.int32)
        gain = np.asarray([frame.analogue_gain for frame in capture.frames], dtype=np.float32)

        # These clips are consumed immediately by the live estimators. ZIP
        # compression delayed shot display by roughly a second on the Pi, so
        # favor fast sequential I/O over the modest storage reduction.
        np.savez(
            shot_dir / "frames.npz",
            frames=images,
            sensor_timestamp_ns=sensor_ns,
            host_timestamp_ns=host_ns,
            exposure_us=exposure_us,
            analogue_gain=gain,
            pre_trigger_count=np.int32(capture.pre_trigger_count),
            trigger_host_timestamp_ns=np.int64(capture.trigger_host_timestamp_ns),
            trigger_epoch_timestamp=np.float64(trigger_epoch),
        )

        for label, index in (
            ("first", 0),
            ("trigger", max(0, capture.pre_trigger_count - 1)),
            ("last", len(images) - 1),
        ):
            _save_pgm(shot_dir / f"{label}.pgm", images[index])

        summary = timing_summary(capture.frames)
        summary.update(
            {
                "sequence": sequence,
                "trigger_timestamp": trigger_epoch,
                "completed_timestamp": time.time(),
                "capture_path": str(shot_dir),
                "pre_trigger_frames": capture.pre_trigger_count,
                "post_trigger_frames": capture.post_trigger_count,
                "trigger_host_timestamp_ns": capture.trigger_host_timestamp_ns,
                "mean_brightness": float(images.mean()),
                "p99_brightness": float(np.percentile(images, 99)),
                "storage_format": "npz_uncompressed",
                "npz_bytes": (shot_dir / "frames.npz").stat().st_size,
                "save_time_ms": (time.monotonic() - started) * 1000.0,
                "settings": {
                    "width": self.settings.width,
                    "height": self.settings.height,
                    "fps": self.settings.fps,
                    "pre_ms": self.settings.pre_ms,
                    "post_ms": self.settings.post_ms,
                    "exposure_us": self.settings.exposure_us,
                    "gain": self.settings.gain,
                    "stream": self.settings.stream,
                    "rotate_180": self.settings.rotate_180,
                    "mirror_horizontal": self.settings.mirror_horizontal,
                    "roll_correction_deg": self.settings.roll_correction_deg,
                    "scaler_crop": self.settings.scaler_crop,
                    "auto_exposure": self.settings.auto_exposure,
                },
                "auto_exposure": auto_exposure or self.auto_exposure_status(),
            }
        )
        (shot_dir / "metadata.json").write_text(json.dumps(summary, indent=2) + "\n")
        logger.info(
            "[CAMERA] Capture #%d saved: %d frames, %.1ffps, gaps=%d -> %s",
            sequence,
            summary["frame_count"],
            summary["delivered_fps"],
            summary["gap_count"],
            shot_dir,
        )
        return SavedCameraCapture(
            sequence=sequence,
            trigger_timestamp=trigger_epoch,
            completed_timestamp=summary["completed_timestamp"],
            path=shot_dir,
            metadata=summary,
        )
