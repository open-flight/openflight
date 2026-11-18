#!/usr/bin/env bash
# Build and install the CP210x USB serial driver that never pauses bulk-in reads.
#
# The IWR6843 UART has no flow control. When the stock driver is throttled (the
# 4 KB tty read buffer is nearly full because the reader is late) it stops reading
# USB and the CP2105 drops data. With this patch the backlog waits in the kernel's
# 640 KB tty flip buffers instead. Rebuild after every kernel update.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
PATCH_FILE="${OPENFLIGHT_CP210X_PATCH:-$REPO_ROOT/drivers/cp210x/cp210x-keep-reading.patch}"
KERNEL_RELEASE="$(uname -r)"
KERNEL_VERSION="${KERNEL_RELEASE%%+*}"
KERNEL_SERIES="$(cut -d. -f1,2 <<<"$KERNEL_VERSION")"
SOURCE_PACKAGE="linux-source-$KERNEL_SERIES"
WORK_ROOT="${OPENFLIGHT_KERNEL_WORKDIR:-$HOME/.cache/openflight-kernel/$KERNEL_RELEASE}"
KERNEL_BUILD="/lib/modules/$KERNEL_RELEASE/build"
MODULE_ROOT="$WORK_ROOT/cp210x-module-root"
MODULE_DIR="$MODULE_ROOT/drivers/usb/serial"
MODINFO_BIN="$(command -v modinfo || echo /sbin/modinfo)"

module_path="$("$MODINFO_BIN" -n cp210x)"
backup_path="${module_path}.openflight-stock-${KERNEL_RELEASE}"

if [[ "${1:-}" == "--restore" ]]; then
    [[ -f "$backup_path" ]] || { echo "No stock backup at $backup_path" >&2; exit 1; }
    sudo cp --preserve=mode,timestamps "$backup_path" "$module_path"
    sudo depmod -a "$KERNEL_RELEASE"
    echo "Stock cp210x restored. Stop the app, then: sudo rmmod cp210x && sudo modprobe cp210x"
    exit 0
elif [[ $# -gt 0 ]]; then
    echo "Usage: $(basename "$0") [--restore]" >&2
    exit 2
fi

[[ -f "$PATCH_FILE" ]] || { echo "Missing patch: $PATCH_FILE" >&2; exit 1; }
[[ -f "$KERNEL_BUILD/Module.symvers" ]] || { echo "Kernel headers missing: $KERNEL_BUILD" >&2; exit 1; }

source_tar="$(find "$WORK_ROOT/package/usr/src" -maxdepth 1 -name 'linux-source-*.tar.*' -print -quit 2>/dev/null || true)"
if [[ -z "$source_tar" ]]; then
    mkdir -p "$WORK_ROOT/download" "$WORK_ROOT/package"
    version="$(apt-cache madison "$SOURCE_PACKAGE" | awk -F'|' -v v="$KERNEL_VERSION" \
        'index($2, v) && index($2, "rpt") { gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2; exit }')"
    [[ -n "$version" ]] || { echo "No $SOURCE_PACKAGE matches kernel $KERNEL_VERSION" >&2; exit 1; }
    (cd "$WORK_ROOT/download" && apt-get download "$SOURCE_PACKAGE=$version")
    dpkg-deb -x "$(find "$WORK_ROOT/download" -maxdepth 1 -name "${SOURCE_PACKAGE}_*.deb" -print -quit)" "$WORK_ROOT/package"
    source_tar="$(find "$WORK_ROOT/package/usr/src" -maxdepth 1 -name 'linux-source-*.tar.*' -print -quit)"
fi

rm -rf "$MODULE_ROOT"
mkdir -p "$MODULE_DIR"
nice -n 19 tar -xf "$source_tar" --wildcards --no-anchored -O 'drivers/usb/serial/cp210x.c' >"$MODULE_DIR/cp210x.c"
(cd "$MODULE_ROOT" && git apply --check --recount --unidiff-zero "$PATCH_FILE" && git apply --recount --unidiff-zero "$PATCH_FILE")
echo 'obj-m += cp210x.o' >"$MODULE_DIR/Makefile"
nice -n 19 make -C "$KERNEL_BUILD" M="$MODULE_DIR" modules
built="$MODULE_DIR/cp210x.ko"
[[ "$("$MODINFO_BIN" -F vermagic "$built" | awk '{print $1}')" == "$KERNEL_RELEASE" ]] || {
    echo "Built module does not match kernel $KERNEL_RELEASE" >&2; exit 1; }

[[ -f "$backup_path" ]] || sudo cp --preserve=mode,timestamps "$module_path" "$backup_path"
case "$module_path" in
    *.xz) xz -c -f "$built" | sudo tee "$module_path" >/dev/null ;;
    *.ko) sudo cp "$built" "$module_path" ;;
    *) echo "Unsupported module format: $module_path" >&2; exit 1 ;;
esac
sudo depmod -a "$KERNEL_RELEASE"
echo "Installed. Stop the app, then: sudo rmmod cp210x && sudo modprobe cp210x"
