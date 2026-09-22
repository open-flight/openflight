"""Contract tests for the custom OV9281 high-speed driver patch."""

from pathlib import Path


def test_320x200_vertical_offset_preserves_native_sensor_window():
    patch = (Path(__file__).parents[1] / "drivers/ov9281/ov9282-high-speed.patch").read_text()

    assert "mode->width == 320 && mode->height == 200" in patch
    assert "y_start = 150 + 2 * offset" in patch
    assert "y_end = 665 + 2 * offset" in patch
    assert "return clamp(strip_y_offset, -75, 75)" in patch


def test_320x200_native_readout_matches_full_frame_optical_center():
    patch = (Path(__file__).parents[1] / "drivers/ov9281/ov9282-high-speed.patch").read_text()
    mode = patch.split("static const struct ov9282_reg mode_320x200_regs[] = {", 1)[1]
    mode = mode.split("};", 1)[0]

    # A stationary target at 53.1% in the 640x400 mode appeared at 68.8% in
    # the nominally centered fast mode. Hardware comparison showed two native
    # columns per output pixel, requiring a 96-column rightward correction.
    assert "{0x3800, 0x01}" in mode
    assert "{0x3801, 0x50}" in mode
    assert "{0x3804, 0x04}" in mode
    assert "{0x3805, 0x7f}" in mode


def test_vertical_offset_uses_kernel_safe_group_writable_permissions():
    patch = (Path(__file__).parents[1] / "drivers/ov9281/ov9282-high-speed.patch").read_text()

    assert "module_param(strip_y_offset, int, 0664)" in patch
    assert "module_param(strip_y_offset, int, 0666)" not in patch


def test_installer_uses_running_kernel_headers_without_full_kernel_build():
    installer = (
        Path(__file__).parents[1] / "scripts/setup/install_ov9281_high_speed_driver.sh"
    ).read_text()

    assert 'KERNEL_BUILD="/lib/modules/$KERNEL_RELEASE/build"' in installer
    assert 'git apply --recount "$PATCH_FILE"' in installer
    assert 'make -C "$SOURCE_DIR" -j"$JOBS"' not in installer
