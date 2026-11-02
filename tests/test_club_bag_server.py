"""Server behaviour for bag clubs: selection, reconciliation, editing and shot stamping."""

from datetime import datetime

import pytest

from openflight import server as server_module
from openflight.clubs import ClubType
from openflight.clubs.bag import BagClub, parse_bag
from openflight.launch_monitor import Shot
from openflight.profiles import ProfileStore
from openflight.server import MockLaunchMonitor, on_shot_detected

WEDGE_A = {
    "id": "sw-a",
    "club": "sw",
    "brand": "Acme",
    "model": "Spinner",
    "loft_deg": 56,
    "bounce_deg": 10,
}
WEDGE_B = {
    "id": "sw-b",
    "club": "sw",
    "brand": "Other",
    "model": "Digger",
    "loft_deg": 56,
    "bounce_deg": 14,
}
PW = {"id": "pw-1", "club": "pw", "brand": "Acme", "loft_deg": 46}


class _Monitor:
    def __init__(self):
        self.clubs = []

    def set_club(self, club):
        self.clubs.append(club)


@pytest.fixture(name="emitted")
def fixture_emitted(monkeypatch):
    """Every Socket.IO emit, as (event, data) pairs."""
    events = []
    monkeypatch.setattr(
        server_module.socketio, "emit", lambda event, data=None, **_kw: events.append((event, data))
    )
    return events


@pytest.fixture(name="store")
def fixture_store(tmp_path, monkeypatch, emitted):  # pylint: disable=unused-argument
    """A fresh roster plus clean, isolated club state."""
    profile_store = ProfileStore(tmp_path / "profiles.json")
    monkeypatch.setattr(server_module, "profile_store", profile_store)
    monkeypatch.setattr(server_module, "active_club", ClubType.DRIVER)
    monkeypatch.setattr(server_module, "active_bag_club_id", "")
    monkeypatch.setattr(server_module, "monitor", None)
    monkeypatch.setattr(server_module, "ble_publisher", None)
    monkeypatch.setattr(server_module.shot_stream, "publish_club", lambda _club: True)
    monkeypatch.setattr(server_module, "_publish_phone_event", lambda _event: None)
    return profile_store


def _give_bag(store, *entries, profile_id=None):
    store.set_bag(profile_id or store.get_active().id, parse_bag(list(entries)))


def _club_events(emitted):
    return [data for event, data in emitted if event == "club_changed"]


class TestSelectByBagClub:
    """A bag club id picks one specific club and implies its type."""

    def test_selects_bag_club_and_its_type(self, store, emitted, monkeypatch):
        _give_bag(store, WEDGE_A, WEDGE_B)
        monitor = _Monitor()
        monkeypatch.setattr(server_module, "monitor", monitor)

        response, status = server_module.apply_club_selection({"bag_club_id": "sw-b"})

        assert status == 200
        # The HTTP/BLE response shape is the phone contract and stays type-only.
        assert response == {"status": "applied", "club": "sw"}
        assert server_module.active_club is ClubType.SW
        assert server_module.active_bag_club_id == "sw-b"
        assert monitor.clubs == [ClubType.SW]
        assert _club_events(emitted) == [{"club": "sw", "bag_club_id": "sw-b"}]

    def test_matching_club_and_bag_club_are_accepted(self, store):
        _give_bag(store, WEDGE_A)

        _, status = server_module.apply_club_selection({"club": "sw", "bag_club_id": "sw-a"})

        assert status == 200
        assert server_module.active_bag_club_id == "sw-a"

    def test_mismatched_club_is_rejected_without_changing_state(self, store, emitted):
        _give_bag(store, WEDGE_A)

        response, status = server_module.apply_club_selection(
            {"club": "pw", "bag_club_id": "sw-a"}
        )

        assert status == 400
        assert "error" in response
        assert server_module.active_club is ClubType.DRIVER
        assert server_module.active_bag_club_id == ""
        assert _club_events(emitted) == []

    def test_unknown_bag_club_is_rejected(self, store, emitted):
        _give_bag(store, WEDGE_A)

        _, status = server_module.apply_club_selection({"bag_club_id": "missing"})

        assert status == 404
        assert server_module.active_club is ClubType.DRIVER
        assert _club_events(emitted) == []

    def test_bag_club_of_another_profile_is_rejected(self, store):
        other = store.add("Other")
        _give_bag(store, WEDGE_A, profile_id=store.list()[0].id)
        store.set_active(other.id)

        _, status = server_module.apply_club_selection({"bag_club_id": "sw-a"})

        assert status == 404

    def test_monitor_failure_leaves_selection_unchanged(self, store, monkeypatch):
        _give_bag(store, WEDGE_A)

        class _Broken:
            def set_club(self, _club):
                raise RuntimeError("radar busy")

        monkeypatch.setattr(server_module, "monitor", _Broken())

        _, status = server_module.apply_club_selection({"bag_club_id": "sw-a"})

        assert status == 500
        assert server_module.active_club is ClubType.DRIVER
        assert server_module.active_bag_club_id == ""


