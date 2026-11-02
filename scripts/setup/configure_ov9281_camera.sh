#!/usr/bin/env bash
# Configure the InnoMaker OV9281 camera without asking which connector it uses.
#
# The Raspberry Pi firmware's camera_auto_detect only recognises official
# Raspberry Pi camera modules, so an OV9281 always needs a dtoverlay line.
# Instead of asking the user for CAM/DISP 0 or 1, this script loads the
# ov9281 overlay on every Pi 5 connector that is not driving a DSI display.
# At boot the kernel probes each one; the empty connector fails its probe
# harmlessly and only the connector with the camera registers a sensor.

set -euo pipefail

BOOT_CONFIG="${OPENFLIGHT_CAMERA_BOOT_CONFIG:-/boot/firmware/config.txt}"
DT_ROOT="${OPENFLIGHT_CAMERA_DT_ROOT:-/proc/device-tree}"
I2C_DEVICES_ROOT="${OPENFLIGHT_CAMERA_I2C_DEVICES_ROOT:-/sys/bus/i2c/devices}"
PI_MODEL_PATH="${OPENFLIGHT_CAMERA_PI_MODEL_PATH:-$DT_ROOT/model}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

BLOCK_BEGIN="# BEGIN OpenFlight OV9281 camera (managed by scripts/setup/configure_ov9281_camera.sh)"
BLOCK_END="# END OpenFlight OV9281 camera"
OV9281_I2C_ADDRESS="0060"

# Pi 5 connector N uses csiN/dsiN and the camera I2C bus i2c-(10 + N).
PI5_CONNECTORS=(0 1)

VERIFY_ONLY=false
REMOVE=false

log() {
    printf '[Camera] %s\n' "$*"
}

warn() {
    printf '[Camera] WARNING: %s\n' "$*" >&2
}

die() {
    printf '[Camera] ERROR: %s\n' "$*" >&2
    return 1
}

usage() {
    cat <<'EOF'
Usage: scripts/setup/configure_ov9281_camera.sh [--verify] [--remove]

Enable the OV9281 camera on whichever CAM/DISP connector it is plugged into.

  --verify    Read-only check after reboot: report which connector has the camera
  --remove    Remove the OpenFlight camera block from the boot configuration
  -h, --help  Show this help

A connector that drives a DSI display (for example the Touch Display 2) is
left alone. Reboot after configuring, then run with --verify.
EOF
}

as_root() {
    if (( EUID == 0 )); then
        "$@"
    else
        sudo "$@"
    fi
}

is_pi_5() {
    [[ -r "$PI_MODEL_PATH" ]] && grep -aq "Raspberry Pi 5" "$PI_MODEL_PATH"
}

# Print the live device-tree status ("okay", "disabled", ...) of a labelled node.
dt_label_status() {
    local label="$1"
    local node_path

    [[ -r "$DT_ROOT/__symbols__/$label" ]] || return 1
    node_path="$(tr -d '\0' < "$DT_ROOT/__symbols__/$label")"
    [[ -r "$DT_ROOT$node_path/status" ]] || return 1
    tr -d '\0' < "$DT_ROOT$node_path/status"
}

# A connector drives a display when the firmware enabled its DSI host, either
# through display_auto_detect or an explicit vc4-kms-dsi overlay.
connector_has_dsi_display() {
    local connector="$1"

    [[ "$(dt_label_status "dsi$connector" || true)" == "okay" ]]
}

