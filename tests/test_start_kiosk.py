"""Contract tests for the thin kiosk wrapper."""

import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="start-kiosk.sh contract tests need bash",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts/start-kiosk.sh"


def _dry_run(*args: str) -> list[str]:
    result = subprocess.run(
        ["bash", str(SCRIPT), *args, "--dry-run"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return shlex.split(result.stdout)


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _run_ui_preparation(
    tmp_path: Path,
    *,
    has_bundle: bool,
    has_node_modules: bool = False,
    npm_exit_code: int = 1,
) -> tuple[subprocess.CompletedProcess, Path]:
    ui_dir = tmp_path / "ui"
    ui_dir.mkdir()
    if has_node_modules:
        (ui_dir / "node_modules").mkdir()
    if has_bundle:
        dist_dir = ui_dir / "dist"
        dist_dir.mkdir()
        (dist_dir / "index.html").write_text("existing bundle", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    npm_invocations = tmp_path / "npm-invocations"
    npm = fake_bin / "npm"
    npm.write_text(
        '#!/bin/bash\nprintf "%s\\n" "$*" >> "$NPM_INVOCATIONS"\nexit "$NPM_EXIT_CODE"\n',
        encoding="utf-8",
    )
    npm.chmod(0o755)

    script = _script()
    start = script.index("if [ ! -d ui/node_modules ]")
    end = script.index("\n\nstart_alloy", start)
    preparation = script[start:end]
    harness = f"""\
set -eo pipefail
warn() {{ printf 'warning: %s\\n' "$1"; }}
show_startup_failure() {{ printf 'hard-failure\\n'; exit 23; }}
{preparation}
printf 'prepared\\n'
"""
    result = subprocess.run(
        ["bash", "-c", harness],
        cwd=tmp_path,
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "NPM_EXIT_CODE": str(npm_exit_code),
            "NPM_INVOCATIONS": str(npm_invocations),
        },
        check=False,
        capture_output=True,
        text=True,
    )
    return result, npm_invocations


def test_default_command_is_minimal():
    assert _dry_run() == ["openflight-server", "--web-port", "8080"]


def test_server_arguments_pass_through_unchanged():
    arguments = [
        "--iwr6843",
        "--iwr6843-port",
        "/dev/tty USB9",
        "--camera-capture",
        "--camera-capture-fps",
        "300",
        "--no-ballistics",
    ]

    assert _dry_run(*arguments) == ["openflight-server", "--web-port", "8080", *arguments]


def test_hardware_trigger_speed_alias_passes_through_to_server():
    """The thin wrapper forwards the documented threshold alias unchanged."""
    arguments = ["--trigger", "hardware", "--trigger-speed", "10"]

    assert _dry_run(*arguments) == ["openflight-server", "--web-port", "8080", *arguments]


@pytest.mark.parametrize("alias", ["--radar-port", "--ops-port"])
def test_radar_alias_is_distinct_from_web_port(alias):
    assert _dry_run(alias, "/dev/serial0", "--port", "9090") == [
        "openflight-server",
        "--web-port",
        "9090",
        "--port",
        "/dev/serial0",
    ]


@pytest.mark.parametrize(
    ("preset", "segments"),
    [("balanced", "16"), ("post-heavy", "12"), ("pre-heavy", "24"), ("20", "20")],
)
def test_buffer_split_alias(preset, segments):
    assert _dry_run("--buffer-split", preset) == [
        "openflight-server",
        "--web-port",
        "8080",
        "--sound-pre-trigger",
        segments,
    ]


def test_short_kiosk_aliases_are_translated():
    assert _dry_run("-m", "-d", "-l", "garage") == [
        "openflight-server",
        "--web-port",
        "8080",
        "--mock",
        "--debug",
        "--session-location",
        "garage",
    ]


@pytest.mark.parametrize("arguments", [("--mock", "--swing-speed"), ("--swing-speed", "--mock")])
def test_mock_swing_speed_alias_is_preserved(arguments):
    assert _dry_run(*arguments) == [
        "openflight-server",
        "--web-port",
        "8080",
        "--mock-swing-speed",
    ]


def test_startup_splash_only_adds_structured_status_to_server_cli():
    baseline = _dry_run()
    enabled = _dry_run("--startup-splash")

    status_index = enabled.index("--startup-status-file")
    del enabled[status_index : status_index + 2]
    assert enabled == baseline


def test_startup_splash_launches_before_environment_sync():
    script = _script()

    assert script.index("\nstart_startup_splash\n") < script.index("\nUV_SYNC_ARGS=(--quiet)\n")
    assert 'launch_kiosk_browser "$splash_url"' in script


def test_pre_sync_startup_status_uses_standalone_script():
    script = _script()
    pre_sync = script[: script.index("\nUV_SYNC_ARGS=(--quiet)\n")]

    assert 'python3 "$PROJECT_DIR/src/openflight/startup_status.py"' in pre_sync
    assert "python3 -m openflight.startup_status" not in pre_sync


def test_startup_splash_reports_enabled_hardware_components():
    splash = _script()[
        _script().index("start_startup_splash() {") : _script().index("show_startup_failure() {")
    ]

    for option in ("--camera-capture", "--iwr6843", "--inclinometer", "--kld7"):
        assert f"has_server_arg {option}" in splash


def test_startup_splash_status_and_failure_contract():
    script = _script()

    assert 'initialize "$STARTUP_STATUS_FILE"' in script
    assert '--startup-status-file "$STARTUP_STATUS_FILE"' in script
    assert 'startup_status ready "$STARTUP_STATUS_FILE"' in script
    assert 'while [ ! -f "$STARTUP_DISMISS_FILE" ]' in script
    assert '"OpenFlight preparation failed"' in script


def test_startup_splash_asset_has_branding_redirect_and_failure_ui():
    splash = (REPO_ROOT / "ui/public/startup-splash.html").read_text(encoding="utf-8")

    assert "openflightlogo.svg" in splash
    assert "Starting OpenFlight" in splash
    assert "window.location.replace(targetUrl)" in splash
    assert "fetch('status.json'" in splash
    assert "status.version !== 1" in splash
    assert "textContent" in splash
    assert "innerHTML" not in splash
    assert 'id="dismiss"' in splash
    assert "if (startupFailed) return" in splash


def test_hardware_shutdown_precedes_force_kill():
    script = _script()
    shutdown = script[
        script.index("shutdown_server() {") : script.index("stop_startup_splash_server() {")
    ]

    assert shutdown.index("/api/shutdown") < shutdown.index("kill -TERM")
    assert shutdown.index("kill -TERM") < shutdown.index("kill -KILL")


def test_camera_capture_uses_system_python_for_sync_and_server_start():
    script = _script()
    camera_branch = script[
        script.index("UV_SYNC_ARGS=(--quiet)") : script.index("\nconfigure_kld7_latency\n")
    ]

    assert "export UV_PYTHON=/usr/bin/python3" in camera_branch
    assert "uv venv --clear --system-site-packages --python /usr/bin/python3" in camera_branch
    assert "UV_SYNC_ARGS+=(--extra camera)" in camera_branch
    assert 'uv sync "${UV_SYNC_ARGS[@]}"' in camera_branch
    assert 'uv run "${UV_RUN_ARGS[@]}" "${SERVER_CMD[@]}" &' in script


def test_startup_applies_kld7_latency_setup_before_server_start():
    script = _script()

    assert script.index("\nconfigure_kld7_latency\n") < script.index('uv run "${UV_RUN_ARGS[@]}"')
    assert "scripts/setup/setup_kld7_latency.sh" in script
    assert 'sudo -n "$setup_script" --latency 1' in script


def test_ui_bundle_is_rebuilt_when_dependencies_are_available(tmp_path):
    result, npm_invocations = _run_ui_preparation(
        tmp_path,
        has_bundle=True,
        has_node_modules=True,
        npm_exit_code=0,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert npm_invocations.read_text(encoding="utf-8").splitlines() == ["--prefix ui run build"]


def test_existing_ui_bundle_allows_offline_startup_without_npm(tmp_path):
    result, npm_invocations = _run_ui_preparation(tmp_path, has_bundle=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "prepared" in result.stdout
    assert "existing UI bundle" in result.stdout
    assert not npm_invocations.exists()


def test_missing_ui_bundle_still_fails_when_npm_install_is_unavailable(tmp_path):
    result, npm_invocations = _run_ui_preparation(tmp_path, has_bundle=False)

    assert result.returncode == 23
    assert "hard-failure" in result.stdout
    assert npm_invocations.read_text(encoding="utf-8").splitlines() == ["--prefix ui install"]


def test_missing_optional_alloy_service_does_not_abort_startup():
    script = _script()
    start = script.index("start_alloy() {")
    end = script.index("\n}\n\ntrap ", start) + 2
    function = script[start:end]

    result = subprocess.run(
        [
            "bash",
            "-c",
            f"set -e\n{function}\nPATH=/definitely-missing\nstart_alloy\nprintf continued",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == "continued"


def test_start_kiosk_script_has_valid_shell_syntax():
    subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
