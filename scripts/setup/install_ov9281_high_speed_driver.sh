#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
PATCH_FILE="$REPO_ROOT/drivers/ov9281/ov9282-high-speed.patch"
KERNEL_RELEASE="$(uname -r)"
KERNEL_VERSION="${KERNEL_RELEASE%%+*}"
KERNEL_SERIES="$(cut -d. -f1,2 <<<"$KERNEL_VERSION")"
SOURCE_PACKAGE="linux-source-$KERNEL_SERIES"
WORK_ROOT="${OPENFLIGHT_KERNEL_WORKDIR:-$HOME/.cache/openflight-kernel/$KERNEL_RELEASE}"
KERNEL_BUILD="/lib/modules/$KERNEL_RELEASE/build"
MODULE_ROOT="$WORK_ROOT/module-root"
MODULE_DIR="$MODULE_ROOT/drivers/media/i2c"
JOBS="${OPENFLIGHT_KERNEL_JOBS:-$(nproc)}"
RESTORE=false

usage() {
    cat <<EOF
Usage: $(basename "$0") [--restore]

Build and install OpenFlight's OV9281/OV9282 high-speed camera driver for the
currently running Raspberry Pi kernel. The first build downloads the matching
stock driver source under:

  $WORK_ROOT

Environment overrides:
  OPENFLIGHT_KERNEL_WORKDIR  Build workspace root
  OPENFLIGHT_KERNEL_JOBS     Parallel build jobs (default: $JOBS)

Options:
  --restore  Restore the stock module backup for the running kernel
  -h, --help Show this help
EOF
}

while (($#)); do
    case "$1" in
        --restore)
            RESTORE=true
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

if [[ "$(uname -s)" != "Linux" ]] || [[ ! -r /proc/device-tree/model ]]; then
    echo "This installer must run on the Raspberry Pi that uses the camera." >&2
    exit 1
fi

if ! grep -qi "raspberry pi" /proc/device-tree/model; then
    echo "Unsupported host: $(tr -d '\0' </proc/device-tree/model)" >&2
    exit 1
fi

if [[ ! -f "$PATCH_FILE" ]]; then
    echo "Missing driver patch: $PATCH_FILE" >&2
    exit 1
fi

MODINFO_BIN="$(command -v modinfo || true)"
if [[ -z "$MODINFO_BIN" && -x /sbin/modinfo ]]; then
    MODINFO_BIN=/sbin/modinfo
fi
if [[ -z "$MODINFO_BIN" ]]; then
    echo "modinfo is required but was not found." >&2
    exit 1
fi

module_path="$($MODINFO_BIN -n ov9282 2>/dev/null || true)"
if [[ -z "$module_path" || ! -f "$module_path" ]]; then
    echo "Could not locate the installed ov9282 module for $KERNEL_RELEASE." >&2
    exit 1
fi
backup_path="${module_path}.openflight-stock-${KERNEL_RELEASE}"

if $RESTORE; then
    if [[ ! -f "$backup_path" ]]; then
        echo "No stock module backup found at $backup_path" >&2
        exit 1
    fi
    echo "Restoring stock OV9281/OV9282 driver for $KERNEL_RELEASE..."
    sudo cp --preserve=mode,timestamps "$backup_path" "$module_path"
    sudo depmod -a "$KERNEL_RELEASE"
    echo "Stock driver restored. Reboot the Pi before using the camera."
    exit 0
fi

echo "OpenFlight OV9281 high-speed driver"
echo "  Pi:      $(tr -d '\0' </proc/device-tree/model)"
echo "  Kernel:  $KERNEL_RELEASE"
echo "  Headers: $KERNEL_BUILD"
echo "  Module:  $module_path"

sudo apt-get update
sudo apt-get install -y \
    build-essential git patch xz-utils

if [[ ! -f "$KERNEL_BUILD/Makefile" || ! -f "$KERNEL_BUILD/Module.symvers" ]]; then
    echo "Installing headers for $KERNEL_RELEASE..."
    sudo apt-get install -y "linux-headers-$KERNEL_RELEASE"
fi
if [[ ! -f "$KERNEL_BUILD/Makefile" || ! -f "$KERNEL_BUILD/Module.symvers" ]]; then
    echo "Matching kernel headers are unavailable at $KERNEL_BUILD." >&2
    exit 1
fi

prepare_driver_source() {
    local package_version
    local package_deb
    local extracted_tar
    local archive_member

    mkdir -p "$WORK_ROOT/download"
    package_version="$(
        apt-cache madison "$SOURCE_PACKAGE" |
            awk -F'|' -v version="$KERNEL_VERSION" '
                index($2, version) && index($2, "rpt") {
                    gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2)
                    print $2
                    exit
                }
            '
    )"
    if [[ -z "$package_version" ]]; then
        echo "No Raspberry Pi $SOURCE_PACKAGE package matches kernel $KERNEL_VERSION." >&2
        echo "Update apt metadata or provide OPENFLIGHT_KERNEL_SOURCE." >&2
        exit 1
    fi

    echo "Downloading $SOURCE_PACKAGE=$package_version..."
    (
        cd "$WORK_ROOT/download"
        apt-get download "$SOURCE_PACKAGE=$package_version"
    )
    package_deb="$(find "$WORK_ROOT/download" -maxdepth 1 -name "${SOURCE_PACKAGE}_*.deb" -print -quit)"
    if [[ -z "$package_deb" ]]; then
        echo "Downloaded source package was not found." >&2
        exit 1
    fi

    rm -rf "$WORK_ROOT/package" "$MODULE_ROOT"
    mkdir -p "$WORK_ROOT/package"
    dpkg-deb -x "$package_deb" "$WORK_ROOT/package"
    extracted_tar="$(find "$WORK_ROOT/package/usr/src" -maxdepth 1 -name 'linux-source-*.tar.*' -print -quit)"
    if [[ -z "$extracted_tar" ]]; then
        echo "Kernel source archive was not found in $package_deb." >&2
        exit 1
    fi

    archive_member="$(
        tar -tf "$extracted_tar" |
            awk '/\/drivers\/media\/i2c\/ov9282\.c$/ && !member { member=$0 } END { print member }'
    )"
    if [[ -z "$archive_member" ]]; then
        echo "ov9282.c was not found in $extracted_tar." >&2
        exit 1
    fi

    mkdir -p "$MODULE_DIR"
    tar -xOf "$extracted_tar" "$archive_member" >"$MODULE_DIR/ov9282.c"
    cat >"$MODULE_DIR/Makefile" <<'EOF'
