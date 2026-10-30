#!/usr/bin/env bash
# Configure native X1202/X1206 telemetry and Raspberry Pi desktop display.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BATTERY_SCRIPT_DIR="$(dirname "$SCRIPT_DIR")"
BOOT_CONFIG="${OPENFLIGHT_GEEKWORM_BOOT_CONFIG:-/boot/firmware/config.txt}"
MODULES_CONFIG="${OPENFLIGHT_GEEKWORM_MODULES_CONFIG:-/etc/modules-load.d/openflight-geekworm.conf}"
POWER_SUPPLY_ROOT="${OPENFLIGHT_GEEKWORM_POWER_SUPPLY_ROOT:-/sys/class/power_supply}"
PI_MODEL_PATH="${OPENFLIGHT_GEEKWORM_PI_MODEL_PATH:-/proc/device-tree/model}"
PANEL_PACKAGE="$BATTERY_SCRIPT_DIR/packages/wfplug-batt_1.11+openflight1_arm64.deb"
PANEL_PACKAGE_VERSION="1.11+openflight1"
PANEL_PACKAGE_SHA256="950e985dcc83c669b6abbc8a4769fc410a5842785c1488a12e7ed8f2829fbda1"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

I2C_LINE="dtparam=i2c_arm=on"
BATTERY_OVERLAY="dtoverlay=i2c-sensor,max17040"
CHARGER_OVERLAY="dtoverlay=gpio-charger,gpio=6,active_low=0,gpio_pull=down,type=mains"

VERIFY_ONLY=false
NO_PANEL=false
REBOOT_REQUIRED=false
PANEL_RESTART_REQUIRED=false

log() {
    printf '[Geekworm] %s\n' "$*"
}

warn() {
    printf '[Geekworm] WARNING: %s\n' "$*" >&2
}

die() {
    printf '[Geekworm] ERROR: %s\n' "$*" >&2
    return 1
}

usage() {
    cat <<'EOF'
Usage: scripts/battery/geekworm/setup.sh [--verify] [--no-panel]

Configure native Linux telemetry for Geekworm X1202/X1206 UPS boards.

  --verify    Read-only verification after reboot
  --no-panel  Do not install or verify the Raspberry Pi taskbar patch
  -h, --help  Show this help

The setup path does not install automatic-shutdown or charging-control services.
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
    [[ "${OPENFLIGHT_GEEKWORM_ALLOW_NON_PI:-0}" == "1" ]] && return 0
    [[ -r "$PI_MODEL_PATH" ]] && grep -aq "Raspberry Pi 5" "$PI_MODEL_PATH"
}

os_codename() {
    if [[ -r /etc/os-release ]]; then
        awk -F= '$1 == "VERSION_CODENAME" { gsub(/"/, "", $2); print $2; exit }' \
            /etc/os-release
    fi
}

panel_package_supported() {
    [[ "$(dpkg --print-architecture 2>/dev/null || true)" == "arm64" ]] \
        && [[ "$(os_codename)" == "trixie" ]]
}

# An OS update can bring an upstream wfplug-batt newer than the bundled build.
# Installing ours over it would be a downgrade against an older panel ABI.
panel_upstream_is_newer() {
    local installed_version="$1"

    [[ -n "$installed_version" && "$installed_version" != *+openflight* ]] \
        && dpkg --compare-versions "$installed_version" gt "$PANEL_PACKAGE_VERSION"
}

warn_panel_rebuild_needed() {
    local installed_version="$1"

    warn "Installed wfplug-batt $installed_version is newer than the bundled $PANEL_PACKAGE_VERSION."
    warn "Rebuild it with scripts/battery/build_wfplug_batt.sh; the taskbar will show 0% until then."
    warn "OpenFlight battery telemetry will still work."
}

