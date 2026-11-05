"""Tests for the persistent profile roster."""

import json
from pathlib import Path

import pytest

from openflight.profiles import (
    DEFAULT_PROFILE_NAME,
    DEFAULT_PROFILES_PATH,
    MAX_PROFILES,
    PROFILES_PATH_ENV,
    Profile,
    ProfileStore,
    resolve_profiles_path,
)


@pytest.fixture(name="store_path")
def fixture_store_path(tmp_path):
    return tmp_path / "config" / "profiles.json"


class TestSeeding:
    """A store always yields a usable roster."""

    def test_missing_file_seeds_one_default_profile(self, store_path):
        store = ProfileStore(store_path)

        profiles = store.list()
        assert len(profiles) == 1
        assert profiles[0].name == DEFAULT_PROFILE_NAME
        assert store.get_active().id == profiles[0].id

    def test_seeded_store_is_written_to_disk(self, store_path):
        ProfileStore(store_path)

        data = json.loads(store_path.read_text(encoding="utf-8"))
        assert len(data["profiles"]) == 1
        assert data["active_profile_id"] == data["profiles"][0]["id"]

    def test_corrupt_file_falls_back_to_seeded_default(self, store_path):
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text("{not json at all", encoding="utf-8")

        store = ProfileStore(store_path)

        assert [profile.name for profile in store.list()] == [DEFAULT_PROFILE_NAME]

    def test_file_with_no_valid_profiles_falls_back_to_seeded_default(self, store_path):
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(
            json.dumps({"profiles": [{"nope": 1}, "banana"], "active_profile_id": "x"}),
            encoding="utf-8",
        )

        store = ProfileStore(store_path)

        assert [profile.name for profile in store.list()] == [DEFAULT_PROFILE_NAME]

    def test_active_id_pointing_at_missing_profile_falls_back_to_first(self, store_path):
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(
            json.dumps(
                {
                    "profiles": [
                        {"id": "aaa", "name": "Home", "created_at": "2026-01-01T00:00:00Z"},
                        {"id": "bbb", "name": "Range", "created_at": "2026-01-01T00:00:00Z"},
                    ],
                    "active_profile_id": "ghost",
                }
            ),
            encoding="utf-8",
        )

        store = ProfileStore(store_path)

        assert store.get_active().id == "aaa"


class TestAdd:
    """Adding a profile."""

    def test_add_appends_and_makes_active(self, store_path):
        store = ProfileStore(store_path)

        added = store.add("Home Range")

        assert added is not None
        assert [profile.name for profile in store.list()] == [DEFAULT_PROFILE_NAME, "Home Range"]
        assert store.get_active().id == added.id

    def test_add_generates_a_unique_id(self, store_path):
        store = ProfileStore(store_path)

        first = store.add("Range")
        second = store.add("Range")

        assert first.id != second.id

    def test_add_allows_duplicate_names(self, store_path):
        store = ProfileStore(store_path)

        store.add("Range")
        store.add("Range")

        assert [profile.name for profile in store.list()].count("Range") == 2

    def test_add_trims_and_caps_name_at_40_characters(self, store_path):
        store = ProfileStore(store_path)

        added = store.add("  " + "x" * 60 + "  ")

        assert added.name == "x" * 40

    def test_add_rejects_blank_name(self, store_path):
        store = ProfileStore(store_path)

        assert store.add("   ") is None
        assert len(store.list()) == 1

    def test_add_rejects_beyond_the_roster_cap(self, store_path):
        store = ProfileStore(store_path)
        for index in range(MAX_PROFILES - 1):
            assert store.add(f"Profile {index + 2}") is not None

        assert store.add("One too many") is None
        assert len(store.list()) == MAX_PROFILES

    def test_add_persists_across_reload(self, store_path):
        store = ProfileStore(store_path)
        added = store.add("Home Range")

        reloaded = ProfileStore(store_path)

        assert [profile.name for profile in reloaded.list()] == [DEFAULT_PROFILE_NAME, "Home Range"]
        assert reloaded.get_active().id == added.id


