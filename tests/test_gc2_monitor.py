"""Tests for the GC2 Bluetooth monitor and stream parser."""

import time

import pytest

from openflight.gc2 import GC2Monitor
from openflight.gc2.gc2_protocol import GC2StreamParser, decode_weco

FIRST = b"TM=2,ID=2,SP=150.0,TS=2500,BS=2400,SS=700,AZ=-1.2,EL=11.5,CY=230,SM=1.5\r\n"
UPDATE = b"TM=3,ID=2,SP=150.0,TS=2800,BS=2700,SS=0,AZ=-1.2,EL=11.5,CY=235,SM=1.5\r\n"
NEXT_SHOT = b"TM=4,ID=3,SP=120.0,TS=3000,BS=3000,SS=0,AZ=0,EL=15,CY=150,SM=0\r\n"


def make_parser(window=0.05):
    shots = []
    return GC2StreamParser(shots.append, merge_window=window), shots


def test_first_reading_after_connect_is_accepted():
    parser, shots = make_parser(window=0)
    parser.feed(FIRST)
    time.sleep(0.01)
    parser.poll()
    assert len(shots) == 1 and shots[0].speed == 150.0


def test_zero_speed_reading_with_no_pending_shot_is_ignored():
    parser, shots = make_parser(window=0)
    parser.feed(b'TM=1,ID=1,SP=0,TS=0,BS=0,SS=0,AZ=0,EL=0,CY=0,SM=0\r\n')
    parser.poll()
    assert shots == []


def test_update_within_window_is_merged_into_one_shot():
    parser, shots = make_parser()
    for chunk in (FIRST, UPDATE):
        parser.feed(chunk)
    assert shots == []

    time.sleep(0.06)
    parser.poll()

    assert len(shots) == 1
    shot = shots[0]
    assert shot.speed == 150.0
    assert shot.total_spin == 2800.0
    assert shot.carry_distance == 235.0
    assert shot.club_speed == pytest.approx(100.0)
    assert shot.vla == 11.5


def test_update_after_window_starts_new_shot():
    parser, shots = make_parser()
    parser.feed(FIRST)
    time.sleep(0.06)
    parser.feed(NEXT_SHOT)
    assert [s.shot_number for s in shots] == [2]
    time.sleep(0.06)
    parser.poll()
    assert [s.shot_number for s in shots] == [2, 3]
    assert shots[1].club_speed == 0.0


def test_split_and_unterminated_messages_are_handled():
    parser, shots = make_parser(window=0)
    parser.feed(FIRST[:20])
    parser.feed(FIRST[20:].rstrip(b"\r\n"))
    time.sleep(0.2)
    parser.poll()
    parser.poll()
    assert len(shots) == 1 and shots[0].speed == 150.0


def test_duplicate_timestamp_is_ignored():
    parser, shots = make_parser(window=0)
    parser.feed(FIRST + FIRST)
    time.sleep(0.01)
    parser.poll()
    assert len(shots) == 1


def test_malformed_weco_returns_none():
    assert decode_weco("#WECO") is None
    assert decode_weco("#WECO" + "x" * 20) is None


def test_build_shot_maps_measured_values():
    parser, shots = make_parser(window=0)
    parser.feed(FIRST)
    time.sleep(0.01)
    parser.poll()

    shot = GC2Monitor()._build_shot(shots[0])  # pylint: disable=protected-access
    assert shot.ball_speed_mph == 150.0
    assert shot.club_speed_mph == pytest.approx(100.0)
    assert shot.launch_angle_vertical == 11.5
    assert shot.launch_angle_horizontal == -1.2
    assert shot.spin_rpm == 2500.0
    assert shot.spin_source == "measured"
    assert shot.mode == "gc2"


def test_invalid_mac_rejected():
    with pytest.raises(ValueError):
        GC2Monitor(mac="not-a-mac")


