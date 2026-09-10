"""Tests for the kiosk entry script flag wiring."""

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

# The contract under test is the bash script's flag forwarding; without a
# POSIX shell there is nothing to exercise. With one present (e.g. Git Bash
# on Windows) --dry-run works everywhere.
pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="start-kiosk.sh contract tests need bash"
)


def _dry_run(*args: str, check: bool = True):
    # pathlib, not string-splitting on "/tests/": __file__ uses backslashes
    # on Windows, which made repo_root the test file itself (cwd=<file> ->
    # NotADirectoryError in CreateProcess).
    repo_root = Path(__file__).resolve().parents[1]
    return subprocess.run(
        ["bash", "scripts/start-kiosk.sh", *args, "--dry-run"],
        cwd=repo_root,
        check=check,
        capture_output=True,
        text=True,
    )


def test_ballistics_is_preferred_by_default():
    command_arguments = _dry_run().stdout.strip().split()

    assert "--ballistics" not in command_arguments
    assert "--no-ballistics" not in command_arguments


def test_no_ballistics_opt_out_is_forwarded():
    command_arguments = _dry_run("--no-ballistics").stdout.strip().split()

    assert "--no-ballistics" in command_arguments


def test_battery_provider_is_forwarded():
    command_arguments = _dry_run("--battery", "geekworm").stdout.strip().split()

    assert command_arguments[command_arguments.index("--battery") + 1] == "geekworm"


def test_removed_geekworm_power_flag_is_not_forwarded():
    command_arguments = _dry_run("--geekworm-power").stdout.strip().split()

    assert "--geekworm-power" not in command_arguments


def test_existing_ballistics_flag_remains_accepted():
    command_arguments = _dry_run("--ballistics").stdout.strip().split()

    assert "--no-ballistics" not in command_arguments


def test_kld7_requires_mount_tilt():
    """--kld7 without a mount tilt must fail loudly rather than assume a default."""
    result = _dry_run("--kld7", check=False)
    assert result.returncode != 0
    assert "mount tilt is unset" in (result.stdout + result.stderr)


def test_iwr6843_enables_ti_launch_pipeline_with_production_defaults():
    result = _dry_run("--iwr6843")
    command = result.stdout.strip()

    assert "--iwr6843" in command
    assert "--trigger sound" in command
    assert "--kld7" not in command


def test_iwr6843_overrides_are_forwarded():
    result = _dry_run(
        "--iwr6843",
        "--iwr6843-port",
        "/dev/ttyUSB9",
        "--iwr6843-trigger-pin",
        "22",
        "--iwr6843-tee-m",
        "1.6",
        "--iwr6843-net-m",
        "4.8",
        "--iwr6843-tilt-deg",
        "10.4",
        "--iwr6843-radar-height-m",
        "0.1524",
        "--iwr6843-ball-height-m",
        "0.065",
        "--iwr6843-tx-order",
        "normal",
        "--iwr6843-capture-timeout",
        "15",
    )
    command = result.stdout.strip()

    assert "--iwr6843-port /dev/ttyUSB9" in command
    assert "--iwr6843-trigger-pin 22" in command
    assert "--iwr6843-tee-m 1.6" in command
    assert "--iwr6843-net-m 4.8" in command
    assert "--iwr6843-tilt-deg 10.4" in command
    assert "--iwr6843-radar-height-m 0.1524" in command
    assert "--iwr6843-ball-height-m 0.065" in command
    assert "--iwr6843-tx-order normal" in command
    assert "--iwr6843-capture-timeout 15" in command