# Print one overlay line per connector the camera may be plugged into.
camera_overlay_lines() {
    local connector
    local -a lines=()

    if ! is_pi_5; then
        # Earlier boards have a single camera connector.
        printf 'dtoverlay=ov9281\n'
        return 0
    fi

    for connector in "${PI5_CONNECTORS[@]}"; do
        if connector_has_dsi_display "$connector"; then
            log "CAM/DISP $connector drives a DSI display; leaving it alone." >&2
            continue
        fi
        # The ov9281 overlay targets connector 1 unless told otherwise.
        if (( connector == 1 )); then
            lines+=("dtoverlay=ov9281")
        else
            lines+=("dtoverlay=ov9281,cam$connector")
        fi
    done

    # set -e does not fire inside a `|| die` list, so return explicitly.
    if (( ${#lines[@]} == 0 )); then
        die "Both CAM/DISP connectors drive DSI displays; there is no free camera connector."
        return 1
    fi
    printf '%s\n' "${lines[@]}"
}

# Rewrite the boot configuration in place:
#   - drop any previous OpenFlight camera block and hand-written ov9281 overlays,
#   - turn every existing camera_auto_detect setting off,
#   - append a fresh managed block holding the given overlay lines.
# With no overlay lines, the block is removed and camera_auto_detect is left as is.
update_boot_config() {
    local config_path="$1"
    shift
    local updated

    [[ -f "$config_path" ]] || die "Boot configuration not found: $config_path"

    updated="$(mktemp)"
    awk -v begin="$BLOCK_BEGIN" -v end="$BLOCK_END" -v configure="$(( $# > 0 ))" '
        $0 == begin { in_block = 1; next }
        in_block { if ($0 == end) in_block = 0; next }
        configure && /^[[:space:]]*dtoverlay=ov9281([,[:space:]]|$)/ { next }
        configure && /^[[:space:]]*camera_auto_detect=/ { print "camera_auto_detect=0"; next }
        { print }
    ' "$config_path" > "$updated"

    # Trim trailing blank lines left behind by a removed block.
    sed -i -e ':a' -e '/^\n*$/{$d;N;ba' -e '}' "$updated"

    if (( $# > 0 )); then
        {
            printf '\n%s\n' "$BLOCK_BEGIN"
            printf '[all]\n'
            # Overrides any earlier camera_auto_detect=1, including one in a
            # conditional section this script did not rewrite.
            printf 'camera_auto_detect=0\n'
            printf '%s\n' "$@"
            printf '%s\n' "$BLOCK_END"
        } >> "$updated"
    fi

    cat "$updated" > "$config_path"
    rm -f "$updated"
}

apply_boot_config() {
    local current
    local updated
    local backup
    local overlay_text
    local -a overlay_lines=()

    [[ -r "$BOOT_CONFIG" ]] || die "Cannot read $BOOT_CONFIG"

    if [[ "$REMOVE" != "true" ]]; then
        # A command substitution, unlike a process substitution, lets a
        # failure here stop the script before the boot configuration changes.
        overlay_text="$(camera_overlay_lines)"
        mapfile -t overlay_lines <<< "$overlay_text"
    fi

    current="$(mktemp)"
    updated="$(mktemp)"
    as_root cp "$BOOT_CONFIG" "$current"
    cp "$current" "$updated"
    update_boot_config "$updated" "${overlay_lines[@]}"

    if cmp -s "$current" "$updated"; then
        log "Boot configuration is already up to date."
        rm -f "$current" "$updated"
        return 0
    fi

    backup="${BOOT_CONFIG}.pre-openflight-camera-${TIMESTAMP}"
    as_root cp -a "$BOOT_CONFIG" "$backup"
    as_root cp "$updated" "$BOOT_CONFIG"
    rm -f "$current" "$updated"

    log "Updated $BOOT_CONFIG"
    log "Boot configuration backup: $backup"
    if (( ${#overlay_lines[@]} > 0 )); then
        log "Camera overlays: ${overlay_lines[*]}"
    else
        log "camera_auto_detect stays 0; set it to 1 in $BOOT_CONFIG to auto-detect official cameras again."
    fi
    log "Reboot required. After reboot, run: scripts/setup/configure_ov9281_camera.sh --verify"
}

# Print the Pi 5 connector numbers whose OV9281 the kernel bound a driver to.
detected_connectors() {
    local connector

    for connector in "${PI5_CONNECTORS[@]}"; do
        if [[ -e "$I2C_DEVICES_ROOT/$(( 10 + connector ))-$OV9281_I2C_ADDRESS/driver" ]]; then
            printf '%s\n' "$connector"
        fi
    done
}

verify_camera() {
    local -a connectors=()
    local listing

    if ! grep -Fqx "$BLOCK_BEGIN" "$BOOT_CONFIG" 2>/dev/null; then
        die "The OpenFlight camera block is missing from $BOOT_CONFIG; run this script without --verify first."
    fi

    if is_pi_5; then
        mapfile -t connectors < <(detected_connectors)
        case "${#connectors[@]}" in
            0)
                die "No OV9281 answered on either connector. Check the ribbon cable orientation and reboot if you have not yet."
                ;;
            1)
                log "OV9281 detected on CAM/DISP ${connectors[0]}."
                ;;
            *)
                log "OV9281 detected on CAM/DISP ${connectors[*]}."
                ;;
        esac
    fi

    if command -v rpicam-hello >/dev/null 2>&1; then
        listing="$(rpicam-hello --list-cameras 2>&1 || true)"
        if grep -Eqi 'ov928[12]' <<< "$listing"; then
            log "libcamera lists the camera:"
            printf '%s\n' "$listing"
        else
            die "libcamera does not list an OV9281. Output of rpicam-hello --list-cameras: $listing"
        fi
    else
        warn "rpicam-hello is unavailable; install rpicam-apps to confirm libcamera sees the camera."
    fi

    log "OV9281 camera is ready."
}

main() {
    while (( $# > 0 )); do
        case "$1" in
            --verify)
                VERIFY_ONLY=true
                ;;
            --remove)
                REMOVE=true
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

    if [[ "$VERIFY_ONLY" == "true" ]]; then
        verify_camera
        return
    fi

    apply_boot_config
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
