#!/bin/bash
#
# OpenFlight Kiosk Startup Script
# Starts the radar server and launches the Electron kiosk shell
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PORT=8080
HOST="localhost"
RADAR_PORT=""
OPS_BAUD=""
MOCK_MODE=false
MOCK_SWING_SPEED=false
RADAR_LOG=false
DEBUG_MODE=false
NO_CAMERA=true  # Camera disabled by default (K-LD7 radar handles angle)
CAMERA_CAPTURE=false
CAMERA_CAPTURE_WIDTH=""
CAMERA_CAPTURE_HEIGHT=""
CAMERA_CAPTURE_FPS=""
CAMERA_CAPTURE_PRE_MS=""
CAMERA_CAPTURE_POST_MS=""
CAMERA_CAPTURE_EXPOSURE_US=""
CAMERA_CAPTURE_GAIN=""
CAMERA_CAPTURE_MOUNT_HEIGHT_M=""
CAMERA_CAPTURE_LATERAL_OFFSET_M=""
CAMERA_CAPTURE_HORIZONTAL_OFFSET_DEG=""
CAMERA_CAPTURE_ROLL_DEG=""
CAMERA_CAPTURE_STREAM=""
CAMERA_CAPTURE_SCALER_CROP=""
CAMERA_CAPTURE_ROTATE_180=false
CAMERA_CAPTURE_MIRROR_HORIZONTAL=false
TRACKMAN_TEST=false
SESSION_LOCATION=""
DRY_RUN=false
STARTUP_SPLASH=false
STARTUP_SPLASH_PORT=""
STARTUP_RUNTIME_DIR=""
STARTUP_STATUS_FILE=""
STARTUP_DISMISS_FILE=""
STARTUP_LOG_PATH="${OPENFLIGHT_STARTUP_LOG:-$HOME/openflight_sessions/terminal_logs/}"
SPLASH_PID=""
BROWSER_PID=""
BROWSER_PGID=""
BROWSER_LAUNCHED=false
SERVER_PID=""
# Rolling buffer mode is the only mode (streaming mode removed)
TRIGGER="sound"  # Default: hardware sound trigger (SEN-14262 → HOST_INT)
SOUND_PRE_TRIGGER=""
BUFFER_SPLIT=""
IWR6843=false
IWR6843_PORT=""
IWR6843_CONFIG=""
IWR6843_CAL=""
IWR6843_TRIGGER_PIN=""
IWR6843_TEE_M=""
IWR6843_NET_M=""
IWR6843_TILT_DEG=""
IWR6843_RADAR_HEIGHT_M=""
IWR6843_BALL_HEIGHT_M=""
IWR6843_TX_ORDER=""
IWR6843_CAPTURE_TIMEOUT=""
IWR6843_OUTPUT_DIR=""
IWR6843_AZIMUTH_OFFSET=""
IWR6843_HORIZONTAL_PHASE_REFERENCE_RAD=""
INCLINOMETER=false
INCLINOMETER_ZERO_OFFSET=""
KLD7=false
KLD7_PORT=""
KLD7_ANGLE_OFFSET=""
KLD7_HORIZONTAL=false
KLD7_HORIZONTAL_PORT=""
KLD7_HORIZONTAL_OFFSET=""
KLD7_MOUNT_TILT=""
KLD7_BALL_DISTANCE=""
NET_DISTANCE=""
KLD7_VERTICAL_RAW=false
EXPERIMENTAL_KLD7_RAW_RADC_LOGGING=false
EXPERIMENTAL_KLD7_RADC_TUNING=false
EXPERIMENTAL_KLD7_SPEED_TOLERANCE=""
EXPERIMENTAL_KLD7_CENTROID_FLOOR=""
EXPERIMENTAL_KLD7_OPS_BIN_TOL=""
EXPERIMENTAL_KLD7_OPS_BIN_PENALTY=""
EXPERIMENTAL_KLD7_OPS_ANCHORED_MIN_SNR=""
EXPERIMENTAL_KLD7_VERTICAL_IMPACT_ENERGY=""
EXPERIMENTAL_KLD7_HORIZONTAL_IMPACT_ENERGY=""
EXPERIMENTAL_KLD7_HORIZONTAL_RETRY_IMPACT_ENERGY=""
EXPERIMENTAL_KLD7_HORIZONTAL_ANGLE_LIMIT=""
BALLISTICS=true
SIM=false
CALCULATED_SPIN=false
BATTERY_PROVIDER=""
SWING_SPEED=false
SWING_SPEED_THRESHOLD=""
SWING_SPEED_MIN_READINGS=""
SWING_SPEED_SINGLE_PEAK=""
SWING_SPEED_NUM_REPORTS=""
SWING_SPEED_END_MS=""
SWING_SPEED_COOLDOWN_MS=""
SWING_SPEED_REJECTED_COOLDOWN_MS=""

