"""Network (SSE) schema v2 catch-up: the same rule BLE ``hello`` applies.

A v2 stream client names the last shot it has with the standard
``Last-Event-ID`` header (or ``?last_event_id=``) and is seeded with the
session shots after it. v2 shot frames carry ``id: <event_id>`` so an
``EventSource`` sends the header on its own when it reconnects.
"""

import json
from datetime import datetime

import pytest

from openflight import server as server_module
from openflight.ble.protocol import encode_shot_event_v2, stable_shot_event_id
from openflight.launch_monitor import ClubType, Shot
from openflight.phone_catch_up import CATCH_UP_LIMIT, PhoneShotCache
from openflight.profiles import ProfileStore
from openflight.shot_stream import HEARTBEAT_FRAME, ShotStreamBroker, format_event


def _shot_data(number=1, ball_speed=150.0):
    return {
        "timestamp": f"2026-09-28T12:00:{number:02d}.000001",
        "club": "driver",
        "ball_speed_mph": ball_speed,
        "estimated_carry_yards": 250,
        "shot_number": number,
    }


def _catch_up_entry(number):
    data = _shot_data(number)
    return stable_shot_event_id(data), encode_shot_event_v2(data, final=True)


def _drain(subscriber):
    events = []
    while not subscriber.empty():
        events.append(subscriber.get_nowait())
    return events


def _parse(frame):
    """(name, id or None, decoded data) from one SSE frame."""
    fields = dict(line.split(": ", 1) for line in frame.strip().split("\n"))
    return fields["event"], fields.get("id"), json.loads(fields["data"])


# -- broker ---------------------------------------------------------------------------


def test_catch_up_replaces_latest_seed_and_keeps_order():
    broker = ShotStreamBroker()
    broker.publish_v2_shot(_shot_data(9), final=True)
    entries = [_catch_up_entry(number) for number in (1, 2, 3)]
    state = [{"schema_version": 2, "type": "club_changed", "club": "7-iron"}]

    subscriber = broker.subscribe(schema=2, initial_events=state, catch_up=entries)

    frames = [_parse(format_event(event)) for event in _drain(subscriber)]
    assert [name for name, _id, _data in frames] == ["club_changed", "shot", "shot", "shot"]
    assert [event_id for _name, event_id, _data in frames[1:]] == [eid for eid, _ in entries]
    assert [data["shot_number"] for _name, _id, data in frames[1:]] == [1, 2, 3]
    assert frames[0][1] is None  # only shots carry an id


def test_empty_catch_up_seeds_no_shot():
    broker = ShotStreamBroker()
    broker.publish_v2_shot(_shot_data(9), final=True)

    subscriber = broker.subscribe(schema=2, catch_up=[])

    assert _drain(subscriber) == []


def test_without_catch_up_the_latest_shot_is_still_seeded():
    broker = ShotStreamBroker()
    broker.publish_v2_shot(_shot_data(9), final=True)

    events = _drain(broker.subscribe(schema=2))

    assert [_parse(format_event(event))[2]["shot_number"] for event in events] == [9]


def test_v1_subscriber_ignores_catch_up():
    broker = ShotStreamBroker()
    broker.publish(_shot_data(4))

    events = _drain(broker.subscribe(catch_up=[_catch_up_entry(1)]))

    assert len(events) == 1
    name, event_id, data = _parse(format_event(events[0]))
    assert (name, event_id, data["schema_version"]) == ("shot", None, 1)


def test_full_catch_up_fits_the_seed_queue():
    broker = ShotStreamBroker(queue_size=2)
    entries = [_catch_up_entry(number) for number in range(1, CATCH_UP_LIMIT + 1)]
    state = [{"schema_version": 2, "type": "club_changed", "club": "driver"}]

    events = _drain(broker.subscribe(schema=2, initial_events=state, catch_up=entries))

    assert len(events) == CATCH_UP_LIMIT + 1


def test_live_v2_shot_frames_carry_event_id_and_v1_frames_do_not():
    broker = ShotStreamBroker()
    v1 = broker.subscribe()
    v2 = broker.subscribe(schema=2)

    broker.publish(_shot_data(5))
    broker.publish_v2_shot(_shot_data(5), final=False, enrichment={"status": "pending"})
    broker.publish_event_v2({"schema_version": 2, "type": "shot_processing", "state": "failed"})

    (v1_frame,) = [_parse(format_event(event)) for event in _drain(v1)]
    v2_frames = [_parse(format_event(event)) for event in _drain(v2)]
    assert v1_frame[1] is None
    assert v2_frames[0][1] == stable_shot_event_id(_shot_data(5))
    assert v2_frames[0][2]["event_id"] == v2_frames[0][1]
    assert v2_frames[1][1] is None


