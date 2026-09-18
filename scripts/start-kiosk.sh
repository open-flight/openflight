#!/usr/bin/env bash
# Start the OpenFlight server and a local Electron (or Chromium) kiosk.

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
HOST="localhost"
WEB_PORT="8080"
DRY_RUN=false
STARTUP_SPLASH=false
STARTUP_SPLASH_PORT=""
STARTUP_RUNTIME_DIR=""
STARTUP_STATUS_FILE=""
STARTUP_DISMISS_FILE=""
STARTUP_LOG_PATH="${OPENFLIGHT_STARTUP_LOG:-$HOME/openflight_sessions/terminal_logs/}"
SERVER_PID=""
SPLASH_PID=""
BROWSER_PID=""
BROWSER_PGID=""
BROWSER_LAUNCHED=false
SERVER_ARGS=()

log() { printf '[OpenFlight] %s\n' "$1"; }
warn() { printf '[OpenFlight] WARNING: %s\n' "$1"; }
error() { printf '[OpenFlight] ERROR: %s\n' "$1" >&2; }

# shellcheck source=kiosk-browser.sh
source "$SCRIPT_DIR/kiosk-browser.sh"

require_value() {
    if [ "$#" -lt 2 ]; then
        error "$1 requires a value"
        exit 2
    fi
}

resolve_buffer_split() {
    case "$1" in
        balanced) echo 16 ;;
        post-heavy) echo 12 ;;
        pre-heavy) echo 24 ;;
        *) echo "$1" ;;
    esac
}

# Only kiosk-specific aliases are interpreted here. Every server option is
# otherwise passed through unchanged, so the server remains the CLI owner.
while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --startup-splash)
            STARTUP_SPLASH=true
            shift
            ;;
        --startup-splash-port)
            require_value "$@"
            STARTUP_SPLASH_PORT="$2"
            shift 2
            ;;
        --port|-p|--web-port)
            require_value "$@"
            WEB_PORT="$2"
            shift 2
            ;;
        --radar-port|--ops-port)
            require_value "$@"
            SERVER_ARGS+=(--port "$2")
            shift 2
            ;;
        --buffer-split)
            require_value "$@"
            SERVER_ARGS+=(--sound-pre-trigger "$(resolve_buffer_split "$2")")
            shift 2
            ;;
        -m)
            SERVER_ARGS+=(--mock)
            shift
            ;;
        -d)
            SERVER_ARGS+=(--debug)
            shift
            ;;
        -l)
            require_value "$@"
            SERVER_ARGS+=(--session-location "$2")
            shift 2
            ;;
        *)
            SERVER_ARGS+=("$1")
            shift
            ;;
    esac
done

has_server_arg() {
    local wanted="$1"
    local argument
    for argument in "${SERVER_ARGS[@]}"; do
        [ "$argument" = "$wanted" ] && return 0
    done
    return 1
}

normalize_mock_swing_speed() {
    has_server_arg --mock && has_server_arg --swing-speed || return 0

    local normalized=()
    local argument
    for argument in "${SERVER_ARGS[@]}"; do
        case "$argument" in
            --mock|--swing-speed) ;;
            *) normalized+=("$argument") ;;
        esac
    done
    SERVER_ARGS=("${normalized[@]}" --mock-swing-speed)
}

normalize_mock_swing_speed

if [ "$STARTUP_SPLASH" = true ]; then
    STARTUP_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp}/openflight-startup-splash-${WEB_PORT}-$$"
    STARTUP_STATUS_FILE="$STARTUP_RUNTIME_DIR/status.json"
    STARTUP_DISMISS_FILE="$STARTUP_RUNTIME_DIR/dismissed"
    SERVER_ARGS+=(--startup-status-file "$STARTUP_STATUS_FILE")
fi

SERVER_CMD=(openflight-server --web-port "$WEB_PORT" "${SERVER_ARGS[@]}")

if [ "$DRY_RUN" = true ]; then
    printf '%q ' "${SERVER_CMD[@]}"
    printf '\n'
    exit 0
fi

