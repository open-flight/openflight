#!/bin/bash
#
# OpenFlight Setup Script
#
# Installs all dependencies, then (on a Raspberry Pi) walks through the
# one-time hardware configuration interactively:
#   - OPS243-A rolling buffer flash config
#   - K-LD7 device naming + FTDI low-latency rules
#   - Optional battery-provider telemetry
#   - Auto-start on boot (systemd service)
#   - Desktop shortcut
#
# Usage:
#   ./scripts/setup/setup.sh                  # full interactive setup
#   ./scripts/setup/setup.sh --deps-only      # install dependencies, skip hardware
#   ./scripts/setup/setup.sh --non-interactive # no prompts (deps only)
#
# Safe to re-run at any time.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log()  { echo -e "${GREEN}[OpenFlight]${NC} $1"; }
warn() { echo -e "${YELLOW}[OpenFlight]${NC} $1"; }
error() { echo -e "${RED}[OpenFlight]${NC} $1"; }
info() { echo -e "${BLUE}[OpenFlight]${NC} $1"; }

INTERACTIVE=true
DEPS_ONLY=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --non-interactive)
            INTERACTIVE=false
            DEPS_ONLY=true
            shift
            ;;
        --deps-only)
            DEPS_ONLY=true
            shift
            ;;
        --help|-h)
            awk 'NR>1 && !/^#/{exit} NR>1{sub(/^# ?/,""); print}' "$0"
            exit 0
            ;;
        *)
            error "Unknown option: $1 (try --help)"
            exit 1
            ;;
    esac
done

# Ask a yes/no question. Returns 0 for yes. Usage: confirm "Question?" [Y|N]
confirm() {
    local question="$1"
    local default="${2:-N}"
    local prompt suffix answer
    if [ "$default" == "Y" ]; then suffix="[Y/n]"; else suffix="[y/N]"; fi
    prompt="$(echo -e "${BLUE}[OpenFlight]${NC} ${question} ${suffix} ")"
    read -r -p "$prompt" answer
    answer="${answer:-$default}"
    [[ "$answer" =~ ^[Yy]$ ]]
}

cd "$PROJECT_DIR"

echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         OpenFlight Setup Script           ║${NC}"
echo -e "${GREEN}║     DIY Golf Launch Monitor Setup         ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════╝${NC}"
echo ""

# Detect platform
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    PLATFORM="linux"
    if grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null; then
        PLATFORM="pi"
        log "Detected Raspberry Pi"
    else
        log "Detected Linux"
    fi
elif [[ "$OSTYPE" == "darwin"* ]]; then
    PLATFORM="macos"
    log "Detected macOS"
else
    PLATFORM="unknown"
    warn "Unknown platform: $OSTYPE"
fi

# ──────────────────────────────────────────────────────────────────────
# Phase 1: Dependencies
# ──────────────────────────────────────────────────────────────────────

# Camera shot replay converts frames to H.264 only after the user selects
# Replay. FFmpeg is a system binary, so it cannot be managed by uv.
if [[ "$PLATFORM" == "pi" ]]; then
    if command -v ffmpeg &> /dev/null; then
        log "FFmpeg found ✓"
    else
        log "Installing FFmpeg for camera shot replay..."
        sudo apt-get update
        sudo apt-get install -y ffmpeg
        log "FFmpeg installed ✓"
    fi
elif ! command -v ffmpeg &> /dev/null; then
    warn "FFmpeg not found. It is required only for on-demand camera shot replay."
fi

# Check for Python 3.9+
log "Checking Python version..."
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
    PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

    if [ "$PYTHON_MAJOR" -ge 3 ] && [ "$PYTHON_MINOR" -ge 9 ]; then
        log "Python $PYTHON_VERSION found ✓"
    else
        error "Python 3.9+ required, found $PYTHON_VERSION"
        exit 1
    fi
else
    error "Python 3 not found. Please install Python 3.9+"
    exit 1
fi

# Check for Node.js
log "Checking Node.js..."
if command -v node &> /dev/null; then
    NODE_VERSION=$(node --version)
    log "Node.js $NODE_VERSION found ✓"