def test_camera_capture_flags_are_forwarded():
    result = _dry_run(
        "--camera-capture",
        "--camera-capture-width",
        "320",
        "--camera-capture-height",
        "240",
        "--camera-capture-fps",
        "300",
        "--camera-capture-pre-ms",
        "150",
        "--camera-capture-post-ms",
        "50",
        "--camera-capture-exposure-us",
        "1000",
        "--camera-capture-gain",
        "4",
        "--camera-capture-mount-height-m",
        "0.20955",
        "--camera-capture-lateral-offset-m",
        "0.0762",
        "--camera-capture-horizontal-offset-deg",
        "-0.45",
        "--camera-capture-roll-deg",
        "2.8",
        "--camera-capture-stream",
        "main-y",
        "--camera-capture-scaler-crop",
        "256,160,768,480",
        "--camera-capture-rotate-180",
    )
    command = result.stdout.strip()

    assert "--camera-capture" in command
    assert "--camera-capture-width 320" in command
    assert "--camera-capture-height 240" in command
    assert "--camera-capture-fps 300" in command
    assert "--camera-capture-pre-ms 150" in command
    assert "--camera-capture-post-ms 50" in command
    assert "--camera-capture-exposure-us 1000" in command
    assert "--camera-capture-gain 4" in command
    assert "--camera-capture-mount-height-m 0.20955" in command
    assert "--camera-capture-lateral-offset-m 0.0762" in command
    assert "--camera-capture-horizontal-offset-deg -0.45" in command
    assert "--camera-capture-roll-deg 2.8" in command
    assert "--camera-capture-stream main-y" in command
    assert "--camera-capture-scaler-crop 256,160,768,480" in command
    assert "--camera-capture-rotate-180" in command


def test_ops_radar_port_is_forwarded_separately_from_web_port():
    result = _dry_run("--radar-port", "/dev/serial0", "--port", "9090")
    command = result.stdout.strip()

    assert command.startswith("openflight-server --web-port 9090 --port /dev/serial0")


def test_ops_port_alias_is_forwarded_to_server_radar_port():
    result = _dry_run("--ops-port", "/dev/serial0")

    assert "--port /dev/serial0" in result.stdout


def test_plain_kld7_enables_two_ray_defaults():
    """--kld7 (with the required tilt) forwards the cleaned-up flag set."""
    result = _dry_run("--kld7", "--kld7-mount-tilt", "10")
    command = result.stdout.strip()

    assert "--kld7 --kld7-port /dev/kld7_vertical" in command
    # Boresight offset defaults to the calibrated 1.5, not the old 8.
    assert "--kld7-angle-offset 1.5" in command
    assert "--kld7-mount-tilt 10" in command
    # The estimator is a fixed cascade now — no selection flag.
    assert "--kld7-vertical-estimator" not in command
    # Gating is on by default; raw mode is opt-in.
    assert "--kld7-vertical-raw" not in command
    # Cosine correction rides on --kld7 server-side, not a kiosk flag.
    assert "--ball-speed-cosine-correction" not in command


def test_kld7_angle_offset_override_wins():
    """An explicit boresight offset overrides the 1.5 default."""
    result = _dry_run("--kld7", "--kld7-mount-tilt", "10", "--kld7-angle-offset", "3.5")
    command = result.stdout.strip()

    assert "--kld7-angle-offset 3.5" in command
    assert "--kld7-angle-offset 1.5" not in command


def test_swing_speed_flags_forwarded():
    """Swing speed training flags should reach the server command."""
    result = _dry_run(
        "--swing-speed",
        "--swing-speed-threshold",
        "35",
        "--swing-speed-max",
        "125",
        "--swing-speed-min-readings",
        "4",
        "--swing-speed-single-peak",
        "65",
        "--swing-speed-num-reports",
        "8",
        "--swing-speed-end-ms",
        "300",
        "--swing-speed-cooldown-ms",
        "900",
        "--swing-speed-rejected-cooldown-ms",
        "50",
    )
    command = result.stdout.strip()

    assert "--swing-speed" in command
    assert "--swing-speed-threshold 35" in command
    assert "--swing-speed-max 125" in command
    assert "--swing-speed-min-readings 4" in command
    assert "--swing-speed-single-peak 65" in command
    assert "--swing-speed-num-reports 8" in command
    assert "--swing-speed-end-ms 300" in command
    assert "--swing-speed-cooldown-ms 900" in command
    assert "--swing-speed-rejected-cooldown-ms 50" in command