class TestSelectByTypeOnly:
    """Simulators, phones and /api/club send only a type."""

    def test_sole_club_of_that_type_is_selected(self, store, emitted):
        _give_bag(store, WEDGE_A, PW)

        server_module.apply_club_selection({"club": "pw"})

        assert server_module.active_bag_club_id == "pw-1"
        assert _club_events(emitted) == [{"club": "pw", "bag_club_id": "pw-1"}]

    def test_ambiguous_type_selects_no_bag_club(self, store):
        _give_bag(store, WEDGE_A, WEDGE_B)
        server_module.apply_club_selection({"club": "driver"})

        server_module.apply_club_selection({"club": "sw"})

        assert server_module.active_club is ClubType.SW
        assert server_module.active_bag_club_id == ""

    def test_same_type_echo_keeps_the_selected_bag_club(self, store, emitted):
        # Simulators echo the club back after every change; with two sand
        # wedges in the bag that echo must not drop the one the player chose.
        _give_bag(store, WEDGE_A, WEDGE_B)
        server_module.apply_club_selection({"bag_club_id": "sw-b"})
        emitted.clear()

        server_module.apply_club_selection({"club": "sw"})

        assert server_module.active_bag_club_id == "sw-b"
        assert _club_events(emitted) == [{"club": "sw", "bag_club_id": "sw-b"}]

    def test_type_change_drops_the_previous_bag_club(self, store):
        _give_bag(store, WEDGE_A, WEDGE_B)
        server_module.apply_club_selection({"bag_club_id": "sw-b"})

        server_module.apply_club_selection({"club": "driver"})

        assert server_module.active_club is ClubType.DRIVER
        assert server_module.active_bag_club_id == ""

    def test_type_not_in_bag_selects_no_bag_club(self, store):
        _give_bag(store, WEDGE_A)

        server_module.apply_club_selection({"club": "driver"})

        assert server_module.active_bag_club_id == ""

    # pylint: disable-next=unused-argument
    def test_without_a_bag_behaves_as_before(self, store, emitted):
        response, status = server_module.apply_club_selection({"club": "7-iron"})

        assert (response, status) == ({"status": "applied", "club": "7-iron"}, 200)
        assert _club_events(emitted) == [{"club": "7-iron", "bag_club_id": ""}]

    def test_get_club_keeps_its_shape(self, store):
        _give_bag(store, WEDGE_A)
        server_module.apply_club_selection({"bag_club_id": "sw-a"})

        assert server_module.current_club_selection() == ({"status": "current", "club": "sw"}, 200)


class TestReconcileOnProfileSwitch:
    """Switching profile never leaves another profile's club selected."""

    def test_switch_to_profile_without_the_club_clears_it(self, store, emitted):
        first = store.get_active().id
        second = store.add("Second").id
        store.set_active(first)
        _give_bag(store, WEDGE_A, profile_id=first)
        server_module.apply_club_selection({"bag_club_id": "sw-a"})
        emitted.clear()

        server_module.apply_active_profile({"profile_id": second})

        assert server_module.active_club is ClubType.SW
        assert server_module.active_bag_club_id == ""
        assert _club_events(emitted) == [{"club": "sw", "bag_club_id": ""}]

    def test_switch_lands_on_the_new_profiles_sole_club_of_that_type(self, store):
        first = store.get_active().id
        second = store.add("Second").id
        store.set_active(first)
        _give_bag(store, WEDGE_A, profile_id=first)
        _give_bag(store, WEDGE_B, profile_id=second)
        server_module.apply_club_selection({"bag_club_id": "sw-a"})

        server_module.apply_active_profile({"profile_id": second})

        assert server_module.active_bag_club_id == "sw-b"

    def test_no_broadcast_when_nothing_changes(self, store, emitted):
        second = store.add("Second").id
        store.set_active(store.list()[0].id)
        emitted.clear()

        server_module.apply_active_profile({"profile_id": second})

        assert _club_events(emitted) == []

    def test_unknown_profile_does_not_reconcile(self, store, emitted):
        _give_bag(store, WEDGE_A)
        server_module.apply_club_selection({"bag_club_id": "sw-a"})
        emitted.clear()

        server_module.apply_active_profile({"profile_id": "nope"})

        assert server_module.active_bag_club_id == "sw-a"
        assert _club_events(emitted) == []