shutdown_server() {
    [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null || return 0

    log "Requesting graceful hardware shutdown..."
    if curl -fsS --max-time 2 -X POST "http://$HOST:$WEB_PORT/api/shutdown" >/dev/null 2>&1; then
        for _ in {1..80}; do
            if ! kill -0 "$SERVER_PID" 2>/dev/null; then
                wait "$SERVER_PID" 2>/dev/null || true
                SERVER_PID=""
                return 0
            fi
            sleep 0.25
        done
        warn "Server did not complete graceful shutdown within 20 seconds"
    fi

    kill -TERM "$SERVER_PID" 2>/dev/null || true
    for _ in {1..8}; do
        if ! kill -0 "$SERVER_PID" 2>/dev/null; then
            wait "$SERVER_PID" 2>/dev/null || true
            SERVER_PID=""
            return 0
        fi
        sleep 0.25
    done
    warn "Forcing server exit; connected radar hardware may require reset"
    kill -KILL "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
    SERVER_PID=""
}

stop_startup_splash_server() {
    if [ -n "$SPLASH_PID" ] && kill -0 "$SPLASH_PID" 2>/dev/null; then
        kill "$SPLASH_PID" 2>/dev/null || true
        wait "$SPLASH_PID" 2>/dev/null || true
    fi
    SPLASH_PID=""

    if [ -n "$STARTUP_RUNTIME_DIR" ] && [ -d "$STARTUP_RUNTIME_DIR" ]; then
        rm -f "$STARTUP_RUNTIME_DIR/startup-splash.html"
        rm -f "$STARTUP_RUNTIME_DIR/openflightlogo.svg"
        rm -f "$STARTUP_RUNTIME_DIR/status.json"
        rm -f "$STARTUP_RUNTIME_DIR/dismissed"
        rmdir "$STARTUP_RUNTIME_DIR" 2>/dev/null || true
    fi
}

cleanup() {
    local exit_code="${1:-$?}"
    trap - EXIT SIGINT SIGTERM
    log "Shutting down..."
    shutdown_server
    stop_startup_splash_server
    stop_kiosk_browser
    exit "$exit_code"
}

acquire_instance_lock() {
    # One kiosk per web port. The default lives in /tmp rather than
    # XDG_RUNTIME_DIR because openflight.service and a desktop session have
    # different runtime dirs, and it was exactly that pair fighting over the
    # screen: a failing boot service ran cleanup every 5 s and killed the
    # desktop session's Electron each time.
    local lock_file="${OPENFLIGHT_KIOSK_LOCK_FILE:-/tmp/openflight-kiosk-${WEB_PORT}.lock}"

    if ! command -v flock >/dev/null 2>&1; then
        warn "flock unavailable; cannot guard against a second OpenFlight instance"
        return 0
    fi
    # Probe in a subshell: a failed redirection on `exec` would abort the script.
    if ! ( : >>"$lock_file" ) 2>/dev/null; then
        warn "Cannot open $lock_file; continuing without the single-instance guard"
        return 0
    fi
    exec {INSTANCE_LOCK_FD}>>"$lock_file"
    if ! flock -n "$INSTANCE_LOCK_FD"; then
        error "OpenFlight is already running (lock held on $lock_file)."
        error "  Stop the other instance first. If it is the boot service: sudo systemctl stop openflight"
        # Exit 3 is listed in openflight.service's RestartPreventExitStatus so
        # systemd does not retry every 5 s while someone else owns the kiosk.
        exit 3
    fi
}

ensure_uv_on_path() {
    # systemd starts the service with a minimal PATH that omits the user-local
    # install dirs astral's installer uses, so `uv` looked missing at boot.
    if command -v uv >/dev/null 2>&1; then
        return 0
    fi
    local candidate
    for candidate in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
        if [ -x "$candidate/uv" ]; then
            export PATH="$candidate:$PATH"
            return 0
        fi
    done
}

start_startup_splash() {
    if [ "$STARTUP_SPLASH" != true ]; then
        return 0
    fi

    local splash_port="${STARTUP_SPLASH_PORT:-$((WEB_PORT + 1))}"
    local splash_assets="$PROJECT_DIR/ui/public"
    local splash_url="http://127.0.0.1:${splash_port}/startup-splash.html?target=http%3A%2F%2F${HOST}%3A${WEB_PORT}"
    local splash_log="${XDG_RUNTIME_DIR:-/tmp}/openflight-startup-splash-${WEB_PORT}.log"
    local status_options=()

    if [ ! -f "$splash_assets/startup-splash.html" ]; then
        warn "Startup splash asset is missing; continuing with normal browser launch"
        return 0
    fi
    if curl -fsS --max-time 1 "http://127.0.0.1:${splash_port}/" >/dev/null 2>&1; then
        warn "Startup splash port $splash_port is already in use; continuing with normal browser launch"
        return 0
    fi

    mkdir -p "$STARTUP_RUNTIME_DIR"
    cp "$splash_assets/startup-splash.html" "$STARTUP_RUNTIME_DIR/startup-splash.html"
    cp "$splash_assets/openflightlogo.svg" "$STARTUP_RUNTIME_DIR/openflightlogo.svg"
    if has_server_arg --mock || has_server_arg --mock-swing-speed; then
        status_options+=(--mock)
    fi
    has_server_arg --camera-capture && status_options+=(--camera)
    has_server_arg --iwr6843 && status_options+=(--iwr6843)
    has_server_arg --inclinometer && status_options+=(--inclinometer)
    has_server_arg --kld7 && status_options+=(--kld7)
    has_server_arg --kld7-horizontal && status_options+=(--kld7-horizontal)
    has_server_arg --battery && status_options+=(--battery)
    has_server_arg --sim && status_options+=(--simulators)
    python3 "$PROJECT_DIR/src/openflight/startup_status.py" \
        initialize "$STARTUP_STATUS_FILE" "${status_options[@]}" || true

    log "Starting startup splash on port $splash_port..."
    python3 "$PROJECT_DIR/scripts/startup_splash_server.py" \
        --port "$splash_port" \
        --bind 127.0.0.1 \
        --directory "$STARTUP_RUNTIME_DIR" \
        --dismiss-file "$STARTUP_DISMISS_FILE" \
        >"$splash_log" 2>&1 &
    SPLASH_PID=$!

    for _ in {1..20}; do
        if curl -fsS --max-time 1 "$splash_url" >/dev/null 2>&1; then
            launch_kiosk_browser "$splash_url" || true
            return 0
        fi
        kill -0 "$SPLASH_PID" 2>/dev/null || break
        sleep 0.1
    done
    warn "Startup splash failed to start; continuing with normal browser launch"
    stop_startup_splash_server
}

show_startup_failure() {
    local component_id="$1"
    local message="$2"
    local recovery="$3"
    local exit_code="${4:-1}"
    local preserve_existing="${5:-false}"

    error "$message"
    error "$recovery"
    if [ -n "$STARTUP_STATUS_FILE" ] && [ -f "$STARTUP_STATUS_FILE" ]; then
        local status_args=(
            fail "$STARTUP_STATUS_FILE"
            --message "$message"
            --recovery "$recovery"
            --log-path "$STARTUP_LOG_PATH"
        )
        [ -n "$component_id" ] && status_args+=(--component "$component_id")
        [ "$preserve_existing" = true ] && status_args+=(--preserve-existing)
        python3 "$PROJECT_DIR/src/openflight/startup_status.py" "${status_args[@]}" || true
    fi

    shutdown_server
    if [ "$BROWSER_LAUNCHED" = true ] && [ -n "$SPLASH_PID" ] && kill -0 "$SPLASH_PID" 2>/dev/null; then
        log "Startup stopped. Use Return to desktop on the splash to close it."
        while [ ! -f "$STARTUP_DISMISS_FILE" ]; do
            kill -0 "$SPLASH_PID" 2>/dev/null || break
            sleep 0.25
        done
    fi
    cleanup "$exit_code"
}

configure_kld7_latency() {
    local setup_script="$PROJECT_DIR/scripts/setup/setup_kld7_latency.sh"
    if ! has_server_arg --kld7 && ! has_server_arg --kld7-horizontal; then
        return 0
    fi
    if [ "$(uname -s)" != Linux ] || [ ! -x "$setup_script" ]; then
        warn "Skipping K-LD7 FTDI latency setup"
        return 0
    fi
    if [ "$(id -u)" -eq 0 ]; then
        "$setup_script" --latency 1 || warn "K-LD7 FTDI latency setup failed"
    elif command -v sudo >/dev/null 2>&1; then
        sudo -n "$setup_script" --latency 1 || warn "K-LD7 FTDI latency setup failed"
    fi
}

start_alloy() {
    command -v systemctl >/dev/null 2>&1 || return 0
    systemctl is-enabled alloy >/dev/null 2>&1 || return 0
    sudo test -f /etc/alloy/credentials.env 2>/dev/null || return 0
    sudo grep -q 'LOKI_URL=https\?://' /etc/alloy/credentials.env 2>/dev/null || return 0
    systemctl is-active alloy >/dev/null 2>&1 || sudo systemctl start alloy 2>/dev/null || true
}

cd "$PROJECT_DIR"
acquire_instance_lock
# shellcheck source=ensure-kiosk-ui.sh
source "$SCRIPT_DIR/ensure-kiosk-ui.sh"
ensure_kiosk_ui

trap 'cleanup $?' EXIT
trap 'cleanup 130' SIGINT SIGTERM

start_startup_splash

ensure_uv_on_path
if ! command -v uv >/dev/null 2>&1; then
    show_startup_failure \
        "server" \
        "OpenFlight preparation failed" \
        "The uv command is unavailable (checked PATH, ~/.local/bin and ~/.cargo/bin). Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

UV_SYNC_ARGS=(--quiet)
if has_server_arg --camera-capture; then
    export UV_PYTHON=/usr/bin/python3
    if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c 'import picamera2' >/dev/null 2>&1; then
        uv venv --clear --system-site-packages --python /usr/bin/python3 || show_startup_failure \
            "server" \
            "OpenFlight preparation failed" \
            "Camera environment preparation failed. Check the terminal log, then relaunch OpenFlight."
    fi
    UV_SYNC_ARGS+=(--extra camera)
fi
uv sync "${UV_SYNC_ARGS[@]}" || show_startup_failure \
    "server" \
    "OpenFlight preparation failed" \
    "Dependency preparation failed. Check the terminal log, then relaunch OpenFlight."

configure_kld7_latency

start_alloy
log "Starting OpenFlight server on port $WEB_PORT"
UV_RUN_ARGS=()
if [ -n "${OPENFLIGHT_UV_RUN_ARGS:-}" ]; then
    read -r -a UV_RUN_ARGS <<< "$OPENFLIGHT_UV_RUN_ARGS"
fi
uv run "${UV_RUN_ARGS[@]}" "${SERVER_CMD[@]}" &
SERVER_PID=$!

log "Waiting for server to start..."
for _ in {1..30}; do
    curl -fsS "http://$HOST:$WEB_PORT" >/dev/null 2>&1 && break
    kill -0 "$SERVER_PID" 2>/dev/null || break
    sleep 0.5
done
if ! curl -fsS "http://$HOST:$WEB_PORT" >/dev/null 2>&1; then
    if kill -0 "$SERVER_PID" 2>/dev/null; then
        show_startup_failure \
            "server" \
            "OpenFlight server timed out" \
            "Wait a moment, then return to the desktop and relaunch OpenFlight." \
            1 \
            true
    fi
    wait "$SERVER_PID" 2>/dev/null || true
    SERVER_PID=""
    show_startup_failure \
        "server" \
        "OpenFlight server exited during startup" \
        "Check the connected radar hardware and terminal log, then relaunch OpenFlight." \
        1 \
        true
fi

if [ -n "$STARTUP_STATUS_FILE" ]; then
    uv run --no-sync python -m openflight.startup_status ready "$STARTUP_STATUS_FILE" || \
        warn "Could not mark startup splash ready; continuing to OpenFlight"
fi

if [ "$BROWSER_LAUNCHED" != true ]; then
    launch_kiosk_browser "http://$HOST:$WEB_PORT" || true
else
    log "Startup splash will continue to OpenFlight"
fi
log "OpenFlight is running. Press Ctrl+C to stop."
wait "$SERVER_PID"