def test_mock_swing_speed_flag_forwards_mock_mode():
    """Mock swing speed should use the server's no-hardware training mode."""
    result = _dry_run("--mock-swing-speed", "--swing-speed-threshold", "45")
    command = result.stdout.strip()

    assert "--mock-swing-speed" in command
    assert "--swing-speed-threshold 45" in command
    assert "--swing-speed " not in f"{command} "
    assert "--mock " not in f"{command} "


def test_mock_and_swing_speed_flags_collapse_to_mock_swing_speed():
    """The friendly --mock --swing-speed combo should avoid the server error path."""
    result = _dry_run("--mock", "--swing-speed")
    command = result.stdout.strip()

    assert "--mock-swing-speed" in command
    assert "--swing-speed " not in f"{command} "
    assert "--mock " not in f"{command} "


def test_kld7_vertical_raw_flag_forwarded():
    """--kld7-vertical-raw reaches the server as the renamed raw flag."""
    result = _dry_run("--kld7", "--kld7-mount-tilt", "10", "--kld7-vertical-raw")
    assert "--kld7-vertical-raw" in result.stdout


def test_trackman_test_dry_run_enables_raw_capture():
    """The field preset captures raw replay data and forwards the clean flags."""
    result = _dry_run("--trackman-test", "--kld7-mount-tilt", "10")
    command = result.stdout.strip()

    assert command.startswith("openflight-server --web-port 8080")
    assert "--session-location trackman" in command
    assert "--kld7-raw-logging" in command
    assert "--experimental-kld7-radc-tuning" not in command
    assert "--kld7 --kld7-port /dev/kld7_vertical --kld7-angle-offset 1.5" in command
    assert "--kld7-mount-tilt 10" in command
    assert "--kld7-horizontal" in command
    assert "--kld7-horizontal-port /dev/kld7_horizontal" in command
    assert "--no-camera" in command
    assert "--trigger sound" in command
    # No legacy estimator selection survives.
    assert "--kld7-vertical-estimator" not in command


def test_trackman_test_allows_explicit_session_location():
    """A bay/location override should survive the TrackMan preset defaults."""
    result = _dry_run("--trackman-test", "--kld7-mount-tilt", "10", "--session-location", "bay-2")

    assert "--session-location bay-2" in result.stdout
    assert "--session-location trackman " not in result.stdout