# Buffer split presets (pre/post trigger segments out of 32 total)
# At 20ksps: each segment = 6.4ms, total buffer = 204.8ms
# At 30ksps: each segment = 4.27ms, total buffer = 136.5ms
#
#   balanced  = S#16 — 50/50 split (recommended starting point)
#   post-heavy = S#12 — 37/63 split (more ball flight, less backswing)
#   pre-heavy  = S#24 — 75/25 split (more backswing, some ball flight)
resolve_buffer_split() {
    case "$1" in
        balanced)   echo 16 ;;
        post-heavy) echo 12 ;;
        pre-heavy)  echo 24 ;;
        *)          echo "$1" ;;  # raw number passthrough
    esac
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --mock|-m)
            MOCK_MODE=true
            shift
            ;;
        --mock-swing-speed)
            MOCK_SWING_SPEED=true
            SWING_SPEED=true
            shift
            ;;
        --radar-log)
            RADAR_LOG=true
            shift
            ;;
        --battery)
            BATTERY_PROVIDER="$2"
            shift 2
            ;;
        --debug|-d)
            DEBUG_MODE=true
            shift
            ;;
        --trackman-test)
            TRACKMAN_TEST=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --startup-splash)
            STARTUP_SPLASH=true
            shift
            ;;
        --startup-splash-port)
            STARTUP_SPLASH_PORT="$2"
            shift 2
            ;;
        --session-location|-l)
            SESSION_LOCATION="$2"
            shift 2
            ;;
        --no-camera)
            NO_CAMERA=true
            shift
            ;;
        --camera-capture)
            CAMERA_CAPTURE=true
            shift
            ;;
        --camera-capture-width)
            CAMERA_CAPTURE_WIDTH="$2"
            shift 2
            ;;
        --camera-capture-height)
            CAMERA_CAPTURE_HEIGHT="$2"
            shift 2
            ;;
        --camera-capture-fps)
            CAMERA_CAPTURE_FPS="$2"
            shift 2
            ;;
        --camera-capture-pre-ms)
            CAMERA_CAPTURE_PRE_MS="$2"
            shift 2
            ;;
        --camera-capture-post-ms)
            CAMERA_CAPTURE_POST_MS="$2"
            shift 2
            ;;
        --camera-capture-exposure-us)
            CAMERA_CAPTURE_EXPOSURE_US="$2"
            shift 2
            ;;
        --camera-capture-gain)
            CAMERA_CAPTURE_GAIN="$2"
            shift 2
            ;;
        --camera-capture-mount-height-m)
            CAMERA_CAPTURE_MOUNT_HEIGHT_M="$2"
            shift 2
            ;;
        --camera-capture-lateral-offset-m)
            CAMERA_CAPTURE_LATERAL_OFFSET_M="$2"
            shift 2
            ;;
        --camera-capture-horizontal-offset-deg)
            CAMERA_CAPTURE_HORIZONTAL_OFFSET_DEG="$2"
            shift 2
            ;;
        --camera-capture-roll-deg)
            CAMERA_CAPTURE_ROLL_DEG="$2"
            shift 2
            ;;
        --camera-capture-stream)
            CAMERA_CAPTURE_STREAM="$2"
            shift 2
            ;;
        --camera-capture-scaler-crop)
            CAMERA_CAPTURE_SCALER_CROP="$2"
            shift 2
            ;;
        --camera-capture-rotate-180)
            CAMERA_CAPTURE_ROTATE_180=true
            shift
            ;;
        --camera-capture-mirror-horizontal)
            CAMERA_CAPTURE_MIRROR_HORIZONTAL=true
            shift
            ;;
        --mode)
            echo "Warning: --mode is deprecated, rolling-buffer is the only mode"
            shift 2
            ;;
        --trigger)
            TRIGGER="$2"
            shift 2
            ;;
        --sound-pre-trigger)
            SOUND_PRE_TRIGGER="$2"
            shift 2
            ;;
        --buffer-split)
            BUFFER_SPLIT="$2"
            shift 2
            ;;
        --sample-rate)
            SAMPLE_RATE="$2"
            shift 2
            ;;
        --iwr6843)
            IWR6843=true
            shift
            ;;
        --iwr6843-port)
            IWR6843_PORT="$2"
            shift 2
            ;;
        --iwr6843-config)
            IWR6843_CONFIG="$2"
            shift 2
            ;;
        --iwr6843-cal)
            IWR6843_CAL="$2"
            shift 2
            ;;
        --iwr6843-trigger-pin)
            IWR6843_TRIGGER_PIN="$2"
            shift 2
            ;;
        --iwr6843-tee-m)
            IWR6843_TEE_M="$2"
            shift 2
            ;;
        --iwr6843-net-m)
            IWR6843_NET_M="$2"
            shift 2
            ;;
        --iwr6843-tilt-deg)
            IWR6843_TILT_DEG="$2"
            shift 2
            ;;
        --iwr6843-radar-height-m)
            IWR6843_RADAR_HEIGHT_M="$2"
            shift 2
            ;;
        --iwr6843-ball-height-m)
            IWR6843_BALL_HEIGHT_M="$2"
            shift 2
            ;;
        --iwr6843-tx-order)
            IWR6843_TX_ORDER="$2"
            shift 2
            ;;
        --iwr6843-capture-timeout)
            IWR6843_CAPTURE_TIMEOUT="$2"
            shift 2
            ;;
        --iwr6843-output-dir)
            IWR6843_OUTPUT_DIR="$2"
            shift 2
            ;;
        --iwr6843-azimuth-offset-deg)
            IWR6843_AZIMUTH_OFFSET="$2"
            shift 2
            ;;
        --iwr6843-horizontal-phase-reference-rad)
            IWR6843_HORIZONTAL_PHASE_REFERENCE_RAD="$2"
            shift 2
            ;;
        --inclinometer)
            INCLINOMETER=true
            shift
            ;;
        --inclinometer-zero-offset)
            INCLINOMETER_ZERO_OFFSET="$2"
            shift 2
            ;;
        --kld7)
            KLD7=true
            shift
            ;;
        --kld7-port)
            KLD7_PORT="$2"
            shift 2
            ;;
        --kld7-angle-offset)
            KLD7_ANGLE_OFFSET="$2"
            shift 2
            ;;
        --kld7-mount-tilt)
            KLD7_MOUNT_TILT="$2"
            shift 2
            ;;
        --kld7-ball-distance)
            KLD7_BALL_DISTANCE="$2"
            shift 2
            ;;
        --net-distance)
            NET_DISTANCE="$2"
            shift 2
            ;;
        --kld7-vertical-raw)
            KLD7_VERTICAL_RAW=true
            shift
            ;;
        --kld7-horizontal)
            KLD7_HORIZONTAL=true
            shift
            ;;
        --kld7-horizontal-port)
            KLD7_HORIZONTAL_PORT="$2"
            shift 2
            ;;
        --kld7-horizontal-offset)
            KLD7_HORIZONTAL_OFFSET="$2"
            shift 2
            ;;
        --kld7-raw-logging)
            EXPERIMENTAL_KLD7_RAW_RADC_LOGGING=true
            shift
            ;;
        --experimental-kld7-radc-tuning)
            EXPERIMENTAL_KLD7_RADC_TUNING=true
            shift
            ;;
        --experimental-kld7-speed-tolerance)
            EXPERIMENTAL_KLD7_SPEED_TOLERANCE="$2"
            shift 2
            ;;
        --experimental-kld7-centroid-floor)
            EXPERIMENTAL_KLD7_CENTROID_FLOOR="$2"
            shift 2
            ;;
        --experimental-kld7-spectrum-source)
            EXPERIMENTAL_KLD7_SPECTRUM_SOURCE="$2"
            shift 2
            ;;
        --experimental-kld7-ops-bin-tol)
            EXPERIMENTAL_KLD7_OPS_BIN_TOL="$2"
            shift 2
            ;;
        --experimental-kld7-ops-bin-penalty)
            EXPERIMENTAL_KLD7_OPS_BIN_PENALTY="$2"
            shift 2
            ;;
        --experimental-kld7-ops-anchored-min-snr)
            EXPERIMENTAL_KLD7_OPS_ANCHORED_MIN_SNR="$2"
            shift 2
            ;;
        --experimental-kld7-vertical-impact-energy)
            EXPERIMENTAL_KLD7_VERTICAL_IMPACT_ENERGY="$2"
            shift 2
            ;;
        --experimental-kld7-horizontal-impact-energy)
            EXPERIMENTAL_KLD7_HORIZONTAL_IMPACT_ENERGY="$2"
            shift 2
            ;;
        --experimental-kld7-horizontal-retry-impact-energy)
            EXPERIMENTAL_KLD7_HORIZONTAL_RETRY_IMPACT_ENERGY="$2"
            shift 2
            ;;
        --experimental-kld7-horizontal-angle-limit)
            EXPERIMENTAL_KLD7_HORIZONTAL_ANGLE_LIMIT="$2"
            shift 2
            ;;
        --ballistics)
            BALLISTICS=true
            shift
            ;;
        --no-ballistics)
            BALLISTICS=false
            shift
            ;;
        --sim)
            SIM=true
            shift
            ;;
        --calculated-spin)
            CALCULATED_SPIN=true
            shift
            ;;
        --swing-speed)
            SWING_SPEED=true
            shift
            ;;
        --swing-speed-threshold)
            SWING_SPEED_THRESHOLD="$2"
            shift 2
            ;;
        --swing-speed-max)
            SWING_SPEED_MAX="$2"
            shift 2
            ;;
        --swing-speed-min-readings)
            SWING_SPEED_MIN_READINGS="$2"
            shift 2
            ;;
        --swing-speed-single-peak)
            SWING_SPEED_SINGLE_PEAK="$2"
            shift 2
            ;;
        --swing-speed-num-reports)
            SWING_SPEED_NUM_REPORTS="$2"
            shift 2
            ;;
        --swing-speed-end-ms)
            SWING_SPEED_END_MS="$2"
            shift 2
            ;;
        --swing-speed-cooldown-ms)
            SWING_SPEED_COOLDOWN_MS="$2"
            shift 2
            ;;
        --swing-speed-rejected-cooldown-ms)
            SWING_SPEED_REJECTED_COOLDOWN_MS="$2"
            shift 2
            ;;
        --radar-port|--ops-port)
            RADAR_PORT="$2"
            shift 2
            ;;
        --ops-baud)
            OPS_BAUD="$2"
            shift 2
            ;;
        --port|-p)
            PORT="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

