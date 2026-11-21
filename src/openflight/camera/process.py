"""High-speed camera capture in its own process, pinned to its own CPU core.

Capturing at ~576 fps takes a Python callback per frame. In the server process
those callbacks compete for the GIL with the TI radar math, which more than
doubled TI processing time (~1.5 s -> ~3.5 s on the Pi 4) and dropped ~12% of
camera frames. Here the whole :class:`CameraCaptureRuntime` runs in a child
process (``python -m openflight.camera.process``) pinned to one core, and the
server keeps the other cores.

:class:`CameraCaptureProcess` is a drop-in stand-in for the runtime: the server
calls the same methods. Three connections link the processes:

* ``cmd``     request/response calls (status, preview, crop, ...)
* ``trigger`` sound-trigger edges, parent -> child, never behind a slow call
* ``events``  saved captures, child -> parent, matched to shots in the parent

If the child crashes, hangs or fails to start, a supervisor restarts it with a
short backoff and no retry limit; meanwhile read-only calls return "not
running" defaults (with the restart count and last error) so the UI shows it.
"""

from __future__ import annotations

import importlib
import logging
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from multiprocessing.connection import Client, Connection, Listener
from pathlib import Path
from typing import Iterable

import numpy as np

from .auto_exposure import measure_exposure
from .capture_runtime import CameraCaptureSettings, CaptureInbox, SavedCameraCapture

logger = logging.getLogger(__name__)

DEFAULT_RUNTIME_FACTORY = "openflight.camera.capture_runtime:CameraCaptureRuntime"
CONNECT_TIMEOUT_S = 15.0
START_TIMEOUT_S = 30.0
CALL_TIMEOUT_S = 10.0
# Moving the sensor window restarts the camera and recalibrates exposure.
SLOW_CALL_TIMEOUT_S = 30.0
STOP_TIMEOUT_S = 6.0
EVENT_POLL_S = 0.5
# Delay before each restart attempt; the last value repeats indefinitely.
RESTART_BACKOFF_S = (0.5, 1.0, 2.0, 5.0, 10.0, 30.0)

# Calls the parent may make on the child's runtime.
_METHODS = frozenset(
    {
        "status",
        "vertical_crop_status",
        "update_vertical_crop",
        "auto_exposure_status",
        "exposure_quality",
        "capture_preview_jpeg",
    }
)
_PROPERTIES = frozenset({"camera_analysis_eligible"})
_ERROR_TYPES = {
    "ValueError": ValueError,
    "RuntimeError": RuntimeError,
    "TimeoutError": TimeoutError,
}


class CameraProcessDied(RuntimeError):
    """The camera child process is gone or stopped answering."""


# The capture callbacks use about 1.1 cores at 576 fps, so one core is not enough.
DEFAULT_CAPTURE_CORES = (2, 3)


def _core_set(cpu_core: int | Iterable[int] | None) -> frozenset[int] | None:
    """Cores to pin to from an int, an iterable of ints, or None (negative: none)."""
    if cpu_core is None:
        return None
    cores = frozenset([cpu_core] if isinstance(cpu_core, int) else (int(c) for c in cpu_core))
    if not cores or any(core < 0 for core in cores):
        return None
    return cores


def _pin_all_threads(cores: set[int]) -> None:
    """Set the CPU affinity of every thread in this process."""
    for task in Path("/proc/self/task").iterdir():
        try:
            os.sched_setaffinity(int(task.name), cores)
        except (ProcessLookupError, PermissionError, OSError):
            # A thread that exited meanwhile, or a restricted container.
            continue


def _load_factory(dotted: str):
    module_name, _, attribute = dotted.partition(":")
    return getattr(importlib.import_module(module_name), attribute)


@dataclass(frozen=True)
class _Init:
    output_dir: str
    settings: CameraCaptureSettings
    use_gpio_trigger: bool
    runtime_factory: str
    cpu_core: frozenset[int] | None


