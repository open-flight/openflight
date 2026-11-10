#!/usr/bin/env bash
# Stop OpenFlight and power the Pi off when the UPS battery runs low on
# battery power. Installed as a service by install_battery_guard.sh.

set -euo pipefail

POWER_SUPPLY_ROOT="${OPENFLIGHT_BATTERY_GUARD_POWER_SUPPLY_ROOT:-/sys/class/power_supply}"
MIN_PERCENT="${OPENFLIGHT_BATTERY_GUARD_MIN_PERCENT:-10}"
MIN_MICROVOLTS="${OPENFLIGHT_BATTERY_GUARD_MIN_MICROVOLTS:-3400000}"
CHECKS_NEEDED="${OPENFLIGHT_BATTERY_GUARD_CHECKS:-3}"
INTERVAL_S="${OPENFLIGHT_BATTERY_GUARD_INTERVAL_S:-20}"
STOP_TIMEOUT_S="${OPENFLIGHT_BATTERY_GUARD_STOP_TIMEOUT_S:-15}"
DRY_RUN="${OPENFLIGHT_BATTERY_GUARD_DRY_RUN:-0}"
# Test hook: exit after this many polls. 0 runs until shutdown.
MAX_POLLS="${OPENFLIGHT_BATTERY_GUARD_MAX_POLLS:-0}"
# Matches both "openflight-server" and "python -m openflight.server".
SERVER_PATTERN="openflight.server"

log() {
    printf '[battery-guard] %s\n' "$*"
}

read_int() {
    local value

    [[ -r "$1" ]] || return 1
    value="$(<"$1")" || return 1
    [[ "$value" =~ ^-?[0-9]+$ ]] || return 1
    printf '%s\n' "$value"
}

supply_type() {
    [[ -r "$1/type" ]] && printf '%s\n' "$(<"$1/type")"
}

find_battery() {
    local candidate

    for candidate in "$POWER_SUPPLY_ROOT"/*; do
        if [[ "$(supply_type "$candidate" || true)" == "Battery" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

# Prints 1 when any non-battery supply is online, 0 when every readable one is
# offline, and nothing when none can be read.
external_power_state() {
    local candidate
    local online
    local found=false

    for candidate in "$POWER_SUPPLY_ROOT"/*; do
        [[ -d "$candidate" ]] || continue
        [[ "$(supply_type "$candidate" || true)" == "Battery" ]] && continue
        online="$(read_int "$candidate/online")" || continue
        found=true
        if (( online > 0 )); then
            printf '1\n'
            return 0
        fi
    done
    [[ "$found" == "true" ]] && printf '0\n'
    return 0
}

stop_openflight_and_power_off() {
    local waited=0

    if [[ "$DRY_RUN" == "1" ]]; then
        log "Dry run: would stop OpenFlight and power off."
        return 0
    fi

    systemctl stop openflight 2>/dev/null || true
    pkill -INT -f "$SERVER_PATTERN" 2>/dev/null || true
    while pgrep -f "$SERVER_PATTERN" >/dev/null 2>&1 && (( waited < STOP_TIMEOUT_S )); do
        sleep 1
        waited=$((waited + 1))
    done
    log "Powering off."
    systemctl poweroff
}

main() {
    local battery
    local external
    local percent
    local microvolts
    local low_count=0
    local polls=0
    local warned_unknown_power=false
    local warned_unreadable_battery=false

    log "Powering off on battery at or below ${MIN_PERCENT}% or ${MIN_MICROVOLTS} uV" \
        "(${CHECKS_NEEDED} readings, ${INTERVAL_S}s apart)."

    while true; do
        percent=""
        microvolts=""
        external="$(external_power_state)"
        if battery="$(find_battery)"; then
            percent="$(read_int "$battery/capacity")" || percent=""
            microvolts="$(read_int "$battery/voltage_now")" || microvolts=""
        fi

        if [[ -z "$external" ]]; then
            if [[ "$warned_unknown_power" == "false" ]]; then
                log "External power state is unreadable; not acting on battery level."
                warned_unknown_power=true
            fi
            low_count=0
        elif [[ -z "$percent" && -z "$microvolts" ]]; then
            if [[ "$warned_unreadable_battery" == "false" ]]; then
                log "Battery level is unreadable; not acting."
                warned_unreadable_battery=true
            fi
            low_count=0
        elif [[ "$external" == "0" ]] \
            && { [[ -n "$percent" ]] && (( percent <= MIN_PERCENT )) \
                || { [[ -n "$microvolts" ]] && (( microvolts <= MIN_MICROVOLTS )); }; }; then
            low_count=$((low_count + 1))
            log "Low battery on battery power: ${percent:-?}% ${microvolts:-?} uV" \
                "(${low_count}/${CHECKS_NEEDED})."
            if (( low_count >= CHECKS_NEEDED )); then
                log "Battery critically low; shutting down."
                stop_openflight_and_power_off
                return 0
            fi
        else
            if (( low_count > 0 )); then
                log "Battery reading recovered; resetting the low count."
            fi
            low_count=0
        fi

        polls=$((polls + 1))
        if (( MAX_POLLS > 0 && polls >= MAX_POLLS )); then
            return 0
        fi
        sleep "$INTERVAL_S"
    done
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