# Resolve buffer split preset to a number (overrides --sound-pre-trigger)
if [ -n "$BUFFER_SPLIT" ]; then
    SOUND_PRE_TRIGGER=$(resolve_buffer_split "$BUFFER_SPLIT")
fi

if [ "$TRACKMAN_TEST" = true ]; then
    KLD7=true
    KLD7_HORIZONTAL=true
    EXPERIMENTAL_KLD7_RAW_RADC_LOGGING=true
    SESSION_LOCATION="${SESSION_LOCATION:-trackman}"
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log() {
    echo -e "${GREEN}[OpenFlight]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[OpenFlight]${NC} $1"
}

error() {
    echo -e "${RED}[OpenFlight]${NC} $1"
}

# shellcheck source=kiosk-browser.sh
source "$SCRIPT_DIR/kiosk-browser.sh"

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

start_startup_splash() {
    if [ "$STARTUP_SPLASH" != true ]; then
        return 0
    fi

    local splash_port="${STARTUP_SPLASH_PORT:-$((PORT + 1))}"
    local splash_assets="$PROJECT_DIR/ui/public"
    local splash_path="$splash_assets/startup-splash.html"
    local target_query="http%3A%2F%2F${HOST}%3A${PORT}"
    local splash_url="http://127.0.0.1:${splash_port}/startup-splash.html?target=${target_query}"
    local splash_log="${XDG_RUNTIME_DIR:-/tmp}/openflight-startup-splash-${PORT}.log"

    if [ ! -f "$splash_path" ]; then
        warn "Startup splash asset is missing; continuing with normal browser launch"
        return 0
    fi

    if curl -fsS --max-time 1 "http://127.0.0.1:${splash_port}/" >/dev/null 2>&1; then
        warn "Startup splash port $splash_port is already in use; continuing with normal browser launch"
        return 0
    fi

    mkdir -p "$STARTUP_RUNTIME_DIR"
    cp "$splash_path" "$STARTUP_RUNTIME_DIR/startup-splash.html"
    cp "$splash_assets/openflightlogo.svg" "$STARTUP_RUNTIME_DIR/openflightlogo.svg"
    local status_options=()
    if [ "$MOCK_MODE" = true ] || [ "$MOCK_SWING_SPEED" = true ]; then
        status_options+=(--mock)
    fi
    if [ "$CAMERA_CAPTURE" = true ] || [ "$NO_CAMERA" != true ]; then
        status_options+=(--camera)
    fi
    [ "$IWR6843" = true ] && status_options+=(--iwr6843)
    [ "$INCLINOMETER" = true ] && status_options+=(--inclinometer)
    [ "$KLD7" = true ] && status_options+=(--kld7)
    [ "$KLD7_HORIZONTAL" = true ] && status_options+=(--kld7-horizontal)
    [ -n "$BATTERY_PROVIDER" ] && status_options+=(--battery)
    [ "$SIM" = true ] && status_options+=(--simulators)
    if ! PYTHONPATH="$PROJECT_DIR/src" python3 -m openflight.startup_status \
        initialize "$STARTUP_STATUS_FILE" "${status_options[@]}"; then
        printf '%s\n' '{"version":1,"overall":"starting","message":"Preparing OpenFlight server","components":[{"id":"server","label":"OpenFlight server","state":"starting"},{"id":"ops","label":"OPS radar","state":"waiting"}]}' > "$STARTUP_STATUS_FILE"
    fi

    log "Starting startup splash on port $splash_port..."
    python3 "$PROJECT_DIR/scripts/startup_splash_server.py" \
        --port "$splash_port" --bind 127.0.0.1 \
        --directory "$STARTUP_RUNTIME_DIR" --dismiss-file "$STARTUP_DISMISS_FILE" \
        >"$splash_log" 2>&1 &
    SPLASH_PID=$!

    for _ in {1..20}; do
        if curl -fsS --max-time 1 "$splash_url" >/dev/null 2>&1; then
            launch_kiosk_browser "$splash_url" || true
            return 0
        fi
        if ! kill -0 "$SPLASH_PID" 2>/dev/null; then
            break
        fi
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
    error "  $recovery"
    if [ -n "$STARTUP_STATUS_FILE" ] && [ -f "$STARTUP_STATUS_FILE" ]; then
        local status_args=(
            fail "$STARTUP_STATUS_FILE"
            --message "$message"
            --recovery "$recovery"
            --log-path "$STARTUP_LOG_PATH"
        )
        [ -n "$component_id" ] && status_args+=(--component "$component_id")
        [ "$preserve_existing" = true ] && status_args+=(--preserve-existing)
        PYTHONPATH="$PROJECT_DIR/src" python3 -m openflight.startup_status "${status_args[@]}" || true
    fi

    shutdown_server

    if [ "$BROWSER_LAUNCHED" = true ] && [ -n "$SPLASH_PID" ] && kill -0 "$SPLASH_PID" 2>/dev/null; then
        log "Startup stopped. Use Return to desktop on the splash to close it."
        while [ ! -f "$STARTUP_DISMISS_FILE" ]; do
            if ! kill -0 "$SPLASH_PID" 2>/dev/null; then
                break
            fi
            sleep 0.25
        done
    fi
    cleanup "$exit_code"
}