def test_startup_applies_kld7_latency_setup_before_server_start():
    """Kiosk startup should attempt the FTDI latency setup for K-LD7 sessions."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    script = (repo_root / "scripts/start-kiosk.sh").read_text(encoding="utf-8")

    setup_idx = script.index("\nconfigure_kld7_latency\n")
    server_start_idx = script.index("$SERVER_CMD &")

    assert "scripts/setup/setup_kld7_latency.sh" in script
    assert 'sudo -n "$setup_script" --latency 1' in script
    assert setup_idx < server_start_idx


def test_startup_splash_flag_only_adds_structured_status_to_server_cli():
    """The experimental splash should not alter any hardware configuration."""
    baseline = _dry_run()
    enabled = _dry_run("--startup-splash")

    enabled_arguments = enabled.stdout.strip().split()
    status_index = enabled_arguments.index("--startup-status-file")
    del enabled_arguments[status_index : status_index + 2]
    assert enabled_arguments == baseline.stdout.strip().split()
    assert "--startup-splash " not in enabled.stdout
    script = (Path(__file__).resolve().parents[1] / "scripts/start-kiosk.sh").read_text(
        encoding="utf-8"
    )
    assert "--startup-splash)" in script


def test_startup_splash_launches_before_environment_sync():
    """The feature only helps if Chromium starts before the slow preparation work."""
    repo_root = Path(__file__).resolve().parents[1]
    script = (repo_root / "scripts/start-kiosk.sh").read_text(encoding="utf-8")

    splash_idx = script.index("\nstart_startup_splash\n")
    sync_idx = script.index('\nif ! uv sync "${UV_SYNC_ARGS[@]}"; then\n')

    assert splash_idx < sync_idx
    assert 'if [ "$STARTUP_SPLASH" != true ]; then' in script
    assert 'launch_kiosk_browser "$KIOSK_URL"' in script


def test_startup_splash_includes_high_speed_camera_capture_component():
    """The current OV9281 capture path should appear when explicitly enabled."""
    repo_root = Path(__file__).resolve().parents[1]
    script = (repo_root / "scripts/start-kiosk.sh").read_text(encoding="utf-8")
    splash_function = script[
        script.index("start_startup_splash() {") : script.index("show_startup_failure() {")
    ]

    assert 'if [ "$CAMERA_CAPTURE" = true ] || [ "$NO_CAMERA" != true ]; then' in splash_function
    assert "status_options+=(--camera)" in splash_function


def test_startup_splash_asset_has_branding_and_redirect_contract():
    """The local page should communicate startup and hand off to the configured app."""
    repo_root = Path(__file__).resolve().parents[1]
    splash = (repo_root / "ui/public/startup-splash.html").read_text(encoding="utf-8")

    assert "openflightlogo.svg" in splash
    assert "Starting OpenFlight" in splash
    assert "Preparing software and connecting hardware" in splash
    assert "searchParams.get('target')" in splash
    assert "window.location.replace(targetUrl)" in splash
    assert "mode: 'no-cors'" in splash


def test_startup_branding_is_top_anchored_while_status_content_changes():
    repo_root = Path(__file__).resolve().parents[1]
    splash = (repo_root / "ui/public/startup-splash.html").read_text(encoding="utf-8")
    body_styles = splash[splash.index("      body {") : splash.index("      main {")]

    assert "align-items: flex-start" in body_styles
    assert "justify-content: center" in body_styles
    assert "place-items: center" not in body_styles


def test_startup_splash_renders_versioned_component_progress_safely():
    repo_root = Path(__file__).resolve().parents[1]
    splash = (repo_root / "ui/public/startup-splash.html").read_text(encoding="utf-8")

    assert "fetch('status.json'" in splash
    assert "status.version !== 1" in splash
    assert 'id="components"' in splash
    assert "component.state" in splash
    assert "textContent" in splash
    assert "innerHTML" not in splash
    assert "waiting" in splash
    assert "starting" in splash
    assert "ready" in splash
    assert "skipped" in splash


def test_startup_splash_holds_component_failure_until_dismissed():
    repo_root = Path(__file__).resolve().parents[1]
    splash = (repo_root / "ui/public/startup-splash.html").read_text(encoding="utf-8")

    assert 'id="error-details"' in splash
    assert 'id="recovery"' in splash
    assert 'id="log-path"' in splash
    assert 'id="dismiss"' in splash
    assert "startupFailed = true" in splash
    assert "document.body.classList.add('failed')" in splash
    assert "if (startupFailed) return" in splash
    assert "fetch('/dismiss', { method: 'POST' })" in splash


def test_unchanged_status_does_not_restart_component_animations():
    """Polling must not replace spinner DOM nodes unless component state changes."""
    repo_root = Path(__file__).resolve().parents[1]
    splash = (repo_root / "ui/public/startup-splash.html").read_text(encoding="utf-8")

    guard_index = splash.index("componentSignature === renderedComponentSignature")
    replacement_index = splash.index("components.replaceChildren")
    assert guard_index < replacement_index


def test_startup_splash_passes_status_file_to_server():
    """The server should publish progress into the splash server's runtime directory."""
    repo_root = Path(__file__).resolve().parents[1]
    script = (repo_root / "scripts/start-kiosk.sh").read_text(encoding="utf-8")

    assert 'STARTUP_STATUS_FILE=""' in script
    assert "--startup-status-file $STARTUP_STATUS_FILE" in script
    assert '"$STARTUP_STATUS_FILE"' in script
    assert 'python -m openflight.startup_status ready "$STARTUP_STATUS_FILE"' in script
    assert "python3 -m openflight.startup_status" in script
    assert 'initialize "$STARTUP_STATUS_FILE"' in script


