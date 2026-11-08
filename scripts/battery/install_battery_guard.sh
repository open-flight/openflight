#!/usr/bin/env bash
# Install, or remove, the optional low-battery shutdown service.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD_SCRIPT="$SCRIPT_DIR/battery_guard.sh"
UNIT_NAME="openflight-battery-guard.service"
UNIT_PATH="${OPENFLIGHT_BATTERY_GUARD_UNIT_PATH:-/etc/systemd/system/$UNIT_NAME}"
CONFIG_PATH="${OPENFLIGHT_BATTERY_GUARD_CONFIG_PATH:-/etc/default/openflight-battery-guard}"

log() {
    printf '[battery-guard] %s\n' "$*"
}

die() {
    printf '[battery-guard] ERROR: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage: scripts/battery/install_battery_guard.sh [--uninstall]

Install a systemd service that stops OpenFlight and powers the Pi off when it
is running on battery and the UPS battery reaches 10% or 3.40 V for three
readings in a row. Thresholds live in /etc/default/openflight-battery-guard.

  --uninstall  Stop, disable and remove the service (keeps the config file)
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
    cat <<EOF
[Unit]
Description=OpenFlight low-battery shutdown
After=multi-user.target

[Service]
Type=simple
EnvironmentFile=-$CONFIG_PATH
ExecStart=/bin/bash $GUARD_SCRIPT
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
}

render_config() {
    cat <<'EOF'
# OpenFlight low-battery shutdown. Restart the service after editing:
#   sudo systemctl restart openflight-battery-guard
# Power off when on battery and either limit is reached for CHECKS readings.
OPENFLIGHT_BATTERY_GUARD_MIN_PERCENT=10
OPENFLIGHT_BATTERY_GUARD_MIN_MICROVOLTS=3400000
OPENFLIGHT_BATTERY_GUARD_CHECKS=3
OPENFLIGHT_BATTERY_GUARD_INTERVAL_S=20
EOF
}

install_guard() {
    [[ -f "$GUARD_SCRIPT" ]] || die "Guard script not found: $GUARD_SCRIPT"
    [[ "$GUARD_SCRIPT" != *[[:space:]]* ]] \
        || die "The repository path cannot contain whitespace: $GUARD_SCRIPT"

    render_unit | as_root tee "$UNIT_PATH" > /dev/null
    if [[ ! -e "$CONFIG_PATH" ]]; then
        render_config | as_root tee "$CONFIG_PATH" > /dev/null
        log "Wrote default thresholds to $CONFIG_PATH"
    else
        log "Kept existing thresholds in $CONFIG_PATH"
    fi
    as_root systemctl daemon-reload
    as_root systemctl enable --now "$UNIT_NAME"
    as_root systemctl restart "$UNIT_NAME"
    log "Installed and started $UNIT_NAME"
    log "Check it with: journalctl -u $UNIT_NAME"
}

uninstall_guard() {
    as_root systemctl disable --now "$UNIT_NAME" 2>/dev/null || true
    as_root rm -f "$UNIT_PATH"
    as_root systemctl daemon-reload
    log "Removed $UNIT_NAME (kept $CONFIG_PATH)"
}

main() {
    case "${1:-}" in
        "")
            install_guard
            ;;
        --uninstall)
            uninstall_guard
            ;;
        -h|--help)
            usage
            ;;
        *)
            usage >&2
            die "Unknown option: $1"
            ;;
    esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