# -- route ----------------------------------------------------------------------------


@pytest.fixture
def session(monkeypatch, tmp_path):
    """A mock monitor with five shots behind the real stream route; returns event ids."""
    monitor = server_module.MockLaunchMonitor()
    for index in range(5):
        monitor._shots.append(
            Shot(
                ball_speed_mph=120.0 + index,
                timestamp=datetime(2026, 9, 28, 12, 0, index, 1000),
                club=ClubType.DRIVER,
                shot_number=index + 1,
            )
        )
    monkeypatch.setattr(server_module, "monitor", monitor)
    monkeypatch.setattr(server_module, "shot_stream", ShotStreamBroker(heartbeat_interval_s=0.01))
    monkeypatch.setattr(server_module, "phone_shot_cache", PhoneShotCache())
    monkeypatch.setattr(server_module, "active_club", ClubType.DRIVER)
    monkeypatch.setattr(server_module, "power_monitor", None)
    monkeypatch.setattr(server_module, "profile_store", ProfileStore(tmp_path / "profiles.json"))
    return [stable_shot_event_id(server_module.shot_to_dict(shot)) for shot in monitor.get_shots()]


def _stream_shot_ids(path, headers=None):
    """Event ids of the shots seeded on connect (before the first idle heartbeat)."""
    response = server_module.app.test_client().get(path, headers=headers or {})
    try:
        assert response.status_code == 200
        frames = response.response
        assert next(frames).decode("utf-8") == HEARTBEAT_FRAME
        ids = []
        for raw in frames:
            frame = raw.decode("utf-8")
            if frame == HEARTBEAT_FRAME:
                break
            name, event_id, data = _parse(frame)
            if name == "shot":
                assert event_id == data.get("event_id")
                ids.append(event_id)
        return ids
    finally:
        response.close()


def test_route_without_anchor_seeds_whole_session(session):
    assert _stream_shot_ids("/api/shots/stream?schema=2") == session


def test_route_last_event_id_header_resends_it_and_seeds_missed_shots(session):
    ids = _stream_shot_ids("/api/shots/stream?schema=2", {"Last-Event-ID": session[2]})

    assert ids == session[2:]


def test_route_last_event_id_query_parameter_matches_header(session):
    assert _stream_shot_ids(f"/api/shots/stream?schema=2&last_event_id={session[2]}") == session[2:]


def test_route_header_takes_precedence_over_query(session):
    ids = _stream_shot_ids(
        f"/api/shots/stream?schema=2&last_event_id={session[0]}",
        {"Last-Event-ID": session[3]},
    )

    assert ids == session[3:]


def test_route_unknown_anchor_seeds_whole_session(session):
    ids = _stream_shot_ids("/api/shots/stream?schema=2", {"Last-Event-ID": "not-a-shot"})

    assert ids == session


def test_route_v1_stream_ignores_last_event_id(session):
    server_module.shot_stream.publish(
        server_module.shot_to_dict(server_module.monitor.get_shots()[-1])
    )

    response = server_module.app.test_client().get(
        "/api/shots/stream", headers={"Last-Event-ID": session[0]}
    )
    try:
        frames = response.response
        next(frames)
        name, event_id, data = _parse(next(frames).decode("utf-8"))
    finally:
        response.close()

    assert (name, event_id, data["schema_version"], data["ball_speed_mph"]) == (
        "shot",
        None,
        1,
        124.0,
    )


def test_route_matches_ble_catch_up_for_the_same_anchor(session):
    """Both transports apply one rule: same anchor, same shots, same bytes."""
    shared = server_module.phone_catch_up_v2(session[1])

    assert _stream_shot_ids("/api/shots/stream?schema=2", {"Last-Event-ID": session[1]}) == [
        event_id for event_id, _payload in shared
    ]


def test_route_survives_catch_up_failure_with_latest_replay(session, monkeypatch):
    server_module.shot_stream.publish_v2_shot(
        server_module.shot_to_dict(server_module.monitor.get_shots()[-1]), final=True
    )

    def broken(_last_event_id=None):
        raise RuntimeError("session unavailable")

    monkeypatch.setattr(server_module, "phone_catch_up_v2", broken)

    assert _stream_shot_ids("/api/shots/stream?schema=2") == [session[-1]]
