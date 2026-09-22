"""Server integration for per-shot LIS3DH tilt compensation."""

import json
import math
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

from openflight import server
from openflight.inclinometer import OrientationSnapshot, SnapshotSelection
from openflight.iwr6843.calibration import Calibration
from openflight.launch_monitor import Shot
from openflight.session_logger import SessionLogger


class FakeInclinometer:
    def __init__(self, selection):
        self.selection = selection
        self.impact_timestamp = None

    def snapshot_for_impact(self, impact_timestamp):
        self.impact_timestamp = impact_timestamp
        return self.selection


def _stable_selection():
    return SnapshotSelection(
        snapshot=OrientationSnapshot(
            timestamp=99.8,
            x_g=0.0,
            y_g=0.0523,
            z_g=0.9986,
            gravity_g=1.0,
            raw_pitch_deg=3.0,
            calibrated_pitch_deg=4.5,
            pitch_std_deg=0.1,
            sample_count=8,
        ),
        status="stable",
        age_s=0.2,
    )


def test_server_passes_effective_preimpact_tilt_to_iwr(monkeypatch):
    sensor = FakeInclinometer(_stable_selection())
    calibration = Calibration.identity()
    calibration.tilt_rad = math.radians(11.5)
    seen = {}

    def process_shot(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(capture=None, measurement=None, club_path=None)

    monkeypatch.setattr(server, "inclinometer_service", sensor)
    monkeypatch.setattr(server, "inclinometer_runtime_config", {"zero_offset_deg": 1.5})
    monkeypatch.setattr(
        server,
        "iwr6843_runtime",
        SimpleNamespace(calibration=calibration, process_shot=process_shot),
    )
    monkeypatch.setattr(server, "get_session_logger", lambda: None)
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), impact_timestamp=100.0)

    server._snapshot_inclinometer_for_shot(shot)
    server._process_iwr6843_angle(shot)

    assert sensor.impact_timestamp == 100.0
    assert shot.inclinometer["configured_iwr_tilt_deg"] == 11.5
    assert shot.inclinometer["effective_iwr_tilt_deg"] == 16.0
    assert shot.inclinometer["age_s"] == 0.2
    assert seen["tilt_deg"] == 16.0


def test_server_preserves_configured_tilt_without_stable_snapshot(monkeypatch):
    sensor = FakeInclinometer(
        SnapshotSelection(snapshot=None, status="no_stable_preimpact_reading")
    )
    calibration = Calibration.identity()
    seen = {}

    def process_shot(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(capture=None, measurement=None, club_path=None)

    monkeypatch.setattr(server, "inclinometer_service", sensor)
    monkeypatch.setattr(server, "inclinometer_runtime_config", {"zero_offset_deg": 1.5})
    monkeypatch.setattr(
        server,
        "iwr6843_runtime",
        SimpleNamespace(calibration=calibration, process_shot=process_shot),
    )
    monkeypatch.setattr(server, "get_session_logger", lambda: None)
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), impact_timestamp=100.0)

    server._snapshot_inclinometer_for_shot(shot)
    server._process_iwr6843_angle(shot)

    assert shot.inclinometer["applied"] is False
    assert shot.inclinometer["status"] == "no_stable_preimpact_reading"
    assert seen["tilt_deg"] is None


def test_init_inclinometer_is_fail_soft(monkeypatch):
    class BrokenSensor:
        def __init__(self, **_kwargs):
            pass

    class BrokenService:
        def __init__(self, _sensor, **_kwargs):
            pass

        def start(self):
            raise OSError("I2C unavailable")

        def stop(self):
            pass

    monkeypatch.setattr("openflight.inclinometer.LIS3DH", BrokenSensor)
    monkeypatch.setattr("openflight.inclinometer.InclinometerService", BrokenService)
    monkeypatch.setattr(server, "log_session_error", lambda *_args, **_kwargs: None)

    assert server.init_inclinometer(zero_offset_deg=1.5) is False
    assert server.inclinometer_service is None
    assert server.inclinometer_runtime_config["requested"] is True
    assert server.inclinometer_runtime_config["error"] == "I2C unavailable"


def test_inclinometer_flag_requires_iwr6843(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["openflight-server", "--inclinometer"])

    with pytest.raises(SystemExit, match="2"):
        server.main()


def test_shot_dict_includes_inclinometer_provenance():
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now())
    shot.inclinometer = {"applied": True, "effective_iwr_tilt_deg": 16.0}

    assert server.shot_to_dict(shot)["inclinometer"] == shot.inclinometer


def test_session_log_records_orientation_used_for_shot(tmp_path):
    logger = SessionLogger(log_dir=tmp_path, enabled=True)
    logger.start_session(mode="rolling-buffer", trigger_type="sound")
    orientation = {
        "applied": True,
        "calibrated_pitch_deg": 4.5,
        "effective_iwr_tilt_deg": 16.0,
    }

    shot = Shot(
        ball_speed_mph=100.0,
        club_speed_mph=70.0,
        timestamp=datetime.now(),
        peak_magnitude=10.0,
        inclinometer=orientation,
    )
    logger.log_shot(shot)

    entry = json.loads(logger.session_path.read_text().strip().splitlines()[-1])
    assert entry["type"] == "shot_detected"
    assert entry["inclinometer"] == orientation
    logger.end_session()
