"""Tests for historical session metadata."""

from openflight.session_metadata import (
    load_hidden_sessions,
    save_hidden_sessions,
)


def test_no_sessions_hidden_initially(tmp_path):
    assert load_hidden_sessions(tmp_path) == set()


def test_hide_and_restore_session(tmp_path):
    session_id = "session_20261028_223723_range"

    save_hidden_sessions(tmp_path, {session_id})
    assert session_id in load_hidden_sessions(tmp_path)

    save_hidden_sessions(tmp_path, set())
    assert session_id not in load_hidden_sessions(tmp_path)


def test_hiding_preserves_original_session(tmp_path):
    session = tmp_path / "session_20261028_223723_range.jsonl"
    original = '{"type":"shot_detected","shot_number":1}\n'
    session.write_text(original)

    save_hidden_sessions(tmp_path, {session.stem})

    assert session.read_text() == original
    assert session.is_file()


def test_session_listing_excludes_empty_sessions(tmp_path):
    import json
    from openflight.session_history import list_sessions

    empty = tmp_path / "session_20261028_100000_range.jsonl"
    active = tmp_path / "session_20261029_100000_range.jsonl"

    empty.write_text(json.dumps({"type": "session_start"}) + "\n")
    active.write_text(json.dumps({
        "type": "shot_detected",
        "shot_number": 1,
    }) + "\n")

    sessions = list_sessions(tmp_path)
    assert len(sessions) == 1
    assert sessions[0]["shot_count"] == 1

    all_sessions = list_sessions(tmp_path, include_empty=True)
    assert len(all_sessions) == 2


def test_invalid_session_id_is_rejected(tmp_path):
    from openflight.session_history import get_session_details

    assert get_session_details("../outside", tmp_path) is None
    assert get_session_details("not-a-session", tmp_path) is None


def test_matching_camera_capture(tmp_path):
    import json
    from openflight.session_history import find_shot_capture

    session_id = "session_20261030_100000_range"
    path = tmp_path / f"{session_id}.jsonl"

    events = [
        {"type": "shot_detected", "shot_number": 1,
         "impact_timestamp": 123.456},
        {"type": "camera_capture", "shot_number": 1,
         "shot_timestamp": 123.456,
         "capture_path": str(tmp_path / "range/camera/test"),
         "metadata": {"frame_count": 60}},
    ]

    path.write_text("".join(json.dumps(e) + "\n" for e in events))

    capture = find_shot_capture(session_id, 1, tmp_path)
    assert capture is not None
    assert capture["metadata"]["frame_count"] == 60
    assert find_shot_capture(session_id, 999, tmp_path) is None


def test_camera_timestamp_mismatch(tmp_path):
    import json
    from openflight.session_history import find_shot_capture

    session_id = "session_20261030_110000_range"
    path = tmp_path / f"{session_id}.jsonl"

    events = [
        {"type": "shot_detected", "shot_number": 1,
         "impact_timestamp": 100.0},
        {"type": "camera_capture", "shot_number": 1,
         "shot_timestamp": 200.0,
         "capture_path": "/some/camera",
         "metadata": {"frame_count": 60}},
    ]

    path.write_text("".join(json.dumps(e) + "\n" for e in events))

    assert find_shot_capture(session_id, 1, tmp_path) is None


def test_visibility_api_hides_and_restores(tmp_path, monkeypatch):
    import json
    from openflight import server

    session_id = "session_20261030_120000_range"
    session = tmp_path / f"{session_id}.jsonl"
    original = json.dumps({
        "type": "shot_detected",
        "shot_number": 1,
    }) + "\n"
    session.write_text(original)

    class FakeLogger:
        log_dir = tmp_path

    monkeypatch.setattr(server, "get_session_logger", lambda: FakeLogger())

    client = server.app.test_client()
    url = f"/api/sessions/history/{session_id}/visibility"

    response = client.post(url, json={"hidden": True})
    assert response.status_code == 200

    response = client.get("/api/sessions/history")
    assert response.get_json()["count"] == 0

    response = client.get("/api/sessions/history?show_hidden=true")
    assert response.get_json()["count"] == 1
    assert response.get_json()["sessions"][0]["hidden"] is True

    response = client.post(url, json={"hidden": False})
    assert response.status_code == 200

    response = client.get("/api/sessions/history")
    assert response.get_json()["count"] == 1

    assert session.read_text() == original


