# Sourced by setup and kiosk scripts. Electron 44's npm installer requires
# Node 22.12+ (see ui/package.json engines and electron's own engines field).
OPENFLIGHT_MIN_NODE="22.12.0"

openflight_node_version() {
    command -v node >/dev/null 2>&1 || return 1
    local v
    v="$(node -v 2>/dev/null || true)"
    v="${v#v}"
    printf '%s' "${v%%[-+]*}"
}

openflight_node_meets_min() {
    local current cmaj cmin cpat mmaj mmin mpat
    current="$(openflight_node_version)" || return 1
    [ -n "$current" ] || return 1

    IFS=. read -r cmaj cmin cpat <<<"$current"
    IFS=. read -r mmaj mmin mpat <<<"$OPENFLIGHT_MIN_NODE"

    cmaj=${cmaj:-0}; cmin=${cmin:-0}; cpat=${cpat:-0}
    mmaj=${mmaj:-0}; mmin=${mmin:-0}; mpat=${mpat:-0}

    if (( cmaj > mmaj )); then return 0; fi
    if (( cmaj < mmaj )); then return 1; fi
    if (( cmin > mmin )); then return 0; fi
    if (( cmin < mmin )); then return 1; fi
    (( cpat >= mpat ))
}

openflight_node_install_hint() {
    cat <<'EOF'
OpenFlight needs Node.js 22.12 or newer to install the Electron kiosk shell.
Raspberry Pi OS / Debian apt Node is often older than that (Node 18 or 20).

Raspberry Pi (64-bit):
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt-get install -y nodejs

macOS:
  brew install node

Then confirm with: node -v
EOF
}