obj-m += ov9282.o
EOF
}

prepare_driver_source
if ! (cd "$MODULE_ROOT" && git apply --check --recount "$PATCH_FILE"); then
    echo "The driver patch does not match this kernel source tree." >&2
    exit 1
fi
(cd "$MODULE_ROOT" && git apply --recount "$PATCH_FILE")

make -C "$KERNEL_BUILD" -j"$JOBS" M="$MODULE_DIR" modules
built_module="$MODULE_DIR/ov9282.ko"
if [[ ! -f "$built_module" ]]; then
    echo "Build completed without producing $built_module." >&2
    exit 1
fi

built_release="$($MODINFO_BIN -F vermagic "$built_module" | awk '{print $1}')"
if [[ "$built_release" != "$KERNEL_RELEASE" ]]; then
    echo "Built module targets $built_release, expected $KERNEL_RELEASE." >&2
    exit 1
fi

if [[ ! -f "$backup_path" ]]; then
    echo "Backing up stock module to $backup_path..."
    sudo cp --preserve=mode,timestamps "$module_path" "$backup_path"
else
    echo "Keeping existing stock backup: $backup_path"
fi

echo "Installing patched module..."
case "$module_path" in
    *.xz)
        xz -c -f "$built_module" | sudo tee "$module_path" >/dev/null
        ;;
    *.zst)
        if ! command -v zstd >/dev/null; then
            sudo apt-get install -y zstd
        fi
        zstd -q -c -f "$built_module" | sudo tee "$module_path" >/dev/null
        ;;
    *.ko)
        sudo cp "$built_module" "$module_path"
        ;;
    *)
        echo "Unsupported installed module compression: $module_path" >&2
        exit 1
        ;;
esac

sudo depmod -a "$KERNEL_RELEASE"

echo "Granting the video group access to live sensor positioning..."
sudo tee /etc/udev/rules.d/99-openflight-ov9281.rules >/dev/null <<'EOF'
ACTION=="add", SUBSYSTEM=="video4linux", KERNEL=="video*", RUN+="/bin/chgrp video /sys/module/ov9282/parameters/strip_y_offset"
EOF
sudo udevadm control --reload-rules

if ! id -nG "$USER" | tr ' ' '\n' | grep -qx video; then
    echo "Adding $USER to the video group; log out and back in after reboot."
    sudo usermod -aG video "$USER"
fi

echo
echo "Driver installed successfully. Do not hot-unload the camera module."
echo "Reboot the Pi, then verify the modes with:"
echo "  rpicam-hello --list-cameras"
