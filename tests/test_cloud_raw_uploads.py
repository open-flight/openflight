"""Tests for opt-in raw uploads: unfiltered sessions + per-shot IWR6843 L3 dumps."""

import gzip
import json

from openflight.cloud import cli, client as cl, commands, filtering, spool
from openflight.cloud.client import UploadResult
from openflight.cloud.config import CloudConfig, load_config, save_config

SESSION_UUID = "1f0e9c2a-7b3d-4e5f-8a9b-0c1d2e3f4a5b"
DUMP = b"ILD1" + b"\x00" * 60


def _dump(tmp_path, name):
    dump_dir = tmp_path / "iwr6843"
    dump_dir.mkdir(exist_ok=True)
    path = dump_dir / name
    path.write_bytes(DUMP)
    return path


def _raw_session(tmp_path, *captures, name="session_20260929_100000_range.jsonl"):
    """A session with raw radar lines and one iwr6843_capture entry per capture."""
    entries = [
        {"type": "session_start", "session_uuid": SESSION_UUID},
        {"type": "shot_detected", "shot_number": 1, "ball_speed_mph": 110},
        # Real rolling buffers are ~52 KB — over the filtered-mode 32 KB cap.
        {"type": "rolling_buffer_capture", "shot_number": 1, "i": "x" * 40_000},
        {"type": "kld7_buffer", "shot_number": 1, "frames": []},
    ]
    for shot_number, capture_path in captures:
        entries.append(
            {"type": "iwr6843_capture", "shot_number": shot_number, "capture_path": capture_path}
        )
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return path


def _raw_config(**over):
    fields = dict(
        endpoint="https://e.test",
        device_token="of_device_tok",
        device_id="dev-1",
        enabled=True,
        upload_raw=True,
    )
    fields.update(over)
    return CloudConfig(**fields)


class FakeClient:
    def __init__(self, uploads=None, captures=None):
        self._uploads = list(uploads or [])
        self._captures = list(captures or [])
        self.uploaded = []
        self.bodies = []
        self.capture_calls = []

    def health(self):
        return True

    def upload_session(self, session_id, body):
        self.uploaded.append(session_id)
        self.bodies.append(body)
        return self._uploads.pop(0)

    def upload_capture(self, session_id, kind, shot_number, data, filename=None):
        self.capture_calls.append((session_id, kind, shot_number, data, filename))
        return self._captures.pop(0)


def _ok_session():
    return UploadResult(201, action="success", session_id=SESSION_UUID, shot_count=1)


def _ok_capture():
    return UploadResult(201, action="success", session_id=SESSION_UUID)


class TestRawFiltering:
    def test_filtered_mode_is_unchanged(self, tmp_path):
        dump = _dump(tmp_path, "d1.l3dump")
        result = filtering.filter_session_file(_raw_session(tmp_path, (1, str(dump))), "dev-1")
        assert set(result.kept_type_counts) == {"session_start", "shot_detected"}
        assert result.captures == []
        assert result.manifest["filtered"] is True
        assert result.manifest["raw"] is False

    def test_raw_mode_keeps_raw_radar_but_not_kld7(self, tmp_path):
        dump = _dump(tmp_path, "d1.l3dump")
        path = _raw_session(tmp_path, (1, str(dump)))
        result = filtering.filter_session_file(path, "dev-1", raw_mode=True)
        assert result.kept_type_counts == {
            "session_start": 1,
            "shot_detected": 1,
            "rolling_buffer_capture": 1,
            "iwr6843_capture": 1,
        }
        assert result.dropped_oversize == 0
        assert result.manifest["filtered"] is False
        assert result.manifest["raw"] is True

    def test_raw_mode_matches_dumps_to_shots(self, tmp_path):
        d1 = _dump(tmp_path, "d1.l3dump")
        path = _raw_session(tmp_path, (1, str(d1)), (2, "iwr6843/d2.l3dump"), (3, None))
        result = filtering.filter_session_file(path, "dev-1", raw_mode=True)
        assert [c.to_dict() for c in result.captures] == [
            {"shot_number": 1, "path": str(d1), "kind": "iwr6843"},
            # Relative capture paths resolve against the session's directory.
            {"shot_number": 2, "path": str(tmp_path / "iwr6843" / "d2.l3dump"), "kind": "iwr6843"},
        ]

    def test_raw_mode_still_drops_pathological_lines(self):
        huge = json.dumps({"type": "iq_blocks", "data": "x" * (filtering.MAX_RAW_LINE_BYTES + 1)})
        result = filtering.filter_session_lines([huge], "dev-1", raw_mode=True)
        assert result.kept_lines == []
        assert result.dropped_oversize == 1