def test_replay_rejects_capture_outside_camera_directory(tmp_path, monkeypatch):
    import json
    from openflight import server
    from openflight.camera.replay import CameraReplayManager

    session_id = "session_20261030_130000_range"
    session = tmp_path / f"{session_id}.jsonl"

    outside = tmp_path / "outside_camera"
    outside.mkdir()
    (outside / "frames.npz").write_bytes(b"test")

    events = [
        {"type": "shot_detected", "shot_number": 1,
         "impact_timestamp": 123.456},
        {"type": "camera_capture", "shot_number": 1,
         "shot_timestamp": 123.456,
         "capture_path": str(outside),
         "metadata": {"frame_count": 60}},
    ]

    session.write_text("".join(json.dumps(e) + "\n" for e in events))

    class FakeLogger:
        log_dir = tmp_path

    monkeypatch.setattr(server, "get_session_logger", lambda: FakeLogger())
    monkeypatch.setattr(
        server,
        "camera_replay_manager",
        CameraReplayManager(tmp_path / "range" / "camera"),
    )

    client = server.app.test_client()
    response = client.post(
        f"/api/sessions/history/{session_id}/shots/1/replay"
    )

    assert response.status_code == 404


def _write_capture_session(log_dir, session_id, capture_dir):
    """Write a one-shot session whose camera capture lives in capture_dir."""
    import json

    capture_dir.mkdir(parents=True, exist_ok=True)
    (capture_dir / "frames.npz").write_bytes(b"frames")
    events = [
        {"type": "shot_detected", "shot_number": 1, "impact_timestamp": 123.456},
        {
            "type": "camera_capture",
            "shot_number": 1,
            "shot_timestamp": 123.456,
            "capture_path": str(capture_dir),
            "metadata": {"frame_count": 60, "pre_trigger_frames": 30},
        },
    ]
    (log_dir / f"{session_id}.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events)
    )


def _history_client(tmp_path, monkeypatch, live_location):
    """Server test client whose live replay manager serves live_location."""
    from openflight import server
    from openflight.camera.replay import CameraReplayManager

    class FakeLogger:
        log_dir = tmp_path

    monkeypatch.setattr(server, "get_session_logger", lambda: FakeLogger())
    monkeypatch.setattr(
        server,
        "camera_replay_manager",
        CameraReplayManager(tmp_path / live_location / "camera"),
    )
    return server.app.test_client()


def test_non_range_session_reports_camera_available(tmp_path):
    from openflight.session_history import get_session_details

    session_id = "session_20261101_124555_trackman"
    _write_capture_session(
        tmp_path, session_id, tmp_path / "trackman" / "camera" / "camera_001"
    )

    details = get_session_details(session_id, tmp_path)

    assert details["shots"][0]["camera_available"] is True


def test_capture_filed_under_another_location_is_unavailable(tmp_path):
    from openflight.session_history import get_session_details

    session_id = "session_20261101_124555_trackman"
    _write_capture_session(
        tmp_path, session_id, tmp_path / "range" / "camera" / "camera_001"
    )

    details = get_session_details(session_id, tmp_path)

    assert details["shots"][0]["camera_available"] is False


def test_session_location_cannot_escape_log_dir(tmp_path):
    from openflight.session_history import find_shot_capture, get_session_details

    log_dir = tmp_path / "sessions"
    log_dir.mkdir()
    session_id = "session_20261101_124555_.."
    _write_capture_session(log_dir, session_id, tmp_path / "camera" / "camera_001")

    details = get_session_details(session_id, log_dir)

    assert details is not None
    assert details["shots"][0]["camera_available"] is False
    assert find_shot_capture(session_id, 1, log_dir) is None


def test_replay_serves_session_from_another_location(tmp_path, monkeypatch):
    session_id = "session_20261101_124555_trackman"
    _write_capture_session(
        tmp_path, session_id, tmp_path / "trackman" / "camera" / "camera_001"
    )
    client = _history_client(tmp_path, monkeypatch, live_location="range")

    response = client.post(f"/api/sessions/history/{session_id}/shots/1/replay")

    assert response.status_code == 200
    assert response.get_json()["frame_count"] == 60


def test_replay_serves_range_session_while_running_elsewhere(tmp_path, monkeypatch):
    session_id = "session_20261030_194840_range"
    _write_capture_session(
        tmp_path, session_id, tmp_path / "range" / "camera" / "camera_001"
    )
    client = _history_client(tmp_path, monkeypatch, live_location="trackman")

    response = client.post(f"/api/sessions/history/{session_id}/shots/1/replay")

    assert response.status_code == 200


def test_replay_rejects_capture_filed_under_another_location(tmp_path, monkeypatch):
    session_id = "session_20261101_124555_trackman"
    _write_capture_session(
        tmp_path, session_id, tmp_path / "range" / "camera" / "camera_001"
    )
    client = _history_client(tmp_path, monkeypatch, live_location="range")

    response = client.post(f"/api/sessions/history/{session_id}/shots/1/replay")

    assert response.status_code == 404