class TestApplyProfileBag:
    """set_profile_bag replaces a whole bag atomically."""

    def test_valid_bag_is_stored_and_broadcast(self, store, emitted):
        response, status = server_module.apply_profile_bag({"bag": [WEDGE_A, WEDGE_B]})

        assert status == 200
        assert [club["id"] for club in response["bag"]] == ["sw-a", "sw-b"]
        assert [club.id for club in store.get_bag(store.get_active().id)] == ["sw-a", "sw-b"]
        profiles_events = [data for event, data in emitted if event == "profiles"]
        assert profiles_events
        assert profiles_events[-1]["profiles"][0]["settings"]["bag"][1]["bounce_deg"] == 14.0

    def test_new_clubs_get_server_ids(self, store):
        _, status = server_module.apply_profile_bag({"bag": [{"club": "lw", "loft_deg": 60}]})

        assert status == 200
        (club,) = store.get_bag(store.get_active().id)
        assert club.id and club.loft_deg == 60.0

    def test_targets_named_profile(self, store):
        active = store.get_active().id
        other = store.add("Other").id
        store.set_active(active)

        server_module.apply_profile_bag({"profile_id": other, "bag": [PW]})

        assert store.get_bag(active) == []
        assert [club.id for club in store.get_bag(other)] == ["pw-1"]

    def test_invalid_bag_is_rejected_and_old_bag_kept(self, store, emitted):
        _give_bag(store, WEDGE_A)
        emitted.clear()

        response, status = server_module.apply_profile_bag(
            {"bag": [WEDGE_B, {**PW, "loft_deg": 460}]}
        )

        assert status == 400
        assert "loft_deg" in response["error"]
        assert [club.id for club in store.get_bag(store.get_active().id)] == ["sw-a"]
        # Clients still get the authoritative roster so stale drafts converge.
        assert any(event == "profiles" for event, _ in emitted)

    @pytest.mark.parametrize("payload", [None, {}, {"bag": "sw"}, "bag"])
    def test_malformed_payload_is_rejected(self, store, payload):  # pylint: disable=unused-argument
        _, status = server_module.apply_profile_bag(payload)

        assert status == 400

    def test_unknown_profile_is_rejected(self, store):  # pylint: disable=unused-argument
        _, status = server_module.apply_profile_bag({"profile_id": "nope", "bag": [PW]})

        assert status == 404

    def test_removing_the_selected_club_clears_the_selection(self, store, emitted):
        _give_bag(store, WEDGE_A, WEDGE_B)
        server_module.apply_club_selection({"bag_club_id": "sw-a"})
        emitted.clear()

        server_module.apply_profile_bag({"bag": [WEDGE_B, PW]})

        # Only one sand wedge is left, so the selection moves to it.
        assert server_module.active_bag_club_id == "sw-b"
        assert _club_events(emitted) == [{"club": "sw", "bag_club_id": "sw-b"}]

    def test_changing_the_selected_clubs_type_clears_it(self, store):
        _give_bag(store, WEDGE_A, WEDGE_B)
        server_module.apply_club_selection({"bag_club_id": "sw-a"})

        # The old club becomes a lob wedge and two new sand wedges make "sw" ambiguous.
        server_module.apply_profile_bag(
            {"bag": [{**WEDGE_A, "club": "lw"}, {**WEDGE_B, "id": "x"}, {**WEDGE_B, "id": "y"}]}
        )

        assert server_module.active_club is ClubType.SW
        assert server_module.active_bag_club_id == ""

    def test_editing_specs_keeps_the_selection(self, store, emitted):
        _give_bag(store, WEDGE_A)
        server_module.apply_club_selection({"bag_club_id": "sw-a"})
        emitted.clear()

        server_module.apply_profile_bag({"bag": [{**WEDGE_A, "bounce_deg": 12}]})

        assert server_module.active_bag_club_id == "sw-a"
        assert _club_events(emitted) == []

    # pylint: disable-next=unused-argument
    def test_socket_handler_reports_rejections(self, store, emitted):
        server_module.handle_set_profile_bag({"bag": [{"club": "putter"}]})

        errors = [data for event, data in emitted if event == "profile_bag_error"]
        assert len(errors) == 1 and "error" in errors[0]

    # pylint: disable-next=unused-argument
    def test_socket_handler_is_quiet_on_success(self, store, emitted):
        server_module.handle_set_profile_bag({"bag": [PW]})

        assert not [event for event, _ in emitted if event == "profile_bag_error"]


