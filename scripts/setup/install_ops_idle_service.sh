#!/usr/bin/env bash
# Install, or remove, a boot service that idles the OPS243-A until OpenFlight starts.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
UNIT_NAME="openflight-ops-idle.service"
UNIT_PATH="${OPENFLIGHT_OPS_IDLE_UNIT_PATH:-/etc/systemd/system/$UNIT_NAME}"
SERVICE_USER="${SUDO_USER:-$USER}"
PORT=""

log() {
    printf '[ops-idle] %s\n' "$*"
}

die() {
    printf '[ops-idle] ERROR: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage: scripts/setup/install_ops_idle_service.sh [--port PORT] [--uninstall]

Install a systemd service that puts the OPS243-A into idle (PI) at boot, so it
does not sample or transmit until OpenFlight starts. OpenFlight wakes the radar
on startup and idles it again on shutdown.

  --port PORT  OPS243 serial port, e.g. /dev/ttyAMA0 for the GPIO UART
               (default: USB auto-detect)
  --uninstall  Disable and remove the service
  -h, --help   Show this help
EOF
}

as_root() {
    if (( EUID == 0 )); then
        "$@"
    else
        sudo "$@"
    fi
}

render_unit() {
    local port_args=""

    [[ -n "$PORT" ]] && port_args=" --port $PORT"
    cat <<EOF
[Unit]
Description=Idle the OPS243-A radar until OpenFlight starts
After=local-fs.target
Before=openflight.service

[Service]
Type=oneshot
User=$SERVICE_USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/.venv/bin/python $PROJECT_DIR/scripts/setup/ops_idle.py$port_args

[Install]
WantedBy=multi-user.target
EOF
}

install_service() {
    [[ "$PROJECT_DIR" != *[[:space:]]* ]] \
        || die "The repository path cannot contain whitespace: $PROJECT_DIR"
    [[ -x "$PROJECT_DIR/.venv/bin/python" ]] \
        || die "No virtual environment at $PROJECT_DIR/.venv; run scripts/setup/setup.sh first"

    render_unit | as_root tee "$UNIT_PATH" > /dev/null
    as_root systemctl daemon-reload
    as_root systemctl enable "$UNIT_NAME"
    log "Installed $UNIT_NAME; idling the radar now."
    as_root systemctl start "$UNIT_NAME" || die "The radar could not be idled; see: journalctl -u $UNIT_NAME"
    log "Done. Check it with: journalctl -u $UNIT_NAME"
}

uninstall_service() {
    as_root systemctl disable "$UNIT_NAME" 2>/dev/null || true
    as_root rm -f "$UNIT_PATH"
    as_root systemctl daemon-reload
    log "Removed $UNIT_NAME"
}

main() {
    local action="install"

    while (( $# > 0 )); do
        case "$1" in
            --port)
                [[ $# -ge 2 ]] || die "--port needs a value"
                PORT="$2"
                shift
                ;;
            --uninstall)
                action="uninstall"
                ;;
            -h|--help)
                usage
                return 0
                ;;
            *)
                usage >&2
                die "Unknown option: $1"
                ;;
        esac
        shift
    done

    if [[ "$action" == "install" ]]; then
        install_service
    else
        uninstall_service
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
