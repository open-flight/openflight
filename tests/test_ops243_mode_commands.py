"""OPS243-A mode commands must match the current API reference.

AN-010-AD (API Commands, p21) is authoritative on mode control:

    GS  CW Mode            Sets CW operation only          (OPS243-A default)
    GC  Rolling Buffer     Sets Rolling Buffer Mode on (GS to disable),
                           *previously was G1*

The older AN-027 Rolling Buffer app note still documents the pre-rename
G1/G0 pair, and a swing-speed branch once renamed the whole driver to match
it -- silently reverting the production launch-monitor path to deprecated
mnemonics. These tests pin the byte sequences so the two docs can't be
confused again, and so swing-speed training stays off the rolling-buffer
path entirely.
"""

import pytest

from openflight.ops243 import OPS243Radar

DEPRECATED_MODE_COMMANDS = (b"G1", b"G0")


class RecordingSerial:
    """Fake serial port that records writes and answers queries quietly."""

    def __init__(self):
        self.is_open = True
        self.writes = []

    @property
    def in_waiting(self):
        return 0

    def read(self, _n):
        return b""

    def reset_input_buffer(self):
        pass

    def write(self, data):
        self.writes.append(data)

    def flush(self):
        pass


@pytest.fixture(name="radar")
def _radar(monkeypatch):
    """A radar wired to a recording port, with sleeps and reads stubbed out."""
    monkeypatch.setattr("openflight.ops243.time.sleep", lambda _s: None)
    instance = OPS243Radar.__new__(OPS243Radar)
    instance.serial = RecordingSerial()
    instance._unit = "mph"
    instance._json_mode = False
    instance._magnitude_enabled = False
    instance._speed_read_buffer = ""
    # _send_command reads a reply; keep it silent so tests only observe writes.
    monkeypatch.setattr(OPS243Radar, "_read_reply", lambda _self, *_a, **_kw: "")
    return instance


def _sent(radar):
    """Concatenated bytes written to the port."""
    return b"".join(radar.serial.writes)


# --- rolling buffer keeps the current mnemonic -------------------------------


def test_enter_rolling_buffer_uses_gc_not_deprecated_g1(radar):
    """Rolling buffer is GC per AN-010-AD; G1 is the pre-rename spelling."""
    radar.enter_rolling_buffer_mode(pre_trigger_segments=16, sample_rate_ksps=30)

    sent = _sent(radar)
    assert b"GC" in sent, f"expected GC to enter rolling buffer, sent: {radar.serial.writes!r}"
    for deprecated in DEPRECATED_MODE_COMMANDS:
        assert deprecated not in sent, (
            f"{deprecated!r} is the deprecated mnemonic (AN-027); "
            f"AN-010-AD renamed it. Sent: {radar.serial.writes!r}"
        )


def test_disable_rolling_buffer_uses_gs_not_deprecated_g0(radar):
    """GS is the documented way out of rolling buffer, back to CW."""
    radar.disable_rolling_buffer()

    sent = _sent(radar)
    assert b"GS" in sent, f"expected GS to leave rolling buffer, sent: {radar.serial.writes!r}"
    assert b"G0" not in sent, "G0 only appears in the superseded AN-027 app note"


def test_switch_to_rolling_buffer_uses_gc(radar):
    """The fast speed-trigger handoff into rolling buffer also uses GC."""
    radar.switch_to_rolling_buffer()

    sent = _sent(radar)
    assert b"GC" in sent
    for deprecated in DEPRECATED_MODE_COMMANDS:
        assert deprecated not in sent


# --- swing-speed training is a separate, CW-only path -----------------------


def test_swing_speed_training_selects_cw_mode(radar):
    """Training reports raw CW speeds, so it must select GS."""
    radar.configure_for_swing_speed_training()

    sent = _sent(radar)
    assert b"GS" in sent, f"training must select CW mode (GS), sent: {radar.serial.writes!r}"
    for deprecated in DEPRECATED_MODE_COMMANDS:
        assert deprecated not in sent