else
    error "Node.js not found. Please install Node.js 18+"
    if [ "$PLATFORM" == "pi" ]; then
        info "On Raspberry Pi, run: sudo apt install nodejs npm"
    elif [ "$PLATFORM" == "macos" ]; then
        info "On macOS, run: brew install node"
    fi
    exit 1
fi

# Check for npm
if ! command -v npm &> /dev/null; then
    error "npm not found. Please install npm"
    exit 1
fi

# Install uv if not present (for faster pip installs)
if ! command -v uv &> /dev/null; then
    log "Installing uv (fast Python package manager)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Source the new path
    export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
fi

# Create virtual environment
if [ ! -d .venv ]; then
    log "Creating Python virtual environment..."
    python3 -m venv .venv
    log "Created venv"
fi

# Activate venv
source .venv/bin/activate
log "Activated virtual environment"

# Install Python dependencies
log "Installing Python dependencies..."
if command -v uv &> /dev/null; then
    uv pip install -e ".[ui,analysis]"
else
    pip install -e ".[ui,analysis]"
fi
# Camera dependencies are disabled for the radar-only production path.
# If camera support returns, re-enable the optional camera extra in
# pyproject.toml and restore installation here.
log "Python dependencies installed ✓"

# Install dev dependencies
log "Installing development dependencies..."
if command -v uv &> /dev/null; then
    uv pip install pytest ruff pylint
else
    pip install pytest ruff pylint
fi
log "Dev dependencies installed ✓"

# Install Node.js dependencies and build UI
log "Installing Node.js dependencies..."
cd ui
npm install
log "Node.js dependencies installed ✓"

log "Building UI..."
npm run build
log "UI built ✓"
cd ..

# Make scripts executable
chmod +x scripts/*.sh scripts/setup/*.sh scripts/battery/*/*.sh

# Run tests to verify installation
log "Running tests to verify installation..."
if python -m pytest tests/ -q --tb=no; then
    log "All tests passed ✓"
else
    warn "Some tests failed - installation may be incomplete"
fi

# ──────────────────────────────────────────────────────────────────────
# Phase 2: Hardware configuration (Raspberry Pi, interactive)
# ──────────────────────────────────────────────────────────────────────

