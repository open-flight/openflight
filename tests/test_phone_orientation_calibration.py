"""Phone-assisted IWR6843 mount-orientation calibration."""

import json
import math
from types import SimpleNamespace

import pytest

from openflight import server
from openflight.iwr6843.calibration import Calibration
from openflight.phone_orientation import (
    PhoneOrientationMeasurement,
    PhoneOrientationValidationError,
    load_phone_orientation_calibration,
)


def _payload(*, tilt_deg: float = 12.0, roll_deg: float = 0.0) -> dict:
    tilt_rad = math.radians(tilt_deg)
    roll_rad = math.radians(roll_deg)
    horizontal_gravity = math.cos(tilt_rad)
    return {
        "schema_version": 1,
        "mount_tilt_deg": tilt_deg,
        "roll_deg": roll_deg,
        "gravity_x_g": math.sin(roll_rad) * horizontal_gravity,
        "gravity_y_g": -math.cos(roll_rad) * horizontal_gravity,
        "gravity_z_g": -math.sin(tilt_rad),
        "tilt_stddev_deg": 0.08,
        "roll_stddev_deg": 0.07,
        "sample_count": 120,
        "measured_at": "2026-08-07T15:30:00Z",
        "device_model": "iPhone",
    }


def _runtime(*, tilt_deg: float = 10.4, azimuth_offset_deg: float = 1.5):
    calibration = Calibration.identity()
    calibration.tilt_rad = math.radians(tilt_deg)
    return SimpleNamespace(
        calibration=calibration,
        azimuth_offset_deg=azimuth_offset_deg,
    )


def test_measurement_recomputes_orientation_from_gravity():
    measurement = PhoneOrientationMeasurement.from_payload(_payload(tilt_deg=12.25, roll_deg=-1.5))

    assert measurement.mount_tilt_deg == pytest.approx(12.25)
    assert measurement.roll_deg == pytest.approx(-1.5)
    assert measurement.sample_count == 120


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"sample_count": 10}, "at least 30"),
        ({"tilt_stddev_deg": 0.8}, "stable"),
        (_payload(roll_deg=4.0), "level the radar left-to-right"),
        ({"mount_tilt_deg": 20.0}, "gravity vector"),
    ],
)
def test_measurement_rejects_untrustworthy_samples(change, message):
    payload = _payload()
    payload.update(change)

    with pytest.raises(PhoneOrientationValidationError, match=message):
        PhoneOrientationMeasurement.from_payload(payload)


def test_phone_calibration_requires_an_enabled_ti_radar(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "iwr6843_runtime", None)
    monkeypatch.setattr(server, "PHONE_ORIENTATION_CALIBRATION_PATH", tmp_path / "orientation.json")

    response = server.app.test_client().post(
        "/api/calibration/iwr6843/orientation",
        json=_payload(),
    )

    assert response.status_code == 409
    assert "not enabled" in response.get_json()["error"]
    assert not (tmp_path / "orientation.json").exists()


def test_phone_calibration_applies_persists_and_logs_tilt(monkeypatch, tmp_path):
    runtime = _runtime()
    original_calibration = runtime.calibration
    config = {"enabled": True, "azimuth_offset_deg": runtime.azimuth_offset_deg}
    changes = []
    session = SimpleNamespace(
        log_config_change=lambda value, source: changes.append((value, source))
    )
    path = tmp_path / "orientation.json"
    monkeypatch.setattr(server, "iwr6843_runtime", runtime)
    monkeypatch.setattr(server, "iwr6843_runtime_config", config)
    monkeypatch.setattr(server, "inclinometer_service", None)
    monkeypatch.setattr(server, "PHONE_ORIENTATION_CALIBRATION_PATH", path)
    monkeypatch.setattr(server, "get_session_logger", lambda: session)

    response = server.app.test_client().post(
        "/api/calibration/iwr6843/orientation",
        json=_payload(tilt_deg=12.25),
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "applied"
    assert body["configured_iwr_tilt_deg"] == pytest.approx(12.25)
    assert body["azimuth_offset_deg"] == 1.5
    assert body["persistent"] is True
    assert runtime.calibration is not original_calibration
    assert math.degrees(runtime.calibration.tilt_rad) == pytest.approx(12.25)
    assert math.degrees(original_calibration.tilt_rad) == pytest.approx(10.4)
    assert runtime.azimuth_offset_deg == 1.5
    assert config["tilt_deg"] == pytest.approx(12.25)
    assert config["tilt_source"] == "ios_companion"
    assert changes[-1][1] == "ios_companion"

    persisted = load_phone_orientation_calibration(path)
    assert persisted["configured_iwr_tilt_deg"] == pytest.approx(12.25)
    assert persisted["measurement"]["device_model"] == "iPhone"


def test_phone_calibration_subtracts_live_enclosure_pitch(monkeypatch, tmp_path):
    runtime = _runtime()
    snapshot = SimpleNamespace(calibrated_pitch_deg=1.75)
    selection = SimpleNamespace(snapshot=snapshot, status="stable")
    sensor = SimpleNamespace(wait_for_stable=lambda timeout_s: selection)
    monkeypatch.setattr(server, "iwr6843_runtime", runtime)
    monkeypatch.setattr(server, "iwr6843_runtime_config", {"enabled": True})
    monkeypatch.setattr(server, "inclinometer_service", sensor)
    monkeypatch.setattr(server, "PHONE_ORIENTATION_CALIBRATION_PATH", tmp_path / "orientation.json")
    monkeypatch.setattr(server, "get_session_logger", lambda: None)

    response = server.app.test_client().post(
        "/api/calibration/iwr6843/orientation",
        json=_payload(tilt_deg=12.25),
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["measured_mount_tilt_deg"] == pytest.approx(12.25)
    assert body["enclosure_pitch_deg"] == pytest.approx(1.75)
    assert body["configured_iwr_tilt_deg"] == pytest.approx(10.5)
    assert math.degrees(runtime.calibration.tilt_rad) == pytest.approx(10.5)


def test_phone_calibration_fails_safe_when_enclosure_sensor_is_unstable(monkeypatch, tmp_path):
    runtime = _runtime()
    original_tilt = runtime.calibration.tilt_rad
    selection = SimpleNamespace(snapshot=None, status="moving")
    sensor = SimpleNamespace(wait_for_stable=lambda timeout_s: selection)
    path = tmp_path / "orientation.json"
    monkeypatch.setattr(server, "iwr6843_runtime", runtime)
    monkeypatch.setattr(server, "iwr6843_runtime_config", {"enabled": True})
    monkeypatch.setattr(server, "inclinometer_service", sensor)
    monkeypatch.setattr(server, "PHONE_ORIENTATION_CALIBRATION_PATH", path)

    response = server.app.test_client().post(
        "/api/calibration/iwr6843/orientation",
        json=_payload(),
    )

    assert response.status_code == 409
    assert "enclosure sensor" in response.get_json()["error"]
    assert runtime.calibration.tilt_rad == original_tilt
    assert not path.exists()


def test_saved_phone_tilt_is_used_on_restart_unless_cli_overrides(monkeypatch, tmp_path):
    path = tmp_path / "orientation.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "configured_iwr_tilt_deg": 11.75,
                "measurement": _payload(tilt_deg=12.25),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "PHONE_ORIENTATION_CALIBRATION_PATH", path)

    assert server._resolve_iwr_mount_tilt(10.4, explicit_tilt_deg=None) == (
        pytest.approx(11.75),
        "ios_companion",
    )
    assert server._resolve_iwr_mount_tilt(10.4, explicit_tilt_deg=8.0) == (
        pytest.approx(8.0),
        "command_line",
    )
