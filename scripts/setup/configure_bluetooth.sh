#!/bin/bash
#
# Configure BlueZ for OpenFlight's BLE phone connection.
#
# OpenFlight is a BLE peripheral with unauthenticated characteristics and
# never needs pairing. By default bluetoothd also acts as a GATT *client*
# toward every phone that connects: it reads the iPhone's own GATT database,
# the iPhone answers "Insufficient Authentication", and BlueZ responds with an
# SMP Security Request. iOS shows a pairing popup, no agent on the headless Pi
# confirms it, and after the 30 s SMP timeout BlueZ drops the link with
# "Authentication Failure". The phone reconnects and the loop repeats.
#
# Setting `Client = false` under [GATT] in /etc/bluetooth/main.conf stops that
# probing. Note: stock main.conf documents `#Client = true` under [CSIS], but
# bluetoothd reads the key from [GATT], so it is written there.
#
# Usage:
#   scripts/setup/configure_bluetooth.sh           # apply and restart bluetooth
#   scripts/setup/configure_bluetooth.sh --check   # report only, change nothing
#
# Idempotent: re-running makes no change once the setting is in place.

set -euo pipefail

CONF="${BLUEZ_MAIN_CONF:-/etc/bluetooth/main.conf}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[Bluetooth Setup]${NC} $1"; }
warn() { echo -e "${YELLOW}[Bluetooth Setup]${NC} $1"; }
err()  { echo -e "${RED}[Bluetooth Setup]${NC} $1"; }

CHECK_ONLY=false
case "${1:-}" in
    --check) CHECK_ONLY=true ;;
    "") ;;
    --help|-h)
        awk 'NR>1 && !/^#/{exit} NR>1{sub(/^# ?/,""); print}' "$0"
        exit 0
        ;;
    *) err "Unknown option: $1 (try --help)"; exit 1 ;;
esac

if [ ! -f "$CONF" ]; then
    err "$CONF not found — is BlueZ installed?"
    exit 1
fi

# Print $CONF with [GATT] Client set to false. Replaces any existing
# (commented or not) Client line inside [GATT], inserts one at the end of
# the section if missing, and appends a [GATT] section if there is none.
# Lines outside [GATT] (including the misplaced #Client under [CSIS]) are
# left untouched.
render_conf() {
    awk '
        /^[[:space:]]*\[/ {
            if (in_gatt && !done) { print "Client = false"; print ""; done = 1 }
            in_gatt = ($0 ~ /^[[:space:]]*\[GATT\][[:space:]]*$/)
            print
            next
        }
        in_gatt && /^[[:space:]#]*Client[[:space:]]*=/ {
            if (!done) { print "Client = false"; done = 1 }
            next
        }
        { print }
        END {
            if (!done) {
                if (!in_gatt) { print ""; print "[GATT]" }
                print "Client = false"
            }
        }
    ' "$CONF"
}

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
render_conf > "$TMP"

if cmp -s "$TMP" "$CONF"; then
    log "[GATT] Client = false already set in $CONF ✓"
    exit 0
fi

if [ "$CHECK_ONLY" == "true" ]; then
    warn "$CONF does not set [GATT] Client = false."
    warn "iPhones may show a pairing prompt every ~30 s. Fix with:"
    warn "    ./scripts/setup/configure_bluetooth.sh"
    exit 1
fi

BACKUP="$CONF.openflight-$(date +%Y%m%d%H%M%S).bak"
sudo cp -p "$CONF" "$BACKUP"
log "Backed up $CONF to $BACKUP"

sudo install -m 644 "$TMP" "$CONF"
log "Set [GATT] Client = false in $CONF ✓"

if command -v systemctl &> /dev/null; then
    log "Restarting bluetooth (connected phones will disconnect)..."
    sudo systemctl restart bluetooth
    log "Bluetooth restarted ✓"
    warn "Restart OpenFlight so its BLE server re-registers with bluetoothd."
    if systemctl is-active --quiet openflight 2>/dev/null; then
        warn "    sudo systemctl restart openflight"
    else
        warn "    (stop and re-run ./scripts/start-kiosk.sh --ble)"
    fi
else
    warn "Restart bluetoothd for the change to take effect."
fi
