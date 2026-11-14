"""Tests for the optional touchscreen sleep endpoint."""

from pathlib import Path
from unittest.mock import patch

from openflight import server


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
