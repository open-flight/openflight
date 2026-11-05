"""Tests for the custom club store. Persistence cases are adapted from weawer's prototype."""

import json
import threading

import pytest

from openflight.clubs import store as club_store_module
from openflight.clubs.store import (
    CLUBS_PATH_ENV,
    DEFAULT_CLUBS_PATH,
    MAX_CLUBS_PER_BAG,
    ClubStore,
    ClubStoreError,
    resolve_clubs_path,
)
from openflight.clubs.types import ClubType

SPINNER = {"club": "sw", "brand": "Acme", "model": "Spinner", "loft_deg": 56, "bounce_deg": 10}


@pytest.fixture(name="path")
def fixture_path(tmp_path):
    return tmp_path / "config" / "clubs.json"


def _fail(*_args, **_kwargs):
    raise OSError("disk failure")


class TestPaths:
    """Where the catalog lives."""

    def test_explicit_path_wins_over_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv(CLUBS_PATH_ENV, str(tmp_path / "env.json"))

        assert resolve_clubs_path(tmp_path / "explicit.json") == tmp_path / "explicit.json"

    def test_env_is_used_when_no_path_is_given(self, tmp_path, monkeypatch):
        monkeypatch.setenv(CLUBS_PATH_ENV, str(tmp_path / "env.json"))

        assert resolve_clubs_path() == tmp_path / "env.json"

    def test_blank_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv(CLUBS_PATH_ENV, "  ")

        assert resolve_clubs_path() == DEFAULT_CLUBS_PATH

    def test_missing_file_is_an_empty_catalog_and_not_written(self, path):
        store = ClubStore(path)

        assert store.list() == []
        assert not path.exists()


class TestAddUpdateRemove:
    """Mutations persist and keep ids stable."""

    def test_add_persists_and_reloads(self, path):
        added = ClubStore(path).add("bag-1", SPINNER)

        (reloaded,) = ClubStore(path).list()
        assert reloaded == added
        assert reloaded.bag == "bag-1"
        assert reloaded.club_type is ClubType.SW
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert data["clubs"][0]["id"] == added.id

    def test_add_always_issues_a_new_id(self, path):
        store = ClubStore(path)

        first = store.add("bag-1", {**SPINNER, "id": "client-chosen"})
        second = store.add("bag-1", SPINNER)

        assert first.id not in ("client-chosen", second.id)

    def test_add_requires_a_bag(self, path):
        with pytest.raises(ClubStoreError, match="bag"):
            ClubStore(path).add("  ", SPINNER)

    def test_add_rejects_invalid_fields_with_a_message(self, path):
        store = ClubStore(path)

        with pytest.raises(ClubStoreError, match="loft_deg"):
            store.add("bag-1", {**SPINNER, "loft_deg": 560})
        assert store.list() == []

    def test_each_bag_is_capped(self, path):
        store = ClubStore(path)
        for _ in range(MAX_CLUBS_PER_BAG):
            store.add("bag-1", SPINNER)

        with pytest.raises(ClubStoreError, match=str(MAX_CLUBS_PER_BAG)):
            store.add("bag-1", SPINNER)
        store.add("bag-2", SPINNER)

    def test_update_keeps_id_and_bag(self, path):
        store = ClubStore(path)
        added = store.add("bag-1", SPINNER)

        updated = store.update(added.id, {**SPINNER, "bounce_deg": 12, "bag": "elsewhere"})

        assert (updated.id, updated.bag, updated.bounce_deg) == (added.id, "bag-1", 12.0)
        assert ClubStore(path).get(added.id) == updated

    def test_update_of_a_removed_club_never_recreates_it(self, path):
        store = ClubStore(path)
        added = store.add("bag-1", SPINNER)
        store.remove(added.id)

        with pytest.raises(ClubStoreError, match="no longer exists"):
            store.update(added.id, SPINNER)
        assert store.list() == []

    def test_update_rejects_invalid_fields_and_keeps_the_old_club(self, path):
        store = ClubStore(path)
        added = store.add("bag-1", SPINNER)

        with pytest.raises(ClubStoreError, match="hand"):
            store.update(added.id, {**SPINNER, "hand": "both"})
        assert store.get(added.id) == added

    def test_remove(self, path):
        store = ClubStore(path)
        added = store.add("bag-1", SPINNER)

        assert store.remove(added.id) is True
        assert store.remove(added.id) is False
        assert ClubStore(path).list() == []

    def test_remove_bag_only_touches_that_bag(self, path):
        store = ClubStore(path)
        store.add("bag-1", SPINNER)
        store.add("bag-1", SPINNER)
        kept = store.add("bag-2", SPINNER)

        assert store.remove_bag("bag-1") == 2
        assert store.remove_bag("bag-1") == 0
        assert ClubStore(path).list() == [kept]

    def test_list_filters_by_bag_in_insertion_order(self, path):
        store = ClubStore(path)
        first = store.add("bag-1", SPINNER)
        store.add("bag-2", SPINNER)
        third = store.add("bag-1", {**SPINNER, "model": "Digger"})

        assert store.list("bag-1") == [first, third]
        assert len(store.list()) == 3