# Mount tilt has no safe default (a wrong value silently biases the launch
# angle), so it must be provided whenever the K-LD7 radars are enabled. Set
# KLD7_MOUNT_TILT or pass --kld7-mount-tilt; measure it with a phone
# inclinometer against the radar face.
if [ "$KLD7" = true ] && [ -z "$KLD7_MOUNT_TILT" ]; then
    error "K-LD7 enabled but mount tilt is unset."
    error "  Measure the radar tilt (phone inclinometer) and pass --kld7-mount-tilt <deg>"
    error "  or set KLD7_MOUNT_TILT in the environment."
    exit 1
fi

shutdown_server() {
    if [ -z "$SERVER_PID" ] || ! kill -0 "$SERVER_PID" 2>/dev/null; then
        return 0
    fi

    log "Requesting graceful hardware shutdown..."
    if curl -fsS --max-time 2 -X POST "http://$HOST:$PORT/api/shutdown" >/dev/null 2>&1; then
        # IWR dumps normally take 5-8 seconds. Allow the server to preserve an
        # active dump, stop capture firmware, and close hardware in order.
        for _ in {1..80}; do
            if ! kill -0 "$SERVER_PID" 2>/dev/null; then
                wait "$SERVER_PID" 2>/dev/null || true
                return 0
            fi
            sleep 0.25
        done
        warn "Server did not complete graceful shutdown within 20 seconds"
    else
        warn "Server shutdown API unavailable; falling back to process signal"
    fi

    if kill -0 "$SERVER_PID" 2>/dev/null; then
        kill -TERM "$SERVER_PID" 2>/dev/null || true
        for _ in {1..8}; do
            if ! kill -0 "$SERVER_PID" 2>/dev/null; then
                wait "$SERVER_PID" 2>/dev/null || true
                return 0
            fi
            sleep 0.25
        done
    fi

    if kill -0 "$SERVER_PID" 2>/dev/null; then
        warn "Forcing server exit; connected radar hardware may require reset"
        kill -KILL "$SERVER_PID" 2>/dev/null || true
    fi
    wait "$SERVER_PID" 2>/dev/null || true
}

cleanup() {
    local exit_code="${1:-0}"
    # Prevent a second signal from re-entering cleanup while hardware drains.
    trap - SIGINT SIGTERM
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
    local lock_file="${OPENFLIGHT_KIOSK_LOCK_FILE:-/tmp/openflight-kiosk-${PORT}.lock}"

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

configure_kld7_latency() {
    local setup_script="$PROJECT_DIR/scripts/setup/setup_kld7_latency.sh"

    if [ "$KLD7" != true ] && [ "$KLD7_HORIZONTAL" != true ] && [ ! -e /dev/kld7_vertical ] && [ ! -e /dev/kld7_horizontal ]; then
        return 0
    fi

    if [ "$(uname -s)" != "Linux" ]; then
        warn "Skipping K-LD7 FTDI latency setup (Linux-only)"
        return 0
    fi

    if [ ! -x "$setup_script" ]; then
        warn "Skipping K-LD7 FTDI latency setup (missing $setup_script)"
        return 0
    fi

    log "Applying K-LD7 FTDI latency setup..."
    if [ "$(id -u)" -eq 0 ]; then
        if "$setup_script" --latency 1; then
            log "K-LD7 FTDI latency setup complete"
        else
            warn "K-LD7 FTDI latency setup failed; continuing startup"
        fi
    elif command -v sudo >/dev/null 2>&1; then
        if sudo -n "$setup_script" --latency 1; then
            log "K-LD7 FTDI latency setup complete"
        else
            warn "K-LD7 FTDI latency setup failed; continuing startup"
            warn "  Run manually if needed: sudo scripts/setup/setup_kld7_latency.sh"
        fi
    else
        warn "Skipping K-LD7 FTDI latency setup (sudo not available)"
    fi
}

trap cleanup SIGINT SIGTERM

cd "$PROJECT_DIR"

if [ "$STARTUP_SPLASH" = true ]; then
    STARTUP_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp}/openflight-startup-splash-${PORT}-$$"
    STARTUP_STATUS_FILE="$STARTUP_RUNTIME_DIR/status.json"
    STARTUP_DISMISS_FILE="$STARTUP_RUNTIME_DIR/dismissed"
