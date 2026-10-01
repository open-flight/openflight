"""Mock mode simulating the optional-hardware shot path.

``MockLaunchMonitor`` reports ``capturing`` then ``calculating`` like the
rolling-buffer radar, can fail a shot on request, and with ``enrichment_ms``
holds back the direction fields (horizontal launch, club path, spin axis) that
IWR6843/camera hardware measures, so mock shots go provisional then final
through the real enrichment pipeline.
"""

import threading
from datetime import datetime

import pytest
from ble_harness import V2_UUIDS, server_loopback, settle

from openflight import server as server_module
from openflight.ble.protocol import CONTROL_V2_CHARACTERISTIC_UUID, SHOT_V2_CHARACTERISTIC_UUID
from openflight.launch_monitor import ClubType, Shot

DIRECTION_FIELDS = ("launch_angle_horizontal", "club_path_deg", "spin_axis_deg")


def _monitor(**kwargs):
    kwargs.setdefault("processing_step_s", 0)
    return server_module.MockLaunchMonitor(**kwargs)


# -- monitor --------------------------------------------------------------------------


class TestProcessingStates:
    def test_shot_reports_capturing_then_calculating(self):
        states, shots = [], []
        monitor = _monitor()
        monitor.start(shot_callback=shots.append, processing_callback=states.append)

        shot = monitor.simulate_shot()

        assert states == ["capturing", "calculating"]
        assert shots == [shot]

    def test_failed_shot_reports_failed_and_records_nothing(self):
        states, shots = [], []
        monitor = _monitor()
        monitor.start(shot_callback=shots.append, processing_callback=states.append)

        assert monitor.simulate_shot(fail=True) is None

        assert states == ["capturing", "calculating", "failed"]
        assert shots == []
        assert monitor.get_shots() == []

    def test_without_processing_callback_shots_still_work(self):
        monitor = _monitor()
        monitor.start()

        assert monitor.simulate_shot() is not None
        assert monitor.simulate_shot(fail=True) is None

    def test_processing_callback_errors_do_not_stop_the_shot(self):
        def broken(_state):
            raise RuntimeError("UI went away")

        monitor = _monitor()
        monitor.start(processing_callback=broken)

        assert monitor.simulate_shot() is not None
        assert len(monitor.get_shots()) == 1


class TestSimulatedEnrichment:
    def test_disabled_by_default_and_shots_are_complete(self):
        monitor = _monitor()
        monitor.start()

        shot = monitor.simulate_shot()

        assert monitor.enrichment_ms == 0
        assert all(getattr(shot, field) is not None for field in DIRECTION_FIELDS)

    def test_enabled_shot_waits_for_enrichment_to_get_direction(self):
        monitor = _monitor(enrichment_ms=1)
        monitor.start()
        shot = monitor.simulate_shot()
        assert all(getattr(shot, field) is None for field in DIRECTION_FIELDS)

        elapsed_ms = monitor.enrich(shot)

        assert elapsed_ms >= 1
        assert all(getattr(shot, field) is not None for field in DIRECTION_FIELDS)
        assert shot.launch_angle_horizontal_source == "mock"

    def test_rejects_negative_enrichment(self):
        with pytest.raises(ValueError):
            server_module.MockLaunchMonitor(enrichment_ms=-1)


# -- server ---------------------------------------------------------------------------


def _mock_shot():
    return Shot(
        ball_speed_mph=150.0,
        timestamp=datetime(2026, 9, 28, 12, 0, 0),
        club=ClubType.DRIVER,
        mode="mock",
    )


def test_mock_shots_skip_slow_enrichment_by_default(monkeypatch):
    monkeypatch.setattr(server_module, "monitor", _monitor())

    assert server_module._has_slow_shot_enrichment(_mock_shot()) is False


def test_mock_enrichment_routes_mock_shots_through_slow_path(monkeypatch):
    monkeypatch.setattr(server_module, "monitor", _monitor(enrichment_ms=1))

    assert server_module._has_slow_shot_enrichment(_mock_shot()) is True


def test_simulate_shot_handler_can_fail_a_shot(monkeypatch):
    monitor = _monitor()
    states, shots = [], []
    monitor.start(shot_callback=shots.append, processing_callback=states.append)
    monkeypatch.setattr(server_module, "monitor", monitor)

    server_module.handle_simulate_shot({"fail": True})
    server_module.handle_simulate_shot()

    assert states == ["capturing", "calculating", "failed", "capturing", "calculating"]
    assert len(shots) == 1


# -- phone, over the BLE loopback -----------------------------------------------------


