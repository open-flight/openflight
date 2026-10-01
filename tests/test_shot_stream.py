"""Tests for Server-Sent Events shot delivery, buffering, and isolation."""

import json
import logging
import queue

import pytest

from openflight.shot_stream import (
    HEARTBEAT_FRAME,
    ShotStreamBroker,
    ShotStreamFull,
    format_event,
)


def _shot_data(ball_speed=150.0):
    return {
        "timestamp": "2026-07-29T19:42:10",
        "club": "driver",
        "ball_speed_mph": ball_speed,
        "club_speed_mph": None,
        "smash_factor": None,
        "estimated_carry_yards": 250,
        "launch_angle_vertical": None,
        "launch_angle_horizontal": None,
        "spin_rpm": None,
        "club_path_deg": None,
        "spin_axis_deg": None,
    }


def _decode_frame(frame):
    lines = frame.split("\n")
    assert lines[0] == "event: shot"
    assert lines[2] == ""
    assert lines[3] == ""
    return json.loads(lines[1].removeprefix("data: "))


def test_rejects_nonsense_configuration():
    with pytest.raises(ValueError):
        ShotStreamBroker(queue_size=0)
    with pytest.raises(ValueError):
        ShotStreamBroker(heartbeat_interval_s=0)
    with pytest.raises(ValueError):
        ShotStreamBroker(max_subscribers=0)


def test_event_frame_is_one_data_line_terminated_by_blank_line():
    frame = format_event(b'{"schema_version":1}')

    assert frame == 'event: shot\ndata: {"schema_version":1}\n\n'
    assert frame.count("\ndata:") == 1


def test_publish_reaches_every_subscriber():
    broker = ShotStreamBroker()
    first = broker.subscribe()
    second = broker.subscribe()

    assert broker.publish(_shot_data(151.4))

    assert _decode_frame(format_event(first.get_nowait()))["ball_speed_mph"] == 151.4
    assert _decode_frame(format_event(second.get_nowait()))["ball_speed_mph"] == 151.4
    assert broker.subscriber_count == 2


def test_club_change_reaches_every_subscriber():
    broker = ShotStreamBroker()
    first = broker.subscribe()
    second = broker.subscribe()

    assert broker.publish_club("3-wood")

    expected = (
        'event: club_changed\ndata: {"club":"3-wood","schema_version":1,"type":"club_changed"}\n\n'
    )
    assert format_event(first.get_nowait()) == expected
    assert format_event(second.get_nowait()) == expected


def test_subscriber_replays_latest_shot_on_connect():
    broker = ShotStreamBroker()
    broker.publish(_shot_data(140.0))
    broker.publish(_shot_data(151.4))

    subscriber = broker.subscribe()

    replayed = _decode_frame(format_event(subscriber.get_nowait()))
    assert replayed["ball_speed_mph"] == 151.4
    assert subscriber.empty()


def test_invalid_shot_does_not_raise_into_caller(caplog):
    broker = ShotStreamBroker()
    subscriber = broker.subscribe()

    with caplog.at_level(logging.WARNING):
        assert broker.publish({}) is False

    assert "Failed to encode shot payload" in caplog.text
    assert subscriber.empty()


def test_full_queue_drops_oldest_unsent_shot(caplog):
    broker = ShotStreamBroker(queue_size=2)
    subscriber = broker.subscribe()

    with caplog.at_level(logging.WARNING):
        for speed in (100.0, 110.0, 120.0):
            broker.publish(_shot_data(speed))

    assert "dropped oldest unsent shot" in caplog.text
    remaining = [
        _decode_frame(format_event(subscriber.get_nowait()))["ball_speed_mph"] for _ in range(2)
    ]
    assert remaining == [110.0, 120.0]


def test_slow_subscriber_does_not_block_a_healthy_one():
    broker = ShotStreamBroker(queue_size=1)
    slow = broker.subscribe()
    healthy = broker.subscribe()

    broker.publish(_shot_data(100.0))
    healthy.get_nowait()
    broker.publish(_shot_data(120.0))

    assert _decode_frame(format_event(healthy.get_nowait()))["ball_speed_mph"] == 120.0
    assert _decode_frame(format_event(slow.get_nowait()))["ball_speed_mph"] == 120.0


def test_subscriber_limit_is_enforced():
    broker = ShotStreamBroker(max_subscribers=1)
    broker.subscribe()

    with pytest.raises(ShotStreamFull):
        broker.subscribe()


def test_unsubscribe_frees_a_slot_and_tolerates_repeats():
    broker = ShotStreamBroker(max_subscribers=1)
    subscriber = broker.subscribe()

    broker.unsubscribe(subscriber)
    broker.unsubscribe(subscriber)

    assert broker.subscriber_count == 0
    assert broker.subscribe() is not subscriber


def test_stream_opens_with_a_heartbeat_so_response_headers_flush():
    """A WSGI server withholds headers until the first chunk of the body."""
    broker = ShotStreamBroker(heartbeat_interval_s=30.0)
    frames = broker.frames(broker.subscribe())

    assert next(frames) == HEARTBEAT_FRAME

    frames.close()


def test_stream_emits_shots_then_heartbeats_while_idle():
    broker = ShotStreamBroker(heartbeat_interval_s=0.01)
    broker.publish(_shot_data(151.4))
    frames = broker.frames(broker.subscribe())

    opening = next(frames)
    replayed = next(frames)
    idle = next(frames)

    assert opening == HEARTBEAT_FRAME
    assert _decode_frame(replayed)["ball_speed_mph"] == 151.4
    assert idle == HEARTBEAT_FRAME
    assert broker.subscriber_count == 1

    frames.close()
    assert broker.subscriber_count == 0


def test_stream_unsubscribes_when_client_disconnects():
    broker = ShotStreamBroker(heartbeat_interval_s=0.01)
    frames = broker.frames(broker.subscribe())
    next(frames)

    frames.close()

    assert broker.subscriber_count == 0
    broker.publish(_shot_data())
    assert broker.subscriber_count == 0


def test_publish_after_disconnect_still_replays_to_the_next_client():
    broker = ShotStreamBroker(heartbeat_interval_s=0.01)
    frames = broker.frames(broker.subscribe())
    frames.close()

    broker.publish(_shot_data(133.0))
    subscriber = broker.subscribe()

    assert _decode_frame(format_event(subscriber.get_nowait()))["ball_speed_mph"] == 133.0
    with pytest.raises(queue.Empty):
        subscriber.get_nowait()
