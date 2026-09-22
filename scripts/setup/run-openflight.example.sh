#!/usr/bin/env bash
# Example user-local OpenFlight launcher.
#
# Copy this file outside the repository, then add this Pi's hardware and
# calibration arguments to openflight_args. The desktop installer derives a
# checkout-specific filename and records the checkout path automatically.

set -o pipefail

openflight_installed_dir="${openflight_installed_dir:-$HOME/openflight}"
project_dir="${OPENFLIGHT_DIR:-$openflight_installed_dir}"
runtime_dir="${XDG_RUNTIME_DIR:-/tmp}"
log_dir="${OPENFLIGHT_LOG_DIR:-$HOME/openflight_sessions/terminal_logs}"
lock_file="$runtime_dir/openflight-launch.lock"

# Ignore repeated taps while this launcher owns the hardware.
mkdir -p "$runtime_dir" "$log_dir"
exec 9>"$lock_file"
if ! flock -n 9; then
    exit 0
fi

if ! cd "$project_dir"; then
    logger -t openflight "OpenFlight project directory not found: $project_dir"
    exit 1
fi

# Keep the splash flag first. Add optional hardware only when it is installed
# and calibrated on this Pi.
openflight_args=(
    # --debug  # Uncomment to enable debug logging.
    --startup-splash
    --ballistics

    # Optional measurement modes:
    # --calculated-spin  # Replaces measured OPS spin with a kinematic estimate.

    # TI IWR6843 example; replace every geometry value with measurements from
    # this installation before uncommenting.
    # --iwr6843
    # --iwr6843-port /dev/serial/by-id/REPLACE_WITH_TI_SERIAL_ID
    # --iwr6843-config config/iwr6843_l3dump_dense_36f2ms_53bin_iq8.cfg
    # --iwr6843-tee-m 1.372
    # --iwr6843-net-m 4.064
    # --iwr6843-tilt-deg 5.5
    # --iwr6843-radar-height-m 0.229
    # --iwr6843-ball-height-m 0.021

    # Optional Geekworm X1202/X1206 monitoring:
    # --battery geekworm

    # Add additional flags here.
)

# Optional startup optimization after dependencies have already been synced:
# export OPENFLIGHT_UV_RUN_ARGS=--no-sync
#
# Camera capture uses Raspberry Pi OS's system Picamera2 package. A local
# camera profile may also need:
# export UV_PYTHON=/usr/bin/python3

log_file="$log_dir/kiosk_$(date +%Y%m%d_%H%M%S).log"
scripts/start-kiosk.sh "${openflight_args[@]}" 2>&1 | tee -a "$log_file"
status=${PIPESTATUS[0]}

if (( status != 0 )); then
    logger -t openflight "OpenFlight exited with status $status; see $log_file"
fi
exit "$status"