class TestRename:
    """Renaming never changes identity."""

    def test_rename_changes_name_but_not_id(self, store_path):
        store = ProfileStore(store_path)
        added = store.add("Rnage")

        assert store.rename(added.id, "Range") is True

        renamed = next(profile for profile in store.list() if profile.id == added.id)
        assert renamed.name == "Range"

    def test_rename_trims_and_caps_name(self, store_path):
        store = ProfileStore(store_path)
        added = store.add("Range")

        store.rename(added.id, "  " + "y" * 60)

        assert store.list()[-1].name == "y" * 40

    def test_rename_rejects_blank_name(self, store_path):
        store = ProfileStore(store_path)
        added = store.add("Range")

        assert store.rename(added.id, "  ") is False
        assert store.list()[-1].name == "Range"

    def test_rename_rejects_unknown_id(self, store_path):
        store = ProfileStore(store_path)

        assert store.rename("ghost", "Range") is False

    def test_rename_persists_across_reload(self, store_path):
        store = ProfileStore(store_path)
        added = store.add("Rnage")
        store.rename(added.id, "Range")

        assert ProfileStore(store_path).list()[-1].name == "Range"


class TestRemove:
    """Removal is refused when it would break an invariant."""

    def test_remove_deletes_an_inactive_profile(self, store_path):
        store = ProfileStore(store_path)
        doomed = store.add("Doomed")
        keeper = store.add("Keeper")

        assert store.remove(doomed.id) is True

        assert [profile.id for profile in store.list()] == [store.list()[0].id, keeper.id]

    def test_remove_rejects_the_active_profile(self, store_path):
        store = ProfileStore(store_path)
        active = store.add("Active")

        assert store.remove(active.id) is False
        assert store.get_active().id == active.id
        assert len(store.list()) == 2

    def test_remove_rejects_the_last_profile(self, store_path):
        store = ProfileStore(store_path)
        only = store.list()[0]

        assert store.remove(only.id) is False
        assert store.list() == [only]

    def test_remove_rejects_unknown_id(self, store_path):
        store = ProfileStore(store_path)

        assert store.remove("ghost") is False
        assert len(store.list()) == 1

    def test_remove_persists_across_reload(self, store_path):
        store = ProfileStore(store_path)
        doomed = store.add("Doomed")
        store.add("Keeper")
        store.remove(doomed.id)

        assert [profile.name for profile in ProfileStore(store_path).list()] == [
            DEFAULT_PROFILE_NAME,
            "Keeper",
        ]


class TestSetActive:
    """Active selection always points at a live profile."""

    def test_set_active_switches_selection(self, store_path):
        store = ProfileStore(store_path)
        first = store.list()[0]
        store.add("Second")

        assert store.set_active(first.id) is True
        assert store.get_active().id == first.id

    def test_set_active_rejects_unknown_id(self, store_path):
        store = ProfileStore(store_path)
        before = store.get_active().id

        assert store.set_active("ghost") is False
        assert store.get_active().id == before

    def test_set_active_persists_across_reload(self, store_path):
        store = ProfileStore(store_path)
        first = store.list()[0]
        store.add("Second")
        store.set_active(first.id)

        assert ProfileStore(store_path).get_active().id == first.id


class TestSettings:
    """The open settings dict is the extension point for later features."""

    def test_settings_default_to_empty_dict(self, store_path):
        store = ProfileStore(store_path)

        assert store.add("Range").settings == {}

    def test_settings_round_trip_unchanged(self, store_path):
        store = ProfileStore(store_path)
        added = store.add("Range")
        payload = {"altitude_m": 120, "nested": {"a": [1, 2, 3]}, "flag": True}
        added.settings.update(payload)
        store.save()

        reloaded = next(
            profile for profile in ProfileStore(store_path).list() if profile.id == added.id
        )
        assert reloaded.settings == payload

    def test_rename_preserves_settings(self, store_path):
        store = ProfileStore(store_path)
        added = store.add("Rnage")
        added.settings["altitude_m"] = 120
        store.save()

        store.rename(added.id, "Range")

        assert ProfileStore(store_path).list()[-1].settings == {"altitude_m": 120}

    def test_to_dict_returns_a_copy_of_settings(self):
        profile = Profile(
            id="abc",
            name="Range",
            created_at="2026-01-01T00:00:00Z",
            settings={"altitude_m": 120},
        )

        payload = profile.to_dict()
        payload["settings"]["altitude_m"] = 999
        payload["settings"]["extra"] = True

        assert profile.settings == {"altitude_m": 120}

    def test_snapshot_settings_cannot_mutate_store_state(self, store_path):
        store = ProfileStore(store_path)
        added = store.add("Range")
        added.settings["altitude_m"] = 120
        store.save()

        snapshot = store.snapshot()
        snapshot["profiles"][-1]["settings"]["altitude_m"] = 999

        assert store.list()[-1].settings == {"altitude_m": 120}


