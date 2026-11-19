"""OPS243 over the J3 GPIO UART instead of USB.

Moving the radar off USB (to free the Pi's USB power budget for the TI
angle radar) changes one thing that matters: baud becomes the real wire
rate. The factory default is 19,200, where a 40.6KB rolling-buffer dump
takes 21s and every capture is truncated. These tests cover the
negotiation that avoids that, and the timeout scaling that keeps a failed
negotiation slow rather than silently broken.
"""

import time
from unittest.mock import patch

import pytest

from openflight.ops243 import UART_BAUD_COMMANDS, OPS243Radar, is_uart_port


class FakeSerial:
    """Fake port that answers ?V only at one configured baud.

    Models the real failure mode: at a wrong baud the radar's reply is
    framing garbage, not silence.
    """

    def __init__(self, baudrate, answers_at, garbage=b"\xff\xfe\x00~~"):
        self.baudrate = baudrate
        self.answers_at = answers_at
        self.garbage = garbage
        self.is_open = True
        self.timeout = 1.0
        self.writes = []
        self._pending = b""

    @property
    def in_waiting(self):
        return len(self._pending)

    def read(self, n):
        chunk, self._pending = self._pending[:n], self._pending[n:]
        return chunk

    def reset_input_buffer(self):
        self._pending = b""

    def write(self, data):
        self.writes.append(data)
        if data == b"?V":
            if self.baudrate == self.answers_at:
                self._pending += b'{"Version":"1.5.2"}\r\n'
            else:
                self._pending += self.garbage
        return len(data)

    def flush(self):
        pass

    def close(self):
        self.is_open = False


def _radar_with_fake_ports(answers_at, port="/dev/ttyAMA0", **kwargs):
    """Build a radar whose serial.Serial yields FakeSerial instances."""
    radar = OPS243Radar(port=port, **kwargs)
    opened = []

    def factory(*_args, **kw):
        fake = FakeSerial(kw["baudrate"], answers_at)
        opened.append(fake)
        return fake

    return radar, opened, factory


class TestUartPortDetection:
    """A raw UART must be recognised as such; USB must not be."""

    @pytest.mark.parametrize(
        "port",
        ["/dev/ttyAMA0", "/dev/ttyAMA10", "/dev/ttyS0", "/dev/serial0", "ttyAMA0"],
    )
    def test_uart_ports_recognised(self, port):
        assert is_uart_port(port)

    @pytest.mark.parametrize(
        "port",
        ["/dev/ttyACM0", "/dev/ttyUSB0", "/dev/cu.usbmodem1234", None, ""],
    )
    def test_usb_and_missing_ports_are_not_uart(self, port):
        assert not is_uart_port(port)

    def test_auto_detect_does_not_claim_uart_ports(self):
        """ttyAMA0 must not be auto-selected — ttyAMA10 is the Pi 5 debug UART."""

        class FakePort:
            def __init__(self, device):
                self.device = device
                self.vid = None
                self.description = None

        with patch(
            "openflight.ops243.serial.tools.list_ports.comports",
            return_value=[FakePort("/dev/ttyAMA0"), FakePort("/dev/ttyAMA10")],
        ):
            assert OPS243Radar.find_radar_ports() == []

    def test_missing_radar_error_mentions_the_uart_option(self):
        radar = OPS243Radar()
        with patch.object(OPS243Radar, "find_radar_ports", return_value=[]):
            with pytest.raises(ConnectionError) as exc:
                radar.connect()
        assert "ttyAMA0" in str(exc.value)

    def test_missing_radar_error_does_not_name_a_caller_specific_flag(self):
        """The driver cannot know which CLI invoked it (issue #270).

        start-kiosk.sh takes --radar-port but the hardware-test scripts take
        --port, so naming either flag misleads half the callers. The hint must
        stay tool-neutral.
        """
        radar = OPS243Radar()
        with patch.object(OPS243Radar, "find_radar_ports", return_value=[]):
            with pytest.raises(ConnectionError) as exc:
                radar.connect()
        message = str(exc.value)
        assert "--radar-port" not in message
        assert "--port" not in message


