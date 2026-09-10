# Sourced by start-kiosk.sh. Build a missing UI, but do not fail startup when
# Electron cannot be installed — Chromium remains the kiosk fallback.

_ensure_kiosk_ui_build() {
    # shellcheck source=require-node.sh
    source "$SCRIPT_DIR/require-node.sh"
    if ! openflight_node_meets_min; then
        openflight_node_install_hint
        show_startup_failure \
            "server" \
            "Node.js is too old to build the UI" \
            "OpenFlight needs Node.js ${OPENFLIGHT_MIN_NODE} or newer (found $(openflight_node_version 2>/dev/null || echo none)). Upgrade Node, then relaunch."
    fi
    if ! (cd "$PROJECT_DIR/ui" && npm install && npm run build); then
        show_startup_failure \
            "server" \
            "OpenFlight interface build failed" \
            "Check the terminal log or network connection, then relaunch OpenFlight."
    fi
}

_try_install_electron_shell() {
    warn "Electron kiosk shell missing. Attempting install..."
    # shellcheck source=require-node.sh
    source "$SCRIPT_DIR/require-node.sh"
    if ! openflight_node_meets_min; then
        warn "Node.js is too old to install Electron (need ${OPENFLIGHT_MIN_NODE}+, found $(openflight_node_version 2>/dev/null || echo none)); falling back to Chromium."
        return 0
    fi
    if ! (cd "$PROJECT_DIR/ui" && npm install); then
        warn "Could not install Electron; falling back to Chromium if available."
        return 0
    fi
    if [ ! -x "$PROJECT_DIR/ui/node_modules/.bin/electron" ]; then
        warn "Electron is still missing after npm install; falling back to Chromium if available."
    fi
}

ensure_kiosk_ui() {
    PROJECT_DIR="${PROJECT_DIR//$'\r'/}"
    SCRIPT_DIR="${SCRIPT_DIR//$'\r'/}"
    local dist_dir="$PROJECT_DIR/ui/dist"
    local electron_bin="$PROJECT_DIR/ui/node_modules/.bin/electron"

    if [ ! -d "$dist_dir" ]; then
        warn "UI not built. Building now..."
        _ensure_kiosk_ui_build
        return
    fi

    if [ -x "$electron_bin" ]; then
        return 0
    fi

    _try_install_electron_shell
}
