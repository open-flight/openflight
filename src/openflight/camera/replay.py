"""Lazy MP4 preparation for persisted high-speed camera captures."""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from uuid import uuid4

import numpy as np

# Pylint's NumPy inference resolves ndarray values as the np.array callable.
# Runtime shape/dtype validation below is covered by focused tests.
# pylint: disable=no-member

logger = logging.getLogger(__name__)

PLAYBACK_FPS = 60
ENCODE_TIMEOUT_S = 30.0


class ReplayNotFoundError(LookupError):
    """The requested replay is not registered in this server process."""


class ReplayNotReadyError(RuntimeError):
    """The replay exists, but no MP4 has been prepared yet."""


class ReplayPreparationError(RuntimeError):
    """The persisted camera frames could not be converted to MP4."""


@dataclass
class PreparedCameraReplay:
    """A browser-ready replay plus its public metadata."""

    video_path: Path
    payload: dict[str, object]


@dataclass
class _ReplayEntry:
    replay_id: str
    capture_path: Path
    payload: dict[str, object]
    lock: threading.Lock = field(default_factory=threading.Lock)


Runner = Callable[..., subprocess.CompletedProcess]


class CameraReplayManager:
    """Register raw clips cheaply and encode them only after a user request."""

    def __init__(
        self,
        output_root: str | Path,
        *,
        runner: Runner | None = None,
        ffmpeg_path: str = "ffmpeg",
    ) -> None:
        self.output_root = Path(output_root).expanduser().resolve()
        self._runner = runner or subprocess.run
        self._resolve_ffmpeg = runner is None
        self._ffmpeg_path = ffmpeg_path
        self._entries: dict[str, _ReplayEntry] = {}
        self._ids_by_capture: dict[Path, str] = {}
        self._lock = threading.Lock()
        self._encode_semaphore = threading.BoundedSemaphore(value=1)

    def register(self, capture_path: str | Path, metadata: dict) -> dict[str, object]:
        """Advertise an existing raw capture without doing any video work."""
        resolved = Path(capture_path).expanduser().resolve()
        if not resolved.is_relative_to(self.output_root):
            raise ValueError("camera capture is outside camera output directory")
        if not (resolved / "frames.npz").is_file():
            raise ValueError("camera capture has no frames archive")

        frame_count = self._positive_int(metadata.get("frame_count"), "frame_count")
        pre_trigger_count = self._positive_int(
            metadata.get("pre_trigger_frames"),
            "pre_trigger_frames",
        )
        trigger_frame = min(frame_count - 1, pre_trigger_count - 1)
        settings = metadata.get("settings")
        saved_mirror_horizontal = (
            settings.get("mirror_horizontal", False) if isinstance(settings, dict) else False
        )
        if not isinstance(saved_mirror_horizontal, bool):
            raise ValueError("camera capture has invalid mirror_horizontal setting")

        with self._lock:
            existing_id = self._ids_by_capture.get(resolved)
            if existing_id is not None:
                return dict(self._entries[existing_id].payload)

            replay_id = uuid4().hex
            payload: dict[str, object] = {
                "id": replay_id,
                "frame_count": frame_count,
                "trigger_frame": trigger_frame,
                "playback_fps": PLAYBACK_FPS,
                "duration_seconds": frame_count / PLAYBACK_FPS,
                # The operator-facing replay is mirrored by default. Captures
                # already mirrored during persistence must not be flipped twice.
                "display_mirror_horizontal": not saved_mirror_horizontal,
            }
            self._entries[replay_id] = _ReplayEntry(
                replay_id=replay_id,
                capture_path=resolved,
                payload=payload,
            )
            self._ids_by_capture[resolved] = replay_id
            return dict(payload)

    def prepare(self, replay_id: str) -> PreparedCameraReplay:
        """Build and cache one MP4. This is called only by the manual API."""
        entry = self._entry(replay_id)
        video_path = entry.capture_path / "replay.mp4"

        with entry.lock:
            try:
                cached = video_path.is_file() and video_path.stat().st_size > 0
            except OSError as error:
                raise ReplayPreparationError("Camera replay storage is unavailable") from error
            if cached:
                return PreparedCameraReplay(video_path=video_path, payload=dict(entry.payload))

            # Loading the archive is included in the manager-wide slot so two
            # manual requests cannot spike Pi memory before FFmpeg starts.
            with self._encode_semaphore:
                return self._encode_replay(entry, video_path)

    def _encode_replay(
        self,
        entry: _ReplayEntry,
        video_path: Path,
    ) -> PreparedCameraReplay:
        """Encode one uncached replay while the manager-wide slot is held."""
        frames = self._load_frames(entry.capture_path / "frames.npz")
        frame_count, height, width = frames.shape
        if frame_count != entry.payload["frame_count"]:
            raise ReplayPreparationError("Camera replay frame metadata does not match capture")

        ffmpeg = self._resolved_ffmpeg_path()
        temporary_path = entry.capture_path / f".replay-{uuid4().hex}.mp4"
        command = self._ffmpeg_command(
            ffmpeg,
            temporary_path,
            width=width,
            height=height,
        )
        try:
            self._runner(
                command,
                input=np.ascontiguousarray(frames).tobytes(),
                check=True,
                capture_output=True,
                timeout=ENCODE_TIMEOUT_S,
            )
            if not temporary_path.is_file() or temporary_path.stat().st_size <= 0:
                raise ReplayPreparationError("Camera replay encoder produced no video")
            temporary_path.replace(video_path)
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or b"").decode("utf-8", errors="replace").strip()
            logger.warning("[CAMERA] Replay encoding failed: %s", detail or error)
            raise ReplayPreparationError("Camera replay encoding failed") from error
        except subprocess.TimeoutExpired as error:
            logger.warning("[CAMERA] Replay encoding timed out")
            raise ReplayPreparationError("Camera replay encoding timed out") from error
        except OSError as error:
            logger.warning("[CAMERA] Replay video could not be written: %s", error)
            raise ReplayPreparationError("Camera replay video could not be written") from error
        finally:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("[CAMERA] Could not remove partial replay %s", temporary_path)

        return PreparedCameraReplay(video_path=video_path, payload=dict(entry.payload))

    def video_path(self, replay_id: str) -> Path:
        """Return a prepared MP4 without triggering conversion."""
        entry = self._entry(replay_id)
        video_path = entry.capture_path / "replay.mp4"
        try:
            ready = video_path.is_file() and video_path.stat().st_size > 0
        except OSError as error:
            raise ReplayPreparationError("Camera replay storage is unavailable") from error
        if not ready:
            raise ReplayNotReadyError("Camera replay has not been prepared")
        return video_path

    def unregister(self, replay_id: str) -> None:
        """Remove live access to a replay while retaining session artifacts."""
        with self._lock:
            entry = self._entries.pop(replay_id, None)
            if entry is not None:
                self._ids_by_capture.pop(entry.capture_path, None)

    def _entry(self, replay_id: str) -> _ReplayEntry:
        with self._lock:
            entry = self._entries.get(replay_id)
        if entry is None:
            raise ReplayNotFoundError("Camera replay was not found")
        return entry

    def _resolved_ffmpeg_path(self) -> str:
        if not self._resolve_ffmpeg:
            return self._ffmpeg_path
        resolved = shutil.which(self._ffmpeg_path)
        if resolved is None:
            raise ReplayPreparationError("FFmpeg is not installed")
        return resolved

    @staticmethod
    def _positive_int(value, label: str) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"camera capture has invalid {label}") from error
        if parsed < 1:
            raise ValueError(f"camera capture has invalid {label}")
        return parsed

    @staticmethod
    def _load_frames(frames_path: Path) -> np.ndarray:
        try:
            with np.load(frames_path) as archive:
                frames = archive["frames"]
        except (OSError, KeyError, ValueError) as error:
            raise ReplayPreparationError("Camera replay frames could not be loaded") from error
        if frames.ndim != 3 or frames.dtype != np.uint8 or not all(frames.shape):
            raise ReplayPreparationError("Camera replay frames have an unsupported format")
        return frames

    @staticmethod
    def _ffmpeg_command(
        ffmpeg: str,
        output_path: Path,
        *,
        width: int,
        height: int,
    ) -> list[str]:
        return [
            ffmpeg,
            "-loglevel",
            "error",
            "-nostdin",
            "-f",
            "rawvideo",
            "-pixel_format",
            "gray",
            "-video_size",
            f"{width}x{height}",
            "-framerate",
            str(PLAYBACK_FPS),
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-y",
            str(output_path),
        ]