@pytest.fixture
def pi(monkeypatch, tmp_path):
    with server_loopback(monkeypatch, tmp_path) as loopback:
        yield loopback


def _run_enrichment_in_threads(monkeypatch):
    monkeypatch.setattr(server_module, "shot_enrichment_task", None)
    monkeypatch.setattr(
        server_module,
        "shot_enrichment_queue",
        server_module.queue.Queue(maxsize=server_module._SHOT_ENRICHMENT_QUEUE_CAPACITY),
    )

    def start_background_task(target, *args, **kwargs):
        thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
        thread.start()
        return thread

    monkeypatch.setattr(server_module.socketio, "start_background_task", start_background_task)


def _wait_idle():
    with server_module._shot_finalization_condition:
        assert server_module._shot_finalization_condition.wait_for(
            lambda: (
                not server_module._shot_finalization_order
                and not server_module._shot_finalization_running
            ),
            timeout=5,
        )


def _started_monitor(monkeypatch, **kwargs):
    monitor = _monitor(**kwargs)
    monitor.start(
        shot_callback=server_module.on_shot_detected,
        processing_callback=server_module.on_shot_processing,
    )
    monkeypatch.setattr(server_module, "monitor", monitor)
    return monitor


def _processing_states(phone):
    return [
        message["state"]
        for message in phone.decoded(CONTROL_V2_CHARACTERISTIC_UUID)
        if message.get("type") == "shot_processing"
    ]


def test_phone_sees_processing_then_provisional_then_final(pi, monkeypatch):
    _run_enrichment_in_threads(monkeypatch)
    monitor = _started_monitor(monkeypatch, enrichment_ms=50)
    phone = pi.central("v2-app")
    phone.subscribe(*V2_UUIDS)
    settle(pi)

    monitor.simulate_shot()
    _wait_idle()

    provisional, final = phone.wait_for_count(SHOT_V2_CHARACTERISTIC_UUID, 2)
    assert (provisional["final"], final["final"]) == (False, True)
    assert provisional["event_id"] == final["event_id"]
    assert provisional["enrichment"] == {"status": "pending"}
    assert final["enrichment"] == {"status": "complete"}
    assert provisional["club_path_deg"] is None
    assert final["club_path_deg"] is not None
    phone.wait_for(
        CONTROL_V2_CHARACTERISTIC_UUID,
        lambda message: message.get("state") == "calculating",
    )
    assert _processing_states(phone) == ["capturing", "calculating"]


def test_enrichment_past_the_deadline_finalizes_as_skipped(pi, monkeypatch):
    """What ``--mock-enrichment-ms`` above the 20 s deadline shows, sped up."""
    _run_enrichment_in_threads(monkeypatch)
    monkeypatch.setattr(server_module, "_SHOT_ENRICHMENT_DEADLINE_S", 0.2)
    monitor = _started_monitor(monkeypatch, enrichment_ms=1500)
    phone = pi.central("v2-app")
    phone.subscribe(*V2_UUIDS)
    settle(pi)

    monitor.simulate_shot()

    provisional, final = phone.wait_for_count(SHOT_V2_CHARACTERISTIC_UUID, 2, timeout=10)
    assert provisional["enrichment"] == {"status": "pending"}
    assert final["final"] is True
    assert final["enrichment"] == {"status": "skipped", "reason": "deadline"}
    assert final["event_id"] == provisional["event_id"]
    # Let the late enrichment finish so it cannot leak into the next test.
    worker = server_module.shot_enrichment_task
    if worker is not None:
        worker.join(timeout=5)
    _wait_idle()


def test_phone_sees_failed_processing_and_no_shot(pi, monkeypatch):
    monitor = _started_monitor(monkeypatch)
    phone = pi.central("v2-app")
    phone.subscribe(*V2_UUIDS)
    settle(pi)

    monitor.simulate_shot(fail=True)

    phone.wait_for(CONTROL_V2_CHARACTERISTIC_UUID, lambda message: message.get("state") == "failed")
    settle(pi, rounds=5)
    assert _processing_states(phone) == ["capturing", "calculating", "failed"]
    assert phone.decoded(SHOT_V2_CHARACTERISTIC_UUID) == []


# -- command line ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["--mock-enrichment-ms", "500"],
        ["--mock-swing-speed", "--mock-enrichment-ms", "500"],
        ["--mock", "--mock-enrichment-ms", "-1"],
    ],
)
def test_mock_enrichment_flag_is_validated_before_startup(monkeypatch, argv):
    monkeypatch.setattr(server_module.sys, "argv", ["openflight-server", *argv])

    with pytest.raises(SystemExit) as exit_info:
        server_module.main()

    assert exit_info.value.code == 2