class TestBaudNegotiation:
    def test_already_at_target_needs_no_switch(self):
        radar, opened, factory = _radar_with_fake_ports(answers_at=230400)
        with patch("openflight.ops243.serial.Serial", side_effect=factory):
            assert radar.negotiate_uart_baud() == 230400
        assert radar.baud == 230400
        assert len(opened) == 1, "probed extra rates after the first one answered"
        assert b"I5" not in b"".join(opened[0].writes)

    def test_factory_default_is_raised_to_230400(self):
        """The board out of the box: answers at 19,200, must end at 230,400."""
        radar, opened, factory = _radar_with_fake_ports(answers_at=19200)
        with patch("openflight.ops243.serial.Serial", side_effect=factory):
            # The FakeSerial only answers at 19200, so after the I5 switch the
            # verification probe fails unless we let it answer at the new rate.
            def factory_then_switch(*args, **kw):
                fake = FakeSerial(kw["baudrate"], answers_at=19200)
                opened.append(fake)
                if any(b"I5" in w for f in opened for w in f.writes):
                    fake.answers_at = 230400
                return fake

            with patch("openflight.ops243.serial.Serial", side_effect=factory_then_switch):
                result = radar.negotiate_uart_baud()

        assert result == 230400
        assert radar.baud == 230400
        all_writes = b"".join(w for f in opened for w in f.writes)
        assert b"I5" in all_writes, "never sent the I5 command to raise the baud"

    def test_probe_order_tries_target_then_factory_default(self):
        radar, opened, factory = _radar_with_fake_ports(answers_at=115200)
        with patch("openflight.ops243.serial.Serial", side_effect=factory):
            radar.negotiate_uart_baud()
        assert [f.baudrate for f in opened][:3] == [230400, 19200, 115200]

    def test_a_non_default_target_baud_is_probed_first(self):
        """Pins the "target first" prefix, which the test above cannot observe.

        ``negotiate_uart_baud`` builds its order as ``[self.uart_baud] + the
        rest of BAUD_PROBE_ORDER``. The test above uses the default target of
        230,400, which is *already* ``BAUD_PROBE_ORDER[0]``, so the prefix is a
        no-op there: deleting it leaves the probe sequence byte-identical and
        every test in this file still passes.

        A target that is not first in the factory order is the only way to see
        the behaviour, and it is the case that matters in the field -- once
        ``A!`` has persisted a rate, the radar answers on probe one instead of
        after three failed opens, each of which costs a full read timeout.
        """
        radar, opened, factory = _radar_with_fake_ports(answers_at=57600, uart_baud=57600)
        with patch("openflight.ops243.serial.Serial", side_effect=factory):
            assert radar.negotiate_uart_baud() == 57600
        assert [f.baudrate for f in opened] == [57600], (
            "57,600 is BAUD_PROBE_ORDER[3]; opening it first only happens if the "
            "configured target is moved to the front of the probe order"
        )

    def test_no_reply_at_any_baud_raises_with_wiring_guidance(self):
        radar, _opened, factory = _radar_with_fake_ports(answers_at=None)
        with patch("openflight.ops243.serial.Serial", side_effect=factory):
            with pytest.raises(ConnectionError) as exc:
                radar.negotiate_uart_baud()
        message = str(exc.value)
        # The three UART-only causes must all be named, since the symptom
        # (silence) is identical for each.
        assert "crossed" in message
        assert "UNPLUGGED" in message
        assert "console" in message

    def test_failed_switch_falls_back_instead_of_raising(self):
        """A radar that ignores I5 should still work, slowly, not crash."""
        radar, opened, factory = _radar_with_fake_ports(answers_at=19200)
        with patch("openflight.ops243.serial.Serial", side_effect=factory):
            result = radar.negotiate_uart_baud()
        assert result == 19200, "did not revert to the rate that actually answers"
        assert radar.baud == 19200

    def test_unsupported_target_baud_is_refused(self):
        radar, _opened, factory = _radar_with_fake_ports(answers_at=19200, uart_baud=250000)
        with patch("openflight.ops243.serial.Serial", side_effect=factory):
            assert radar.negotiate_uart_baud() == 19200

    def test_every_probe_candidate_has_an_api_command(self):
        for baud in OPS243Radar.BAUD_PROBE_ORDER:
            assert baud in UART_BAUD_COMMANDS

    def test_usb_connect_does_not_negotiate(self):
        radar, opened, factory = _radar_with_fake_ports(answers_at=None, port="/dev/ttyACM0")
        with patch("openflight.ops243.serial.Serial", side_effect=factory):
            with patch.object(OPS243Radar, "_drain_serial"):
                assert radar.connect()
        assert len(opened) == 1, "negotiated baud on a USB port"
        assert radar.baud == OPS243Radar.DEFAULT_BAUD

    def test_negotiate_baud_override_forces_it_off_for_uart(self):
        radar, opened, factory = _radar_with_fake_ports(answers_at=None, negotiate_baud=False)
        with patch("openflight.ops243.serial.Serial", side_effect=factory):
            with patch.object(OPS243Radar, "_drain_serial"):
                assert radar.connect()
        assert len(opened) == 1