update_boot_config() {
    local config_path="$1"
    local conflicting_gpio
    local -a missing=()

    [[ -f "$config_path" ]] || die "Boot configuration not found: $config_path"

    conflicting_gpio="$(
        grep -E '^[[:space:]]*dtoverlay=gpio-charger([,[:space:]]|$)' "$config_path" \
            | grep -Fvx "$CHARGER_OVERLAY" || true
    )"
    if [[ -n "$conflicting_gpio" ]]; then
        die "A different gpio-charger overlay already exists: $conflicting_gpio"
    fi

    grep -Fqx "$I2C_LINE" "$config_path" || missing+=("$I2C_LINE")
    grep -Fqx "$BATTERY_OVERLAY" "$config_path" || missing+=("$BATTERY_OVERLAY")
    grep -Fqx "$CHARGER_OVERLAY" "$config_path" || missing+=("$CHARGER_OVERLAY")

    if (( ${#missing[@]} == 0 )); then
        return 0
    fi

    {
        printf '\n[all]\n'
        printf '# OpenFlight Geekworm X1202/X1206 telemetry\n'
        printf '%s\n' "${missing[@]}"
    } >> "$config_path"
}

set_key_value() {
    local config_path="$1"
    local key="$2"
    local value="$3"
    local updated

    updated="$(mktemp)"
    awk -v key="$key" -v value="$value" '
        BEGIN { found = 0 }
        $0 ~ ("^" key "=") {
            if (!found) {
                print key "=" value
                found = 1
            }
            next
        }
        { print }
        END {
            if (!found)
                print key "=" value
        }
    ' "$config_path" > "$updated"
    mv "$updated" "$config_path"
}

update_eeprom_config() {
    local config_path="$1"

    set_key_value "$config_path" "PSU_MAX_CURRENT" "5000"
    set_key_value "$config_path" "POWER_OFF_ON_HALT" "1"
}

install_required_packages() {
    local -a missing=()
    local package

    for package in i2c-tools upower; do
        dpkg-query -W "$package" >/dev/null 2>&1 || missing+=("$package")
    done

    if (( ${#missing[@]} == 0 )); then
        log "Required OS packages are already installed."
        return
    fi

    log "Installing OS packages: ${missing[*]}"
    as_root apt-get update
    as_root apt-get install -y "${missing[@]}"
}

configure_i2c_module() {
    local current=""
    local temp

    if [[ -r "$MODULES_CONFIG" ]]; then
        current="$(<"$MODULES_CONFIG")"
    fi

    if [[ "$current" == "i2c-dev" ]] \
        || { [[ "$MODULES_CONFIG" == /etc/modules-load.d/* ]] \
            && grep -RqsE '^[[:space:]]*i2c-dev([[:space:]#]|$)' \
                /etc/modules /etc/modules-load.d 2>/dev/null; }; then
        log "i2c-dev boot module is already configured."
    else
        temp="$(mktemp)"
        printf 'i2c-dev\n' > "$temp"
        as_root install -D -m 0644 "$temp" "$MODULES_CONFIG"
        rm -f "$temp"
        log "Configured i2c-dev in $MODULES_CONFIG"
    fi

    as_root modprobe i2c-dev
}

configure_boot_overlays() {
    local current
    local updated
    local backup

    [[ -r "$BOOT_CONFIG" ]] || die "Cannot read $BOOT_CONFIG"

    current="$(mktemp)"
    updated="$(mktemp)"
    as_root cp "$BOOT_CONFIG" "$current"
    cp "$current" "$updated"
    update_boot_config "$updated"

    if cmp -s "$current" "$updated"; then
        log "I2C and power-supply overlays are already configured."
    else
        backup="${BOOT_CONFIG}.pre-openflight-geekworm-${TIMESTAMP}"
        as_root cp -a "$BOOT_CONFIG" "$backup"
        as_root cp "$updated" "$BOOT_CONFIG"
        log "Updated $BOOT_CONFIG"
        log "Boot configuration backup: $backup"
        REBOOT_REQUIRED=true
    fi

    rm -f "$current" "$updated"
}

configure_eeprom() {
    local current
    local updated
    local backup

    if ! command -v rpi-eeprom-config >/dev/null 2>&1; then
        die "rpi-eeprom-config is unavailable; install current Raspberry Pi OS utilities"
    fi

    current="$(mktemp)"
    updated="$(mktemp)"
    rpi-eeprom-config > "$current"
    cp "$current" "$updated"
    update_eeprom_config "$updated"

    if cmp -s "$current" "$updated"; then
        log "Pi 5 EEPROM power settings are already configured."
    else
        backup="/boot/firmware/openflight-geekworm-eeprom-${TIMESTAMP}.conf"
        as_root install -m 0644 "$current" "$backup"
        as_root rpi-eeprom-config --apply "$updated"
        log "Scheduled Pi 5 EEPROM power settings for the next reboot."
        log "EEPROM configuration backup: $backup"
        REBOOT_REQUIRED=true
    fi

    rm -f "$current" "$updated"
}

install_panel_package() {
    local architecture
    local actual_sha
    local codename=""
    local installed_version=""

    if [[ "$NO_PANEL" == "true" ]]; then
        log "Skipping Raspberry Pi taskbar patch (--no-panel)."
        return
    fi

    architecture="$(dpkg --print-architecture)"
    if [[ "$architecture" != "arm64" ]]; then
        warn "The bundled taskbar package supports arm64 only; found $architecture."
        warn "OpenFlight battery telemetry will still work."
        return
    fi

    codename="$(os_codename)"
    if ! panel_package_supported; then
        warn "The bundled taskbar package requires arm64 Raspberry Pi OS Trixie."
        warn "Found architecture=$architecture codename=${codename:-unknown}; skipping the taskbar patch."
        warn "OpenFlight battery telemetry will still work."
        return
    fi

    [[ -f "$PANEL_PACKAGE" ]] || die "Panel package not found: $PANEL_PACKAGE"
    actual_sha="$(sha256sum "$PANEL_PACKAGE" | awk '{print $1}')"
    [[ "$actual_sha" == "$PANEL_PACKAGE_SHA256" ]] \
        || die "Panel package checksum mismatch: $actual_sha"

    installed_version="$(dpkg-query -W -f='${Version}' wfplug-batt 2>/dev/null || true)"
    if [[ "$installed_version" == "$PANEL_PACKAGE_VERSION" ]] \
        && dpkg -V wfplug-batt >/dev/null 2>&1; then
        log "Raspberry Pi taskbar patch is already installed."
        return
    fi
    if panel_upstream_is_newer "$installed_version"; then
        warn_panel_rebuild_needed "$installed_version"
        return
    fi

    log "Installing Raspberry Pi taskbar battery compatibility support."
    if ! as_root apt-get install -y "$PANEL_PACKAGE"; then
        die "apt-get could not install the bundled panel package; rerun with --no-panel"
    fi
    PANEL_RESTART_REQUIRED=true
}

find_power_supply() {
    local expected_type="$1"
    local candidate

    for candidate in "$POWER_SUPPLY_ROOT"/*; do
        [[ -d "$candidate" && -r "$candidate/type" ]] || continue
        if [[ "$(<"$candidate/type")" == "$expected_type" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

verify_installation() {
    local failures=0
    local battery=""
    local charger=""
    local capacity
    local voltage
    local online
    local installed_version=""
    local eeprom
    local line

    log "Verifying Geekworm Raspberry Pi configuration..."

    for line in "$I2C_LINE" "$BATTERY_OVERLAY" "$CHARGER_OVERLAY"; do
        if grep -Fqx "$line" "$BOOT_CONFIG" 2>/dev/null; then
            log "Boot setting present: $line"
        else
            warn "Missing boot setting: $line"
            failures=$((failures + 1))
        fi
    done

    eeprom="$(rpi-eeprom-config 2>/dev/null || true)"
    for line in "PSU_MAX_CURRENT=5000" "POWER_OFF_ON_HALT=1"; do
        if grep -Fqx "$line" <<< "$eeprom"; then
            log "EEPROM setting present: $line"
        else
            warn "Missing EEPROM setting: $line"
            failures=$((failures + 1))
        fi
    done

    if lsmod | grep -q '^max17040_battery'; then
        log "Kernel module loaded: max17040_battery"
    else
        warn "max17040_battery is not loaded; reboot may still be required."
        failures=$((failures + 1))
    fi

    if lsmod | grep -q '^gpio_charger'; then
        log "Kernel module loaded: gpio_charger"
    else
        warn "gpio_charger is not loaded; reboot may still be required."
        failures=$((failures + 1))
    fi

    battery="$(find_power_supply Battery || true)"
    if [[ -n "$battery" && -r "$battery/capacity" && -r "$battery/voltage_now" ]]; then
        capacity="$(<"$battery/capacity")"
        voltage="$(<"$battery/voltage_now")"
        log "Battery: capacity=${capacity}% voltage_now=${voltage}uV"
    else
        warn "Native Battery supply is unavailable under $POWER_SUPPLY_ROOT"
        failures=$((failures + 1))
    fi

    charger="$(find_power_supply Mains || true)"
    if [[ -n "$charger" && -r "$charger/online" ]]; then
        online="$(<"$charger/online")"
        log "External power: online=$online"
    else
        warn "Native Mains supply is unavailable under $POWER_SUPPLY_ROOT"
        failures=$((failures + 1))
    fi

    if command -v upower >/dev/null 2>&1; then
        if upower -e | grep -q '/battery_'; then
            log "UPower discovered the battery."
        else
            warn "UPower has not discovered the battery yet."
        fi
    fi

    if [[ "$NO_PANEL" != "true" ]] && panel_package_supported; then
        installed_version="$(dpkg-query -W -f='${Version}' wfplug-batt 2>/dev/null || true)"
        if [[ "$installed_version" == "$PANEL_PACKAGE_VERSION" ]] \
            && dpkg -V wfplug-batt >/dev/null 2>&1; then
            log "Taskbar package verified: wfplug-batt $installed_version"
        elif panel_upstream_is_newer "$installed_version"; then
            warn_panel_rebuild_needed "$installed_version"
            failures=$((failures + 1))
        else
            warn "Taskbar package is not the verified OpenFlight build (found: ${installed_version:-missing})."
            failures=$((failures + 1))
        fi
    elif [[ "$NO_PANEL" != "true" ]]; then
        warn "Skipping taskbar package verification on this OS/architecture."
    fi

    if command -v vcgencmd >/dev/null 2>&1; then
        log "Pi power flags: $(vcgencmd get_throttled)"
    fi

    if (( failures > 0 )); then
        die "Verification failed with $failures issue(s)."
        return 1
    fi

    log "Geekworm power telemetry is ready."
}

main() {
    while (( $# > 0 )); do
        case "$1" in
            --verify)
                VERIFY_ONLY=true
                ;;
            --no-panel)
                NO_PANEL=true
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

    is_pi_5 || die "This setup supports Raspberry Pi 5 only."

    if [[ "$VERIFY_ONLY" == "true" ]]; then
        verify_installation
        return
    fi

    if (( EUID != 0 )); then
        sudo -v
    fi

    log "Configuring X1202/X1206 native power telemetry."
    install_required_packages
    configure_i2c_module
    configure_boot_overlays
    configure_eeprom
    install_panel_package

    log "No automatic-shutdown or charging-control service was installed."
    if [[ "$REBOOT_REQUIRED" == "true" ]]; then
        log "Reboot required. After reboot, run: scripts/battery/geekworm/setup.sh --verify"
    elif [[ "$PANEL_RESTART_REQUIRED" == "true" ]]; then
        log "Restart the desktop panel or reboot to load the taskbar update."
    else
        verify_installation
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
