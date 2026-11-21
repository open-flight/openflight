"""Camera capture in its own process, pinned to its own CPU core."""

import os
import time
from pathlib import Path

import pytest

from openflight.camera.capture_runtime import CameraCaptureSettings
from openflight.camera.process import CameraCaptureProcess

TESTS = str(Path(__file__).parent)
SETTINGS = CameraCaptureSettings(width=640, height=100, fps=600.0)


def _process(tmp_path, factory="camera_process_fakes:FakeRuntime", core=None):
    return CameraCaptureProcess(
        output_dir=tmp_path,
        settings=SETTINGS,
        use_gpio_trigger=False,
        cpu_core=core,
        runtime_factory=factory,
        extra_python_path=[TESTS],
        pin_parent=False,
        call_timeout_s=1.0,
        restart_backoff_s=(0.1, 0.2),
    )


def _wait_until(predicate, timeout_s=15.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def camera(tmp_path):
    proc = _process(tmp_path)
    proc.start()
    yield proc
    proc.stop()


def test_runs_in_a_separate_process_and_reports_settings(camera):
    status = camera.status()
    assert status["running"] is True
    assert status["restarts"] == 0 and status["restarting"] is False
    assert status["pid"] != os.getpid()
    # Settings changed by the child during start (auto exposure) reach the parent.
    assert camera.settings.exposure_us == 777 and camera.settings.gain == 7.0


def test_trigger_produces_a_capture_matched_to_the_shot(camera):
    edge = time.time()
    camera.notify_trigger(edge)
    saved = camera.capture_for_shot(edge + 0.3, timeout_s=2.0)
    assert saved is not None and saved.sequence == 1
    assert saved.trigger_timestamp == pytest.approx(edge)
    assert saved.path.name == "c1"


def test_read_only_calls_pass_through(camera):
    assert camera.capture_preview_jpeg(quality=55).endswith(bytes([55]))
    assert camera.camera_analysis_eligible is True
    assert camera.exposure_quality() == {"status": "good"}
    assert camera.auto_exposure_status()["exposure_us"] == 777
    assert camera.vertical_crop_status()["raw_crop_adjustable"] is False


def test_child_errors_keep_their_type(camera):
    with pytest.raises(ValueError, match="vertical crop is unavailable"):
        camera.update_vertical_crop(10)
    assert camera.status()["running"] is True  # the child survives a failed command


def test_child_is_pinned_to_its_core(tmp_path):
    core = max(os.sched_getaffinity(0))
    proc = _process(tmp_path, core=core)
    proc.start()
    try:
        assert proc.status()["affinity"] == [core]
    finally:
        proc.stop()


def test_crashed_child_is_restarted(tmp_path):
    proc = _process(tmp_path, factory="camera_process_fakes:CrashOnFirstTriggerRuntime")
    proc.start()
    try:
        first_pid = proc.status()["pid"]
        proc.notify_trigger(time.time())  # the child dies handling this edge
        assert _wait_until(lambda: proc.status().get("pid") not in (None, first_pid))
        status = proc.status()
        assert status["running"] is True and status["restarts"] == 1
        assert "exited" in status["last_error"] or "closed" in status["last_error"]
        # The restarted camera captures shots again.
        edge = time.time()
        assert proc.notify_trigger(edge) is True
        assert proc.capture_for_shot(edge, timeout_s=2.0) is not None
    finally:
        proc.stop()


def test_start_failure_is_retried_until_the_camera_starts(tmp_path):
    proc = _process(tmp_path, factory="camera_process_fakes:FailFirstStartRuntime")
    proc.start()  # does not give up on a failed start
    try:
        assert _wait_until(lambda: proc.status()["running"] is True)
        assert proc.status()["restarts"] == 1
        assert proc.settings.exposure_us == 777
    finally:
        proc.stop()


def test_camera_that_never_starts_keeps_retrying_and_reports_it(tmp_path):
    proc = _process(tmp_path, factory="camera_process_fakes:AlwaysFailingRuntime")
    proc.start()
    try:
        assert _wait_until(lambda: proc.status()["restarts"] >= 3)
        status = proc.status()
        assert status["running"] is False and status["restarting"] is True
        assert "picamera2 is required" in status["last_error"]
        assert proc.capture_preview_jpeg() is None
        assert proc.camera_analysis_eligible is False
        assert proc.notify_trigger(time.time()) is False
    finally:
        proc.stop()


def test_hung_child_is_killed_and_restarted(tmp_path):
    proc = _process(tmp_path, factory="camera_process_fakes:HangOnFirstStatusRuntime")
    proc.start()
    try:
        assert proc.status()["running"] is False  # times out after call_timeout_s
        assert _wait_until(lambda: proc.status()["running"] is True)
        assert proc.status()["restarts"] == 1
    finally:
        proc.stop()


def test_stop_during_restarts_returns_promptly(tmp_path):
    proc = _process(tmp_path, factory="camera_process_fakes:AlwaysFailingRuntime")
    proc.start()
    started = time.monotonic()
    proc.stop()
    assert time.monotonic() - started < 10.0
    assert proc.status()["running"] is False


def test_stop_ends_the_child(tmp_path):
    proc = _process(tmp_path)
    proc.start()
    pid = proc.status()["pid"]
    proc.stop()
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_child_is_pinned_to_a_set_of_cores(tmp_path):
    available = sorted(os.sched_getaffinity(0))
    if len(available) < 3:
        pytest.skip("needs three or more cores")
    cores = available[-2:]
    proc = _process(tmp_path, core=cores)
    proc.start()
    try:
        assert proc.status()["affinity"] == cores
        assert proc.cpu_cores == frozenset(cores)
    finally:
        proc.stop()


def test_negative_or_empty_core_settings_mean_unpinned():
    from openflight.camera.process import _core_set

    assert _core_set(None) is None
    assert _core_set(-1) is None
    assert _core_set(()) is None
    assert _core_set(3) == frozenset({3})
    assert _core_set((2, 3)) == frozenset({2, 3})