class TestTimeoutScaling:
    """Dump timeouts must track baud, or a slow link truncates every capture."""

    def _radar_at(self, baud, port="/dev/ttyAMA0"):
        radar = OPS243Radar.__new__(OPS243Radar)
        radar.baud = baud
        radar.port = port
        return radar

    def test_usb_keeps_the_tuned_floors_regardless_of_nominal_baud(self):
        """CDC-ACM is not rate-limited by the host baud, so don't scale from it."""
        radar = self._radar_at(57600, port="/dev/ttyACM0")
        assert radar.transfer_budget_s(floor=5.0) == 5.0
        assert radar.transfer_budget_s(floor=8.0) == 8.0

    def test_230400_keeps_the_tuned_floors(self):
        radar = self._radar_at(230400)
        # A dump is ~1.8s here, so the existing 8s/5s constants already fit
        # and must not be inflated.
        assert radar.transfer_budget_s(floor=8.0) == 8.0
        assert radar.transfer_budget_s(floor=5.0) == 5.0

    def test_19200_scales_past_the_dump_time(self):
        radar = self._radar_at(19200)
        dump_s = OPS243Radar.DUMP_BYTES / 1920.0  # ~23.4s
        budget = radar.transfer_budget_s(floor=8.0)
        assert budget > dump_s, (
            f"budget {budget:.1f}s is shorter than the {dump_s:.1f}s a dump "
            "takes at 19200 baud — captures would be cut off"
        )

    def test_budget_is_monotonic_in_baud(self):
        budgets = [self._radar_at(b).transfer_budget_s(floor=0.0) for b in (9600, 19200, 230400)]
        assert budgets == sorted(budgets, reverse=True)

    def test_missing_baud_attribute_falls_back_to_default(self):
        """Tests and helpers build radars via __new__ without a baud."""
        radar = OPS243Radar.__new__(OPS243Radar)
        assert radar.bytes_per_second == OPS243Radar.DEFAULT_BAUD / 10.0

    def test_hardware_trigger_grace_derives_from_baud(self):
        recorded = {}

        class SilentSerial:
            is_open = True
            in_waiting = 0

            def reset_input_buffer(self):
                pass

            def read(self, _n):
                return b""

        radar = OPS243Radar.__new__(OPS243Radar)
        radar.baud = 19200
        radar.port = "/dev/ttyAMA0"
        radar.serial = SilentSerial()
        radar.last_hardware_trigger_first_byte_timestamp = None

        original = OPS243Radar.transfer_budget_s

        def spy(self, floor, **kwargs):
            recorded["floor"] = floor
            return original(self, floor, **kwargs)

        with patch.object(OPS243Radar, "transfer_budget_s", spy):
            radar.wait_for_hardware_trigger(timeout=0.01)

        assert recorded["floor"] == 8.0, "dump_grace no longer derives from baud"


