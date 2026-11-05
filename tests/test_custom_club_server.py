"""Server behaviour for custom clubs: selection, reconciliation, editing and shot stamping."""

from datetime import datetime

import pytest

from openflight import server as server_module
from openflight.clubs import ClubType
from openflight.clubs.store import ClubStore
from openflight.launch_monitor import Shot
from openflight.profiles import ProfileStore
from openflight.server import MockLaunchMonitor, on_shot_detected

SPINNER = {"club": "sw", "brand": "Acme", "model": "Spinner", "loft_deg": 56, "bounce_deg": 10}
DIGGER = {"club": "sw", "brand": "Other", "model": "Digger", "loft_deg": 56, "bounce_deg": 14}
PW = {"club": "pw", "brand": "Acme", "loft_deg": 46}

# Every test gets a fresh roster and club catalog; see fixture_profiles.
pytestmark = pytest.mark.usefixtures("profiles")


class _Monitor:
    def __init__(self):
        self.clubs = []

    def set_club(self, club):
        self.clubs.append(club)


class _Publisher:
    def __init__(self):
        self.clubs = []
        self.events = []

    def publish_club(self, club, custom_club_id=""):
        self.clubs.append((club, custom_club_id))
        return True

    def publish_event(self, event):
        self.events.append(event)
        return True


@pytest.fixture(name="emitted")
def fixture_emitted(monkeypatch):
    """Every Socket.IO emit, as (event, data) pairs."""
    events = []
    monkeypatch.setattr(
        server_module.socketio, "emit", lambda event, data=None, **_kw: events.append((event, data))
    )
    return events


@pytest.fixture(name="phones")
def fixture_phones(monkeypatch):
    """Stands in for both the network stream and BLE."""
    phones = _Publisher()
    monkeypatch.setattr(server_module, "shot_stream", phones)
    monkeypatch.setattr(server_module, "_publish_phone_event", phones.publish_event)
    return phones


@pytest.fixture(name="profiles")
def fixture_profiles(tmp_path, monkeypatch, emitted, phones):  # pylint: disable=unused-argument
    """A fresh roster and club catalog, and clean selection state."""
    profile_store = ProfileStore(tmp_path / "profiles.json")
    monkeypatch.setattr(server_module, "profile_store", profile_store)
    monkeypatch.setattr(server_module, "club_store", ClubStore(tmp_path / "clubs.json"))
    monkeypatch.setattr(server_module, "active_club", ClubType.DRIVER)
    monkeypatch.setattr(server_module, "active_custom_club_id", "")
    monkeypatch.setattr(server_module, "monitor", None)
    monkeypatch.setattr(server_module, "ble_publisher", None)
    return profile_store


def _add(fields, bag=None):
    """Add a club through the store and return its id."""
    store = server_module.get_club_store()
    return store.add(bag or server_module.get_profile_store().get_active().id, fields).id


def _club_events(emitted):
    return [data for event, data in emitted if event == "club_changed"]