fi

# Build server command
SERVER_CMD="openflight-server --web-port $PORT"

if [ -n "$STARTUP_STATUS_FILE" ]; then
    SERVER_CMD="$SERVER_CMD --startup-status-file $STARTUP_STATUS_FILE"
fi

if [ "$MOCK_MODE" = true ] && [ "$SWING_SPEED" = true ]; then
    MOCK_SWING_SPEED=true
fi

if [ -n "$RADAR_PORT" ]; then
    SERVER_CMD="$SERVER_CMD --port $RADAR_PORT"
fi

if [ -n "$OPS_BAUD" ]; then
    SERVER_CMD="$SERVER_CMD --ops-baud $OPS_BAUD"
fi

if [ "$MOCK_SWING_SPEED" = true ]; then
    SERVER_CMD="$SERVER_CMD --mock-swing-speed"
elif [ "$MOCK_MODE" = true ]; then
    SERVER_CMD="$SERVER_CMD --mock"
fi

if [ "$RADAR_LOG" = true ]; then
    SERVER_CMD="$SERVER_CMD --radar-log"
fi

if [ "$DEBUG_MODE" = true ]; then
    SERVER_CMD="$SERVER_CMD --debug"
fi

if [ "$NO_CAMERA" = true ]; then
    SERVER_CMD="$SERVER_CMD --no-camera"
fi

if [ -n "$BATTERY_PROVIDER" ]; then
    SERVER_CMD="$SERVER_CMD --battery $BATTERY_PROVIDER"
fi

if [ "$BALLISTICS" = false ]; then
    SERVER_CMD="$SERVER_CMD --no-ballistics"
fi

if [ "$CAMERA_CAPTURE" = true ]; then
    SERVER_CMD="$SERVER_CMD --camera-capture"
    [ -n "$CAMERA_CAPTURE_WIDTH" ] && SERVER_CMD="$SERVER_CMD --camera-capture-width $CAMERA_CAPTURE_WIDTH"
    [ -n "$CAMERA_CAPTURE_HEIGHT" ] && SERVER_CMD="$SERVER_CMD --camera-capture-height $CAMERA_CAPTURE_HEIGHT"
    [ -n "$CAMERA_CAPTURE_FPS" ] && SERVER_CMD="$SERVER_CMD --camera-capture-fps $CAMERA_CAPTURE_FPS"
    [ -n "$CAMERA_CAPTURE_PRE_MS" ] && SERVER_CMD="$SERVER_CMD --camera-capture-pre-ms $CAMERA_CAPTURE_PRE_MS"
    [ -n "$CAMERA_CAPTURE_POST_MS" ] && SERVER_CMD="$SERVER_CMD --camera-capture-post-ms $CAMERA_CAPTURE_POST_MS"
    [ -n "$CAMERA_CAPTURE_EXPOSURE_US" ] && SERVER_CMD="$SERVER_CMD --camera-capture-exposure-us $CAMERA_CAPTURE_EXPOSURE_US"
    [ -n "$CAMERA_CAPTURE_GAIN" ] && SERVER_CMD="$SERVER_CMD --camera-capture-gain $CAMERA_CAPTURE_GAIN"
    [ -n "$CAMERA_CAPTURE_MOUNT_HEIGHT_M" ] && SERVER_CMD="$SERVER_CMD --camera-capture-mount-height-m $CAMERA_CAPTURE_MOUNT_HEIGHT_M"
    [ -n "$CAMERA_CAPTURE_LATERAL_OFFSET_M" ] && SERVER_CMD="$SERVER_CMD --camera-capture-lateral-offset-m $CAMERA_CAPTURE_LATERAL_OFFSET_M"
    [ -n "$CAMERA_CAPTURE_HORIZONTAL_OFFSET_DEG" ] && SERVER_CMD="$SERVER_CMD --camera-capture-horizontal-offset-deg $CAMERA_CAPTURE_HORIZONTAL_OFFSET_DEG"
    [ -n "$CAMERA_CAPTURE_ROLL_DEG" ] && SERVER_CMD="$SERVER_CMD --camera-capture-roll-deg $CAMERA_CAPTURE_ROLL_DEG"
    [ -n "$CAMERA_CAPTURE_STREAM" ] && SERVER_CMD="$SERVER_CMD --camera-capture-stream $CAMERA_CAPTURE_STREAM"
    [ -n "$CAMERA_CAPTURE_SCALER_CROP" ] && SERVER_CMD="$SERVER_CMD --camera-capture-scaler-crop $CAMERA_CAPTURE_SCALER_CROP"
    [ "$CAMERA_CAPTURE_ROTATE_180" = true ] && SERVER_CMD="$SERVER_CMD --camera-capture-rotate-180"
    [ "$CAMERA_CAPTURE_MIRROR_HORIZONTAL" = true ] && SERVER_CMD="$SERVER_CMD --camera-capture-mirror-horizontal"
fi

# Simulator connectors: off unless --sim; targets come from config/sim.json
if [ "$SIM" = true ]; then
    SERVER_CMD="$SERVER_CMD --sim"
fi

if [ "$CALCULATED_SPIN" = true ]; then
    SERVER_CMD="$SERVER_CMD --calculated-spin"
fi

