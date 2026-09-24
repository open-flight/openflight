"""Contract tests for the thin kiosk wrapper."""

import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import time
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
        ["bash", "scripts/start-kiosk.sh", *args, "--dry-run"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return shlex.split(result.stdout)


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


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


def test_camera_shot_analysis_is_a_standalone_kiosk_camera_mode():
    assert _dry_run("--camera-shot-analysis") == [
        "openflight-server",
        "--web-port",
        "8080",
        "--camera-shot-analysis",
    ]

    script = _script()
    camera_helper = script[
        script.index("has_camera_arg() {") : script.index("normalize_mock_swing_speed() {")
    ]
    assert "--camera-capture" in camera_helper
    assert "--camera-shot-analysis" in camera_helper
    assert "if has_camera_arg; then" in script


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

    assert "has_camera_arg" in splash
    for option in ("--iwr6843", "--inclinometer", "--kld7"):
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


def test_missing_optional_alloy_service_does_not_abort_startup():
    script = _script()
    start = script.index("start_alloy() {")
    end = script.index("\n}\n\ncd ", start) + 2
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
    for relative in (
        "scripts/start-kiosk.sh",
        "scripts/ensure-kiosk-ui.sh",
        "scripts/kiosk-browser.sh",
    ):
        subprocess.run(
            ["bash", "-n", relative],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )


def test_launcher_reports_distinct_failures_and_waits_for_dismissal():
    script = _script()
    ensure_ui = (REPO_ROOT / "scripts/ensure-kiosk-ui.sh").read_text(encoding="utf-8")

    assert "show_startup_failure()" in script
    assert '"OpenFlight preparation failed"' in script
    assert '"server"' in script
    assert 'while [ ! -f "$STARTUP_DISMISS_FILE" ]' in script
    assert 'uv sync "${UV_SYNC_ARGS[@]}"' in script
    assert "ensure_kiosk_ui" in script
    assert "npm run build" in ensure_ui


def test_kiosk_shell_scripts_use_unix_newlines():
    for relative in (
        "scripts/start-kiosk.sh",
        "scripts/ensure-kiosk-ui.sh",
        "scripts/kiosk-browser.sh",
        "scripts/require-node.sh",
    ):
        data = (REPO_ROOT / relative).read_bytes()
        assert b"\r" not in data, f"{relative} must use LF newlines so sourced path checks match on the Pi"


def test_ui_is_ensured_before_the_kiosk_browser_launches():
    script = _script()
    ensure_call = script.index("\nensure_kiosk_ui\n")
    splash_call = script.index("\nstart_startup_splash\n")
    assert ensure_call < splash_call


def _read_kiosk_browser_helper() -> str:
    return (REPO_ROOT / "scripts/kiosk-browser.sh").read_text(encoding="utf-8")


def _launcher_function() -> str:
    helper = _read_kiosk_browser_helper()
    return helper[helper.index("launch_kiosk_browser() {") : helper.index("stop_kiosk_browser() {")]


def test_launch_kiosk_browser_prefers_the_electron_shell():
    """The pinned Electron runtime must be tried before any system browser."""
    launcher = _launcher_function()

    electron_idx = launcher.index('if [ -x "$electron_bin" ]; then')
    chromium_browser_idx = launcher.index("command -v chromium-browser")
    chromium_idx = launcher.index("command -v chromium &> /dev/null")

    assert electron_idx < chromium_browser_idx < chromium_idx
    assert 'local electron_bin="$PROJECT_DIR/ui/node_modules/.bin/electron"' in launcher
    assert '"$electron_bin" "$PROJECT_DIR/ui"' in launcher


def test_launch_kiosk_browser_still_falls_back_without_electron():
    """A Pi that hasn't run `npm install` yet must not lose its kiosk entirely."""
    launcher = _launcher_function()

    assert "chromium-browser --kiosk" in launcher
    assert "chromium --kiosk" in launcher
    assert "No Electron kiosk shell and no fallback browser found" in launcher


def test_cleanup_stops_only_the_browser_it_launched():
    """A path-matching pkill killed Electron windows owned by *other* launcher instances.

    A crash-looping systemd unit ran cleanup every 5 s and each pass killed the
    desktop session's kiosk (Chromium then died with "GPU process isn't usable").
    """
    script = _script()
    helper = _read_kiosk_browser_helper()
    cleanup_fn = script[script.index("cleanup() {") : script.index("configure_kld7_latency() {")]

    assert "pkill" not in script
    assert "pkill" not in helper
    assert 'source "$SCRIPT_DIR/kiosk-browser.sh"' in script
    assert "stop_kiosk_browser" in cleanup_fn


def test_ui_build_check_does_not_block_startup_on_missing_electron():
    """A built UI must still start when Electron cannot be installed."""
    script = _script()

    assert 'source "$SCRIPT_DIR/ensure-kiosk-ui.sh"' in script
    assert "ensure_kiosk_ui" in script
    assert 'if [ ! -d "ui/dist" ] || [ ! -x "ui/node_modules/.bin/electron" ]; then' not in script


def _bash_path(path: Path) -> str:
    resolved = str(path.resolve())
    if os.name != "nt":
        return resolved
    converted = subprocess.run(
        ["bash", "-lc", f"wslpath -u {shlex.quote(resolved.replace(chr(92), '/'))}"],
        capture_output=True,
        text=True,
        check=False,
    )
    mapped = converted.stdout.strip()
    if converted.returncode == 0 and mapped:
        return mapped
    posix = Path(resolved).as_posix()
    return f"/{posix[0].lower()}{posix[2:]}"


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_ensure_kiosk_ui(
    tmp_path: Path,
    *,
    node_version: str,
    has_dist: bool,
    npm_exit: int,
    has_node_modules: bool = False,
) -> subprocess.CompletedProcess[str]:
    repo_scripts = REPO_ROOT / "scripts"
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    for name in ("ensure-kiosk-ui.sh", "require-node.sh"):
        text = (repo_scripts / name).read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
        (scripts_dir / name).write_bytes(text.encode("utf-8"))
    project_dir = tmp_path / "project"
    ui_dir = project_dir / "ui"
    ui_dir.mkdir(parents=True)
    if has_dist:
        (ui_dir / "dist").mkdir()
        (ui_dir / "dist" / "index.html").write_text("<html></html>\n", encoding="utf-8")
    if has_node_modules:
        (ui_dir / "node_modules").mkdir()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npm_called = tmp_path / "npm-called"
    _write_executable(
        bin_dir / "node",
        f"#!/usr/bin/env bash\necho 'v{node_version}'\n",
    )
    _write_executable(
        bin_dir / "npm",
        "\n".join(
            [
                "#!/usr/bin/env bash",
                f"printf '%s\\n' \"$*\" >> {_bash_path(npm_called)}",
                f"exit {npm_exit}",
                "",
            ]
        ),
    )

    harness = tmp_path / "run-ensure.sh"
    _write_executable(
        harness,
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'PROJECT_DIR="$1"',
                'SCRIPT_DIR="$2"',
                'BIN_DIR="$3"',
                'chmod +x "$BIN_DIR"/* || true',
                'export PATH="$BIN_DIR:$PATH"',
                "log() { printf 'LOG %s\\n' \"$1\"; }",
                "warn() { printf 'WARN %s\\n' \"$1\"; }",
                "show_startup_failure() {",
                '  printf \'FAILURE component=%s message=%s\\n\' "$1" "$2"',
                "  exit 42",
                "}",
                "# shellcheck source=/dev/null",
                'source "$SCRIPT_DIR/ensure-kiosk-ui.sh"',
                "ensure_kiosk_ui",
                "printf 'CONTINUED\\n'",
                "",
            ]
        ),
    )

    return subprocess.run(
        [
            "bash",
            _bash_path(harness),
            _bash_path(project_dir),
            _bash_path(scripts_dir),
            _bash_path(bin_dir),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )


def test_existing_ui_continues_when_node_is_too_old_to_install_electron(tmp_path):
    """A Pi with a built UI and Node 20 must keep using Chromium instead of dying at startup."""
    result = _run_ensure_kiosk_ui(
        tmp_path,
        node_version="20.19.0",
        has_dist=True,
        has_node_modules=True,
        npm_exit=1,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "CONTINUED" in result.stdout
    assert "FAILURE" not in combined
    assert not (tmp_path / "npm-called").exists()


def test_existing_ui_bundle_skips_npm_when_dependencies_are_missing(tmp_path):
    """A built UI with no node_modules must start offline instead of calling npm."""
    result = _run_ensure_kiosk_ui(
        tmp_path,
        node_version="22.12.0",
        has_dist=True,
        npm_exit=1,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "CONTINUED" in result.stdout
    assert "existing UI bundle" in result.stdout
    assert "FAILURE" not in combined
    assert not (tmp_path / "npm-called").exists()


def test_existing_ui_continues_when_electron_npm_install_fails(tmp_path):
    """A failed Electron install must not block a unit that already has ui/dist."""
    result = _run_ensure_kiosk_ui(
        tmp_path,
        node_version="22.12.0",
        has_dist=True,
        has_node_modules=True,
        npm_exit=1,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "CONTINUED" in result.stdout
    assert "FAILURE" not in combined
    assert (tmp_path / "npm-called").exists()


def test_missing_ui_still_fails_when_npm_install_is_unavailable(tmp_path):
    result = _run_ensure_kiosk_ui(
        tmp_path,
        node_version="22.12.0",
        has_dist=False,
        npm_exit=1,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 42, combined
    assert "FAILURE" in combined
    assert "CONTINUED" not in result.stdout
    assert (tmp_path / "npm-called").exists()


def test_missing_ui_still_fails_when_node_is_too_old(tmp_path):
    result = _run_ensure_kiosk_ui(
        tmp_path,
        node_version="20.19.0",
        has_dist=False,
        npm_exit=0,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 42, combined
    assert "FAILURE" in combined
    assert "CONTINUED" not in result.stdout


def _is_alive(pid: int) -> bool:
    """True while the process exists and is not a zombie."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    status = Path(f"/proc/{pid}/status")
    if status.exists():
        for line in status.read_text(encoding="utf-8").splitlines():
            if line.startswith("State:"):
                return "Z" not in line.split()[1]
    return True


def _wait_until_dead(pids: list[int], timeout_s: float = 5.0) -> list[int]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        survivors = [pid for pid in pids if _is_alive(pid)]
        if not survivors:
            return []
        time.sleep(0.05)
    return [pid for pid in pids if _is_alive(pid)]


def _make_fake_electron(project_dir: Path) -> Path:
    """A stand-in Electron: a main process that forks a lingering child, like Chromium does."""
    bin_dir = project_dir / "ui" / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    fake = bin_dir / "electron"
    _write_executable(
        fake,
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "sleep 300 &",
                "child=$!",
                'printf \'%s %s\\n\' "$$" "$child" >> "$OPENFLIGHT_TEST_PID_LOG"',
                'wait "$child"',
                "",
            ]
        ),
    )
    return fake


def _read_pid_log(path: Path) -> list[int]:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if path.exists() and path.read_text(encoding="utf-8").strip():
            return [int(token) for token in path.read_text(encoding="utf-8").split()]
        time.sleep(0.05)
    raise AssertionError(f"fake electron never recorded its pids in {path}")


@pytest.mark.skipif(
    shutil.which("setsid") is None or os.name == "nt",
    reason="process-group ownership needs setsid (util-linux)",
)
def test_stop_kiosk_browser_kills_the_launched_tree_and_spares_other_instances(tmp_path):
    """Stopping must take the whole tree we started and nothing we did not start."""
    project_dir = tmp_path / "project"
    fake_electron = _make_fake_electron(project_dir)
    ours_log = tmp_path / "ours.pids"
    theirs_log = tmp_path / "theirs.pids"

    other = subprocess.Popen(
        ["bash", str(fake_electron)],
        env={**os.environ, "OPENFLIGHT_TEST_PID_LOG": str(theirs_log)},
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        their_pids = _read_pid_log(theirs_log)

        harness = tmp_path / "run-browser.sh"
        _write_executable(
            harness,
            "\n".join(
                [
                    "#!/usr/bin/env bash",
                    "set -u",
                    'PROJECT_DIR="$1"',
                    'SCRIPT_DIR="$2"',
                    'BROWSER_PID=""',
                    'BROWSER_PGID=""',
                    "BROWSER_LAUNCHED=false",
                    "log() { printf 'LOG %s\\n' \"$1\"; }",
                    "warn() { printf 'WARN %s\\n' \"$1\"; }",
                    "# shellcheck source=/dev/null",
                    'source "$SCRIPT_DIR/kiosk-browser.sh"',
                    'launch_kiosk_browser "http://127.0.0.1:1/"',
                    'printf \'LAUNCHED pid=%s pgid=%s\\n\' "$BROWSER_PID" "$BROWSER_PGID"',
                    "sleep 0.5",
                    "stop_kiosk_browser",
                    "printf 'STOPPED\\n'",
                    "",
                ]
            ),
        )
        result = subprocess.run(
            ["bash", str(harness), str(project_dir), str(REPO_ROOT / "scripts")],
            env={**os.environ, "OPENFLIGHT_TEST_PID_LOG": str(ours_log)},
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        combined = result.stdout + result.stderr
        assert result.returncode == 0, combined
        assert "STOPPED" in result.stdout, combined

        our_pids = _read_pid_log(ours_log)
        assert _wait_until_dead(our_pids) == [], f"launched tree survived cleanup: {combined}"
        assert other.poll() is None, "cleanup killed a kiosk it did not launch"
        assert all(_is_alive(pid) for pid in their_pids), (
            "cleanup killed another instance's children"
        )
    finally:
        try:
            os.killpg(other.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        other.wait(timeout=5)


def test_launch_kiosk_browser_puts_every_browser_in_its_own_process_group():
    """Electron and the Chromium fallbacks must be stoppable as one group, not by pattern."""
    launcher = _launcher_function()
    helper = _read_kiosk_browser_helper()

    assert "setsid" in helper
    assert "BROWSER_PGID" in helper
    assert launcher.count("_launch_kiosk_process ") == 3
    assert " &\n" not in launcher


def _hold_lock(path: Path):
    fcntl = pytest.importorskip("fcntl")
    handle = open(path, "w", encoding="utf-8")  # noqa: SIM115 - closed by the caller
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return handle


@pytest.mark.skipif(shutil.which("flock") is None, reason="instance guard needs flock (util-linux)")
def test_second_launcher_instance_exits_without_running_cleanup(tmp_path):
    """While another instance owns the kiosk, a new one must not build, launch, or kill anything."""
    lock_path = tmp_path / "kiosk.lock"
    holder = _hold_lock(lock_path)
    try:
        result = subprocess.run(
            ["bash", "scripts/start-kiosk.sh", "--mock"],
            cwd=REPO_ROOT,
            env={**os.environ, "OPENFLIGHT_KIOSK_LOCK_FILE": str(lock_path)},
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    finally:
        holder.close()

    combined = result.stdout + result.stderr
    assert result.returncode == 3, combined
    assert "already running" in combined
    assert str(lock_path) in combined
    assert "Shutting down" not in combined
    assert "Building" not in combined


@pytest.mark.skipif(shutil.which("flock") is None, reason="instance guard needs flock (util-linux)")
def test_dry_run_ignores_the_instance_lock(tmp_path):
    lock_path = tmp_path / "kiosk.lock"
    holder = _hold_lock(lock_path)
    try:
        result = subprocess.run(
            ["bash", "scripts/start-kiosk.sh", "--mock", "--dry-run"],
            cwd=REPO_ROOT,
            env={**os.environ, "OPENFLIGHT_KIOSK_LOCK_FILE": str(lock_path)},
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    finally:
        holder.close()

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().startswith("openflight-server")


def test_instance_lock_is_taken_after_dry_run_and_before_any_side_effect():
    script = _script()

    dry_run_idx = script.index('if [ "$DRY_RUN" = true ]; then')
    lock_idx = script.index("\nacquire_instance_lock\n")
    ensure_idx = script.index("\nensure_kiosk_ui\n")
    splash_idx = script.index("\nstart_startup_splash\n")

    assert dry_run_idx < lock_idx < ensure_idx < splash_idx
    guard = script[script.index("acquire_instance_lock() {") : lock_idx]
    assert "flock -n" in guard
    assert "exit 3" in guard
    assert "OPENFLIGHT_KIOSK_LOCK_FILE:-/tmp/openflight-kiosk-${WEB_PORT}.lock" in guard


def test_uv_is_found_in_user_install_dirs_before_the_preparation_check():
    """systemd's PATH omits ~/.local/bin, which is where astral's installer puts uv."""
    script = _script()

    resolve_idx = script.index("\nensure_uv_on_path\n")
    check_idx = script.index("if ! command -v uv >/dev/null 2>&1; then")
    assert resolve_idx < check_idx

    resolver = script[script.index("ensure_uv_on_path() {") : resolve_idx]
    assert '"$HOME/.local/bin"' in resolver
    assert '"$HOME/.cargo/bin"' in resolver


def test_startup_failure_prints_the_recovery_hint_to_the_terminal():
    """journalctl only showed "preparation failed"; the reason lived in the splash JSON."""
    script = _script()
    failure_fn = script[
        script.index("show_startup_failure() {") : script.index("configure_kld7_latency() {")
    ]

    assert re.search(r'error ".*\$recovery"', failure_fn), failure_fn