class TestSelectByCustomClub:
    """A custom club id picks one specific club and implies its type."""

    def test_selects_the_club_and_its_type_on_every_transport(self, emitted, phones, monkeypatch):
        _add(SPINNER)
        digger = _add(DIGGER)
        monitor = _Monitor()
        monkeypatch.setattr(server_module, "monitor", monitor)

        response, status = server_module.apply_club_selection({"custom_club_id": digger})

        assert (response, status) == (
            {"status": "applied", "club": "sw", "custom_club_id": digger},
            200,
        )
        assert server_module.active_club is ClubType.SW
        assert monitor.clubs == [ClubType.SW]
        assert _club_events(emitted) == [{"club": "sw", "custom_club_id": digger}]
        assert phones.clubs == [("sw", digger)]
        assert server_module.current_club_selection() == (
            {"status": "current", "club": "sw", "custom_club_id": digger},
            200,
        )

    def test_matching_club_is_accepted(self):
        spinner = _add(SPINNER)

        _, status = server_module.apply_club_selection({"club": "sw", "custom_club_id": spinner})

        assert status == 200
        assert server_module.active_custom_club_id == spinner

    def test_mismatched_club_is_rejected_without_changing_state(self, emitted):
        spinner = _add(SPINNER)

        response, status = server_module.apply_club_selection(
            {"club": "pw", "custom_club_id": spinner}
        )

        assert status == 400
        assert "error" in response
        assert server_module.active_club is ClubType.DRIVER
        assert server_module.active_custom_club_id == ""
        assert _club_events(emitted) == []

    def test_unknown_club_id_is_rejected(self):
        _add(SPINNER)

        _, status = server_module.apply_club_selection({"custom_club_id": "missing"})

        assert status == 404
        assert server_module.active_club is ClubType.DRIVER

    def test_club_from_another_bag_is_rejected(self, profiles):
        first = profiles.get_active().id
        spinner = _add(SPINNER, bag=first)
        profiles.set_active(profiles.add("Other").id)

        _, status = server_module.apply_club_selection({"custom_club_id": spinner})

        assert status == 404

    def test_monitor_failure_leaves_selection_unchanged(self, monkeypatch):
        spinner = _add(SPINNER)

        class _Broken:
            def set_club(self, _club):
                raise RuntimeError("radar busy")

        monkeypatch.setattr(server_module, "monitor", _Broken())

        _, status = server_module.apply_club_selection({"custom_club_id": spinner})

        assert status == 500
        assert server_module.active_club is ClubType.DRIVER
        assert server_module.active_custom_club_id == ""


class TestSelectByTypeOnly:
    """Simulators, phones and /api/club may send only a type."""

    def test_the_bags_only_club_of_that_type_is_selected(self, emitted):
        _add(SPINNER)
        pitching = _add(PW)

        server_module.apply_club_selection({"club": "pw"})

        assert server_module.active_custom_club_id == pitching
        assert _club_events(emitted) == [{"club": "pw", "custom_club_id": pitching}]

    def test_ambiguous_type_selects_no_custom_club(self):
        _add(SPINNER)
        _add(DIGGER)

        server_module.apply_club_selection({"club": "sw"})

        assert server_module.active_club is ClubType.SW
        assert server_module.active_custom_club_id == ""

    def test_same_type_echo_keeps_the_selected_club(self, emitted):
        # Simulators echo the club back after every change; with two sand
        # wedges in the bag that echo must not drop the one the player chose.
        _add(SPINNER)
        digger = _add(DIGGER)
        server_module.apply_club_selection({"custom_club_id": digger})
        emitted.clear()

        server_module.apply_club_selection({"club": "sw"})

        assert server_module.active_custom_club_id == digger
        assert _club_events(emitted) == [{"club": "sw", "custom_club_id": digger}]

    def test_type_change_drops_the_previous_club(self):
        digger = _add(DIGGER)
        server_module.apply_club_selection({"custom_club_id": digger})

        server_module.apply_club_selection({"club": "driver"})

        assert server_module.active_club is ClubType.DRIVER
        assert server_module.active_custom_club_id == ""

    def test_without_clubs_behaves_as_before(self, emitted):
        response, status = server_module.apply_club_selection({"club": "7-iron"})

        assert (response, status) == (
            {"status": "applied", "club": "7-iron", "custom_club_id": ""},
            200,
        )
        assert _club_events(emitted) == [{"club": "7-iron", "custom_club_id": ""}]


class TestReconcile:
    """The selection never names a club outside the active bag."""

    def test_profile_switch_clears_a_club_the_new_bag_lacks(self, profiles, emitted):
        first = profiles.get_active().id
        second = profiles.add("Second").id
        profiles.set_active(first)
        spinner = _add(SPINNER, bag=first)
        server_module.apply_club_selection({"custom_club_id": spinner})
        emitted.clear()

        server_module.apply_active_profile({"profile_id": second})

        assert server_module.active_club is ClubType.SW
        assert server_module.active_custom_club_id == ""
        assert _club_events(emitted) == [{"club": "sw", "custom_club_id": ""}]

    def test_profile_switch_lands_on_the_new_bags_only_club(self, profiles):
        first = profiles.get_active().id
        second = profiles.add("Second").id
        profiles.set_active(first)
        spinner = _add(SPINNER, bag=first)
        digger = _add(DIGGER, bag=second)
        server_module.apply_club_selection({"custom_club_id": spinner})

        server_module.apply_active_profile({"profile_id": second})

        assert server_module.active_custom_club_id == digger

    def test_profile_switch_sends_phones_the_new_bag(self, profiles, phones):
        first = profiles.get_active().id
        second = profiles.add("Second").id
        profiles.set_active(first)
        _add(SPINNER, bag=first)
        digger = _add(DIGGER, bag=second)
        phones.events.clear()

        server_module.apply_active_profile({"profile_id": second})

        clubs_events = [event for event in phones.events if event["type"] == "clubs"]
        assert clubs_events[-1]["bag"] == second
        assert [club["id"] for club in clubs_events[-1]["clubs"]] == [digger]

    def test_unknown_profile_does_not_reconcile(self, emitted):
        spinner = _add(SPINNER)
        server_module.apply_club_selection({"custom_club_id": spinner})
        emitted.clear()

        server_module.apply_active_profile({"profile_id": "nope"})

        assert server_module.active_custom_club_id == spinner
        assert _club_events(emitted) == []