if [ "$SWING_SPEED" = true ] && [ "$MOCK_SWING_SPEED" != true ]; then
    SERVER_CMD="$SERVER_CMD --swing-speed"
fi

if [ -n "$SWING_SPEED_THRESHOLD" ]; then
    SERVER_CMD="$SERVER_CMD --swing-speed-threshold $SWING_SPEED_THRESHOLD"
fi

if [ -n "$SWING_SPEED_MAX" ]; then
    SERVER_CMD="$SERVER_CMD --swing-speed-max $SWING_SPEED_MAX"
fi

if [ -n "$SWING_SPEED_MIN_READINGS" ]; then
    SERVER_CMD="$SERVER_CMD --swing-speed-min-readings $SWING_SPEED_MIN_READINGS"
fi

if [ -n "$SWING_SPEED_SINGLE_PEAK" ]; then
    SERVER_CMD="$SERVER_CMD --swing-speed-single-peak $SWING_SPEED_SINGLE_PEAK"
fi

if [ -n "$SWING_SPEED_NUM_REPORTS" ]; then
    SERVER_CMD="$SERVER_CMD --swing-speed-num-reports $SWING_SPEED_NUM_REPORTS"
fi

if [ -n "$SWING_SPEED_END_MS" ]; then
    SERVER_CMD="$SERVER_CMD --swing-speed-end-ms $SWING_SPEED_END_MS"
fi

if [ -n "$SWING_SPEED_COOLDOWN_MS" ]; then
    SERVER_CMD="$SERVER_CMD --swing-speed-cooldown-ms $SWING_SPEED_COOLDOWN_MS"
fi

if [ -n "$SWING_SPEED_REJECTED_COOLDOWN_MS" ]; then
    SERVER_CMD="$SERVER_CMD --swing-speed-rejected-cooldown-ms $SWING_SPEED_REJECTED_COOLDOWN_MS"
fi

if [ -n "$TRIGGER" ] && [ "$SWING_SPEED" != true ]; then
    SERVER_CMD="$SERVER_CMD --trigger $TRIGGER"
fi

if [ -n "$SOUND_PRE_TRIGGER" ] && [ "$SWING_SPEED" != true ]; then
    SERVER_CMD="$SERVER_CMD --sound-pre-trigger $SOUND_PRE_TRIGGER"
fi

if [ -n "$SAMPLE_RATE" ]; then
    SERVER_CMD="$SERVER_CMD --sample-rate $SAMPLE_RATE"
fi

if [ -n "$SESSION_LOCATION" ]; then
    SERVER_CMD="$SERVER_CMD --session-location $SESSION_LOCATION"
fi

if [ "$IWR6843" = true ]; then
    SERVER_CMD="$SERVER_CMD --iwr6843"
    [ -n "$IWR6843_PORT" ] && SERVER_CMD="$SERVER_CMD --iwr6843-port $IWR6843_PORT"
    [ -n "$IWR6843_CONFIG" ] && SERVER_CMD="$SERVER_CMD --iwr6843-config $IWR6843_CONFIG"
    [ -n "$IWR6843_CAL" ] && SERVER_CMD="$SERVER_CMD --iwr6843-cal $IWR6843_CAL"
    [ -n "$IWR6843_TRIGGER_PIN" ] && SERVER_CMD="$SERVER_CMD --iwr6843-trigger-pin $IWR6843_TRIGGER_PIN"
    [ -n "$IWR6843_TEE_M" ] && SERVER_CMD="$SERVER_CMD --iwr6843-tee-m $IWR6843_TEE_M"
    [ -n "$IWR6843_NET_M" ] && SERVER_CMD="$SERVER_CMD --iwr6843-net-m $IWR6843_NET_M"
    [ -n "$IWR6843_TILT_DEG" ] && SERVER_CMD="$SERVER_CMD --iwr6843-tilt-deg $IWR6843_TILT_DEG"
    [ -n "$IWR6843_RADAR_HEIGHT_M" ] && SERVER_CMD="$SERVER_CMD --iwr6843-radar-height-m $IWR6843_RADAR_HEIGHT_M"
    [ -n "$IWR6843_BALL_HEIGHT_M" ] && SERVER_CMD="$SERVER_CMD --iwr6843-ball-height-m $IWR6843_BALL_HEIGHT_M"
    [ -n "$IWR6843_TX_ORDER" ] && SERVER_CMD="$SERVER_CMD --iwr6843-tx-order $IWR6843_TX_ORDER"
    [ -n "$IWR6843_CAPTURE_TIMEOUT" ] && SERVER_CMD="$SERVER_CMD --iwr6843-capture-timeout $IWR6843_CAPTURE_TIMEOUT"
    [ -n "$IWR6843_OUTPUT_DIR" ] && SERVER_CMD="$SERVER_CMD --iwr6843-output-dir $IWR6843_OUTPUT_DIR"
    [ -n "$IWR6843_AZIMUTH_OFFSET" ] && SERVER_CMD="$SERVER_CMD --iwr6843-azimuth-offset-deg $IWR6843_AZIMUTH_OFFSET"
    [ -n "$IWR6843_HORIZONTAL_PHASE_REFERENCE_RAD" ] && SERVER_CMD="$SERVER_CMD --iwr6843-horizontal-phase-reference-rad $IWR6843_HORIZONTAL_PHASE_REFERENCE_RAD"
fi

if [ "$INCLINOMETER" = true ]; then
    SERVER_CMD="$SERVER_CMD --inclinometer"
    [ -n "$INCLINOMETER_ZERO_OFFSET" ] && SERVER_CMD="$SERVER_CMD --inclinometer-zero-offset $INCLINOMETER_ZERO_OFFSET"
fi

if [ "$EXPERIMENTAL_KLD7_RAW_RADC_LOGGING" = true ]; then
    SERVER_CMD="$SERVER_CMD --kld7-raw-logging"
fi