class TestLoading:
    """A bad or foreign file never blocks startup."""

    def test_skips_invalid_duplicate_and_incomplete_records(self, path):
        path.parent.mkdir(parents=True)
        valid = {"id": "stable-id", "bag": "bag-1", **SPINNER}
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "clubs": [
                        valid,
                        {**valid, "model": "Duplicate"},
                        {"id": "no-bag", **SPINNER},
                        {"bag": "bag-1", **SPINNER},
                        {"id": "bad-loft", "bag": "bag-1", **SPINNER, "loft_deg": 900},
                        "not an object",
                    ],
                }
            ),
            encoding="utf-8",
        )

        (club,) = ClubStore(path).list()

        assert (club.id, club.model) == ("stable-id", "Spinner")

    def test_unreadable_json_is_an_empty_catalog(self, path, caplog):
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")

        assert ClubStore(path).list() == []
        assert "could not read" in caplog.text

    def test_future_schema_is_never_rewritten(self, path, caplog):
        path.parent.mkdir(parents=True)
        original = json.dumps({"version": 2, "clubs": []})
        path.write_text(original, encoding="utf-8")
        store = ClubStore(path)

        with pytest.raises(ClubStoreError):
            store.add("bag-1", SPINNER)
        assert store.save() is False
        assert path.read_text(encoding="utf-8") == original
        assert "unsupported schema version" in caplog.text


class TestRollback:
    """A failed write leaves memory and disk as they were."""

    @pytest.mark.parametrize(
        ("module", "attribute"),
        [
            (club_store_module.json, "dump"),
            (club_store_module.os, "fsync"),
            (club_store_module.os, "replace"),
        ],
    )
    def test_add_rolls_back_at_each_persistence_stage(self, path, monkeypatch, module, attribute):
        store = ClubStore(path)
        store.save()
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(module, attribute, _fail)

        with pytest.raises(ClubStoreError, match="Could not save"):
            store.add("bag-1", SPINNER)

        assert store.list() == []
        assert path.read_text(encoding="utf-8") == original
        assert list(path.parent.glob("*.tmp")) == []

    def test_update_remove_and_remove_bag_roll_back(self, path, monkeypatch):
        store = ClubStore(path)
        added = store.add("bag-1", SPINNER)
        original = path.read_text(encoding="utf-8")
        monkeypatch.setattr(club_store_module.os, "replace", _fail)

        with pytest.raises(ClubStoreError):
            store.update(added.id, {**SPINNER, "model": "Changed"})
        assert store.remove(added.id) is False
        assert store.remove_bag("bag-1") == 0
        assert store.list() == [added]
        assert path.read_text(encoding="utf-8") == original

    def test_save_reports_failure(self, path, monkeypatch):
        store = ClubStore(path)
        monkeypatch.setattr(club_store_module.os, "replace", _fail)

        assert store.save() is False

    def test_save_holds_the_lock_through_the_atomic_replace(self, path, monkeypatch):
        store = ClubStore(path)
        replace_started = threading.Event()
        release_replace = threading.Event()
        mutation_finished = threading.Event()
        real_replace = club_store_module.os.replace
        calls = []
        added = []

        def blocking_replace(source, destination):
            calls.append(source)
            if len(calls) == 1:
                replace_started.set()
                assert release_replace.wait(timeout=2)
            real_replace(source, destination)

        monkeypatch.setattr(club_store_module.os, "replace", blocking_replace)
        save_thread = threading.Thread(target=store.save)
        save_thread.start()
        assert replace_started.wait(timeout=2)

        def add_club():
            added.append(store.add("bag-1", SPINNER))
            mutation_finished.set()

        mutation_thread = threading.Thread(target=add_club)
        mutation_thread.start()
        assert not mutation_finished.wait(timeout=0.1)
        release_replace.set()
        save_thread.join(timeout=2)
        mutation_thread.join(timeout=2)

        assert mutation_finished.is_set()
        assert ClubStore(path).list() == added