class TestRawPush:
    def test_uploads_raw_session_then_its_dumps(self, tmp_path):
        dump = _dump(tmp_path, "iwr6843_20260929_100500_000_001.l3dump")
        path = _raw_session(tmp_path, (1, str(dump)))
        client = FakeClient(uploads=[_ok_session()], captures=[_ok_capture()])

        summary = commands.cmd_push(_raw_config(), tmp_path, client, out=lambda _m: None)

        assert client.uploaded == [SESSION_UUID]
        assert client.capture_calls == [
            (SESSION_UUID, "iwr6843", 1, DUMP, "iwr6843_20260929_100500_000_001.l3dump")
        ]
        assert summary["captures_uploaded"] == 1
        assert spool.pending_captures(path) == []
        body = gzip.decompress(client.bodies[0]).decode()
        assert "rolling_buffer_capture" in body

    def test_filtered_config_never_uploads_dumps(self, tmp_path):
        dump = _dump(tmp_path, "d1.l3dump")
        _raw_session(tmp_path, (1, str(dump)))
        client = FakeClient(uploads=[_ok_session()])
        commands.cmd_push(_raw_config(upload_raw=False), tmp_path, client, out=lambda _m: None)
        assert client.capture_calls == []

    def test_failed_dump_stays_queued_and_retries_next_push(self, tmp_path):
        dump = _dump(tmp_path, "d1.l3dump")
        path = _raw_session(tmp_path, (1, str(dump)))
        client = FakeClient(
            uploads=[_ok_session()],
            captures=[UploadResult(404, action="retry", reason="session_not_found")],
        )
        commands.cmd_push(_raw_config(), tmp_path, client, out=lambda _m: None)
        assert spool.is_pushed(path)
        assert spool.pending_captures(path)[0]["attempts"] == 1

        # Next timer tick: no pending sessions, but the dump is still queued.
        client = FakeClient(captures=[_ok_capture()])
        summary = commands.cmd_push(_raw_config(), tmp_path, client, out=lambda _m: None)
        assert client.uploaded == []
        assert summary["captures_uploaded"] == 1
        assert spool.pending_captures(path) == []

    def test_rejected_or_missing_dumps_are_parked(self, tmp_path):
        good = _dump(tmp_path, "d1.l3dump")
        path = _raw_session(tmp_path, (1, str(good)), (2, str(tmp_path / "gone.l3dump")))
        client = FakeClient(
            uploads=[_ok_session()],
            captures=[UploadResult(422, action="park", reason="invalid_capture")],
        )
        commands.cmd_push(_raw_config(), tmp_path, client, out=lambda _m: None)
        parked = spool.read_pushed(path)["captures_parked"]
        assert [(p["shot_number"], p["reason"]) for p in parked] == [
            (1, "invalid_capture"),
            (2, "missing_file"),
        ]
        assert spool.pending_captures(path) == []

    def test_relink_stops_dump_uploads(self, tmp_path):
        d1, d2 = _dump(tmp_path, "d1.l3dump"), _dump(tmp_path, "d2.l3dump")
        path = _raw_session(tmp_path, (1, str(d1)), (2, str(d2)))
        client = FakeClient(uploads=[_ok_session()], captures=[UploadResult(401, action="relink")])
        summary = commands.cmd_push(_raw_config(), tmp_path, client, out=lambda _m: None)
        assert summary["needs_relink"] is True
        assert len(client.capture_calls) == 1
        assert [c["shot_number"] for c in spool.pending_captures(path)] == [1, 2]

    def test_dry_run_lists_dumps_without_uploading(self, tmp_path):
        dump = _dump(tmp_path, "d1.l3dump")
        _raw_session(tmp_path, (1, str(dump)))
        client = FakeClient()
        out = []
        commands.cmd_push(_raw_config(), tmp_path, client, dry_run=True, out=out.append)
        text = "\n".join(out)
        assert "(raw)" in text
        assert "keep      1 x rolling_buffer_capture" in text
        assert "dump      1 x iwr6843 capture file(s)" in text
        assert client.uploaded == [] and client.capture_calls == []

    def test_status_reports_raw_mode_and_queued_dumps(self, tmp_path):
        dump = _dump(tmp_path, "d1.l3dump")
        path = _raw_session(tmp_path, (1, str(dump)))
        spool.mark_pushed(path, SESSION_UUID, 1, captures=[{"shot_number": 1, "path": str(dump)}])
        out = []
        commands.cmd_status(_raw_config(), tmp_path, out=out.append)
        text = "\n".join(out)
        assert "Raw upload: on" in text
        assert "1 raw capture file(s) waiting" in text