if [ "$EXPERIMENTAL_KLD7_RADC_TUNING" = true ]; then
    SERVER_CMD="$SERVER_CMD --experimental-kld7-radc-tuning"

    if [ -n "$EXPERIMENTAL_KLD7_SPEED_TOLERANCE" ]; then
        SERVER_CMD="$SERVER_CMD --experimental-kld7-speed-tolerance $EXPERIMENTAL_KLD7_SPEED_TOLERANCE"
    fi

    if [ -n "$EXPERIMENTAL_KLD7_CENTROID_FLOOR" ]; then
        SERVER_CMD="$SERVER_CMD --experimental-kld7-centroid-floor $EXPERIMENTAL_KLD7_CENTROID_FLOOR"
    fi

    if [ -n "$EXPERIMENTAL_KLD7_SPECTRUM_SOURCE" ]; then
        SERVER_CMD="$SERVER_CMD --experimental-kld7-spectrum-source $EXPERIMENTAL_KLD7_SPECTRUM_SOURCE"
    fi

    if [ -n "$EXPERIMENTAL_KLD7_OPS_BIN_TOL" ]; then
        SERVER_CMD="$SERVER_CMD --experimental-kld7-ops-bin-tol $EXPERIMENTAL_KLD7_OPS_BIN_TOL"
    fi

    if [ -n "$EXPERIMENTAL_KLD7_OPS_BIN_PENALTY" ]; then
        SERVER_CMD="$SERVER_CMD --experimental-kld7-ops-bin-penalty $EXPERIMENTAL_KLD7_OPS_BIN_PENALTY"
    fi

    if [ -n "$EXPERIMENTAL_KLD7_OPS_ANCHORED_MIN_SNR" ]; then
        SERVER_CMD="$SERVER_CMD --experimental-kld7-ops-anchored-min-snr $EXPERIMENTAL_KLD7_OPS_ANCHORED_MIN_SNR"
    fi

    if [ -n "$EXPERIMENTAL_KLD7_VERTICAL_IMPACT_ENERGY" ]; then
        SERVER_CMD="$SERVER_CMD --experimental-kld7-vertical-impact-energy $EXPERIMENTAL_KLD7_VERTICAL_IMPACT_ENERGY"
    fi

    if [ -n "$EXPERIMENTAL_KLD7_HORIZONTAL_IMPACT_ENERGY" ]; then
        SERVER_CMD="$SERVER_CMD --experimental-kld7-horizontal-impact-energy $EXPERIMENTAL_KLD7_HORIZONTAL_IMPACT_ENERGY"
    fi

    if [ -n "$EXPERIMENTAL_KLD7_HORIZONTAL_RETRY_IMPACT_ENERGY" ]; then
        SERVER_CMD="$SERVER_CMD --experimental-kld7-horizontal-retry-impact-energy $EXPERIMENTAL_KLD7_HORIZONTAL_RETRY_IMPACT_ENERGY"
    fi

    if [ -n "$EXPERIMENTAL_KLD7_HORIZONTAL_ANGLE_LIMIT" ]; then
        SERVER_CMD="$SERVER_CMD --experimental-kld7-horizontal-angle-limit $EXPERIMENTAL_KLD7_HORIZONTAL_ANGLE_LIMIT"
    fi
elif [ -n "$EXPERIMENTAL_KLD7_SPEED_TOLERANCE$EXPERIMENTAL_KLD7_CENTROID_FLOOR$EXPERIMENTAL_KLD7_SPECTRUM_SOURCE$EXPERIMENTAL_KLD7_OPS_BIN_TOL$EXPERIMENTAL_KLD7_OPS_BIN_PENALTY$EXPERIMENTAL_KLD7_OPS_ANCHORED_MIN_SNR$EXPERIMENTAL_KLD7_VERTICAL_IMPACT_ENERGY$EXPERIMENTAL_KLD7_HORIZONTAL_IMPACT_ENERGY$EXPERIMENTAL_KLD7_HORIZONTAL_RETRY_IMPACT_ENERGY$EXPERIMENTAL_KLD7_HORIZONTAL_ANGLE_LIMIT" ]; then
    warn "Ignoring experimental K-LD7 RADC tuning values without --experimental-kld7-radc-tuning"
fi

# K-LD7 radar defaults when --kld7 is enabled. This enables the two-ray
# launch-angle estimator and the ball-speed cosine correction on the server
# side; mount tilt is required (checked above) and the offset defaults to the
# calibrated 1.5 unless overridden.
# (Deprecated: K-LD7 hardware is superseded by a more capable radar chip)
if [ "$KLD7" = true ]; then
    SERVER_CMD="$SERVER_CMD --kld7"
    SERVER_CMD="$SERVER_CMD --kld7-port ${KLD7_PORT:-/dev/kld7_vertical}"
    SERVER_CMD="$SERVER_CMD --kld7-angle-offset ${KLD7_ANGLE_OFFSET:-1.5}"
    SERVER_CMD="$SERVER_CMD --kld7-mount-tilt $KLD7_MOUNT_TILT"
    [ -n "$KLD7_BALL_DISTANCE" ] && SERVER_CMD="$SERVER_CMD --kld7-ball-distance $KLD7_BALL_DISTANCE"
    [ -n "$NET_DISTANCE" ] && SERVER_CMD="$SERVER_CMD --net-distance $NET_DISTANCE"
    [ "$KLD7_VERTICAL_RAW" = true ] && SERVER_CMD="$SERVER_CMD --kld7-vertical-raw"
    # Auto-enable horizontal if symlink exists and not explicitly disabled
    if [ "$KLD7_HORIZONTAL" != true ] && [ -e /dev/kld7_horizontal ]; then
        KLD7_HORIZONTAL=true
    fi
    if [ "$KLD7_HORIZONTAL" = true ]; then
        SERVER_CMD="$SERVER_CMD --kld7-horizontal"
        SERVER_CMD="$SERVER_CMD --kld7-horizontal-port ${KLD7_HORIZONTAL_PORT:-/dev/kld7_horizontal}"
        SERVER_CMD="$SERVER_CMD --kld7-horizontal-offset ${KLD7_HORIZONTAL_OFFSET:-0}"
    fi