class TestSnapshot:
    """snapshot() is the socket payload."""

    def test_snapshot_shape(self, store_path):
        store = ProfileStore(store_path)
        added = store.add("Range")

        snapshot = store.snapshot()

        assert snapshot["active_profile_id"] == added.id
        assert [entry["name"] for entry in snapshot["profiles"]] == [
            DEFAULT_PROFILE_NAME,
            "Range",
        ]
        assert set(snapshot["profiles"][0]) == {"id", "name", "created_at", "settings"}


class TestAtomicWrite:
    """A crash mid-write must not truncate the roster."""

    def test_failed_write_leaves_previous_file_intact(self, store_path, monkeypatch):
        store = ProfileStore(store_path)
        store.add("Keeper")
        before = store_path.read_text(encoding="utf-8")

        def boom(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("openflight.profiles.os.replace", boom)
        store.add("Never persisted")

        assert store_path.read_text(encoding="utf-8") == before

    def test_no_temp_files_left_behind(self, store_path):
        store = ProfileStore(store_path)
        store.add("Range")

        assert [path.name for path in store_path.parent.iterdir()] == [store_path.name]

    def test_save_builds_payload_while_holding_the_lock(self, store_path):
        store = ProfileStore(store_path)

        def observing_payload():
            assert store._lock.locked()
            return {
                "profiles": [profile.to_dict() for profile in store.list()],
                "active_profile_id": store.get_active().id,
            }

        store._payload = observing_payload
        store.save()

    def test_snapshot_builds_payload_while_holding_the_lock(self, store_path):
        store = ProfileStore(store_path)

        def observing_payload():
            assert store._lock.locked()
            return {
                "profiles": [profile.to_dict() for profile in store.list()],
                "active_profile_id": store.get_active().id,
            }

        store._payload = observing_payload
        store.snapshot()


class TestResolvePath:
    """Roster location is constructor, then env, then the user default."""

    def test_explicit_path_wins_over_env(self, store_path, tmp_path, monkeypatch):
        monkeypatch.setenv(PROFILES_PATH_ENV, str(tmp_path / "from-env.json"))

        store = ProfileStore(store_path)
        store.add("Range")

        assert store_path.exists()
        assert not (tmp_path / "from-env.json").exists()

    def test_env_is_used_when_no_path_is_given(self, tmp_path, monkeypatch):
        isolated = tmp_path / "worker-0" / "profiles.json"
        monkeypatch.setenv(PROFILES_PATH_ENV, str(isolated))

        store = ProfileStore()
        store.add("Alex")

        assert isolated.exists()
        names = [
            entry["name"] for entry in json.loads(isolated.read_text(encoding="utf-8"))["profiles"]
        ]
        assert "Alex" in names

    def test_blank_env_falls_back_to_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv(PROFILES_PATH_ENV, "   ")
        monkeypatch.setattr("openflight.profiles.DEFAULT_PROFILES_PATH", tmp_path / "default.json")

        assert resolve_profiles_path() == tmp_path / "default.json"

    def test_default_constant_is_the_user_config_location(self):
        assert DEFAULT_PROFILES_PATH == Path.home() / ".config" / "openflight" / "profiles.json"


class TestEnvDoesNotTouchUserConfig:
    """E2E-style construction (no path argument) must not rewrite the user roster."""

    def test_store_with_env_never_creates_or_rewrites_default_path(self, tmp_path, monkeypatch):
        isolated = tmp_path / "e2e-worker" / "profiles.json"
        user_roster = tmp_path / "home" / ".config" / "openflight" / "profiles.json"
        user_roster.parent.mkdir(parents=True)
        user_roster.write_text("do-not-touch", encoding="utf-8")
        monkeypatch.setenv(PROFILES_PATH_ENV, str(isolated))
        monkeypatch.setattr("openflight.profiles.DEFAULT_PROFILES_PATH", user_roster)

        store = ProfileStore()
        store.add("Alex")
        store.add("Range")
        store.remove(store.list()[0].id)

        assert user_roster.read_text(encoding="utf-8") == "do-not-touch"
        assert isolated.exists()
        assert isolated != user_roster