class CameraCaptureProcess:  # pylint: disable=too-many-instance-attributes
    """Parent-side stand-in for :class:`CameraCaptureRuntime` running in a child.

    A supervisor thread restarts the child whenever it crashes, stops
    answering, or fails to start, with a short backoff and no retry limit:
    running without the camera is a failure, never a mode.
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        *,
        output_dir: str | Path,
        settings: CameraCaptureSettings,
        use_gpio_trigger: bool = True,
        cpu_core: int | Iterable[int] | None = DEFAULT_CAPTURE_CORES,
        runtime_factory: str = DEFAULT_RUNTIME_FACTORY,
        extra_python_path: list[str] | None = None,
        pin_parent: bool = True,
        call_timeout_s: float = CALL_TIMEOUT_S,
        restart_backoff_s: tuple[float, ...] = RESTART_BACKOFF_S,
    ):
        self.output_dir = Path(output_dir).expanduser()
        self.settings = settings
        self._use_gpio_trigger = use_gpio_trigger
        self._cpu_cores = _core_set(cpu_core)
        self._runtime_factory = runtime_factory
        self._extra_python_path = list(extra_python_path or [])
        self._pin_parent = pin_parent
        self._call_timeout_s = call_timeout_s
        self._restart_backoff_s = tuple(restart_backoff_s) or RESTART_BACKOFF_S
        self._inbox = CaptureInbox()
        self._cores: frozenset[int] | None = None
        self._child: subprocess.Popen | None = None
        self._listener: Listener | None = None
        self._socket_dir: tempfile.TemporaryDirectory | None = None
        self._cmd: Connection | None = None
        self._trigger: Connection | None = None
        self._events: Connection | None = None
        self._call_lock = threading.Lock()
        self._trigger_lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._alive = False
        self._dead = threading.Event()
        self._stopping = threading.Event()
        self._supervisor: threading.Thread | None = None
        self._restarts = 0
        self._last_error: str | None = None

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Start the child and its supervisor. A failed start is retried, not raised."""
        if self._supervisor is not None and self._supervisor.is_alive():
            return
        self._stopping.clear()
        self._cores = self._usable_cores()
        if self._cores is not None and self._pin_parent:
            _pin_all_threads(set(os.sched_getaffinity(0)) - self._cores)
        self._dead.clear()
        try:
            self._spawn()
        except Exception as error:  # pylint: disable=broad-exception-caught
            self._record_failure(f"start failed: {error}")
        self._supervisor = threading.Thread(
            target=self._supervise, name="camera-process-supervisor", daemon=True
        )
        self._supervisor.start()

    def stop(self) -> None:
        """Stop capture in the child, end the process, and stop supervising."""
        self._stopping.set()
        self._dead.set()  # wake the supervisor so it can exit
        if self._alive:
            try:
                self._call("stop", timeout_s=STOP_TIMEOUT_S)
            except (CameraProcessDied, RuntimeError):
                logger.warning("[CAMERA] Capture process did not stop cleanly")
        self._alive = False
        if self._supervisor is not None:
            self._supervisor.join(timeout=STOP_TIMEOUT_S + max(self._restart_backoff_s))
            self._supervisor = None
        self._shutdown_child(graceful=True)

    # -- calls the server makes ---------------------------------------------

    def status(self) -> dict:
        """Runtime status plus supervision state; not-running while restarting."""
        try:
            payload = dict(self._call("status"))
        except CameraProcessDied:
            payload = {
                "running": False,
                "armed": False,
                "buffered_frames": 0,
                "required_pre_frames": self.settings.pre_frames,
                "auto_exposure": {"enabled": self.settings.auto_exposure, "status": "unavailable"},
            }
        payload.update(
            {
                "restarts": self._restarts,
                "restarting": not self._alive and not self._stopping.is_set(),
                "last_error": self._last_error,
            }
        )
        return payload

    def vertical_crop_status(self) -> dict:
        """Sensor-window adjustment state from the child."""
        try:
            return self._call("vertical_crop_status")
        except CameraProcessDied:
            return {"raw_crop_adjustable": False, "vertical_offset_px": 0}

    def update_vertical_crop(self, offset_px: int) -> dict:
        """Move the sensor window (restarts capture in the child)."""
        return self._call("update_vertical_crop", offset_px, timeout_s=SLOW_CALL_TIMEOUT_S)

    def auto_exposure_status(self) -> dict:
        """Auto-exposure controller state from the child."""
        try:
            return self._call("auto_exposure_status")
        except CameraProcessDied:
            return {"enabled": self.settings.auto_exposure, "status": "unavailable"}

    @property
    def camera_analysis_eligible(self) -> bool:
        """Whether lighting permits camera metrics; False while the child is down."""
        try:
            return bool(self._call("camera_analysis_eligible"))
        except CameraProcessDied:
            return False

    def exposure_quality(self) -> dict:
        """Exposure rating of the latest frame."""
        try:
            return self._call("exposure_quality")
        except CameraProcessDied:
            return measure_exposure(np.asarray([])).to_dict()

    def capture_preview_jpeg(self, quality: int = 80) -> bytes | None:
        """Latest frame as JPEG, or None."""
        try:
            return self._call("capture_preview_jpeg", quality=quality)
        except CameraProcessDied:
            return None

    def notify_trigger(self, timestamp: float | None = None) -> bool:
        """Forward a sound-trigger edge to the child without waiting on other calls."""
        connection = self._trigger
        if not self._alive or connection is None:
            logger.warning("[CAMERA] Trigger edge while the capture process is restarting")
            return False
        edge = timestamp if timestamp is not None else time.time()
        try:
            with self._trigger_lock:
                connection.send(float(edge))
        except (OSError, EOFError, ValueError):
            self._mark_dead("trigger channel closed", connection)
            return False
        return True

    def capture_for_shot(
        self, impact_timestamp: float | None, *, timeout_s: float = 1.0
    ) -> SavedCameraCapture | None:
        """Consume the saved capture matching the OPS shot."""
        return self._inbox.take(
            impact_timestamp,
            tolerance_s=self.settings.match_tolerance_s,
            timeout_s=timeout_s if self._alive else 0.0,
        )

    # -- supervision ---------------------------------------------------------

    def _supervise(self) -> None:
        failures = 0
        while not self._stopping.is_set():
            self._dead.wait()
            if self._stopping.is_set():
                return
            delay = self._restart_backoff_s[min(failures, len(self._restart_backoff_s) - 1)]
            if self._stopping.wait(delay):
                return
            self._restarts += 1
            logger.warning(
                "[CAMERA] Restarting capture process (restart %d, last error: %s)",
                self._restarts,
                self._last_error,
            )
            self._shutdown_child(graceful=False)
            self._dead.clear()
            try:
                self._spawn()
            except Exception as error:  # pylint: disable=broad-exception-caught
                failures += 1
                self._record_failure(f"restart failed: {error}")
                continue
            failures = 0
            logger.info("[CAMERA] Capture process restarted (restart %d)", self._restarts)

    def _record_failure(self, reason: str) -> None:
        self._last_error = reason
        logger.error("[CAMERA] %s; retrying", reason)
        self._dead.set()

    def _mark_dead(self, reason: str, connection: Connection | None = None) -> None:
        # A channel from an already-replaced child must not kill the new one.
        if connection is not None and connection not in (self._cmd, self._trigger, self._events):
            return
        if self._alive:
            logger.error("[CAMERA] Capture process lost (%s); restarting", reason)
            self._last_error = reason
        self._alive = False
        self._dead.set()

    # -- internals -----------------------------------------------------------

    @property
    def cpu_cores(self) -> frozenset[int] | None:
        """Cores the capture process is pinned to (None: not pinned)."""
        return self._cores

    def _usable_cores(self) -> frozenset[int] | None:
        if self._cpu_cores is None:
            return None
        available = os.sched_getaffinity(0)
        if not self._cpu_cores <= available or not available - self._cpu_cores:
            logger.warning(
                "[CAMERA] CPU cores %s unavailable (have %s); capture process not pinned",
                sorted(self._cpu_cores),
                sorted(available),
            )
            return None
        return self._cpu_cores

    def _spawn(self) -> None:
        """Launch one child generation and start capture in it."""
        with self._lifecycle_lock:
            self._socket_dir = tempfile.TemporaryDirectory(prefix="openflight-camera-")
            address = str(Path(self._socket_dir.name) / "camera.sock")
            authkey = secrets.token_bytes(32)
            self._listener = Listener(address, family="AF_UNIX", authkey=authkey)
            env = dict(os.environ)
            if self._extra_python_path:
                env["PYTHONPATH"] = os.pathsep.join(
                    self._extra_python_path + [p for p in [env.get("PYTHONPATH")] if p]
                )
            self._child = subprocess.Popen(  # pylint: disable=consider-using-with
                [sys.executable, "-m", "openflight.camera.process", address, authkey.hex()],
                env=env,
            )
            try:
                self._accept_connections()
                self._cmd.send(
                    _Init(
                        output_dir=str(self.output_dir),
                        settings=self.settings,
                        use_gpio_trigger=self._use_gpio_trigger,
                        runtime_factory=self._runtime_factory,
                        cpu_core=self._cores,
                    )
                )
                reply = self._receive(self._cmd, START_TIMEOUT_S)
                if reply[0] != "ok":
                    raise _ERROR_TYPES.get(reply[1], RuntimeError)(reply[2])
            except Exception:
                self._shutdown_child(graceful=False)
                raise
            self.settings = reply[1]
            self._alive = True
            threading.Thread(
                target=self._events_loop,
                args=(self._events,),
                name="camera-process-events",
                daemon=True,
            ).start()
            logger.info(
                "[CAMERA] Capture process pid %d on CPU core %s",
                self._child.pid,
                ",".join(map(str, sorted(self._cores))) if self._cores is not None else "any",
            )

    def _accept_connections(self) -> None:
        connections: dict[str, Connection] = {}
        failure: list[BaseException] = []

        def accept():
            try:
                while len(connections) < 3:
                    connection = self._listener.accept()
                    connections[connection.recv()] = connection
            except (OSError, EOFError) as error:  # listener closed on timeout
                failure.append(error)

        thread = threading.Thread(target=accept, daemon=True)
        thread.start()
        thread.join(CONNECT_TIMEOUT_S)
        if thread.is_alive() or failure or set(connections) != {"cmd", "trigger", "events"}:
            raise RuntimeError("camera capture process did not connect")
        self._cmd = connections["cmd"]
        self._trigger = connections["trigger"]
        self._events = connections["events"]

    def _receive(self, connection: Connection, timeout_s: float):
        try:
            if not connection.poll(timeout_s):
                raise CameraProcessDied(f"camera process did not answer within {timeout_s:.0f}s")
            return connection.recv()
        except (EOFError, OSError) as error:
            child = self._child
            code = child.poll() if child is not None else None
            detail = f"exited ({code})" if code is not None else f"connection closed: {error}"
            raise CameraProcessDied(f"camera process {detail}") from error

    def _call(self, name: str, *args, timeout_s: float | None = None, **kwargs):
        timeout_s = self._call_timeout_s if timeout_s is None else timeout_s
        with self._call_lock:
            connection = self._cmd
            if not self._alive or connection is None:
                raise CameraProcessDied("camera process is not running")
            try:
                connection.send((name, args, kwargs))
                reply = self._receive(connection, timeout_s)
            except (OSError, ValueError) as error:
                self._mark_dead(f"command channel closed: {error}", connection)
                raise CameraProcessDied("camera process is not running") from error
            except CameraProcessDied as error:
                self._mark_dead(str(error), connection)
                raise
        status, *rest = reply
        if status == "ok":
            result, self.settings = rest
            return result
        error_type, message, self.settings = rest
        raise _ERROR_TYPES.get(error_type, RuntimeError)(message)

    def _events_loop(self, connection: Connection) -> None:
        while True:
            try:
                kind, payload, settings = connection.recv()
            except (EOFError, OSError):
                child = self._child
                code = child.poll() if child is not None else None
                reason = (
                    f"camera process exited ({code})"
                    if code is not None
                    else "event channel closed"
                )
                self._mark_dead(reason, connection)
                return
            self.settings = settings
            if kind == "capture":
                self._inbox.add(payload)

    def _shutdown_child(self, *, graceful: bool) -> None:
        with self._lifecycle_lock:
            for connection in (self._cmd, self._trigger, self._events):
                if connection is not None:
                    try:
                        connection.close()
                    except OSError:
                        pass
            self._cmd = self._trigger = self._events = None
            child, self._child = self._child, None
            if child is not None:
                if not graceful and child.poll() is None:
                    child.terminate()
                try:
                    child.wait(timeout=STOP_TIMEOUT_S if graceful else 2.0)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=STOP_TIMEOUT_S)
            if self._listener is not None:
                self._listener.close()
                self._listener = None
            if self._socket_dir is not None:
                self._socket_dir.cleanup()
                self._socket_dir = None