def test_swing_speed_training_never_enters_rolling_buffer(radar):
    """Training must not arm the rolling buffer: no GC, no S#n, no S! trigger."""
    radar.configure_for_swing_speed_training()

    for command in (b"GC", b"S#", b"S!"):
        assert command not in _sent(radar), (
            f"{command!r} is a rolling-buffer command and must not appear in the "
            f"training path. Sent: {radar.serial.writes!r}"
        )


def test_swing_speed_training_never_writes_flash(radar):
    """A! writes persistent memory; only the one-time setup path may do that.

    A stray A! here would overwrite the persisted rolling-buffer boot state
    that the HOST_INT workaround depends on, breaking launch mode until the
    setup script was re-run.
    """
    radar.configure_for_swing_speed_training()

    assert b"A!" not in _sent(radar), (
        "training must not persist config to flash — it would clobber the "
        f"persisted rolling-buffer boot state. Sent: {radar.serial.writes!r}"
    )


def test_swing_speed_training_activates_and_applies_thresholds(radar):
    """Training ends active (PA) with the caller's speed filter and report count."""
    radar.configure_for_swing_speed_training(min_speed_mph=45, num_reports=3)

    sent = _sent(radar)
    assert b"R>45" in sent, f"expected R>45 min-speed filter, sent: {radar.serial.writes!r}"
    assert b"O3" in sent, f"expected O3 for 3 reports, sent: {radar.serial.writes!r}"
    assert sent.rstrip().endswith(b"PA") or b"PA" in sent, "training must leave the radar active"


def test_swing_speed_training_requires_connection():
    """Configuring a disconnected radar must fail loudly, not silently no-op."""
    instance = OPS243Radar.__new__(OPS243Radar)
    instance.serial = None

    with pytest.raises(ConnectionError):
        instance.configure_for_swing_speed_training()


# --- the restore path returns to rolling buffer without touching flash ------


def test_persisted_startup_sends_no_mode_switch_at_all(radar):
    """The default sound-trigger startup must not transition radar modes.

    This is the production launch-monitor path. The OPS243-A flips HOST_INT
    pin mode when it changes modes at runtime, so the board is persisted into
    rolling-buffer mode with A! and power-cycled once (CLAUDE.md "Radar
    Setup"); startup then only re-arms. A GC/PI here would re-introduce the
    runtime transition the whole workaround exists to avoid.

    TestRollingBufferStartupMode asserts the *dispatch* (which radar method
    the monitor picks) with a MagicMock radar, so it cannot see the bytes.
    This pins the bytes.
    """
    radar.prepare_persisted_rolling_buffer(pre_trigger_segments=16, sample_rate_ksps=30)

    sent = _sent(radar)
    for mode_switch in (b"GC", b"GS", b"PI", b"A!"):
        assert mode_switch not in sent, (
            f"{mode_switch!r} is a mode switch/flash write and must not appear on the "
            f"persisted startup path. Sent: {radar.serial.writes!r}"
        )
    # It must still actually re-arm, or the sound trigger never fires.
    assert b"PA" in sent, f"startup must re-arm with PA, sent: {radar.serial.writes!r}"
    assert b"S#16" in sent, f"startup must set the trigger split, sent: {radar.serial.writes!r}"


def test_restore_rolling_buffer_uses_gc_and_no_flash_write(radar):
    """Returning from training re-enters rolling buffer volatilely."""
    radar.restore_rolling_buffer_mode(pre_trigger_segments=16, sample_rate_ksps=30)

    sent = _sent(radar)
    assert b"GC" in sent
    for deprecated in DEPRECATED_MODE_COMMANDS:
        assert deprecated not in sent
    assert b"A!" not in sent, "mode switching must not write flash"


@pytest.mark.parametrize(("enabled", "command"), [(True, b"OL"), (False, b"Ol")])
def test_set_leds_sends_the_an010_led_command(radar, enabled, command):
    """AN-010-AF (p16): OL turns the LEDs on, lowercase-L Ol turns them off."""
    radar.set_leds(enabled)

    assert _sent(radar).startswith(command)
    assert b"A!" not in _sent(radar), "an LED toggle must not write flash"