def test_handle_shot_data_invokes_callback_and_records():
    received = []
    monitor = GC2Monitor()
    monitor._shot_callback = received.append  # pylint: disable=protected-access
    parser, shots = make_parser(window=0)
    parser.feed(FIRST)
    time.sleep(0.01)
    parser.poll()

    monitor._handle_shot_data(shots[0])  # pylint: disable=protected-access
    assert len(received) == 1 and received[0].ball_speed_mph == 150.0
    assert monitor.get_session_stats()["shot_count"] == 1


def test_replayed_shot_after_reconnect_is_suppressed_but_new_shot_accepted():
    parser, shots = make_parser(window=60)
    parser.feed(FIRST + UPDATE)
    parser.flush()
    assert len(shots) == 1

    shots2 = []
    reconnected = GC2StreamParser(shots2.append, merge_window=60, last_shot_tms=parser.last_shot_tms)
    reconnected.feed(FIRST)
    reconnected.feed(UPDATE)
    reconnected.feed(NEXT_SHOT)
    reconnected.flush()
    assert [s.speed for s in shots2] == [120.0]


def test_flush_emits_pending_shot():
    parser, shots = make_parser(window=60)
    parser.feed(FIRST)
    parser.flush()
    assert len(shots) == 1


def test_connection_callback_fires_only_on_state_change():
    monitor = GC2Monitor()
    states = []
    monitor._connection_callback = states.append  # pylint: disable=protected-access

    monitor._set_connected(False)  # pylint: disable=protected-access
    monitor._set_connected(True)  # pylint: disable=protected-access
    monitor._set_connected(True)  # pylint: disable=protected-access
    monitor._set_connected(False)  # pylint: disable=protected-access

    assert states == [True, False]


def test_early_shot_callback_fires_once_for_each_accepted_shot():
    early = []
    shots = []
    parser = GC2StreamParser(shots.append, merge_window=60, shot_started_callback=early.append)

    parser.feed(b"TM=1,ID=1,SP=0\r\n" + FIRST + FIRST + UPDATE)
    assert len(early) == 1
    assert shots == []

    parser.feed(NEXT_SHOT)
    assert len(early) == 2
    assert len(shots) == 1
    assert early[0] == shots[0].impact_time
    parser.flush()
    assert early[1] == shots[1].impact_time

    reconnected = GC2StreamParser(
        shots.append, merge_window=60, last_shot_tms=parser.last_shot_tms,
        shot_started_callback=early.append,
    )
    reconnected.feed(NEXT_SHOT)
    assert len(early) == 2


def test_monitor_early_callback_failure_does_not_prevent_shot(caplog):
    monitor = GC2Monitor()
    shots = []
    monitor._shot_callback = shots.append  # pylint: disable=protected-access

    def fail(_timestamp):
        raise RuntimeError("camera failed")

    monitor._shot_started_callback = fail  # pylint: disable=protected-access
    parser = GC2StreamParser(
        monitor._handle_shot_data,  # pylint: disable=protected-access
        merge_window=60,
        shot_started_callback=monitor._handle_shot_started,  # pylint: disable=protected-access
    )
    parser.feed(FIRST)
    parser.flush()

    assert len(shots) == 1
    assert "GC2 early shot callback failed" in caplog.text


def test_read_loop_delivers_early_event_before_merged_shot(monkeypatch):
    from openflight.gc2 import gc2_monitor

    events = []
    monitor = GC2Monitor()
    monitor._running = True  # pylint: disable=protected-access
    monitor._shot_started_callback = lambda timestamp: events.append(("early", timestamp))  # pylint: disable=protected-access
    monitor._shot_callback = lambda shot: events.append(("shot", shot))  # pylint: disable=protected-access

    class Socket:
        def recv(self, _size):
            if not events:
                return FIRST + UPDATE
            return b""

    monkeypatch.setattr(gc2_monitor.select, "select", lambda *args: ([args[0][0]], [], []))
    with pytest.raises(OSError, match="Bluetooth connection closed"):
        monitor._read_loop(Socket())  # pylint: disable=protected-access

    assert [event[0] for event in events] == ["early", "shot"]
    assert events[0][1] == events[1][1].impact_timestamp
    assert events[1][1].spin_rpm == 2800.0