def test_launcher_reports_distinct_failures_and_waits_for_dismissal():
    repo_root = Path(__file__).resolve().parents[1]
    script = (repo_root / "scripts/start-kiosk.sh").read_text(encoding="utf-8")
    ensure_ui = (repo_root / "scripts/ensure-kiosk-ui.sh").read_text(encoding="utf-8")

    assert "show_startup_failure()" in script
    assert '"OpenFlight preparation failed"' in script
    assert '"server"' in script
    assert 'while [ ! -f "$STARTUP_DISMISS_FILE" ]' in script
    assert 'uv sync "${UV_SYNC_ARGS[@]}"' in script
    assert "ensure_kiosk_ui" in script
    assert "npm run build" in ensure_ui


def test_start_kiosk_script_has_valid_shell_syntax():
    repo_root = Path(__file__).resolve().parents[1]
    subprocess.run(
        ["bash", "-n", "scripts/start-kiosk.sh"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["bash", "-n", "scripts/ensure-kiosk-ui.sh"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["bash", "-n", "scripts/kiosk-browser.sh"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )


def test_camera_capture_uses_system_python_for_sync_and_server_start():
    """Camera startup must keep system Python through sync and server launch."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    script = (repo_root / "scripts/start-kiosk.sh").read_text(encoding="utf-8")

    sync_setup_idx = script.index("UV_SYNC_ARGS=(--quiet)")
    camera_branch_idx = script.index('if [ "$CAMERA_CAPTURE" = true ]; then', sync_setup_idx)
    export_idx = script.index("export UV_PYTHON=/usr/bin/python3", camera_branch_idx)
    camera_sync_idx = script.index('if ! uv sync "${UV_SYNC_ARGS[@]}"; then', export_idx)
    server_start_idx = script.index("uv run ${OPENFLIGHT_UV_RUN_ARGS:-} $SERVER_CMD &")

    assert "uv venv --clear --system-site-packages --python /usr/bin/python3" in script
    assert "UV_SYNC_ARGS+=(--extra camera)" in script
    assert camera_branch_idx < export_idx < camera_sync_idx < server_start_idx


def test_shutdown_requests_server_cleanup_before_forcing_process_exit():
    """The wrapper must let the server drain IWR data and stop firmware first."""
    repo_root = Path(__file__).resolve().parents[1]
    script = (repo_root / "scripts/start-kiosk.sh").read_text(encoding="utf-8")
    cleanup = script[script.index("shutdown_server() {") : script.index("configure_kld7_latency()")]

    api_idx = cleanup.index("/api/shutdown")
    wait_idx = cleanup.index('kill -0 "$SERVER_PID"', api_idx)
    term_idx = cleanup.index('kill -TERM "$SERVER_PID"')

    assert api_idx < wait_idx < term_idx


def test_radc_tuning_values_are_ignored_without_experimental_gate():
    """Loose tuning flags should not alter production extraction by accident."""
    result = _dry_run(
        "--experimental-kld7-speed-tolerance", "6", "--experimental-kld7-spectrum-source", "sum12"
    )

    assert "--experimental-kld7-speed-tolerance 6" not in result.stdout
    assert "--experimental-kld7-spectrum-source sum12" not in result.stdout
    assert "Ignoring experimental K-LD7 RADC tuning values" in result.stdout


def test_radc_tuning_values_are_forwarded_with_experimental_gate():
    """When explicitly enabled, replay-discovered RADC knobs reach the server."""
    result = _dry_run(
        "--kld7-raw-logging",
        "--experimental-kld7-radc-tuning",
        "--experimental-kld7-speed-tolerance",
        "6",
        "--experimental-kld7-spectrum-source",
        "sum12",
        "--experimental-kld7-horizontal-angle-limit",
        "30",
    )
    command = result.stdout.strip()

    assert "--kld7-raw-logging" in command
    assert "--experimental-kld7-radc-tuning" in command
    assert "--experimental-kld7-speed-tolerance 6" in command
    assert "--experimental-kld7-spectrum-source sum12" in command
    assert "--experimental-kld7-horizontal-angle-limit 30" in command


def test_ops_uart_port_and_baud_are_forwarded():
    """The GPIO-UART wiring needs both the device and the target rate."""
    result = _dry_run("--radar-port", "/dev/ttyAMA0", "--ops-baud", "230400")
    command = result.stdout.strip()

    assert "--port /dev/ttyAMA0" in command
    assert "--ops-baud 230400" in command


def test_ops_baud_is_omitted_by_default():
    """No flag means the driver picks its own target; don't pin it here."""
    command = _dry_run().stdout.strip()
    assert "--ops-baud" not in command


def test_ops_port_alias_matches_radar_port():
    """--ops-port and --radar-port must behave identically (docs use both)."""
    via_ops = _dry_run("--ops-port", "/dev/ttyAMA0").stdout.strip()
    via_radar = _dry_run("--radar-port", "/dev/ttyAMA0").stdout.strip()
    assert via_ops == via_radar
    assert "--port /dev/ttyAMA0" in via_ops


def test_iwr6843_azimuth_offset_is_forwarded():
    """Club path is relative to the target line, so the aim offset must reach the server."""
    command = _dry_run("--iwr6843", "--iwr6843-azimuth-offset-deg", "1.5").stdout.strip()
    assert "--iwr6843-azimuth-offset-deg 1.5" in command


def test_iwr6843_azimuth_offset_omitted_by_default():
    command = _dry_run("--iwr6843").stdout.strip()
    assert "--iwr6843-azimuth-offset-deg" not in command


def test_iwr6843_horizontal_phase_reference_is_forwarded():
    command = _dry_run(
        "--iwr6843",
        "--iwr6843-horizontal-phase-reference-rad",
        "-0.5",
    ).stdout.strip()

    assert "--iwr6843-horizontal-phase-reference-rad -0.5" in command


def test_iwr6843_horizontal_phase_reference_is_omitted_by_default():
    command = _dry_run("--iwr6843").stdout.strip()
    assert "--iwr6843-horizontal-phase-reference-rad" not in command


def test_kiosk_shell_scripts_use_unix_newlines():
    repo_root = Path(__file__).resolve().parents[1]
    for relative in (
        "scripts/start-kiosk.sh",
        "scripts/ensure-kiosk-ui.sh",
        "scripts/kiosk-browser.sh",
        "scripts/require-node.sh",
    ):
        data = (repo_root / relative).read_bytes()
        assert b"\r" not in data, f"{relative} must use LF newlines so sourced path checks match on the Pi"


def test_ui_is_ensured_before_the_kiosk_browser_launches():
    script = _read_script()
    ensure_call = script.index("\nensure_kiosk_ui\n")
    splash_call = script.index("\nstart_startup_splash\n")
    assert ensure_call < splash_call


def _read_script() -> str:
    return (Path(__file__).resolve().parents[1] / "scripts/start-kiosk.sh").read_text(
        encoding="utf-8"
    )


def _read_kiosk_browser_helper() -> str:
    return (Path(__file__).resolve().parents[1] / "scripts/kiosk-browser.sh").read_text(
        encoding="utf-8"
    )


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
    script = _read_script()
    helper = _read_kiosk_browser_helper()
    cleanup_fn = script[script.index("cleanup() {") : script.index("configure_kld7_latency() {")]

    assert "pkill" not in script
    assert "pkill" not in helper
    assert 'source "$SCRIPT_DIR/kiosk-browser.sh"' in script
    assert "stop_kiosk_browser" in cleanup_fn


def test_ui_build_check_does_not_block_startup_on_missing_electron():
    """A built UI must still start when Electron cannot be installed."""
    script = _read_script()

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
) -> subprocess.CompletedProcess[str]:
    repo_scripts = Path(__file__).resolve().parents[1] / "scripts"
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
                '# shellcheck source=/dev/null',
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
        npm_exit=1,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "CONTINUED" in result.stdout
    assert "FAILURE" not in combined
    assert not (tmp_path / "npm-called").exists()


def test_existing_ui_continues_when_electron_npm_install_fails(tmp_path):
    """Offline or failed Electron install must not block a unit that already has ui/dist."""
    result = _run_ensure_kiosk_ui(
        tmp_path,
        node_version="22.12.0",
        has_dist=True,
        npm_exit=1,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "CONTINUED" in result.stdout
    assert "FAILURE" not in combined
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


# --- Browser process-tree ownership -----------------------------------------


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
    repo_root = Path(__file__).resolve().parents[1]
    project_dir = tmp_path / "project"
    fake_electron = _make_fake_electron(project_dir)
    ours_log = tmp_path / "ours.pids"
    theirs_log = tmp_path / "theirs.pids"

    # Another launcher's kiosk (same binary path) that must survive our cleanup.
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
            ["bash", str(harness), str(project_dir), str(repo_root / "scripts")],
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
    # Every branch launches through the same wrapper so none can regress to a bare `&`.
    assert launcher.count("_launch_kiosk_process ") == 3
    assert " &\n" not in launcher


# --- Single-instance guard --------------------------------------------------


def _hold_lock(path: Path):
    fcntl = pytest.importorskip("fcntl")
    handle = open(path, "w", encoding="utf-8")  # noqa: SIM115 - closed by the caller
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return handle


@pytest.mark.skipif(shutil.which("flock") is None, reason="instance guard needs flock (util-linux)")
def test_second_launcher_instance_exits_without_running_cleanup(tmp_path):
    """While another instance owns the kiosk, a new one must not build, launch, or kill anything."""
    repo_root = Path(__file__).resolve().parents[1]
    lock_path = tmp_path / "kiosk.lock"
    holder = _hold_lock(lock_path)
    try:
        result = subprocess.run(
            ["bash", "scripts/start-kiosk.sh", "--mock"],
            cwd=repo_root,
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
    repo_root = Path(__file__).resolve().parents[1]
    lock_path = tmp_path / "kiosk.lock"
    holder = _hold_lock(lock_path)
    try:
        result = subprocess.run(
            ["bash", "scripts/start-kiosk.sh", "--mock", "--dry-run"],
            cwd=repo_root,
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
    script = _read_script()

    dry_run_idx = script.index('if [ "$DRY_RUN" = true ]; then')
    lock_idx = script.index("\nacquire_instance_lock\n")
    ensure_idx = script.index("\nensure_kiosk_ui\n")
    splash_idx = script.index("\nstart_startup_splash\n")

    assert dry_run_idx < lock_idx < ensure_idx < splash_idx
    guard = script[script.index("acquire_instance_lock() {") : lock_idx]
    assert "flock -n" in guard
    assert "exit 3" in guard
    # The default path must not depend on XDG_RUNTIME_DIR: a system service and
    # a desktop session have different runtime dirs but must share one lock.
    assert "OPENFLIGHT_KIOSK_LOCK_FILE:-/tmp/openflight-kiosk-${PORT}.lock" in guard


# --- Running under systemd --------------------------------------------------


def test_uv_is_found_in_user_install_dirs_before_the_preparation_check():
    """systemd's PATH omits ~/.local/bin, which is where astral's installer puts uv."""
    script = _read_script()

    resolve_idx = script.index("\nensure_uv_on_path\n")
    check_idx = script.index("if ! command -v uv >/dev/null 2>&1; then")
    assert resolve_idx < check_idx

    resolver = script[script.index("ensure_uv_on_path() {") : resolve_idx]
    assert '"$HOME/.local/bin"' in resolver
    assert '"$HOME/.cargo/bin"' in resolver


def test_startup_failure_prints_the_recovery_hint_to_the_terminal():
    """journalctl only showed "preparation failed"; the reason lived in the splash JSON."""
    script = _read_script()
    failure_fn = script[
        script.index("show_startup_failure() {") : script.index("# Mount tilt has no safe default")
    ]

    assert re.search(r'error ".*\$recovery"', failure_fn), failure_fn