class TestShotStamping:
    """Shots record which bag club hit them."""

    @staticmethod
    def _shot(club=ClubType.SW):
        return Shot(ball_speed_mph=80.0, club_speed_mph=65.0, timestamp=datetime.now(), club=club)

    def test_selected_bag_club_is_attributed(self, store):
        _give_bag(store, WEDGE_A, WEDGE_B)
        server_module.apply_club_selection({"bag_club_id": "sw-b"})

        bag_club = server_module._bag_club_for_shot(  # pylint: disable=protected-access
            ClubType.SW, store.get_active().id
        )

        assert isinstance(bag_club, BagClub)
        assert (bag_club.id, bag_club.bounce_deg) == ("sw-b", 14.0)

    def test_type_mismatch_is_not_attributed(self, store):
        _give_bag(store, WEDGE_A)
        server_module.apply_club_selection({"bag_club_id": "sw-a"})

        assert (
            server_module._bag_club_for_shot(  # pylint: disable=protected-access
                ClubType.PW, store.get_active().id
            )
            is None
        )

    def test_club_removed_from_bag_is_not_attributed(self, store, monkeypatch):
        _give_bag(store, WEDGE_A)
        server_module.apply_club_selection({"bag_club_id": "sw-a"})
        store.set_bag(store.get_active().id, [])
        monkeypatch.setattr(server_module, "active_bag_club_id", "sw-a")

        assert (
            server_module._bag_club_for_shot(  # pylint: disable=protected-access
                ClubType.SW, store.get_active().id
            )
            is None
        )

    def test_no_selection_is_not_attributed(self, store):
        _give_bag(store, WEDGE_A, WEDGE_B)

        assert (
            server_module._bag_club_for_shot(  # pylint: disable=protected-access
                ClubType.SW, store.get_active().id
            )
            is None
        )

    def test_shot_callback_stamps_the_bag_club(self, store, monkeypatch):
        _give_bag(store, WEDGE_A, WEDGE_B)
        monitor = MockLaunchMonitor()
        monitor.connect()
        monitor.start()
        monkeypatch.setattr(server_module, "monitor", monitor)
        server_module.apply_club_selection({"bag_club_id": "sw-a"})
        shot = monitor.simulate_shot(ball_speed=80.0)

        on_shot_detected(shot)

        assert shot.club is ClubType.SW
        assert shot.bag_club is not None and shot.bag_club.id == "sw-a"
        assert shot.to_dict()["bag_club"]["model"] == "Spinner"
        assert server_module.shot_to_dict(shot)["bag_club"]["loft_deg"] == 56.0


class TestShotDict:
    """The bag club rides on the canonical shot schema (UI + session logs)."""

    def test_bag_club_defaults_to_none(self):
        shot = Shot(ball_speed_mph=150.0, timestamp=datetime.now())

        assert shot.to_dict()["bag_club"] is None

    def test_bag_club_is_serialised(self):
        (club,) = parse_bag([WEDGE_A])
        shot = Shot(ball_speed_mph=80.0, timestamp=datetime.now(), club=ClubType.SW, bag_club=club)

        assert shot.to_dict()["bag_club"] == club.to_dict()


def test_session_state_carries_the_bag_club_id(store, monkeypatch):
    _give_bag(store, WEDGE_A)
    server_module.apply_club_selection({"bag_club_id": "sw-a"})
    monkeypatch.setattr(server_module, "monitor", None)

    payload = server_module._session_state_payload()  # pylint: disable=protected-access

    assert payload["bag_club_id"] == "sw-a"