# -- child side ----------------------------------------------------------------


def _child_main(address: str, authkey: bytes) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    connections = {}
    for role in ("cmd", "trigger", "events"):
        connection = Client(address, family="AF_UNIX", authkey=authkey)
        connection.send(role)
        connections[role] = connection
    cmd, trigger, events = connections["cmd"], connections["trigger"], connections["events"]
    init: _Init = cmd.recv()
    if init.cpu_core is not None:
        _pin_all_threads(set(init.cpu_core))
    try:
        runtime = _load_factory(init.runtime_factory)(
            output_dir=init.output_dir,
            settings=init.settings,
            use_gpio_trigger=init.use_gpio_trigger,
        )
        runtime.start()
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.exception("[CAMERA] Capture process could not start")
        cmd.send(("error", type(error).__name__, str(error)))
        return 1
    cmd.send(("ok", runtime.settings))

    running = threading.Event()
    running.set()
    send_lock = threading.Lock()

    def forward_triggers():
        while running.is_set():
            try:
                edge = trigger.recv()
            except (EOFError, OSError):
                running.clear()
                return
            runtime.notify_trigger(edge)

    def forward_captures():
        while running.is_set():
            saved = runtime.capture_for_shot(None, timeout_s=EVENT_POLL_S)
            if saved is None:
                continue
            try:
                with send_lock:
                    events.send(("capture", saved, runtime.settings))
            except (OSError, ValueError):
                running.clear()
                return

    for target in (forward_triggers, forward_captures):
        threading.Thread(target=target, name=target.__name__, daemon=True).start()

    try:
        while running.is_set():
            try:
                name, args, kwargs = cmd.recv()
            except (EOFError, OSError):
                break
            if name == "stop":
                runtime.stop()
                cmd.send(("ok", None, runtime.settings))
                break
            try:
                if name in _PROPERTIES:
                    result = getattr(runtime, name)
                elif name in _METHODS:
                    result = getattr(runtime, name)(*args, **kwargs)
                else:
                    raise RuntimeError(f"unknown camera call {name!r}")
            except Exception as error:  # pylint: disable=broad-exception-caught
                cmd.send(("error", type(error).__name__, str(error), runtime.settings))
            else:
                cmd.send(("ok", result, runtime.settings))
    finally:
        running.clear()
        try:
            runtime.stop()
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("[CAMERA] Capture stop failed", exc_info=True)
    return 0


if __name__ == "__main__":
    sys.exit(_child_main(sys.argv[1], bytes.fromhex(sys.argv[2])))
