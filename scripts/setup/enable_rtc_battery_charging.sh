#!/usr/bin/env bash
# Enable (or disable) charging of a RECHARGEABLE Raspberry Pi 5 RTC battery,
# so log times stay correct while the Pi is unplugged.

set -euo pipefail

BOOT_CONFIG="${OPENFLIGHT_RTC_BOOT_CONFIG:-/boot/firmware/config.txt}"
RTC_SYSFS="${OPENFLIGHT_RTC_SYSFS:-/sys/class/rtc/rtc0}"
PI_MODEL_PATH="${OPENFLIGHT_RTC_PI_MODEL_PATH:-/proc/device-tree/model}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

# Raspberry Pi's documented charge voltage for the official lithium manganese
# RTC battery. The RTC charger is constant-current (3 mA) constant-voltage.
CHARGE_MICROVOLTS=3000000
CHARGE_LINE="dtparam=rtc_bbat_vchg=$CHARGE_MICROVOLTS"
CHARGE_COMMENT="# OpenFlight: charge the rechargeable RTC backup battery"
CONFIRM_WORD="rechargeable"

MODE=enable
ASSUME_YES=false
CONFIG_CHANGED=false

log() {
    printf '[RTC battery] %s\n' "$*"
}

warn() {
    printf '[RTC battery] WARNING: %s\n' "$*" >&2
}

die() {
    printf '[RTC battery] ERROR: %s\n' "$*" >&2
    return 1
}

usage() {
    cat <<'EOF'
Usage: scripts/setup/enable_rtc_battery_charging.sh [--disable | --verify] [--yes]

Turn on charging for a RECHARGEABLE Pi 5 RTC battery (J5 / BAT), so log
times stay correct while unplugged. Never use it with a CR2032 or other
non-rechargeable cell.

  --disable  Remove the charging setting from config.txt (stop charging)
  --verify   Read-only: show the current charging voltage from sysfs
  --yes      Skip the typed confirmation (you have checked the battery)
  -h, --help Show this help

Changes take effect after a reboot.
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
    [[ "${OPENFLIGHT_RTC_ALLOW_NON_PI:-0}" == "1" ]] && return 0
    [[ -r "$PI_MODEL_PATH" ]] && grep -aq "Raspberry Pi 5" "$PI_MODEL_PATH"
}

print_battery_warning() {
    cat <<EOF

  RECHARGEABLE RTC BATTERY ONLY (e.g. the official Raspberry Pi one).
  NEVER a CR2032 or other non-rechargeable cell: charging it can make it
  leak or burst. Not sure? Stop and check the label.

EOF
}

confirm_rechargeable() {
    local answer

    print_battery_warning
    if [[ "$ASSUME_YES" == "true" ]]; then
        return 0
    fi
    if [[ ! -t 0 ]]; then
        die "Refusing to enable charging without confirmation; rerun interactively or pass --yes"
        return 1
    fi

    read -r -p "Type '$CONFIRM_WORD' to confirm your RTC battery is rechargeable: " answer
    if [[ "${answer,,}" != "$CONFIRM_WORD" ]]; then
        log "Not confirmed. Charging was NOT enabled."
        return 1
    fi
}

# Remove every rtc_bbat_vchg line from a config file, plus the blank line,
# [all] header and comment that update_boot_config added around ours.
strip_charging_lines() {
    local config_path="$1"
    local updated

    updated="$(mktemp)"
    awk -v comment="$CHARGE_COMMENT" '
        { lines[NR] = $0 }
        END {
            kept = 0
            for (i = 1; i <= NR; i++) {
                if (lines[i] ~ /rtc_bbat_vchg/ || lines[i] == comment)
                    continue
                if (lines[i] == "[all]" && lines[i + 1] == comment) {
                    if (kept > 0 && out[kept] == "")
                        kept--
                    continue
                }
                out[++kept] = lines[i]
            }
            for (i = 1; i <= kept; i++)
                print out[i]
        }
    ' "$config_path" > "$updated"
    mv "$updated" "$config_path"
}

