#!/usr/bin/env bash
# Rebuild Raspberry Pi's wfplug-batt taskbar plugin with OpenFlight's
# capacity/online patch. Run on an arm64 Raspberry Pi OS Trixie Pi.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH="$SCRIPT_DIR/patches/wfplug-batt-capacity-and-power.patch"
PACKAGES_DIR="$SCRIPT_DIR/packages"
UPSTREAM_URL="https://github.com/raspberrypi-ui/pplug-batt.git"
# pplug-batt 1.11 ("Add tooltip for widget browser"). Upstream has no tags.
UPSTREAM_COMMIT="c622de2e95bf8ee5d4a0e28f3fdd5e3191bbdbbd"
UPSTREAM_VERSION="1.11"
OPENFLIGHT_REVISION="1"
PACKAGE_VERSION="${UPSTREAM_VERSION}+openflight${OPENFLIGHT_REVISION}"

BUILD_DEPENDS=(
    git dpkg-dev debhelper meson
    libgtk-3-dev libgtkmm-3.0-dev lxpanel-pi-dev wf-panel-pi-dev
    libgtk-layer-shell-dev libglm-dev
)

INSTALL_DEPS=false
KEEP_BUILD=false

log() {
    printf '[wfplug-batt] %s\n' "$*"
}

die() {
    printf '[wfplug-batt] ERROR: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<EOF
Usage: scripts/battery/build_wfplug_batt.sh [--install-deps] [--keep-build]

Build wfplug-batt ${PACKAGE_VERSION} from pplug-batt ${UPSTREAM_VERSION}
(${UPSTREAM_COMMIT:0:7}) plus patches/wfplug-batt-capacity-and-power.patch,
and copy the .deb into scripts/battery/packages/.

  --install-deps  apt-get install the build dependencies first (uses sudo)
  --keep-build    Keep the temporary build directory
  -h, --help      Show this help

After building, update PANEL_PACKAGE, PANEL_PACKAGE_VERSION and
PANEL_PACKAGE_SHA256 in scripts/battery/geekworm/setup.sh.
EOF
}

while (( $# > 0 )); do
    case "$1" in
        --install-deps) INSTALL_DEPS=true ;;
        --keep-build) KEEP_BUILD=true ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "Unknown option: $1" ;;
    esac
    shift
done

[[ "$(dpkg --print-architecture)" == "arm64" ]] || die "Build on an arm64 Raspberry Pi OS Pi."
[[ -f "$PATCH" ]] || die "Patch not found: $PATCH"

if [[ "$INSTALL_DEPS" == "true" ]]; then
    log "Installing build dependencies: ${BUILD_DEPENDS[*]}"
    sudo apt-get update
    sudo apt-get install -y --no-install-recommends "${BUILD_DEPENDS[@]}"
fi

BUILD_DIR="$(mktemp -d)"
if [[ "$KEEP_BUILD" == "true" ]]; then
    log "Build directory: $BUILD_DIR"
else
    trap 'rm -rf "$BUILD_DIR"' EXIT
fi

log "Fetching pplug-batt ${UPSTREAM_COMMIT:0:7}"
git -C "$BUILD_DIR" init -q pplug-batt
SOURCE_DIR="$BUILD_DIR/pplug-batt"
git -C "$SOURCE_DIR" fetch -q --depth 1 "$UPSTREAM_URL" "$UPSTREAM_COMMIT"
git -C "$SOURCE_DIR" checkout -q FETCH_HEAD

upstream_changelog_version="$(dpkg-parsechangelog -l "$SOURCE_DIR/debian/changelog" -S Version)"
[[ "$upstream_changelog_version" == "$UPSTREAM_VERSION" ]] \
    || die "Expected upstream version $UPSTREAM_VERSION, found $upstream_changelog_version"

log "Applying $(basename "$PATCH")"
git -C "$SOURCE_DIR" apply "$PATCH"

changelog="$SOURCE_DIR/debian/changelog"
{
    printf 'pplug-batt (%s) trixie; urgency=medium\n\n' "$PACKAGE_VERSION"
    printf '  * OpenFlight: read the power supply capacity percentage and the\n'
    printf '    external supply online state (MAX17040 UPS boards).\n\n'
    printf ' -- OpenFlight <noreply@github.com>  %s\n\n' "$(date -R)"
    cat "$changelog"
} > "$changelog.new"
mv "$changelog.new" "$changelog"

log "Building ${PACKAGE_VERSION}"
(cd "$SOURCE_DIR" && dpkg-buildpackage -b -us -uc)

deb="$BUILD_DIR/wfplug-batt_${PACKAGE_VERSION}_arm64.deb"
[[ -f "$deb" ]] || die "Build did not produce $(basename "$deb")"
mkdir -p "$PACKAGES_DIR"
cp "$deb" "$PACKAGES_DIR/"

log "Built $PACKAGES_DIR/$(basename "$deb")"
log "PANEL_PACKAGE_VERSION=\"$PACKAGE_VERSION\""
log "PANEL_PACKAGE_SHA256=\"$(sha256sum "$deb" | awk '{print $1}')\""
