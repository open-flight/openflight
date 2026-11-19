#!/bin/bash
#
# Interactive K-LD7 USB adapter naming wizard.
#
# DEPRECATED: the K-LD7 angle radars are deprecated (superseded by a more
# capable radar chip). This script is kept for existing builds only.
#
# Identifies each K-LD7's FTDI adapter by plug-in order — no serial numbers
# to look up, no udev rules to edit by hand. Writes a udev rule so the
# radars always appear at /dev/kld7_vertical and /dev/kld7_horizontal no
# matter which USB port they use or what order they enumerate in, then
# installs the FTDI low-latency rule.
#
# Usage:
#   scripts/setup/setup_kld7_devices.sh            # interactive wizard
#   scripts/setup/setup_kld7_devices.sh --show     # show current mapping
#
# Re-run any time to redo the mapping (e.g. after replacing an adapter).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RULE_FILE="/etc/udev/rules.d/99-kld7.rules"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[K-LD7 Setup]${NC} $1"; }
warn() { echo -e "${YELLOW}[K-LD7 Setup]${NC} $1"; }
err()  { echo -e "${RED}[K-LD7 Setup]${NC} $1"; }
ask()  { echo -e "${BLUE}[K-LD7 Setup]${NC} $1"; }

show_mapping() {
    echo ""
    if [ -e /dev/kld7_vertical ] || [ -e /dev/kld7_horizontal ]; then
        log "Current K-LD7 device names:"
        for name in kld7_vertical kld7_horizontal; do
            if [ -e "/dev/$name" ]; then
                echo "    /dev/$name -> $(readlink -f "/dev/$name")"
            else
                echo "    /dev/$name -> (radar not connected or not mapped)"
            fi
        done
    else
        warn "No /dev/kld7_* device names found."
    fi
    if [ -f "$RULE_FILE" ]; then
        echo ""
        log "Installed rule ($RULE_FILE):"
        sed 's/^/    /' "$RULE_FILE"
    fi
}

if [ "${1:-}" == "--show" ]; then
    show_mapping
    exit 0
fi

if ! command -v udevadm &> /dev/null; then
    err "udevadm not found — this wizard only runs on Linux (Raspberry Pi)."
    exit 1
fi

list_ttyusb() {
    ls /dev/ttyUSB* 2>/dev/null || true
}

# Wait for exactly one new /dev/ttyUSB* device to appear vs a snapshot.
# Prints the new device path.
wait_for_new_device() {
    local before="$1"
    local waited=0
    while [ "$waited" -lt 90 ]; do
        sleep 1
        waited=$((waited + 1))
        local now new
        now="$(list_ttyusb)"
        new="$(comm -13 <(echo "$before" | sort) <(echo "$now" | sort) | head -1)"
        if [ -n "$new" ]; then
            # Give udev a moment to finish setting the device up
            sleep 2
            echo "$new"
            return 0
        fi
    done
    return 1
}

device_serial() {
    udevadm info -q property -n "$1" 2>/dev/null | sed -n 's/^ID_SERIAL_SHORT=//p'
}

device_usb_path() {
    udevadm info -q property -n "$1" 2>/dev/null | sed -n 's/^ID_PATH=//p'
}

identify_adapter() {
    # $1 = orientation label. Asks the user to plug that radar in and
    # prints "serial|usb_path" for the adapter that appears.
    local label="$1"
    local before
    before="$(list_ttyusb)"
    echo "" >&2
    ask "Plug in the ${label} K-LD7's USB cable now (waiting up to 90s)..." >&2
    local dev
    if ! dev="$(wait_for_new_device "$before")"; then
        err "No new USB serial device appeared. Check the cable and re-run the wizard." >&2
        exit 1
    fi
    log "Found ${label} adapter at $dev" >&2
    echo "$(device_serial "$dev")|$(device_usb_path "$dev")"
}

echo ""
echo -e "${GREEN}=== K-LD7 Device Naming Wizard ===${NC}"
echo ""
echo "This identifies which USB adapter belongs to which radar so OpenFlight"
echo "always knows which K-LD7 is which. You'll unplug both adapters, then"
echo "plug them back in one at a time when asked."
echo ""