# Make the config contain exactly one active charging line at our voltage.
update_boot_config() {
    local config_path="$1"

    [[ -f "$config_path" ]] || die "Boot configuration not found: $config_path"

    if [[ "$(grep -c 'rtc_bbat_vchg' "$config_path" || true)" == "1" ]] \
        && grep -Fqx "$CHARGE_LINE" "$config_path"; then
        return 0
    fi

    strip_charging_lines "$config_path"
    {
        printf '\n[all]\n'
        printf '%s\n' "$CHARGE_COMMENT"
        printf '%s\n' "$CHARGE_LINE"
    } >> "$config_path"
}

disable_boot_config() {
    local config_path="$1"

    [[ -f "$config_path" ]] || die "Boot configuration not found: $config_path"
    strip_charging_lines "$config_path"
}

# Apply one of the edit functions above to the real boot config, with a backup.
# Sets CONFIG_CHANGED=true when the file was rewritten.
apply_boot_config_change() {
    local edit_function="$1"
    local current
    local updated
    local backup

    [[ -r "$BOOT_CONFIG" ]] || die "Cannot read $BOOT_CONFIG"

    current="$(mktemp)"
    updated="$(mktemp)"
    as_root cp "$BOOT_CONFIG" "$current"
    cp "$current" "$updated"
    "$edit_function" "$updated"

    if cmp -s "$current" "$updated"; then
        rm -f "$current" "$updated"
        return 0
    fi

    backup="${BOOT_CONFIG}.pre-openflight-rtc-${TIMESTAMP}"
    as_root cp -a "$BOOT_CONFIG" "$backup"
    as_root cp "$updated" "$BOOT_CONFIG"
    log "Updated $BOOT_CONFIG"
    log "Boot configuration backup: $backup"
    rm -f "$current" "$updated"
    CONFIG_CHANGED=true
}

verify_charging() {
    local voltage_path="$RTC_SYSFS/charging_voltage"
    local voltage

    if [[ ! -r "$voltage_path" ]]; then
        die "Cannot read $voltage_path — is this a Raspberry Pi 5 with current firmware?"
        return 1
    fi

    voltage="$(<"$voltage_path")"
    if [[ "$voltage" == "0" ]]; then
        log "RTC battery charging is OFF (charging_voltage=0)."
    else
        log "RTC battery charging is ON (charging_voltage=${voltage} uV)."
    fi

    if grep -q 'rtc_bbat_vchg' "$BOOT_CONFIG" 2>/dev/null; then
        log "config.txt setting: $(grep 'rtc_bbat_vchg' "$BOOT_CONFIG" | tr '\n' ' ')"
    else
        log "config.txt has no rtc_bbat_vchg setting."
    fi
    log "Current RTC time: $(cat "$RTC_SYSFS/date" 2>/dev/null || echo '?') $(cat "$RTC_SYSFS/time" 2>/dev/null || echo '?') (UTC)"
}

main() {
    while (( $# > 0 )); do
        case "$1" in
            --disable)
                MODE=disable
                ;;
            --verify)
                MODE=verify
                ;;
            --yes)
                ASSUME_YES=true
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

    is_pi_5 || die "RTC battery charging is only available on Raspberry Pi 5."

    case "$MODE" in
        verify)
            verify_charging
            ;;
        disable)
            apply_boot_config_change disable_boot_config
            if [[ "$CONFIG_CHANGED" == "true" ]]; then
                log "RTC battery charging disabled. Reboot to apply: sudo reboot"
            else
                log "RTC battery charging was already disabled in config.txt."
            fi
            ;;
        enable)
            confirm_rechargeable || return 1
            apply_boot_config_change update_boot_config
            if [[ "$CONFIG_CHANGED" == "true" ]]; then
                log "RTC battery charging enabled at ${CHARGE_MICROVOLTS} uV."
                log "Reboot to apply (sudo reboot), then check with:"
                log "    ./scripts/setup/enable_rtc_battery_charging.sh --verify"
            else
                log "RTC battery charging is already enabled in config.txt."
            fi
            log "Stop charging any time with: ./scripts/setup/enable_rtc_battery_charging.sh --disable"
            ;;
    esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
