"""Tests for the optional touchscreen sleep endpoint and double-tap wake service."""

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

from openflight import server

WAKE_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "touchscreen" / "screen_wake.py"


def _load_screen_wake():
    spec = importlib.util.spec_from_file_location("screen_wake", WAKE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _backlight(root: Path, name: str, level: str = "15", max_level: str = "31") -> Path:
    device = root / name
    device.mkdir(parents=True)
    (device / "brightness").write_text(f"{level}\n")
    (device / "max_brightness").write_text(f"{max_level}\n")
    return device / "brightness"


def test_sleep_finds_the_touch_display_2_backlight_without_configuration(tmp_path, monkeypatch):
    backlight = _backlight(tmp_path, "panel_backlight@0")
    monkeypatch.delenv("OPENFLIGHT_BACKLIGHT_PATH", raising=False)
    monkeypatch.setattr(server, "BACKLIGHT_ROOT", tmp_path)

    response = server.app.test_client().post("/api/screen/sleep")

    assert response.status_code == 200
    assert backlight.read_text() == "0\n"


def test_sleep_reports_no_backlight_when_none_exists(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENFLIGHT_BACKLIGHT_PATH", raising=False)
    monkeypatch.setattr(server, "BACKLIGHT_ROOT", tmp_path)

    response = server.app.test_client().post("/api/screen/sleep")

    assert response.status_code == 404
    assert response.json["error"] == "Display backlight not found"


@pytest.mark.parametrize("find_backlight", ["server", "wake"])
def test_backlight_detection_prefers_the_panel_and_honours_the_override(
    find_backlight, tmp_path, monkeypatch
):
    finder = (
        server.find_backlight if find_backlight == "server" else _load_screen_wake().find_backlight
    )
    monkeypatch.delenv("OPENFLIGHT_BACKLIGHT_PATH", raising=False)
    assert finder(tmp_path) is None

    other = _backlight(tmp_path, "10-0045")
    assert finder(tmp_path) == other

    panel = _backlight(tmp_path, "panel_backlight@0")
    assert finder(tmp_path) == panel

    monkeypatch.setenv("OPENFLIGHT_BACKLIGHT_PATH", str(other))
    assert finder(tmp_path) == other


def test_wake_restores_the_last_brightness_within_the_display_range():
    screen_wake = _load_screen_wake()

    assert screen_wake.wake_level(last_brightness=22, max_brightness=31) == 22
    assert screen_wake.wake_level(last_brightness=0, max_brightness=31) == 15
    assert screen_wake.wake_level(last_brightness=0, max_brightness=10) == 10
    assert screen_wake.wake_level(last_brightness=400, max_brightness=255) == 255


def test_sleep_switches_off_backlight(tmp_path, monkeypatch):
    backlight = tmp_path / "brightness"
    backlight.write_text("15\n")

    monkeypatch.setenv("OPENFLIGHT_BACKLIGHT_PATH", str(backlight))

    response = server.app.test_client().post("/api/screen/sleep")

    assert response.status_code == 200
    assert response.json["status"] == "sleeping"
    assert backlight.read_text() == "0\n"


def test_sleep_reports_missing_backlight(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "OPENFLIGHT_BACKLIGHT_PATH",
        str(tmp_path / "missing_brightness"),
    )

    response = server.app.test_client().post("/api/screen/sleep")

    assert response.status_code == 404
    assert response.json["error"] == "Display backlight not found"


def test_sleep_handles_permission_error(tmp_path, monkeypatch):
    backlight = tmp_path / "brightness"
    backlight.write_text("15\n")
    monkeypatch.setenv("OPENFLIGHT_BACKLIGHT_PATH", str(backlight))

    with patch.object(Path, "write_text", side_effect=PermissionError("Denied")):
        response = server.app.test_client().post("/api/screen/sleep")

    assert response.status_code == 500
    assert response.json["error"] == "Unable to control backlight"
