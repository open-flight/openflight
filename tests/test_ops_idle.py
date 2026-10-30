"""The OPS243-A idles (PI) whenever OpenFlight is not using it."""

import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openflight.ops243 import OPS243Radar

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OPS_IDLE_SCRIPT = PROJECT_ROOT / "scripts" / "setup" / "ops_idle.py"
INSTALL_SCRIPT = PROJECT_ROOT / "scripts" / "setup" / "install_ops_idle_service.sh"


def _load_ops_idle():
    spec = importlib.util.spec_from_file_location("ops_idle", OPS_IDLE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingSerial:
    """Fake serial port that records writes and answers quietly."""

    def __init__(self):
        self.is_open = True
        self.writes = []

    @property
    def in_waiting(self):
        return 0

    def read(self, _n):
        return b""

    def reset_input_buffer(self):
        pass

    def write(self, data):
        self.writes.append(data)

    def flush(self):
        pass


def test_idle_sends_pi_and_no_mode_change(monkeypatch):
    monkeypatch.setattr(OPS243Radar, "_read_reply", lambda _self, *_a, **_kw: "")
    radar = OPS243Radar.__new__(OPS243Radar)
    radar.serial = RecordingSerial()

    radar.idle()

    assert radar.serial.writes == [b"PI"]


def _monitor_with_mock_radar():
    from openflight.rolling_buffer import RollingBufferMonitor

    monitor = RollingBufferMonitor(port=None, trigger_type="sound")
    monitor.radar = MagicMock()
    return monitor


def test_monitor_disconnect_idles_the_radar_before_releasing_the_port():
    monitor = _monitor_with_mock_radar()

    monitor.disconnect()

    calls = [name for name, *_ in monitor.radar.method_calls]
    assert calls.index("idle") < calls.index("disconnect")
    assert not monitor.radar.disable_rolling_buffer.called


def test_monitor_disconnect_releases_the_port_when_idle_fails():
    monitor = _monitor_with_mock_radar()
    monitor.radar.idle.side_effect = ConnectionError("Not connected to radar")

    monitor.disconnect()

    assert monitor.radar.disconnect.called


@pytest.mark.parametrize(
    ("argv", "is_server"),
    [
        (["/home/pi/openflight/.venv/bin/python", "-m", "openflight.server", "--mock"], True),
        (
            [
                "/home/pi/openflight/.venv/bin/python3",
                "/home/pi/openflight/.venv/bin/openflight-server",
            ],
            True,
        ),
        (["/home/pi/openflight/.venv/bin/openflight-server", "--web-port", "8080"], True),
        (["bash", "-c", "python -m openflight.server --mock"], False),
        (["journalctl", "-u", "openflight.service"], False),
        (["python3", "scripts/setup/ops_idle.py", "--port", "/dev/ttyAMA0"], False),
        ([], False),
    ],
)
def test_only_real_server_processes_count_as_running(argv, is_server):
    assert _load_ops_idle().is_server_command(argv) is is_server


def test_openflight_running_reads_proc_cmdlines(tmp_path):
    ops_idle = _load_ops_idle()
    (tmp_path / "self").mkdir()
    (tmp_path / "12").mkdir()
    (tmp_path / "12" / "cmdline").write_bytes(b"bash\0-c\0python -m openflight.server\0")
    assert ops_idle.openflight_running(tmp_path) is False

    (tmp_path / "34").mkdir()
    (tmp_path / "34" / "cmdline").write_bytes(b"/usr/bin/python3\0-m\0openflight.server\0--mock\0")
    assert ops_idle.openflight_running(tmp_path) is True


def test_boot_idle_leaves_a_running_openflight_alone():
    ops_idle = _load_ops_idle()
    factory = MagicMock()

    result = ops_idle.main(
        ["--port", "/dev/ttyAMA0"], radar_factory=factory, is_openflight_running=lambda: True
    )

    assert result == 0
    assert not factory.called


def test_boot_idle_idles_the_radar_on_the_given_port():
    ops_idle = _load_ops_idle()
    radar = MagicMock(port="/dev/ttyAMA0")
    factory = MagicMock(return_value=radar)

    result = ops_idle.main(
        ["--port", "/dev/ttyAMA0"], radar_factory=factory, is_openflight_running=lambda: False
    )

    assert result == 0
    factory.assert_called_once_with(port="/dev/ttyAMA0")
    assert radar.idle.called
    assert radar.disconnect.called


def test_boot_idle_retries_then_fails_and_always_releases_the_port():
    ops_idle = _load_ops_idle()
    radar = MagicMock(port=None)
    radar.connect.side_effect = ConnectionError("No OPS243 radar found")
    factory = MagicMock(return_value=radar)
    sleeps = []

    result = ops_idle.main(
        ["--attempts", "3", "--retry-delay", "0.5"],
        radar_factory=factory,
        is_openflight_running=lambda: False,
        sleep=sleeps.append,
    )

    assert result == 1
    assert factory.call_count == 3
    assert radar.disconnect.call_count == 3
    assert sleeps == [0.5, 0.5]


def _render_unit(*args):
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; shift; for a in "$@"; do PORT="$a"; done; render_unit',
            "install-test",
            str(INSTALL_SCRIPT),
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_installer_has_valid_bash_syntax():
    subprocess.run(["bash", "-n", INSTALL_SCRIPT], check=True)


@pytest.mark.parametrize("port", ["/dev/ttyAMA0", ""])
def test_installer_unit_runs_before_openflight(port):
    unit = _render_unit(port)

    assert "Type=oneshot" in unit
    assert "Before=openflight.service" in unit
    assert f"{PROJECT_ROOT.as_posix()}/scripts/setup/ops_idle.py" in unit
    assert ("--port /dev/ttyAMA0" in unit) is bool(port)