class TestReplyReading:
    """A reply ends at sustained silence, not at the first empty poll."""

    class GappyReplySerial:
        """Delivers a reply in two bursts separated by a real time gap.

        Models a radar that pauses mid-reply (multi-line ``??`` output, or a
        slow wire rate) for longer than one reader poll cycle.
        """

        GAP_S = 0.08

        def __init__(self, first: bytes, second: bytes):
            self.is_open = True
            self.writes = []
            self._first = first
            self._second = second
            self._first_read_at = None

        @property
        def in_waiting(self):
            if self._first:
                return len(self._first)
            if self._second and self._first_read_at is not None:
                if time.monotonic() - self._first_read_at >= self.GAP_S:
                    return len(self._second)
            return 0

        def read(self, _n):
            if self._first:
                chunk, self._first = self._first, b""
                self._first_read_at = time.monotonic()
                return chunk
            if self.in_waiting:
                chunk, self._second = self._second, b""
                return chunk
            return b""

        def reset_input_buffer(self):
            pass

        def write(self, data):
            self.writes.append(data)
            return len(data)

        def flush(self):
            pass

    FIRST = b'{"Product":"OPS243"}\r\n{"Version":"1.5.2"}\r\n'
    SECOND = b'{"SamplingRate":30000}\r\n{"SampleSize":128}\r\n'

    def test_gappy_reply_is_read_to_completion(self):
        radar = OPS243Radar.__new__(OPS243Radar)
        radar.baud = 19200
        radar.serial = self.GappyReplySerial(self.FIRST, self.SECOND)

        info = radar.get_info()
        assert info.get("Version") == "1.5.2"
        assert info.get("SampleSize") == 128, (
            "trailing JSON line lost — read stopped at a mid-reply gap"
        )

    def test_silent_command_returns_promptly(self):
        class SilentSerial:
            is_open = True
            in_waiting = 0

            def reset_input_buffer(self):
                pass

            def read(self, _n):
                return b""

            def write(self, _data):
                return 0

            def flush(self):
                pass

        radar = OPS243Radar.__new__(OPS243Radar)
        radar.baud = 230400
        radar.serial = SilentSerial()

        start = time.monotonic()
        assert radar._send_command("PA") == ""
        assert time.monotonic() - start < 0.5


class TestBaudPlumbing:
    """The target rate must survive the trip from CLI to driver."""

    def test_monitor_passes_ops_baud_as_uart_baud(self):
        from openflight.rolling_buffer.monitor import RollingBufferMonitor

        with patch("openflight.rolling_buffer.monitor.OPS243Radar") as radar_cls:
            RollingBufferMonitor(port="/dev/ttyAMA0", trigger_type="sound", ops_baud=115200)
        assert radar_cls.call_args.kwargs["uart_baud"] == 115200

    def test_monitor_omits_uart_baud_when_unset(self):
        """Absent override must not pin the driver's own default."""
        from openflight.rolling_buffer.monitor import RollingBufferMonitor

        with patch("openflight.rolling_buffer.monitor.OPS243Radar") as radar_cls:
            RollingBufferMonitor(port="/dev/ttyAMA0", trigger_type="sound")
        assert "uart_baud" not in radar_cls.call_args.kwargs

    def test_server_exposes_ops_baud_flag(self):
        """--port is the radar device and --web-port the HTTP port, so the
        baud flag has to be discoverable in --help to be usable."""
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-c", "from openflight.server import main; main()", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert "--ops-baud" in result.stdout
