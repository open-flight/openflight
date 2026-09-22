"""E2E backends must not read or write the developer's real profile roster."""

from pathlib import Path

from openflight import server as server_module
from openflight.profiles import PROFILES_PATH_ENV, ProfileStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAYWRIGHT_CONFIG = REPO_ROOT / "ui" / "playwright.config.ts"
ISOLATE_HELPER = REPO_ROOT / "ui" / "tests" / "e2e" / "isolateProfilesPath.ts"


def test_playwright_does_not_collect_vitest_files():
    """Playwright defaults to *(test|spec).ts, which would execute Vitest describe()."""
    config = PLAYWRIGHT_CONFIG.read_text(encoding="utf-8")
    e2e_dir = REPO_ROOT / "ui" / "tests" / "e2e"

    assert "testMatch:" in config
    assert "**/*.spec.ts" in config
    assert list(e2e_dir.glob("*.test.ts")) == []


def test_playwright_config_points_the_backend_at_a_unique_temp_path():
    config = PLAYWRIGHT_CONFIG.read_text(encoding="utf-8")
    helper = ISOLATE_HELPER.read_text(encoding="utf-8")

    assert "uniqueE2eProfilesPath" in config
    assert "PROFILES_PATH_ENV" in config
    assert "env: backendEnv()" in config
    assert "OPENFLIGHT_PROFILES_PATH" in helper
    assert "mkdtempSync" in helper
    assert "openflight-e2e-w" in helper
    assert "~/.config/openflight/profiles.json" not in config


def test_get_profile_store_with_env_never_touches_the_user_roster(tmp_path, monkeypatch):
    isolated = tmp_path / "worker-7" / "profiles.json"
    user_roster = tmp_path / "home" / ".config" / "openflight" / "profiles.json"
    user_roster.parent.mkdir(parents=True)
    user_roster.write_text("developer-roster", encoding="utf-8")
    monkeypatch.setenv(PROFILES_PATH_ENV, str(isolated))
    monkeypatch.setattr("openflight.profiles.DEFAULT_PROFILES_PATH", user_roster)
    monkeypatch.setattr(server_module, "profile_store", None)

    store = server_module.get_profile_store()
    store.add("Alex")

    assert isinstance(store, ProfileStore)
    assert isolated.exists()
    assert user_roster.read_text(encoding="utf-8") == "developer-roster"
    assert store._path == isolated  # pylint: disable=protected-access
