"""Integration: two connectors (GSPro + OpenGolfSim) running concurrently.

Both ride the shared OpenConnect V1 codec; they differ only in target/name. The
shot must reach each connector's own endpoint independently.
"""
import json
import sys
import threading
import time
from datetime import datetime
from typing import List

from openflight.clubs import ClubType
from openflight.launch_monitor import Shot
from openflight.sim.codec import build_connectors
from openflight.sim.config import ConnectorConfig
from openflight.sim.resolver import resolve_shot
from openflight.sim.transport import find_json_end
from openflight.sim.types import ConnectionState, PlayerState, PlayerUpdate
from tests.conftest import MockSimServer


def _wait(connector, state, deadline=3.0):
    end = time.time() + deadline
    while time.time() < end:
        if connector.state == state:
            return True
        time.sleep(0.05)
    return False


def _frames(received_chunks):
    """Concatenate received bytes and split into individual JSON objects."""
    buf = b"".join(received_chunks)
    out = []
    while True:
        end = find_json_end(buf)
        if end is None:
            break
        out.append(json.loads(buf[:end]))
        buf = buf[end:]
    return out


def test_shot_reaches_both_sims():
    gspro_srv = MockSimServer()
    ogs_srv = MockSimServer()
    try:
        cfgs = [
            ConnectorConfig(type="gspro", enabled=True, host=gspro_srv.host,
                            port=gspro_srv.port),
            ConnectorConfig(type="opengolfsim", enabled=True, host=ogs_srv.host,
                            port=ogs_srv.port),
        ]
        connectors = build_connectors(cfgs)
        assert {c.name for c in connectors} == {"gspro", "opengolfsim"}
        for c in connectors:
            c.start()
        try:
            assert all(_wait(c, ConnectionState.CONNECTED) for c in connectors)

            shot = Shot(ball_speed_mph=135.0, timestamp=datetime(2026, 6, 13, 12, 0, 0),
                        club=ClubType.DRIVER, launch_angle_vertical=11.1,
                        launch_angle_horizontal=1.2)
            resolved = resolve_shot(shot, PlayerState())
            for c in connectors:
                c.send_shot(resolved)

            deadline = time.time() + 1.5
            while time.time() < deadline and not (gspro_srv.received and ogs_srv.received):
                time.sleep(0.05)

            # Both speak OpenConnect V1 — each endpoint gets the BallData shot.
            for srv in (gspro_srv, ogs_srv):
                shot_msg = next(m for m in _frames(srv.received) if "BallData" in m)
                assert shot_msg["BallData"]["Speed"] == 135.0
                assert shot_msg["APIversion"] == "1"
        finally:
            for c in connectors:
                c.stop()
    finally:
        gspro_srv.stop()
        ogs_srv.stop()


def _hammer(callable_no_args, *, threads=8, per_thread=2000):
    """Run callable_no_args concurrently and return every value it produced.

    Drops the interpreter switch interval to maximize interleaving. Note: on a
    CPython GIL build the ``shot_counter += 1`` race is effectively unobservable
    (verified empirically up to ~1.6M increments), so these assertions hold with
    or without the lock today — they are guarantee tests that pin the contract
    and would catch a regression on a free-threaded (no-GIL) build.
    """
    results: List = []
    results_lock = threading.Lock()
    barrier = threading.Barrier(threads)

    def worker():
        barrier.wait()  # release all threads at once to maximize contention
        local = [callable_no_args() for _ in range(per_thread)]
        with results_lock:
            results.extend(local)

    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        workers = [threading.Thread(target=worker) for _ in range(threads)]
        for t in workers:
            t.start()
        for t in workers:
            t.join()
    finally:
        sys.setswitchinterval(old_interval)
    return results


def test_player_state_next_shot_number_is_thread_safe():
    """next_shot_number() must hand out a unique number to every caller, even
    under concurrent connectors (PR #115 review #5).

    ``shot_counter += 1`` is a read-modify-write; on a free-threaded build two
    threads can read the same value, lose an increment, and reuse a shot number
    (two sims receiving the same shot under different numbers). The lock makes
    that impossible. Guarantee test: the GIL masks the race on CPython, so this
    also passes unlocked today — it pins the invariant against regressions.
    """
    ps = PlayerState()
    threads, per_thread = 8, 2000
    expected = threads * per_thread

    numbers = _hammer(ps.next_shot_number, threads=threads, per_thread=per_thread)

    assert ps.shot_counter == expected  # no increments lost
    assert len(numbers) == expected
    assert len(set(numbers)) == expected  # no shot number handed out twice


def test_player_state_apply_is_thread_safe():
    """apply() mutates two fields; concurrent applies must not leave a torn state
    (PR #115 review #5). With the lock every observed snapshot is one input in
    full — never a half-applied ("LH", DRIVER) mix. Guarantee test: GIL-masked on
    CPython, so it also passes unlocked today; it pins the all-or-nothing contract.
    """
    ps = PlayerState()
    lh_i7 = PlayerUpdate(handed="LH", club=ClubType.IRON_7)
    rh_dr = PlayerUpdate(handed="RH", club=ClubType.DRIVER)
    updates = (lh_i7, rh_dr)

    seen: List[tuple] = []
    seen_lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker(idx):
        barrier.wait()
        local = []
        for i in range(2000):
            ps.apply(updates[(idx + i) % 2])
            local.append((ps.handed, ps.club))
        with seen_lock:
            seen.extend(local)

    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        workers = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in workers:
            t.start()
        for t in workers:
            t.join()
    finally:
        sys.setswitchinterval(old_interval)

    # Every snapshot must be one of the two consistent pairs — never a torn
    # ("LH", DRIVER) / ("RH", IRON_7) mix from a half-applied update.
    valid = {("LH", ClubType.IRON_7), ("RH", ClubType.DRIVER)}
    assert set(seen) <= valid