if [ "$PLATFORM" == "pi" ] && [ "$DEPS_ONLY" == "false" ] && [ "$INTERACTIVE" == "true" ]; then
    echo ""
    echo -e "${GREEN}=== Hardware Setup ===${NC}"
    echo ""
    info "Dependencies are installed. The next steps configure your hardware."
    info "You can skip any step and re-run this script later."

    # --- OPS243-A rolling buffer flash config ---
    echo ""
    if confirm "Configure the OPS243-A radar now? (it must be plugged in)" "Y"; then
        log "Saving rolling buffer mode to the radar's flash memory..."
        if python scripts/hardware-test/test_rolling_buffer_persist.py --setup; then
            echo ""
            info "Now power cycle the radar: unplug its USB cable, wait 3 seconds,"
            info "and plug it back in. (This works around a firmware bug — one time only.)"
            read -r -p "Press Enter after plugging it back in... " _
            sleep 2
            log "Verifying (make a sharp sound near the sound detector when asked)..."
            if python scripts/hardware-test/test_rolling_buffer_persist.py --test; then
                log "OPS243-A configured ✓"
            else
                warn "Verification failed. See docs/raspberry-pi-setup.md → Radar Setup."
            fi
        else
            warn "Radar configuration failed — is the OPS243-A plugged in?"
            warn "You can re-run this script, or see docs/raspberry-pi-setup.md."
        fi
    else
        info "Skipped. Run later with:"
        info "    uv run python scripts/hardware-test/test_rolling_buffer_persist.py --setup"
    fi

    # --- K-LD7 device naming + latency ---
    echo ""
    if confirm "Do you have K-LD7 angle radars to set up?" "N"; then
        "$SCRIPT_DIR/setup_kld7_devices.sh"
    else
        info "Skipped. Run later with: ./scripts/setup/setup_kld7_devices.sh"
    fi

    # --- Geekworm X1202/X1206 power telemetry ---
    echo ""
    if confirm "Do you have a Geekworm X1202 or X1206 UPS to set up?" "N"; then
        "$PROJECT_DIR/scripts/battery/geekworm/setup.sh"
        info "Reboot before verifying Geekworm telemetry with:"
        info "    ./scripts/battery/geekworm/setup.sh --verify"
    else
        info "Skipped. Run later with: ./scripts/battery/geekworm/setup.sh"
    fi

    # --- Auto-start service ---
    echo ""
    if confirm "Start OpenFlight automatically on boot?" "N"; then
        log "Installing systemd service for user '$USER'..."
        sed -e "s|^User=.*|User=$USER|" \
            -e "s|/home/coleman/openflight|$PROJECT_DIR|g" \
            "$SCRIPT_DIR/openflight.service" | sudo tee /etc/systemd/system/openflight.service > /dev/null
        sudo systemctl daemon-reload
        sudo systemctl enable openflight
        log "Service installed and enabled ✓ (starts on next boot)"
        info "Manage it with: sudo systemctl {start|stop|status} openflight"
    else
        info "Skipped. See docs/raspberry-pi-setup.md → Auto-Start on Boot."
    fi

    # --- Desktop shortcut ---
    echo ""
    if confirm "Add an OpenFlight shortcut to the desktop?" "N"; then
        "$SCRIPT_DIR/install_desktop_launcher.sh"
        log "Desktop shortcut setup complete ✓"
    fi

    # --- Cloud sync (opt-in) ---
    echo ""
    info "Cloud sync pushes *filtered* session logs (shots, not raw radar) to"
    info "FlightWeb so you can review them from anywhere. It is opt-in: nothing"
    info "leaves this Pi until you link it to your account."
    echo ""
    if confirm "Enable cloud sync for this Pi?" "N"; then
        log "Installing the cloud uploader timer (runs every ~10 min)..."
        for unit in openflight-cloud.service openflight-cloud.timer; do
            sed -e "s|^User=.*|User=$USER|" \
                -e "s|/home/coleman/openflight|$PROJECT_DIR|g" \
                "$SCRIPT_DIR/$unit" | sudo tee "/etc/systemd/system/$unit" > /dev/null
        done
        sudo systemctl daemon-reload
        sudo systemctl enable --now openflight-cloud.timer
        log "Cloud uploader timer installed and enabled ✓"

        echo ""
        if confirm "Link this Pi to your FlightWeb account now?" "Y"; then
            log "Starting device linking..."
            echo ""
            # `openflight-cloud` is on PATH via the activated venv. The command
            # prints a short code; enter it in your browser when prompted.
            if openflight-cloud link; then
                log "Pi linked ✓ — sessions will sync automatically from now on."
            else
                warn "Linking did not complete. Re-run any time with:"
                warn "    openflight-cloud link"
            fi
        else
            info "Skipped. Link later (the timer keeps running, but uploads stay"
            info "off until you do) with:"
            info "    openflight-cloud link"
        fi
        info "Check sync state any time with: openflight-cloud status"
    else
        info "Skipped. Enable later by re-running this script, or see"
        info "    docs/cloud-sync.md"
    fi
elif [ "$PLATFORM" == "pi" ]; then
    info "Skipping hardware setup ($([ "$INTERACTIVE" == "false" ] && echo "non-interactive" || echo "--deps-only"))."
    info "Run ./scripts/setup/setup.sh again without flags to configure hardware."
fi

# ──────────────────────────────────────────────────────────────────────
# Done
# ──────────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         Setup Complete! 🎉                ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════╝${NC}"
echo ""
log "Start OpenFlight:"
echo "    ./scripts/start-kiosk.sh                # Default: rolling buffer + sound trigger"
echo "    ./scripts/start-kiosk.sh --kld7         # With K-LD7 angle radars"
echo "    ./scripts/start-kiosk.sh --battery geekworm  # With Geekworm battery display"
echo "    ./scripts/start-kiosk.sh --mock         # Mock mode (no hardware)"
echo ""
log "Then open http://localhost:8080 (or use the touchscreen)."
echo ""
log "Cloud sync (optional):"
echo "    openflight-cloud link                   # pair this Pi with FlightWeb"
echo "    openflight-cloud status                 # linked? queued? parked?"
echo "    openflight-cloud push --dry-run         # see exactly what would upload"
echo ""
log "For details and troubleshooting, see docs/raspberry-pi-setup.md"
log "For cloud sync details, see docs/cloud-sync.md"
echo ""
