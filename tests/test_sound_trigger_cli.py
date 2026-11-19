"""Port selection for the OPS sound-trigger hardware test."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hardware-test" / "test_sound_trigger.py"
spec = importlib.util.spec_from_file_location("sound_trigger_cli_script", SCRIPT)
sound_trigger = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(sound_trigger)


@pytest.fixture
def runner(monkeypatch):
    run = Mock()
    monkeypatch.setattr(sound_trigger, "run", run)
    return run


@pytest.mark.parametrize(
    ("arguments", "expected_port"),
    [
        ([], "/dev/ttyAMA0"),
        (["--port", "/dev/ttyACM0"], "/dev/ttyACM0"),
        (["--port", "/dev/ttyAMA1"], "/dev/ttyAMA1"),
        (["--port", "/dev/cu.usbmodem1234"], "/dev/cu.usbmodem1234"),
    ],
)
def test_cli_passes_selected_port_to_runner(monkeypatch, runner, arguments, expected_port):
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), *arguments])

    sound_trigger.main()

    runner.assert_called_once_with(expected_port, 12, False, 30.0)


def test_help_explains_uart_default_and_usb_override(monkeypatch, runner, capsys):
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--help"])

    with pytest.raises(SystemExit) as exc:
        sound_trigger.main()

    assert exc.value.code == 0
    runner.assert_not_called()
    help_text = capsys.readouterr().out
    assert "default: /dev/ttyAMA0" in help_text
    assert "--port /dev/ttyACM0" in help_text