if [ -f "$RULE_FILE" ]; then
    show_mapping
    echo ""
    read -r -p "A mapping already exists. Redo it? [y/N] " redo
    if [[ ! "$redo" =~ ^[Yy]$ ]]; then
        log "Keeping the existing mapping."
        exit 0
    fi
fi

read -r -p "How many K-LD7 radars do you have? [2/1] " count
count="${count:-2}"
if [[ "$count" != "1" && "$count" != "2" ]]; then
    err "Please answer 1 or 2."
    exit 1
fi

echo ""
ask "Unplug BOTH K-LD7 USB cables from the Pi (leave the OPS243 connected)."
read -r -p "Press Enter when both are unplugged... " _

VERT_INFO="$(identify_adapter "VERTICAL (launch angle — mounted upright)")"
VERT_SERIAL="${VERT_INFO%%|*}"
VERT_PATH="${VERT_INFO##*|}"

HORIZ_SERIAL=""
HORIZ_PATH=""
if [ "$count" == "2" ]; then
    HORIZ_INFO="$(identify_adapter "HORIZONTAL (club path — mounted flat)")"
    HORIZ_SERIAL="${HORIZ_INFO%%|*}"
    HORIZ_PATH="${HORIZ_INFO##*|}"
fi

# Build the rule. Prefer the adapter's unique serial number; fall back to
# the physical USB port path for cheap clones with no serial (in that case
# the cables must stay in the same USB ports).
RULE_LINES=()
USED_PATH_FALLBACK=false

rule_for() {
    local serial="$1" path="$2" name="$3"
    if [ -n "$serial" ]; then
        echo "SUBSYSTEM==\"tty\", ATTRS{serial}==\"$serial\", SYMLINK+=\"$name\""
    else
        USED_PATH_FALLBACK=true
        echo "SUBSYSTEM==\"tty\", ENV{ID_PATH}==\"$path\", SYMLINK+=\"$name\""
    fi
}

if [ "$count" == "2" ] && [ -n "$VERT_SERIAL" ] && [ "$VERT_SERIAL" == "$HORIZ_SERIAL" ]; then
    warn "Both adapters report the same serial number — falling back to USB port position."
    VERT_SERIAL=""
    HORIZ_SERIAL=""
fi

RULE_LINES+=("$(rule_for "$VERT_SERIAL" "$VERT_PATH" "kld7_vertical")")
if [ "$count" == "2" ]; then
    RULE_LINES+=("$(rule_for "$HORIZ_SERIAL" "$HORIZ_PATH" "kld7_horizontal")")
fi

echo ""
log "Writing $RULE_FILE (you may be asked for your password):"
printf '    %s\n' "${RULE_LINES[@]}"
printf '%s\n' "${RULE_LINES[@]}" | sudo tee "$RULE_FILE" > /dev/null
sudo udevadm control --reload-rules
sudo udevadm trigger
sleep 2

echo ""
ok=true
if [ ! -e /dev/kld7_vertical ]; then
    ok=false
fi
if [ "$count" == "2" ] && [ ! -e /dev/kld7_horizontal ]; then
    ok=false
fi

if [ "$ok" == "true" ]; then
    log "Device names verified ✓"
    show_mapping
else
    err "Device names did not appear. Re-run the wizard, or see the manual"
    err "steps in docs/setup/raspberry-pi.md (K-LD7 Device Names)."
    exit 1
fi

if [ "$USED_PATH_FALLBACK" == "true" ]; then
    echo ""
    warn "Your adapters have no unique serial number, so the mapping is tied"
    warn "to the physical USB ports. Keep each cable in the port it's in now."
fi

# FTDI low-latency rule (the 3 Mbaud RADC stream needs latency_timer=1ms)
echo ""
log "Installing the FTDI low-latency rule..."
sudo "$SCRIPT_DIR/setup_kld7_latency.sh"

echo ""
log "K-LD7 setup complete. The radars are now at:"
echo "    /dev/kld7_vertical$([ "$count" == "2" ] && echo " and /dev/kld7_horizontal")"