class TestSaveAndRemove:
    """save_custom_club and remove_custom_club."""

    def test_add_goes_to_the_active_bag_and_broadcasts(self, profiles, emitted, phones):
        response, status = server_module.apply_save_custom_club(SPINNER)

        assert (response, status) == ({"status": "applied"}, 200)
        (club,) = server_module.get_club_store().list()
        assert club.bag == profiles.get_active().id
        snapshots = [data for event, data in emitted if event == "clubs"]
        assert snapshots[-1]["clubs"][0]["model"] == "Spinner"
        assert snapshots[-1]["lofts"]["sw"] == 54.0  # what a blank loft inherits
        assert phones.events[-1]["type"] == "clubs"
        assert phones.events[-1]["clubs"] == [
            {"id": club.id, "club": "sw", "loft_deg": 56.0, "model": "Spinner"}
        ]

    def test_add_to_a_named_profiles_bag(self, profiles):
        active = profiles.get_active().id
        other = profiles.add("Other").id
        profiles.set_active(active)

        server_module.apply_save_custom_club({**PW, "bag": other})

        assert server_module.get_club_store().list(active) == []
        assert len(server_module.get_club_store().list(other)) == 1

    def test_add_to_a_bag_that_is_no_profile_is_rejected(self):
        _, status = server_module.apply_save_custom_club({**PW, "bag": "stranger"})

        assert status == 404
        assert server_module.get_club_store().list() == []

    def test_update_keeps_the_id(self):
        digger = _add(DIGGER)

        server_module.apply_save_custom_club({**DIGGER, "id": digger, "bounce_deg": 12})

        assert server_module.get_club_store().get(digger).bounce_deg == 12.0

    def test_invalid_club_is_reported_and_nothing_changes(self, emitted):
        spinner = _add(SPINNER)
        emitted.clear()

        server_module.handle_save_custom_club({**SPINNER, "id": spinner, "loft_deg": 460})

        errors = [data for event, data in emitted if event == "custom_club_error"]
        assert len(errors) == 1 and "loft_deg" in errors[0]["error"]
        assert server_module.get_club_store().get(spinner).loft_deg == 56.0
        assert any(event == "clubs" for event, _ in emitted)

    @pytest.mark.parametrize("payload", [None, {}, {"club": "putter"}, "sw"])
    def test_malformed_payload_is_rejected(self, payload):
        _, status = server_module.apply_save_custom_club(payload)

        assert status == 400

    def test_removing_the_selected_club_moves_the_selection(self, emitted):
        spinner = _add(SPINNER)
        digger = _add(DIGGER)
        server_module.apply_club_selection({"custom_club_id": spinner})
        emitted.clear()

        response, status = server_module.apply_remove_custom_club({"id": spinner})

        assert (response, status) == ({"status": "removed"}, 200)
        # Only one sand wedge is left, so the selection moves to it.
        assert server_module.active_custom_club_id == digger
        assert _club_events(emitted) == [{"club": "sw", "custom_club_id": digger}]

    def test_removing_an_unknown_club_is_reported(self, emitted):
        server_module.handle_remove_custom_club({"id": "missing"})

        assert [event for event, _ in emitted if event == "custom_club_error"]

    def test_changing_the_selected_clubs_type_clears_it(self):
        spinner = _add(SPINNER)
        _add(DIGGER)
        _add(DIGGER)
        server_module.apply_club_selection({"custom_club_id": spinner})

        server_module.apply_save_custom_club({**SPINNER, "id": spinner, "club": "lw"})

        assert server_module.active_club is ClubType.SW
        assert server_module.active_custom_club_id == ""

    def test_removing_a_profile_removes_its_bag(self, profiles, emitted):
        active = profiles.get_active().id
        other = profiles.add("Other").id
        profiles.set_active(active)
        _add(PW, bag=other)
        kept = _add(SPINNER, bag=active)

        server_module.handle_remove_profile({"profile_id": other})

        assert [club.id for club in server_module.get_club_store().list()] == [kept]
        assert any(event == "clubs" for event, _ in emitted)

    def test_get_clubs_is_a_read_only_phone_command(self, phones):
        _add(SPINNER)

        response, status = server_module.dispatch_phone_control_command("get_clubs", None)

        assert (response, status) == ({"status": "sent"}, 200)
        assert phones.events[-1]["type"] == "clubs"
        for command in ("save_custom_club", "remove_custom_club"):
            assert server_module.dispatch_phone_control_command(command, SPINNER)[1] == 400