class FakeTransportRecorder:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, method, url, data=None, headers=None, timeout=30):
        self.calls.append({"method": method, "url": url, "data": data, "headers": headers or {}})
        return self._responses.pop(0)


class TestUploadCaptureClient:
    def _client(self, responses):
        transport = FakeTransportRecorder(responses)
        return cl.CloudClient(
            "https://e.test", token="of_device_tok", request_fn=transport
        ), transport

    def test_puts_bytes_to_capture_path(self):
        client, t = self._client([cl.HttpResponse(201, {}, b"{}")])
        result = client.upload_capture(SESSION_UUID, "iwr6843", 3, DUMP, filename="d.l3dump")
        call = t.calls[0]
        assert call["method"] == "PUT"
        assert call["url"] == f"https://e.test/v1/sessions/{SESSION_UUID}/captures/iwr6843/3"
        assert call["data"] == DUMP
        assert call["headers"]["Authorization"] == "Bearer of_device_tok"
        assert call["headers"]["Content-Type"] == "application/octet-stream"
        assert call["headers"]["X-Capture-Filename"] == "d.l3dump"
        assert result.action == "success"

    def test_status_mapping(self):
        cases = {
            200: "success",
            401: "relink",
            404: "retry",
            413: "park",
            422: "park",
            503: "retry",
        }
        for status, action in cases.items():
            client, _ = self._client([cl.HttpResponse(status, {}, b"{}")])
            assert client.upload_capture(SESSION_UUID, "iwr6843", 1, DUMP).action == action

    def test_rate_limit_carries_retry_after(self):
        client, _ = self._client([cl.HttpResponse(429, {"Retry-After": "30"}, b"{}")])
        result = client.upload_capture(SESSION_UUID, "iwr6843", 1, DUMP)
        assert (result.action, result.retry_after) == ("rate_limited", 30)


class TestRawConfigAndCli:
    def test_upload_raw_defaults_off_and_round_trips(self, tmp_path):
        path = tmp_path / "cloud.json"
        path.write_text(json.dumps({"device_token": "t", "device_id": "d"}))
        assert load_config(path).upload_raw is False
        save_config(_raw_config(), path)
        assert load_config(path).upload_raw is True

    def test_raw_on_and_off_persist(self, tmp_path):
        path = tmp_path / "cloud.json"
        save_config(_raw_config(upload_raw=False), path)
        assert cli.main(["raw", "on", "--config", str(path)]) == 0
        assert load_config(path).upload_raw is True
        # Linking state is untouched.
        assert load_config(path).device_token == "of_device_tok"
        assert cli.main(["raw", "off", "--config", str(path)]) == 0
        assert load_config(path).upload_raw is False