fi

if [ "$DRY_RUN" = true ]; then
    echo "$SERVER_CMD"
    exit 0
fi

acquire_instance_lock

# shellcheck source=ensure-kiosk-ui.sh
source "$SCRIPT_DIR/ensure-kiosk-ui.sh"
ensure_kiosk_ui

start_startup_splash

# Ensure the environment is in sync (uv recreates/repairs .venv as needed,
# so a moved project dir self-heals instead of failing with "command not found")
ensure_uv_on_path
if ! command -v uv >/dev/null 2>&1; then
    show_startup_failure \
        "server" \
        "OpenFlight preparation failed" \
        "The uv command is unavailable (checked PATH, ~/.local/bin and ~/.cargo/bin). Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

UV_SYNC_ARGS=(--quiet)
if [ "$CAMERA_CAPTURE" = true ]; then
    # Picamera2 is supplied by Raspberry Pi OS and must remain visible inside
    # the project environment; the camera extra supplies portable OpenCV. Keep
    # this selection through the final `uv run`: .python-version pins 3.11,
    # while libcamera's native extension is built for the OS Python ABI.
    export UV_PYTHON=/usr/bin/python3
    if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c "import picamera2" >/dev/null 2>&1; then
        if ! uv venv --clear --system-site-packages --python /usr/bin/python3; then
            show_startup_failure \
                "server" \
                "OpenFlight preparation failed" \
                "Camera environment preparation failed. Check the terminal log, then relaunch OpenFlight."
        fi
    fi
    UV_SYNC_ARGS+=(--extra camera)
fi

if ! uv sync "${UV_SYNC_ARGS[@]}"; then
    show_startup_failure \
        "server" \
        "OpenFlight preparation failed" \
        "Dependency preparation failed. Check the terminal log, then relaunch OpenFlight."
fi

configure_kld7_latency

# Start Grafana Alloy for log shipping (if installed and credentials configured)
if command -v alloy &> /dev/null || systemctl is-enabled alloy &> /dev/null 2>&1; then
    if sudo test -f /etc/alloy/credentials.env; then
        # Check if credentials are actually filled in (not just the template)
        if sudo grep -q "LOKI_URL=https\?://" /etc/alloy/credentials.env 2>/dev/null; then
            if ! systemctl is-active alloy &> /dev/null 2>&1; then
                log "Starting Grafana Alloy for log shipping..."
                sudo systemctl start alloy 2>/dev/null || warn "Failed to start Alloy (try: sudo systemctl start alloy)"
            else
                log "Grafana Alloy already running (log shipping active)"
            fi
        else
            warn "Alloy installed but credentials not configured (/etc/alloy/credentials.env)"
        fi
    else
        warn "Alloy installed but no credentials file found (run: sudo scripts/setup/setup_alloy.sh)"
    fi
else
    warn "Grafana Alloy not installed — session logs will only be saved locally"
    warn "  Install with: sudo scripts/setup/setup_alloy.sh"
fi

# Start the server
if [ "$MOCK_MODE" = true ]; then
    log "Starting OpenFlight server on port $PORT (MOCK MODE)..."
else
    log "Starting OpenFlight server on port $PORT..."
    if [ -n "$TRIGGER" ]; then
        log "Trigger: $TRIGGER"
    fi
    if [ -n "$SOUND_PRE_TRIGGER" ]; then
        log "Buffer split: S#$SOUND_PRE_TRIGGER ($SOUND_PRE_TRIGGER pre / $((32 - SOUND_PRE_TRIGGER)) post segments)"
    fi
fi

if [ "$DEBUG_MODE" = true ]; then
    log "Debug mode enabled (verbose output)"
fi

if [ "$TRACKMAN_TEST" = true ]; then
    log "TrackMan test mode enabled (dual K-LD7, raw RADC logging, location: $SESSION_LOCATION)"
fi

if [ "$KLD7" = true ]; then
    warn "K-LD7 angle radars are deprecated (superseded by a more capable radar chip)"
fi

if [ "$NO_CAMERA" = true ]; then
    log "Camera disabled"
else
    log "Camera enabled (Hough + ByteTrack)"
fi

if [ "$BALLISTICS" = true ]; then
    log "Ballistic carry model enabled (simulator + drag/Magnus)"
else
    log "Ballistic carry model disabled (using legacy table)"
fi

uv run ${OPENFLIGHT_UV_RUN_ARGS:-} $SERVER_CMD &
SERVER_PID=$!

# Wait for server to be ready
log "Waiting for server to start..."
for i in {1..30}; do
    if curl -s "http://$HOST:$PORT" > /dev/null 2>&1; then
        break
    fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        break
    fi
    sleep 0.5
done

if ! curl -s "http://$HOST:$PORT" > /dev/null 2>&1; then
    if kill -0 "$SERVER_PID" 2>/dev/null; then
        show_startup_failure \
            "server" \
            "OpenFlight server timed out" \
            "Wait a moment, then return to the desktop and relaunch OpenFlight." \
            1 \
            true
    else
        wait "$SERVER_PID" 2>/dev/null || true
        SERVER_PID=""
        show_startup_failure \
            "server" \
            "OpenFlight server exited during startup" \
            "Check the connected radar hardware and terminal log, then relaunch OpenFlight." \
            1 \
            true
    fi
fi

if [ -n "$STARTUP_STATUS_FILE" ]; then
    uv run --no-sync python -m openflight.startup_status ready "$STARTUP_STATUS_FILE" || \
        warn "Could not mark startup splash ready; continuing to OpenFlight"
fi

log "Server is running!"

KIOSK_URL="http://$HOST:$PORT"
if [ "$STARTUP_SPLASH" != true ] || [ "$BROWSER_LAUNCHED" != true ]; then
    launch_kiosk_browser "$KIOSK_URL" || true
else
    log "Startup splash will continue to OpenFlight"
fi

log "OpenFlight is running! Press Ctrl+C to stop."

# Wait for server process — exits when server stops (Ctrl+C or UI shutdown)
wait $SERVER_PID
cleanup