class TestShotStamping:
    """Shots record which custom club hit them."""

    def test_selected_club_is_attributed(self, profiles):
        _add(SPINNER)
        digger = _add(DIGGER)
        server_module.apply_club_selection({"custom_club_id": digger})

        club = server_module._custom_club_for_shot(  # pylint: disable=protected-access
            ClubType.SW, profiles.get_active().id
        )

        assert (club.id, club.bounce_deg) == (digger, 14.0)

    def test_type_mismatch_is_not_attributed(self, profiles):
        spinner = _add(SPINNER)
        server_module.apply_club_selection({"custom_club_id": spinner})

        assert (
            server_module._custom_club_for_shot(  # pylint: disable=protected-access
                ClubType.PW, profiles.get_active().id
            )
            is None
        )

    def test_club_of_another_profile_is_not_attributed(self, profiles, monkeypatch):
        first = profiles.get_active().id
        spinner = _add(SPINNER, bag=first)
        monkeypatch.setattr(server_module, "active_custom_club_id", spinner)

        assert (
            server_module._custom_club_for_shot(  # pylint: disable=protected-access
                ClubType.SW, profiles.add("Other").id
            )
            is None
        )

    def test_removed_club_is_not_attributed(self, profiles, monkeypatch):
        spinner = _add(SPINNER)
        server_module.get_club_store().remove(spinner)
        monkeypatch.setattr(server_module, "active_custom_club_id", spinner)

        assert (
            server_module._custom_club_for_shot(  # pylint: disable=protected-access
                ClubType.SW, profiles.get_active().id
            )
            is None
        )

    def test_shot_callback_stamps_the_club(self, monkeypatch):
        spinner = _add(SPINNER)
        _add(DIGGER)
        monitor = MockLaunchMonitor()
        monitor.connect()
        monitor.start()
        monkeypatch.setattr(server_module, "monitor", monitor)
        server_module.apply_club_selection({"custom_club_id": spinner})
        shot = monitor.simulate_shot(ball_speed=80.0)

        on_shot_detected(shot)

        assert shot.club is ClubType.SW
        data = server_module.shot_to_dict(shot)
        assert data["custom_club_id"] == spinner
        assert data["custom_club"]["model"] == "Spinner"
        assert data["custom_club"]["name"] == "Sand Wedge"


class TestShotDict:
    """The custom club rides on the canonical shot schema (UI + session logs)."""

    def test_defaults_to_none(self):
        data = Shot(ball_speed_mph=150.0, timestamp=datetime.now()).to_dict()

        assert (data["custom_club_id"], data["custom_club"]) == ("", None)


def test_session_state_carries_the_custom_club_id(monkeypatch):
    spinner = _add(SPINNER)
    server_module.apply_club_selection({"custom_club_id": spinner})
    monkeypatch.setattr(server_module, "monitor", None)

    payload = server_module._session_state_payload()  # pylint: disable=protected-access

    assert payload["custom_club_id"] == spinner
