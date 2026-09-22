"""
OPS243-A Doppler Radar Driver for Golf Launch Monitor.

This module provides a Python interface to the OmniPreSense OPS243-A
short-range radar sensor over either transport it offers:

- USB CDC-ACM (``/dev/ttyACM*``). The host baud setting is nominal; the
  link is not rate-limited by it.
- The 3.3V UART on the J3 header (``/dev/ttyAMA0`` on a Pi 5). Here baud
  is the real wire rate, the factory default is 19,200, and a 40.6KB I/Q
  dump takes 21s at that rate — so the driver negotiates up to 230,400
  (``I5``) on connect. See ``negotiate_uart_baud``.

Only one transport is live at a time: per AN-010-AD, enumerating USB
silences the UART.

Key specs for golf application:
- Speed accuracy: +/- 0.5%
- Direction reporting (inbound/outbound)
- Detection range: 50-100m (RCS=10), ~4-5m for golf ball sized objects

Recommended golf configuration (per OmniPreSense AN-027):
- 30ksps sample rate (max ~208 mph, sufficient for all golf)
- 128 buffer size
- FFT 4096 (X=32) for ±0.1 mph resolution at ~56 Hz report rate
- Positioning: 6-8 feet behind ball, 10° upward angle

Speed limits by sample rate:
- 10kHz (SX): max 69.5 mph  - too slow for golf
- 20kHz (S2): max 139 mph   - marginal for fast shots
- 30kHz (S=30): max 208 mph - RECOMMENDED for golf
- 50kHz (SL): max 347 mph   - overkill, lower resolution
- 100kHz (SC): max 695 mph  - overkill
"""

import json
import logging
import math
import re
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional

import serial
import serial.tools.list_ports

from .serial_latency import log_usb_serial_latency_timer

# Configure logging for raw radar data
logger = logging.getLogger("ops243")
raw_logger = logging.getLogger("ops243.raw")

# UART baud rates and their API command (AN-010-AD p19). The datasheet's
# claim of a 57,600 default topping out at 115,200 is stale; OmniPreSense
# confirmed 19,200 default and I5=230,400 on the non-WiFi 10-pin header.
UART_BAUD_COMMANDS = {
    9600: "I1",
    19200: "I2",
    57600: "I3",
    115200: "I4",
    230400: "I5",
}

# Device-name fragments that mean "raw UART", not USB-serial. A raw UART
# has no USB descriptors, so it can neither be auto-detected by VID nor
# assumed to be at any particular baud.
_UART_PORT_PREFIXES = ("ttyAMA", "ttyS", "serial0", "serial1")


def is_uart_port(port: Optional[str]) -> bool:
    """Return True when ``port`` names a raw UART rather than USB-serial.

    Used to decide whether baud is a real wire rate that must be
    negotiated (UART) or a nominal CDC-ACM setting (USB).
    """
    if not port:
        return False
    name = port.rsplit("/", 1)[-1]
    return name.startswith(_UART_PORT_PREFIXES)


# Global flag to control raw reading console output
_show_raw_readings = False


def set_show_raw_readings(enabled: bool):
    """Enable/disable printing raw radar readings to console."""
    global _show_raw_readings  # pylint: disable=global-statement
    _show_raw_readings = enabled


_CLOCK_RE = re.compile(r'"?Clock"?\s*:\s*"?(-?\d+(?:\.\d+)?)"?')
_OPS243_FIRMWARE_RE = re.compile(r"(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)")


def _parse_ops_clock(response: str) -> Optional[float]:
    """Pull the numeric clock value (seconds since power-on) from a C? reply.

    The OPS243 answers C? with e.g. ``{"Clock":"137.429"}``. Clock resolution
    varies by firmware (some report whole seconds), so the caller keeps the raw
    reply; here we only extract the value. Returns None when no value is found.
    """
    if not response:
        return None
    match = _CLOCK_RE.search(response)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


class SpeedUnit(Enum):
    """Speed units supported by OPS243-A."""

    MPS = "UM"  # meters per second (default)
    MPH = "US"  # miles per hour
    KPH = "UK"  # kilometers per hour
    FPS = "UF"  # feet per second
    CMS = "UC"  # centimeters per second


class Direction(Enum):
    """Direction of detected object."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"
    UNKNOWN = "unknown"


@dataclass
class SpeedReading:
    """A single speed reading from the radar."""

    speed: float
    direction: Direction
    magnitude: Optional[float] = None
    timestamp: Optional[float] = None
    unit: str = "mph"


class OPS243Radar:
    """
    Driver for OPS243-A Doppler radar sensor.

    Production mode uses rolling buffer capture exclusively.

    Example usage:
        radar = OPS243Radar()
        radar.connect()
        radar.configure_for_rolling_buffer()

        # Wait for hardware trigger (sound trigger via HOST_INT)
        response = radar.wait_for_hardware_trigger()

        # Re-arm for next capture
        radar.rearm_rolling_buffer()
    """

    # Default serial settings per datasheet
    DEFAULT_BAUD = 57600
    DEFAULT_TIMEOUT = 1.0
    INTERNAL_TRIGGER_FIRMWARE_MINIMUM = (1, 3, 2)
    REQUIRED_INTERNAL_TRIGGER_FIRMWARE = ".".join(
        str(part) for part in INTERNAL_TRIGGER_FIRMWARE_MINIMUM
    )

    # Target rate on the J3 UART. At 230,400 a dump moves in ~1.8s; the
    # 19,200 factory default would take 21s and miss every shot.
    DEFAULT_UART_BAUD = 230400

    # Probe order when connecting over UART. The target comes first (an
    # already-configured board is the common case), then the factory
    # default, then the rest fastest-first.
    BAUD_PROBE_ORDER = (230400, 19200, 115200, 57600, 9600)

    # Measured size of one rolling-buffer dump: 40,556 bytes of JSON for
    # 4096 I + 4096 Q samples, plus margin for whitespace and timing lines.
    DUMP_BYTES = 45000

    # Bound every serial write. A radar that is mid-dump (e.g. HOST_INT
    # re-asserted by the ball hitting the net) stops servicing commands;
    # without a write timeout, serial.write() blocks the capture thread
    # forever (observed in the field via py-spy: thread wedged in
    # serialposix write inside read_clock_sync).
    SERIAL_WRITE_TIMEOUT_S = 2.0

    # Re-arm drain budget: a straggling dump finishes in well under this;
    # anything still streaming past it means the radar is continuously
    # sending (immediate re-triggers or streaming-mode fallback) and the
    # drain must bail out loudly instead of hanging the monitor thread.
    REARM_DRAIN_TIMEOUT_S = 5.0

    # Common USB identifiers for OPS243
    VENDOR_IDS = [0x0483]  # STMicroelectronics

    def __init__(
        self,
        port: Optional[str] = None,
        baud: int = DEFAULT_BAUD,
        *,
        uart_baud: int = DEFAULT_UART_BAUD,
        negotiate_baud: Optional[bool] = None,
    ):
        """
        Initialize radar driver.

        Args:
            port: Serial port ('/dev/ttyACM0' for USB, '/dev/ttyAMA0' for
                the J3 UART). If None, auto-detect USB only — a raw UART
                has no USB descriptors to auto-detect.
            baud: Baud rate to open with (default 57600 per datasheet).
                Over UART this is only the first probe candidate;
                ``self.baud`` ends up at whatever rate was negotiated.
            uart_baud: Target UART rate to negotiate to (default 230400).
            negotiate_baud: Force baud negotiation on/off. Default None
                means "negotiate iff the port is a raw UART".
        """
        self.port = port
        self.baud = baud
        self.uart_baud = uart_baud
        self._negotiate_baud = negotiate_baud
        self.serial: Optional[serial.Serial] = None
        self._unit = "mph"
        self._json_mode = False
        self._magnitude_enabled = False
        self._speed_read_buffer = ""
        self._internal_speed_trigger_config = None
        self._hardware_trigger_recovery_required = False
        self.last_hardware_trigger_first_byte_timestamp: Optional[float] = None
        # Most recent OPS-clock -> host-epoch sync (see read_clock_sync).
        self.last_clock_sync: Optional[dict] = None

    @staticmethod
    def find_radar_ports() -> List[str]:
        """
        Find potential OPS243 radar ports.

        Returns:
            List of port names that might be OPS243 devices
        """
        ports = []
        for port in serial.tools.list_ports.comports():
            # OPS243 shows up as USB serial device
            if port.vid in OPS243Radar.VENDOR_IDS or "ACM" in port.device:
                ports.append(port.device)
            # Also check description for OmniPreSense
            elif port.description and "OmniPreSense" in port.description:
                ports.append(port.device)
        return ports

    def connect(self, timeout: float = DEFAULT_TIMEOUT) -> bool:
        """
        Connect to the radar sensor.

        Over USB this opens the port and drains any stale dump. Over the
        J3 UART it also negotiates baud, because the board's actual rate is
        unknown at connect time (19,200 from the factory, or whatever a
        previous session left in flash).

        Args:
            timeout: Serial read timeout in seconds

        Returns:
            True if connection successful
        """
        if self.port is None:
            ports = self.find_radar_ports()
            if not ports:
                raise ConnectionError(
                    "No OPS243 radar found on USB. If it is wired to the Pi GPIO "
                    "UART (J3 pins 6/7), pass the port explicitly — e.g. "
                    "--radar-port /dev/ttyAMA0 — since a raw UART has no USB "
                    "descriptors to auto-detect."
                )
            self.port = ports[0]

        try:
            self._open_serial(self.baud, timeout)
            if self._should_negotiate_baud():
                self.negotiate_uart_baud(timeout=timeout)
            self._log_transport()
            # Drain any in-progress dump (e.g. radar triggered while no software was running).
            # Opening the port unblocks the radar's UART TX, so we read until silence.
            self._drain_serial()
            return True
        except serial.SerialException as e:
            raise ConnectionError(f"Failed to connect to {self.port}: {e}") from e

    def _open_serial(self, baud: int, timeout: float = DEFAULT_TIMEOUT):
        """(Re)open ``self.port`` at ``baud``, closing any existing handle."""
        if self.serial is not None and getattr(self.serial, "is_open", False):
            self.serial.close()
        self.serial = serial.Serial(
            port=self.port,
            baudrate=baud,
            timeout=timeout,
            write_timeout=self.SERIAL_WRITE_TIMEOUT_S,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
        )
        self.baud = baud

    def _should_negotiate_baud(self) -> bool:
        """Whether to run baud negotiation for the current port."""
        if self._negotiate_baud is not None:
            return self._negotiate_baud
        return is_uart_port(self.port)

    def _log_transport(self):
        """Log which transport we came up on, plus its timing-relevant facts."""
        if is_uart_port(self.port):
            logger.info(
                "[OPS] Transport=uart port=%s baud=%d (dump ~%.1fs)",
                self.port,
                self.baud,
                self.DUMP_BYTES / self.bytes_per_second,
            )
            return
        logger.info("[OPS] Transport=usb port=%s baud=%d (nominal)", self.port, self.baud)
        log_usb_serial_latency_timer(logger, "OPS", self.port)

    @property
    def bytes_per_second(self) -> float:
        """Wire throughput at the current baud, 8N1 (10 bits per byte)."""
        baud = getattr(self, "baud", None) or self.DEFAULT_BAUD
        return max(1.0, baud / 10.0)

    def transfer_budget_s(self, floor: float, safety: float = 1.5, overhead: float = 1.0) -> float:
        """Seconds to allow for one dump, at least ``floor``.

        Over UART a dump takes ``DUMP_BYTES / (baud / 10)`` seconds — 1.8s at
        230,400, 21s at 19,200 — so the tuned constants become floors that
        scale up when the negotiated rate is slow. Without this, a failed
        negotiation would truncate every capture instead of merely running
        slowly.

        Over USB the host baud is nominal and the link is not rate-limited by
        it (CDC-ACM moves a dump in ~4-5s regardless), so scaling from 57,600
        would inflate every timeout for no reason. There the floor stands.
        """
        if not is_uart_port(getattr(self, "port", None)):
            return floor
        return max(floor, self.DUMP_BYTES / self.bytes_per_second * safety + overhead)

    def query_uart_baud(self) -> str:
        """Return the radar's raw ``I?`` reply (baud + oversampling setting).

        Reported verbatim because AN-010-AD does not document the reply
        format; it is for logs and diagnostics, not for control flow.
        """
        return self._send_command("I?")

    def negotiate_uart_baud(self, timeout: float = DEFAULT_TIMEOUT) -> int:
        """Find the rate the radar is actually talking at, then raise it.

        Probes ``BAUD_PROBE_ORDER`` with ``?V`` — the one command whose
        reply format is known — and accepts the first rate that answers with
        a parseable ``{"Version": ...}``. Framing garbage from a wrong-rate
        read cannot produce that, so a match is unambiguous.

        Returns the negotiated baud, also left on ``self.baud``.
        """
        order = [self.uart_baud] + [b for b in self.BAUD_PROBE_ORDER if b != self.uart_baud]

        found = None
        for candidate in order:
            self._open_serial(candidate, timeout)
            version = self._probe_firmware_version()
            if version is not None:
                found = candidate
                logger.info("[OPS] UART answered at %d baud (firmware %s)", candidate, version)
                break
            logger.debug("[OPS] No valid reply at %d baud", candidate)

        if found is None:
            raise ConnectionError(
                f"No reply from OPS243 on {self.port} at any of "
                f"{', '.join(str(b) for b in order)} baud. Check that TX/RX are "
                "crossed (J3 pin 7 -> Pi RXD0, Pi TXD0 -> J3 pin 6), that 5V and "
                "ground are connected (J3 pins 9/10), that the OPS USB cable is "
                "UNPLUGGED (enumerating USB silences the UART), and that the Linux "
                "serial console is disabled on this port."
            )

        if found != self.uart_baud:
            self._raise_uart_baud(found, self.uart_baud, timeout)

        return self.baud

    def _raise_uart_baud(self, current: int, target: int, timeout: float):
        """Switch the radar from ``current`` to ``target`` baud and verify.

        On failure the port is reopened at ``current`` and a warning logged
        rather than raising: a slow link still measures shots (the derived
        timeouts absorb it), so degraded operation beats none.
        """
        command = UART_BAUD_COMMANDS.get(target)
        if command is None:
            logger.warning(
                "[OPS] %d is not a supported UART baud (%s) — staying at %d",
                target,
                ", ".join(str(b) for b in sorted(UART_BAUD_COMMANDS)),
                current,
            )
            return

        logger.info("[OPS] Switching UART %d -> %d baud (%s)", current, target, command)
        self.serial.write(command.encode("ascii"))
        self.serial.flush()
        time.sleep(0.2)

        self._open_serial(target, timeout)
        if self._probe_firmware_version() is not None:
            return

        logger.warning(
            "[OPS] Radar did not answer at %d baud after %s — reverting to %d. "
            "A dump will take ~%.0fs; expect slow shot-to-shot cadence.",
            target,
            command,
            current,
            self.DUMP_BYTES / (current / 10.0),
        )
        self._open_serial(current, timeout)

    def _probe_firmware_version(self) -> Optional[str]:
        """Return the firmware version if the radar answers ``?V``, else None.

        The probe behind baud negotiation: at a wrong baud the reply is
        framing garbage that will not parse as JSON containing "Version".
        """
        try:
            response = self._send_command("?V")
        except (serial.SerialException, OSError) as exc:
            logger.debug("[OPS] Probe write failed at %d baud: %s", self.baud, exc)
            return None

        for line in response.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and "Version" in data:
                return str(data["Version"])
        return None

    def disconnect(self):
        """Disconnect from the radar sensor."""
        if self.serial and self.serial.is_open:
            self.serial.close()
            self.serial = None

    def _drain_serial(self, quiet_period: float = 0.5, max_wait: Optional[float] = None):
        """
        Drain serial port until no data arrives for quiet_period seconds.

        Handles the case where the radar was triggered while no software was
        running. The radar may be mid-dump (I/Q data streaming out) when we
        connect. We need to let it finish before sending any commands.

        Args:
            quiet_period: Seconds of silence before considering drain complete
            max_wait: Maximum total seconds to wait before giving up. None
                derives it from baud (floor 5s) so a slow UART link gets
                long enough to finish a straggling dump.
        """
        if max_wait is None:
            max_wait = self.transfer_budget_s(floor=5.0)
        start = time.monotonic()
        drained = 0
        old_timeout = self.serial.timeout
        self.serial.timeout = quiet_period

        while time.monotonic() - start < max_wait:
            chunk = self.serial.read(4096)
            if not chunk:
                break  # No data for quiet_period — drain complete
            drained += len(chunk)

        self.serial.timeout = old_timeout
        self.serial.reset_input_buffer()

        if drained > 0:
            logger.info("[OPS] Drained %d bytes of stale data from serial buffer", drained)

    def _send_command(self, cmd: str) -> str:
        """
        Send a command to the radar and return response.

        Args:
            cmd: Two-character command (e.g., "??", "US")

        Returns:
            Response string from radar
        """
        if not self.serial or not self.serial.is_open:
            raise ConnectionError("Not connected to radar")

        # Clear input buffer
        self.serial.reset_input_buffer()

        # Send command
        self.serial.write(cmd.encode("ascii"))

        # For commands that require carriage return
        # Note: S# commands (trigger split) also need \r
        if "=" in cmd or ">" in cmd or "<" in cmd or "#" in cmd:
            self.serial.write(b"\r")

        return self._read_reply().strip()

    def _read_reply(
        self,
        first_byte_wait: float = 0.2,
        quiet_period: float = 0.1,
        max_wait: Optional[float] = None,
    ) -> str:
        """Read a command reply until the radar goes quiet.

        Replaces a fixed post-write sleep followed by a drain that stopped at
        the first empty ``in_waiting``. That stop condition made reply
        completeness depend on the reader winning a race against the wire: any
        gap longer than one poll cycle ended the read mid-reply, and a radar
        that took longer than the sleep to start answering returned "".
        Multi-line replies (``??``) and slow wire rates both widen those gaps,
        so the end of a reply is now defined by sustained silence instead.

        Costs about the same as the old fixed sleep in the common case, since
        ``quiet_period`` matches what that sleep already spent.

        Args:
            first_byte_wait: Give up this early if the radar never answers
                (many commands are silent).
            quiet_period: Silence that marks the end of a reply.
            max_wait: Hard ceiling; derived from baud when None.
        """
        if max_wait is None:
            # A command reply is small; 1KB of headroom covers the longest
            # (`??`) at any baud without stalling on silent commands.
            max_wait = max(0.5, 1024.0 / self.bytes_per_second + 0.3)

        response = ""
        start = time.monotonic()
        last_rx: Optional[float] = None

        while True:
            now = time.monotonic()
            if self.serial.in_waiting:
                response += self.serial.read(self.serial.in_waiting).decode(
                    "ascii", errors="ignore"
                )
                last_rx = now
            elif last_rx is not None:
                if now - last_rx >= quiet_period:
                    break
            elif now - start >= first_byte_wait:
                break  # Silent command — nothing to read
            if now - start >= max_wait:
                break
            time.sleep(0.005)

        return response

    def read_clock_sync(
        self,
        samples: int = 7,
        per_read_timeout: float = 0.2,
        max_sync_duration_s: float = 1.25,
        sample_interval_s: float = 0.01,
        store: bool = True,
    ) -> dict:
        """Map the OPS internal clock to host epoch via repeated ``C?`` reads.

        The radar stamps its rolling-buffer trigger on an internal clock
        (``trigger_time``, fractional seconds) that is *immune to USB read
        latency*. To convert that to a host epoch later we need the offset
        ``O = host_epoch - radar_clock``.

        Each read is bracketed by ``time.time()`` before and after a *tight*
        poll for the reply (not the fixed-0.1s ``_send_command`` path), so the
        radar sampled its clock somewhere inside that bracket. ``offset_s`` is
        the bracket midpoint minus the radar clock; ``read_latency_ms`` (the
        bracket width) bounds its uncertainty.

        Some OPS firmware reports ``C?`` in whole seconds while rolling-buffer
        captures report fractional ``trigger_time`` values. Whole-second reads
        are not directly usable because their unknown fractional phase can be
        almost one second off. When the clock is integer-only, this method keeps
        sampling until it observes a one-second rollover and uses that boundary
        to estimate the offset. If no precise sync is available, the summary is
        marked unusable so the trigger path can fall back to first-byte timing.

        Sound-triggered captures use this mapping to convert the radar's
        internal ``trigger_time`` to host epoch only when
        ``usable_for_trigger_timestamps`` is true. Returns a summary dict. By
        default it is also stored on ``self.last_clock_sync``; pass
        ``store=False`` for diagnostics that should not affect the live timing
        path. Never raises on a missing/garbled reply.
        """
        if not self.serial or not self.serial.is_open:
            raise ConnectionError("Not connected to radar")

        reads: List[dict] = []

        def read_once() -> bool:
            self.serial.reset_input_buffer()
            host_before = time.time()
            try:
                self.serial.write(b"C?")
            except serial.SerialTimeoutException:
                # Port jammed — radar not servicing commands (likely
                # mid-dump). Abandon the sync; the caller falls back to
                # first-byte timing. Retrying writes would only re-block.
                logger.warning(
                    "[OPS] Clock sync C? write timed out — port jammed, "
                    "abandoning clock sync"
                )
                return False
            buf = ""
            deadline = time.monotonic() + per_read_timeout
            while time.monotonic() < deadline:
                waiting = self.serial.in_waiting
                if waiting:
                    buf += self.serial.read(waiting).decode("ascii", errors="ignore")
                    if "}" in buf:  # full JSON reply received
                        break
                else:
                    time.sleep(0.0005)
            host_after = time.time()
            host_mid = (host_before + host_after) / 2.0
            radar_clock = _parse_ops_clock(buf)
            reads.append(
                {
                    "host_before": host_before,
                    "host_after": host_after,
                    "host_mid": host_mid,
                    "read_latency_ms": (host_after - host_before) * 1000.0,
                    "radar_clock_s": radar_clock,
                    "offset_s": None if radar_clock is None else host_mid - radar_clock,
                    "raw": buf.strip(),
                }
            )
            return True

        for idx in range(max(1, samples)):
            if idx:
                time.sleep(max(0.0, sample_interval_s))
            if not read_once():
                break

        valid = [r for r in reads if r["radar_clock_s"] is not None]
        has_fractional_clock = any(
            abs(float(r["radar_clock_s"]) - round(float(r["radar_clock_s"]))) > 1e-6 for r in valid
        )

        def find_integer_rollover() -> Optional[tuple[dict, dict]]:
            previous = None
            for read in valid:
                if previous is not None:
                    prev_clock = float(previous["radar_clock_s"])
                    current_clock = float(read["radar_clock_s"])
                    if current_clock - prev_clock == 1.0:
                        return previous, read
                previous = read
            return None

        rollover = None if has_fractional_clock else find_integer_rollover()
        sync_deadline = time.monotonic() + max(0.0, max_sync_duration_s)
        while valid and not has_fractional_clock and rollover is None:
            if time.monotonic() >= sync_deadline:
                break
            time.sleep(max(0.0, sample_interval_s))
            if not read_once():
                break
            valid = [r for r in reads if r["radar_clock_s"] is not None]
            rollover = find_integer_rollover()

        best_raw = min(valid, key=lambda r: r["read_latency_ms"]) if valid else None
        offsets = [r["offset_s"] for r in valid]
        usable_for_trigger_timestamps = False
        clock_sync_method = "no_valid_reads"
        clock_resolution = None
        best_offset_s = None
        rollover_uncertainty_ms = None

        if valid and has_fractional_clock:
            usable_for_trigger_timestamps = True
            clock_sync_method = "fractional_clock"
            clock_resolution = "fractional"
            best_offset_s = best_raw["offset_s"] if best_raw else None
        elif valid:
            clock_resolution = "integer"
            if rollover is not None:
                before_rollover, after_rollover = rollover
                rollover_host_mid = (
                    float(before_rollover["host_mid"]) + float(after_rollover["host_mid"])
                ) / 2.0
                best_offset_s = rollover_host_mid - float(after_rollover["radar_clock_s"])
                rollover_uncertainty_ms = (
                    float(after_rollover["host_mid"]) - float(before_rollover["host_mid"])
                ) * 1000.0
                usable_for_trigger_timestamps = True
                clock_sync_method = "integer_rollover"
            else:
                clock_sync_method = "integer_unusable_no_rollover"

        summary = {
            "samples": len(reads),
            "valid_samples": len(valid),
            "best_offset_s": best_offset_s,
            "raw_best_offset_s": best_raw["offset_s"] if best_raw else None,
            "best_read_latency_ms": best_raw["read_latency_ms"] if best_raw else None,
            "offset_spread_ms": (
                (max(offsets) - min(offsets)) * 1000.0 if len(offsets) >= 2 else None
            ),
            "clock_resolution": clock_resolution,
            "clock_sync_method": clock_sync_method,
            "usable_for_trigger_timestamps": usable_for_trigger_timestamps,
            "rollover_uncertainty_ms": rollover_uncertainty_ms,
            "reads": reads,
        }
        if store:
            self.last_clock_sync = summary
        if usable_for_trigger_timestamps:
            logger.info(
                "[OPS] Clock sync: method=%s offset=%.3fs best_read_latency=%.1fms "
                "spread=%sms rollover_uncertainty=%sms (%d/%d valid)",
                clock_sync_method,
                best_offset_s,
                best_raw["read_latency_ms"],
                "n/a"
                if summary["offset_spread_ms"] is None
                else f"{summary['offset_spread_ms']:.1f}",
                "n/a" if rollover_uncertainty_ms is None else f"{rollover_uncertainty_ms:.1f}",
                len(valid),
                len(reads),
            )
        elif valid:
            logger.warning(
                "[OPS] Clock sync unusable for trigger timestamps: method=%s "
                "resolution=%s spread=%sms (%d/%d valid); falling back to first-byte timing",
                clock_sync_method,
                clock_resolution,
                "n/a"
                if summary["offset_spread_ms"] is None
                else f"{summary['offset_spread_ms']:.1f}",
                len(valid),
                len(reads),
            )
        else:
            logger.warning("[OPS] Clock sync: no valid C? responses (%d attempts)", len(reads))
        return summary

    def get_info(self) -> dict:
        """
        Get radar module information.

        Returns:
            Dict with product, version, settings info
        """
        response = self._send_command("??")
        info = {}

        for line in response.split("\n"):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    data = json.loads(line)
                    info.update(data)
                except json.JSONDecodeError:
                    pass

        return info

    def get_firmware_version(self) -> str:
        """Get firmware version string."""
        response = self._send_command("?V")
        try:
            data = json.loads(response)
            return data.get("Version", "unknown")
        except json.JSONDecodeError:
            return response

    def validate_internal_trigger_firmware(self) -> str:
        """Require a compatible OPS243-A firmware release for internal triggering."""
        version = self._probe_firmware_version()
        required = self.REQUIRED_INTERNAL_TRIGGER_FIRMWARE
        match = _OPS243_FIRMWARE_RE.fullmatch(version or "")
        parsed = (
            tuple(int(match.group(part)) for part in ("major", "minor", "patch")) if match else None
        )
        minimum = self.INTERNAL_TRIGGER_FIRMWARE_MINIMUM
        if parsed is None or parsed[:2] != minimum[:2] or parsed < minimum:
            detected = f"v{version}" if version else "no response"
            raise RuntimeError(
                f"Internal hardware trigger requires OPS243-A firmware v{required} "
                "or newer in the 1.3 release train; "
                f"detected {detected}. Update the OPS243 before using --trigger hardware."
            )
        return version

    def set_units(self, unit: SpeedUnit):
        """
        Set speed output units.

        Args:
            unit: SpeedUnit enum value
        """
        self._send_command(unit.value)
        unit_names = {
            SpeedUnit.MPS: "m/s",
            SpeedUnit.MPH: "mph",
            SpeedUnit.KPH: "kph",
            SpeedUnit.FPS: "fps",
            SpeedUnit.CMS: "cm/s",
        }
        self._unit = unit_names[unit]

    def set_sample_rate(self, rate: int):
        """
        Set sampling rate for speed measurement.

        Higher rates allow detecting faster objects but reduce resolution.
        Max detectable speeds by rate:
        - 10kHz: 69.5 mph (too slow for golf)
        - 20kHz: 139.1 mph (marginal for fast shots)
        - 30kHz: 208.5 mph (RECOMMENDED for golf per OmniPreSense)
        - 50kHz: 347.7 mph (overkill, lower resolution)
        - 100kHz: 695.4 mph (overkill)

        Args:
            rate: Sample rate in samples/second
                  Common values: 10000, 20000, 30000 (recommended), 50000, 100000
        """
        rate_commands = {
            1000: "SI",
            5000: "SV",
            10000: "SX",
            20000: "S2",
            50000: "SL",
            100000: "SC",
        }

        if rate in rate_commands:
            self._send_command(rate_commands[rate])
        else:
            # Use configurable rate command (S=nn where nn is in ksps)
            # 30ksps is recommended for golf (S=30)
            ksps = rate // 1000
            self._send_command(f"S={ksps}")

    def set_buffer_size(self, size: int):
        """
        Set sample buffer size.

        Smaller buffers = faster updates but lower resolution.

        Args:
            size: Buffer size (128, 256, 512, or 1024)
        """
        size_commands = {128: "S(", 256: "S[", 512: "S<", 1024: "S>"}
        if size in size_commands:
            self._send_command(size_commands[size])

    def set_min_speed_filter(self, min_speed: float):
        """
        Set minimum speed filter - ignore speeds below this.

        Args:
            min_speed: Minimum speed to report (in current units)
        """
        self._send_command(f"R>{min_speed}")

    def set_max_speed_filter(self, max_speed: float):
        """
        Set maximum speed filter - ignore speeds above this.

        Args:
            max_speed: Maximum speed to report (in current units)
        """
        self._send_command(f"R<{max_speed}")

    def set_magnitude_filter(self, min_mag: int = 0, max_mag: int = 0):
        """
        Set magnitude (signal strength) filter.

        Higher magnitude = larger/closer/more reflective objects.

        Args:
            min_mag: Minimum magnitude to report (0 = no filter)
            max_mag: Maximum magnitude to report (0 = no filter)
        """
        if min_mag > 0:
            self._send_command(f"M>{min_mag}")
        if max_mag > 0:
            self._send_command(f"M<{max_mag}")

    def set_direction_filter(self, direction: Optional[Direction]):
        """
        Filter by direction at the hardware level.

        Per API doc AN-010-AD:
        - R+ = Inbound Only Direction (toward radar)
        - R- = Outbound Only Direction (away from radar)
        - R| = Both directions

        Args:
            direction: Direction.INBOUND, Direction.OUTBOUND, or None for both
        """
        if direction == Direction.INBOUND:
            cmd = "R+"
        elif direction == Direction.OUTBOUND:
            cmd = "R-"
        else:
            cmd = "R|"

        logger.info("[OPS] Setting direction filter: %s", cmd)
        self._send_command(cmd)

    def enable_json_output(self, enabled: bool = True):
        """
        Enable/disable JSON formatted output.

        Args:
            enabled: True for JSON, False for plain numbers
        """
        self._send_command("OJ" if enabled else "Oj")
        self._json_mode = enabled

    def enable_magnitude_report(self, enabled: bool = True):
        """
        Enable/disable magnitude reporting with speed.

        Args:
            enabled: True to include magnitude in readings
        """
        self._send_command("OM" if enabled else "Om")
        self._magnitude_enabled = enabled

    def set_transmit_power(self, level: int):
        """
        Set transmit power level.

        Args:
            level: 0-7, where 0 is max power and 7 is min power
        """
        if level < 0 or level > 7:
            raise ValueError("Power level must be 0-7")
        self._send_command(f"P{level}")

    def enable_peak_averaging(self, enabled: bool = True):
        """
        Enable/disable peak speed averaging.

        When enabled, filters out multiple speed reports from signal reflections
        and provides just the primary speed of the detected object. Recommended
        for golf to get cleaner ball speed readings.

        Args:
            enabled: True to enable averaging, False to disable
        """
        self._send_command("K+" if enabled else "K-")

    def set_fft_size(self, size: int):
        """
        Set FFT size for frequency analysis.

        FFT size affects speed resolution and report rate.
        The X= command sets FFT size as a multiplier of buffer size:
        - X=1: FFT = buffer size
        - X=2: FFT = 2x buffer size
        - X=32: FFT = 32x buffer size (4096 with 128 buffer)

        With 30ksps and buffer 128:
        - X=1 (128 FFT): ~234 Hz, ±1.6 mph resolution
        - X=2 (256 FFT): ~117 Hz, ±0.8 mph resolution
        - X=32 (4096 FFT): ~56 Hz, ±0.1 mph resolution (recommended for golf)

        Args:
            size: FFT multiplier (1, 2, 4, 8, 16, 32)
        """
        valid_sizes = [1, 2, 4, 8, 16, 32]
        if size not in valid_sizes:
            raise ValueError(f"FFT size must be one of {valid_sizes}")
        self._send_command(f"X={size}")

    def set_num_reports(self, num: int):
        """
        Set number of objects to report per sample cycle.

        For golf, setting this to 4+ allows detecting both club head and ball
        in the same sample window. The radar will report the N strongest
        signals detected.

        Args:
            num: Number of reports per cycle (1-9 with On, up to 16 with O=n)
        """
        if num < 1:
            num = 1
        if num <= 9:
            cmd = f"O{num}"
        else:
            cmd = f"O={num}"

        logger.debug("[OPS] Sending num_reports command: %s", cmd)
        self._send_command(cmd)

    def system_reset(self):
        """Perform a full system reset including the clock."""
        self._send_command("P!")
        time.sleep(1)

    def get_serial_number(self) -> str:
        """Get the radar's serial number."""
        response = self._send_command("?N")
        try:
            data = json.loads(response)
            return data.get("SerialNumber", "unknown")
        except json.JSONDecodeError:
            return response

    def get_speed_filter(self) -> dict:
        """
        Get current speed filter settings.

        Returns:
            Dict with min/max speed filter values
        """
        response = self._send_command("R?")
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            return {"raw": response}

    def get_current_units(self) -> str:
        """Get the currently configured speed units."""
        response = self._send_command("U?")
        try:
            data = json.loads(response)
            return data.get("Units", "unknown")
        except json.JSONDecodeError:
            return response

    def _parse_reading(self, line: str) -> Optional[SpeedReading]:
        """
        Parse a reading from the radar output.

        Direction is determined by the SIGN of the speed value.

        With R| (both directions) mode:
        - Negative speed = OUTBOUND (away from radar - ball flight)
        - Positive speed = INBOUND (toward radar - backswing)

        With O4 (multi-object) mode, speed and magnitude are arrays.
        We return the first/strongest reading here; the full array is
        available via read_speed_multi().

        Args:
            line: Raw line from serial output

        Returns:
            SpeedReading or None if parse fails
        """
        # Always log raw line when debugging enabled (before any parsing)
        if _show_raw_readings:
            print(f"[SERIAL] {line!r}")

        try:
            if self._json_mode and line.startswith("{"):
                data = json.loads(line)
                speed_data = data.get("speed", 0)
                magnitude_data = data.get("magnitude")

                # Handle array format from O4 multi-object mode
                # Arrays are ordered by magnitude (strongest first)
                if isinstance(speed_data, list):
                    if not speed_data:
                        return None
                    speed = float(speed_data[0])
                    magnitude = float(magnitude_data[0]) if magnitude_data else None

                    if _show_raw_readings:
                        print(
                            f"[MULTI] {len(speed_data)} objects: speeds={speed_data} mags={magnitude_data}"
                        )
                else:
                    speed = float(speed_data)
                    magnitude = float(magnitude_data) if magnitude_data else None

                # Direction from sign of speed value
                # Negative = OUTBOUND (away from radar - golf ball flight)
                # Positive = INBOUND (toward radar - backswing)
                if speed > 0:
                    direction = Direction.INBOUND
                else:
                    direction = Direction.OUTBOUND

                # Debug: print raw reading to console (sign indicates direction)
                if _show_raw_readings:
                    print(f"[RAW] {speed:+.1f} mph -> {direction.value} (mag: {magnitude})")

                # Log parsed reading for debugging
                logger.debug(
                    "[OPS] PARSED: raw_speed=%.2f abs_speed=%.2f dir=%s mag=%s",
                    speed,
                    abs(speed),
                    direction.value,
                    magnitude,
                )

                return SpeedReading(
                    speed=abs(speed),
                    direction=direction,
                    magnitude=magnitude,
                    timestamp=time.time(),
                    unit=self._unit,
                )

            # Plain number format - direction from sign
            speed = float(line)
            if speed > 0:
                direction = Direction.INBOUND
            else:
                direction = Direction.OUTBOUND

            # Debug: print raw reading to console
            if _show_raw_readings:
                print(f"[RAW] {speed:+.1f} mph -> {direction.value}")

            logger.debug(
                "[OPS] PARSED (plain): raw_speed=%.2f abs_speed=%.2f dir=%s",
                speed,
                abs(speed),
                direction.value,
            )

            return SpeedReading(
                speed=abs(speed), direction=direction, timestamp=time.time(), unit=self._unit
            )
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning("[OPS] Failed to parse reading: %r - %s", line, e)
            return None

    def _parse_readings(self, line: str) -> List[SpeedReading]:
        """Parse all readings from a radar output line.

        JSON multi-object mode reports arrays ordered by magnitude. For normal
        launch-monitor trigger detection, _parse_reading() keeps returning the
        strongest target for backwards compatibility. Swing-speed training needs
        every candidate so it can choose the fastest club-head-like reflection.
        """
        if _show_raw_readings:
            print(f"[SERIAL] {line!r}")

        try:
            if self._json_mode and line.startswith("{"):
                data = json.loads(line)
                speed_data = data.get("speed", 0)
                magnitude_data = data.get("magnitude")

                if not isinstance(speed_data, list):
                    reading = self._parse_reading(line)
                    return [reading] if reading else []

                readings = []
                for index, raw_speed in enumerate(speed_data):
                    speed = float(raw_speed)
                    if speed > 0:
                        direction = Direction.INBOUND
                    else:
                        direction = Direction.OUTBOUND

                    magnitude = None
                    if isinstance(magnitude_data, list) and index < len(magnitude_data):
                        magnitude = float(magnitude_data[index])

                    readings.append(
                        SpeedReading(
                            speed=abs(speed),
                            direction=direction,
                            magnitude=magnitude,
                            timestamp=time.time(),
                            unit=self._unit,
                        )
                    )
                return readings

            reading = self._parse_reading(line)
            return [reading] if reading else []
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning("[OPS] Failed to parse readings: %r - %s", line, e)
            return []

    def save_config(self):
        """Save current configuration to persistent memory."""
        self._send_command("A!")
        time.sleep(1)  # Wait for flash write

    def reset_config(self):
        """Reset configuration to factory defaults."""
        self._send_command("AX")
        time.sleep(1)

    # =========================================================================
    # Rolling Buffer Mode (G1)
    # =========================================================================

    def enter_rolling_buffer_mode(self, pre_trigger_segments: int = 16, sample_rate_ksps: int = 30):
        """
        Enter rolling buffer mode using the verified working sequence.

        This is the SINGLE SOURCE OF TRUTH for entering rolling buffer mode.
        All other methods that need rolling buffer mode should call this.

        The sequence follows OmniPreSense API doc AN-010-AD exactly:
        1. PI - reset to idle (clean state)
        2. GC - enter rolling buffer mode
        3. PA - activate sampling
        4. S=30 - set sample rate (with \\r)
        5. S#n - set trigger split (with \\r)
        6. PA - reactivate (CRITICAL after settings changes)
        7. Wait for buffer to fill

        Args:
            pre_trigger_segments: Number of pre-trigger segments (0-32).
                Each segment = 128 samples = ~4.27ms at 30ksps.
                Default 12 gives ~51ms pre-trigger, ~85ms post-trigger.
        """
        if not self.serial or not self.serial.is_open:
            raise ConnectionError("Not connected to radar")

        print(
            f"[RADAR] Entering rolling buffer mode (S#{pre_trigger_segments}, S={sample_rate_ksps})..."
        )
        logger.info(
            "[OPS] Entering rolling buffer mode (pre_trigger_segments=%d)...", pre_trigger_segments
        )

        # Clear any stale data
        self.serial.reset_input_buffer()

        # Step 1: Reset to idle for clean state
        self.serial.write(b"PI")
        time.sleep(0.2)
        logger.debug("[OPS] PI: reset to idle")

        # Step 2: Enter rolling buffer mode
        self.serial.write(b"GC")
        time.sleep(0.1)
        logger.debug("[OPS] GC: rolling buffer mode")

        # Step 3: Activate sampling
        self.serial.write(b"PA")
        time.sleep(0.1)
        logger.debug("[OPS] PA: activate sampling")

        # Step 4: Set sample rate - requires \r
        self.serial.write(f"S={sample_rate_ksps}\r".encode())
        self.serial.flush()
        time.sleep(0.15)
        logger.debug("[OPS] S=%d: %dksps sample rate", sample_rate_ksps, sample_rate_ksps)

        # Step 5: Set trigger split - requires \r
        pre_trigger_segments = max(0, min(32, pre_trigger_segments))
        self.serial.write(f"S#{pre_trigger_segments}\r".encode())
        self.serial.flush()
        time.sleep(0.15)
        logger.debug("[OPS] S#%d: pre-trigger segments", pre_trigger_segments)

        # Step 6: CRITICAL - Reactivate after settings changes
        self.serial.write(b"PA")
        time.sleep(0.1)
        logger.debug("[OPS] PA: reactivate sampling")

        # Clear any response data from commands
        self.serial.reset_input_buffer()

        # Step 7: Wait for buffer to fill
        # At 30ksps, 4096 samples takes ~137ms, but we wait a bit longer
        # to ensure stable state before accepting triggers
        time.sleep(0.3)

        print(
            f"[RADAR] Rolling buffer mode ACTIVE (S#{pre_trigger_segments}, {sample_rate_ksps}ksps)"
        )
        logger.info(
            "[OPS] Rolling buffer mode active (S#%d, %dksps)",
            pre_trigger_segments,
            sample_rate_ksps,
        )

    def disable_rolling_buffer(self):
        """Disable rolling buffer mode and return to normal CW mode."""
        logger.info("[OPS] Disabling rolling buffer mode...")
        self._send_command("GS")  # Return to standard CW mode
        time.sleep(0.1)
        logger.info("[OPS] Rolling buffer mode disabled (returned to CW mode)")

    def persist_rolling_buffer_mode(
        self, pre_trigger_segments: int = 16, sample_rate_ksps: int = 30
    ):
        """
        Save rolling buffer mode to persistent memory.

        The OPS243-A has a bug where the HOST_INT pin mode switches
        unexpectedly when transitioning from normal mode (GS) to rolling
        buffer mode (GC) at runtime. OmniPreSense workaround:

        1. Enter rolling buffer mode (GC) with desired settings
        2. Save to persistent memory (A!)
        3. Power cycle the board

        After power cycle, the board starts in rolling buffer mode and
        HOST_INT works correctly. Re-arm after each capture with PA.

        This only needs to be done ONCE per radar board (or when changing
        sample rate / pre-trigger settings).

        Args:
            pre_trigger_segments: Number of pre-trigger segments (0-32).
            sample_rate_ksps: Sample rate in ksps (default: 30).
        """
        if not self.serial or not self.serial.is_open:
            raise ConnectionError("Not connected to radar")

        logger.info("[OPS] Persisting rolling buffer mode to flash memory...")

        # Enter rolling buffer mode with desired settings
        self.enter_rolling_buffer_mode(
            pre_trigger_segments=pre_trigger_segments, sample_rate_ksps=sample_rate_ksps
        )

        # Save to persistent memory
        self.serial.write(b"A!")
        time.sleep(0.5)

        logger.info(
            "[OPS] Rolling buffer mode saved to persistent memory. "
            "Power cycle the board for changes to take effect."
        )
        print("[RADAR] Settings saved to persistent memory.")
        print("[RADAR] Power cycle the board (unplug USB, wait 3s, replug).")

    def restore_rolling_buffer_mode(
        self, pre_trigger_segments: int = 16, sample_rate_ksps: int = 30
    ):
        """
        Restore rolling-buffer mode after a temporary speed-mode session.

        Swing-speed training uses CW speed reporting and sends GS as part of
        that setup. Before returning the radar to launch-monitor use, put it
        back into rolling-buffer mode using volatile commands only. Saving with
        A! is intentionally reserved for the one-time setup path because it
        writes persistent flash and is not needed for normal mode switching.
        """
        if not self.serial or not self.serial.is_open:
            raise ConnectionError("Not connected to radar")

        logger.info("[OPS] Restoring rolling buffer mode after speed mode...")
        self.set_units(SpeedUnit.MPH)
        logger.info("[OPS] Units: MPH")
        self.set_transmit_power(3)
        logger.info("[OPS] Transmit power: level 3 (reduced to avoid clipping)")
        self.enter_rolling_buffer_mode(
            pre_trigger_segments=pre_trigger_segments,
            sample_rate_ksps=sample_rate_ksps,
        )
        self.serial.reset_input_buffer()
        logger.info("[OPS] Rolling buffer mode restored for launch-monitor use")

    def prepare_persisted_rolling_buffer(
        self, pre_trigger_segments: int = 16, sample_rate_ksps: int = 30
    ):
        """
        Prepare a radar that already booted in persisted rolling-buffer mode.

        Do not send GC/A! here. The OPS243-A HOST_INT workaround requires the
        board to boot in rolling-buffer mode from flash. If swing-speed mode
        has put the radar into speed-reporting mode, save rolling-buffer mode
        separately and physically power-cycle the radar before launch mode.
        """
        if not self.serial or not self.serial.is_open:
            raise ConnectionError("Not connected to radar")

        self.set_units(SpeedUnit.MPH)
        logger.info("[OPS] Units: MPH")
        self.set_transmit_power(3)
        logger.info("[OPS] Transmit power: level 3 (reduced to avoid clipping)")
        self.set_sample_rate(sample_rate_ksps * 1000)
        logger.info("[OPS] Sample rate: %dksps", sample_rate_ksps)
        self.rearm_rolling_buffer(pre_trigger_segments=pre_trigger_segments)
        logger.info(
            "[OPS] Persisted rolling buffer prepared (S#%d, %dksps)",
            pre_trigger_segments,
            sample_rate_ksps,
        )

    def trigger_capture(self, timeout: Optional[float] = None) -> str:
        """
        Trigger buffer capture and return raw I/Q data.

        Sends S! command to dump the rolling buffer contents.
        The response contains:
        - {"sample_time": "xxx.xxx"}
        - {"trigger_time": "xxx.xxx"}
        - {"I": [4096 integers...]}
        - {"Q": [4096 integers...]}

        Note: one dump is ~40.6KB of JSON — 1.8s over UART at 230,400 baud,
        21s at the 19,200 factory default. The timeout is therefore a floor
        that scales with the negotiated baud, not an absolute.

        Args:
            timeout: Minimum time to wait for the response (default 10s).
                Scaled up when the current baud needs longer.

        Returns:
            Raw response string containing JSON lines
        """
        if not self.serial or not self.serial.is_open:
            raise ConnectionError("Not connected to radar")

        timeout = self.transfer_budget_s(floor=10.0 if timeout is None else timeout)

        # Clear input buffer
        self.serial.reset_input_buffer()

        # Send trigger command
        self.serial.write(b"S!\r")
        self.serial.flush()

        response_lines = []
        start_time = time.time()
        last_data_time = start_time
        bytes_received = 0

        # Read data until timeout or complete response
        while (time.time() - start_time) < timeout:
            if self.serial.in_waiting:
                chunk = self.serial.read(self.serial.in_waiting)
                response_lines.append(chunk.decode("ascii", errors="ignore"))
                bytes_received += len(chunk)
                last_data_time = time.time()

                # Check if we have complete data (Q array ends the response)
                full_response = "".join(response_lines)
                if '"Q"' in full_response:
                    # Look for closing bracket of Q array followed by newline or EOF
                    q_idx = full_response.rfind('"Q"')
                    remaining = full_response[q_idx:]
                    if "]}" in remaining or (
                        remaining.rstrip().endswith("]")
                        and remaining.count("[") == remaining.count("]")
                    ):
                        break

                time.sleep(0.01)  # Short sleep to accumulate data
            else:
                # No data available
                # If we've received some data and haven't gotten more in 0.5s, consider done
                if bytes_received > 100 and (time.time() - last_data_time) > 0.5:
                    full_response = "".join(response_lines)
                    if '"Q"' in full_response:
                        break
                time.sleep(0.02)

        full_response = "".join(response_lines)

        # Only log issues, not normal operation
        if not full_response:
            logger.warning(
                "[OPS] S! trigger returned empty response after %.1fs", time.time() - start_time
            )
        else:
            logger.info(
                "[OPS] S! trigger: %d bytes in %.1fs", len(full_response), time.time() - start_time
            )
            if len(full_response) < 1000:
                # Short response usually means mode not configured correctly
                logger.info(
                    "[OPS] S! response too short (%s bytes): %s",
                    len(full_response),
                    repr(full_response[:100]),
                )

        return full_response

    def wait_for_hardware_trigger(
        self,
        timeout: float = 30.0,
        dump_grace: Optional[float] = None,
        cancel_event: Optional[threading.Event] = None,
        on_first_byte: Optional[Callable[[], None]] = None,
    ) -> str:
        """
        Wait for hardware trigger to fire and read the buffer dump.

        Unlike trigger_capture() which sends S!, this method just waits
        for data to appear on serial — triggered externally via J3 pin 3
        (HOST_INT). Used with SoundTrigger (SparkFun SEN-14262).

        Args:
            timeout: Maximum time to wait for the trigger to fire
            dump_grace: Extra time allowed for the dump to finish once the
                first byte has arrived. A trigger firing near the end of the
                timeout window must not have its dump cut off by the original
                deadline. None derives it from baud (floor 8s): the ~40.6KB
                dump takes ~1.8s over UART at 230,400 but 21s at 19,200.
            cancel_event: Stops an idle wait promptly during shutdown. Once a
                dump has started, it is allowed to finish normally.
            on_first_byte: Called once when a hardware-triggered dump begins.

        Returns:
            Raw response string containing JSON lines, or empty string on timeout
        """
        if not self.serial or not self.serial.is_open:
            raise ConnectionError("Not connected to radar")

        if dump_grace is None:
            dump_grace = self.transfer_budget_s(floor=8.0)

        # Clear any stale data
        self.serial.reset_input_buffer()

        response_lines = []
        idle_bytes = bytearray()
        capture_markers = (b'{"sample_time"', b'{"trigger_time"')
        start_time = time.time()
        deadline = start_time + timeout
        last_data_time = None
        bytes_received = 0
        self.last_hardware_trigger_first_byte_timestamp = None
        recovery_required = bool(getattr(self, "_hardware_trigger_recovery_required", False))
        capture_started = False
        if recovery_required:
            logger.info(
                "[OPS] Re-arm recovery: discarding trailing output until a fresh capture starts"
            )

        while time.time() < deadline:
            waiting = self.serial.in_waiting
            if waiting:
                chunk = self.serial.read(waiting)
                first_byte_timestamp = None
                if not capture_started:
                    idle_bytes.extend(chunk)
                    marker_offsets = [idle_bytes.find(marker) for marker in capture_markers]
                    marker_offsets = [offset for offset in marker_offsets if offset >= 0]
                    if not marker_offsets:
                        # Preserve enough trailing bytes to recognize a marker split
                        # across reads, while discarding unsolicited CLI/clock noise.
                        max_marker = max(len(marker) for marker in capture_markers)
                        if len(idle_bytes) > max_marker:
                            del idle_bytes[:-max_marker]
                        time.sleep(0.01)
                        continue
                    capture_start = min(marker_offsets)
                    chunk = bytes(idle_bytes[capture_start:])
                    idle_bytes.clear()
                    capture_started = True
                    if recovery_required:
                        self._hardware_trigger_recovery_required = False
                        logger.info("[OPS] Re-arm recovery: fresh capture boundary found")
                    first_byte_timestamp = time.time()

                response_lines.append(chunk.decode("ascii", errors="ignore"))
                bytes_received += len(chunk)
                if first_byte_timestamp is not None:
                    last_data_time = first_byte_timestamp
                    self.last_hardware_trigger_first_byte_timestamp = last_data_time
                    if on_first_byte is not None:
                        try:
                            on_first_byte()
                        except Exception:
                            logger.warning("[OPS] First-byte callback failed", exc_info=True)
                    # The trigger fired — the dump is now in flight. Extend
                    # the deadline so a late trigger gets its full dump.
                    deadline = max(deadline, last_data_time + dump_grace)
                    logger.debug(
                        "[OPS] Hardware trigger: first byte after %.1fs",
                        last_data_time - start_time,
                    )
                else:
                    last_data_time = time.time()

                # Check if we have complete I/Q data
                full_response = "".join(response_lines)
                if '"Q"' in full_response:
                    q_idx = full_response.rfind('"Q"')
                    remaining = full_response[q_idx:]
                    if "]}" in remaining or (
                        remaining.rstrip().endswith("]")
                        and remaining.count("[") == remaining.count("]")
                    ):
                        break

                time.sleep(0.01)
            else:
                if cancel_event is not None and cancel_event.is_set() and last_data_time is None:
                    logger.info("[OPS] Hardware trigger wait cancelled before capture")
                    break
                # If we've started receiving data, use shorter timeout
                if last_data_time and (time.time() - last_data_time) > 0.5:
                    full_response = "".join(response_lines)
                    if '"Q"' in full_response:
                        break
                time.sleep(0.02)

        full_response = "".join(response_lines) if capture_started else ""

        if not full_response:
            logger.info("[OPS] Hardware trigger: no data received within %.0fs", timeout)
        else:
            logger.info(
                "[OPS] Hardware trigger: %d bytes in %.1fs",
                len(full_response),
                time.time() - start_time,
            )

        return full_response

    def rearm_rolling_buffer(self, pre_trigger_segments: int = 16):
        """
        Re-arm rolling buffer for next capture.

        After a hardware trigger dumps data, the sensor pauses in Idle mode.
        Per OmniPreSense: "do a PA or GC to start the Rolling Buffer
        sampling again" after each capture.

        We also re-send S#n to ensure the pre/post trigger split is
        correct, then PA again to activate with the new setting.

        Args:
            pre_trigger_segments: Number of pre-trigger segments (0-32).
                Each segment = 128 samples = ~4.27ms at 30ksps.
        """
        if not self.serial or not self.serial.is_open:
            raise ConnectionError("Not connected to radar")

        # Drain the serial buffer until no new bytes arrive for 200ms.
        # A full I/Q dump is ~41KB at 57600 baud (~7s). If the previous
        # capture wasn't fully read, bytes are still streaming in.
        # Bounded: if the radar streams continuously (immediate re-triggers
        # or a fallback into streaming mode), an unbounded drain hangs the
        # monitor thread forever with nothing logged and the radar never
        # re-armed. Bail out loudly instead, keeping a tail sample of the
        # traffic so the log identifies WHAT the radar was sending.
        # The budget is a floor scaled by baud: unchanged over USB and over
        # UART at 230,400 (dump ~1.8s), but a 19,200 link needs ~21s just to
        # finish one straggling dump and would otherwise trip this every time.
        drain_budget = self.transfer_budget_s(floor=self.REARM_DRAIN_TIMEOUT_S)
        drain_start = time.time()
        total_drained = 0
        drain_tail = b""
        drain_timed_out = False
        while True:
            waiting = self.serial.in_waiting
            if waiting:
                chunk = self.serial.read(waiting)
                total_drained += len(chunk)
                drain_tail = (drain_tail + chunk)[-200:]
            time.sleep(0.2)
            if self.serial.in_waiting == 0:
                break
            if time.time() - drain_start > drain_budget:
                drain_timed_out = True
                break
        if drain_timed_out:
            logger.warning(
                "[OPS] Re-arm drain timed out after %.1fs (%d bytes and still "
                "streaming) — radar is continuously sending. Last bytes: %r",
                drain_budget,
                total_drained,
                drain_tail.decode("ascii", errors="replace"),
            )
        else:
            logger.info(
                "[OPS] Re-arm drain: %d bytes in %.1fs", total_drained, time.time() - drain_start
            )

        pre_trigger_segments = max(0, min(32, pre_trigger_segments))
        try:
            # Restart sampling
            self.serial.write(b"PA")
            self.serial.flush()
            time.sleep(0.1)

            # Re-send trigger split (may reset after capture dump)
            self.serial.write(f"S#{pre_trigger_segments}\r".encode())
            self.serial.flush()
            time.sleep(0.1)

            # Reactivate after settings change
            self.serial.write(b"PA")
            self.serial.flush()
            time.sleep(0.15)
        except serial.SerialTimeoutException:
            # Radar not servicing commands (likely mid-dump from an
            # immediate re-trigger). Don't hang the capture thread — the
            # next wait cycle reads out whatever the radar is sending and
            # re-arms again.
            logger.warning(
                "[OPS] Re-arm write timed out — radar busy (mid-dump?); "
                "re-arm will be retried after the next capture cycle"
            )
            return

        self.serial.reset_input_buffer()
        logger.info("[OPS] Rolling buffer re-armed (S#%d)", pre_trigger_segments)

    def _drain_rearm_serial(self, quiet_period: float = 0.2):
        """Drain any tail bytes before changing the board mode.

        The internal trigger returns a complete I/Q dump without an ``S!``
        command.  A few firmware versions can still have a short tail in the
        UART buffer when the Q array is complete, so GC must wait for a quiet
        gap before it is sent.  The wait is bounded by the same dump budget
        used by the ordinary re-arm path.
        """
        if not self.serial or not self.serial.is_open:
            raise ConnectionError("Not connected to radar")

        budget = self.transfer_budget_s(floor=self.REARM_DRAIN_TIMEOUT_S)
        started = time.monotonic()
        last_data = started
        drained = 0

        while time.monotonic() - started < budget:
            waiting = self.serial.in_waiting
            if waiting:
                drained += len(self.serial.read(waiting))
                last_data = time.monotonic()
            elif time.monotonic() - last_data >= quiet_period:
                break
            time.sleep(0.02)

        if drained:
            logger.info("[OPS] Internal-trigger re-arm drained %d trailing bytes", drained)

    @staticmethod
    def _format_internal_trigger_threshold(threshold_mph: float) -> str:
        """Format the outbound (negative radar velocity) ``ST`` threshold."""
        return f"-{abs(float(threshold_mph)):g}"

    def configure_for_internal_speed_trigger(
        self,
        trigger_threshold_mph: float = 25.0,
        pre_trigger_segments: int = 6,
        trigger_magnitude: int = 25,
        sample_rate_ksps: int = 30,
    ):
        """Configure the OPS243's internal speed-triggered rolling buffer.

        The board owns the trigger edge in this mode.  ``ST`` arms the
        internal speed threshold, ``GC`` enters rolling-buffer mode, and the
        detector settings are restored after GC because the firmware resets
        them when changing modes.  This path intentionally does not enable
        the optional SZ board-processing stream.
        """
        if not self.serial or not self.serial.is_open:
            raise ConnectionError("Not connected to radar")

        threshold = float(trigger_threshold_mph)
        if not math.isfinite(threshold) or threshold < 0:
            raise ValueError("Trigger threshold must be non-negative")
        if not isinstance(pre_trigger_segments, int):
            raise ValueError("Pre-trigger segments must be an integer")
        if not isinstance(trigger_magnitude, int) or not 1 <= trigger_magnitude <= 2000:
            raise ValueError("Trigger magnitude must be between 1 and 2000")
        if sample_rate_ksps != 30:
            raise ValueError("Internal speed trigger requires a 30 ksps sample rate")

        self.validate_internal_trigger_firmware()

        pre_trigger_segments = max(0, min(32, pre_trigger_segments))
        signed_threshold = self._format_internal_trigger_threshold(threshold)
        self._speed_read_buffer = ""
        self.serial.reset_input_buffer()
        self._hardware_trigger_recovery_required = False

        # OPS243 internal-trigger order: idle, arm the threshold, enter GC.
        self._send_command("PI")
        self.serial.write(f"ST{signed_threshold}\r".encode("ascii"))
        self.serial.flush()
        time.sleep(0.1)
        self._send_command("GC")

        self._internal_speed_trigger_config = (
            threshold,
            pre_trigger_segments,
            trigger_magnitude,
            sample_rate_ksps,
        )
        self._restore_internal_speed_trigger_settings()
        self.serial.reset_input_buffer()

        # A 4,096-sample buffer takes about 136.5 ms at 30 ksps.
        time.sleep(0.3)
        logger.info(
            "[OPS] Internal speed trigger armed (ST%s, S#%d, S=%d, SM%d)",
            signed_threshold,
            pre_trigger_segments,
            sample_rate_ksps,
            trigger_magnitude,
        )

    def _restore_internal_speed_trigger_settings(self):
        """Restore detector settings cleared by the last GC command."""
        if self._internal_speed_trigger_config is None:
            raise RuntimeError("Internal speed trigger has not been configured")

        threshold, pre_trigger_segments, trigger_magnitude, sample_rate_ksps = (
            self._internal_speed_trigger_config
        )
        threshold_text = f"{threshold:g}"
        signed_threshold = self._format_internal_trigger_threshold(threshold)

        self.set_sample_rate(sample_rate_ksps * 1000)
        self.set_units(SpeedUnit.MPH)
        self.set_transmit_power(0)
        self.set_buffer_size(128)
        self.set_fft_size(2)
        self._send_command("R-")
        self._send_command(f"R>{threshold_text}")
        self.enable_json_output(True)
        self.enable_magnitude_report(True)
        self._send_command("W0")
        self._send_command(f"S#{pre_trigger_segments}")

        # ST/SM require a terminating carriage return and are deliberately
        # sent after MPH is restored so the threshold is interpreted in mph.
        self.serial.write(f"ST{signed_threshold}\r".encode("ascii"))
        self.serial.write(f"SM{trigger_magnitude}\r".encode("ascii"))
        self.serial.flush()
        time.sleep(0.1)

    def rearm_internal_speed_trigger(self, sample_rate_ksps: int = 30) -> bool:
        """Re-arm an internal hardware trigger after a completed dump.

        GC restarts the rolling buffer but also restores firmware defaults, so
        the cached detector settings are applied again.  A serial write timeout
        is recoverable: the capture is valid and the next cycle can retry the
        GC sequence.
        """
        if not self.serial or not self.serial.is_open:
            raise ConnectionError("Not connected to radar")
        if sample_rate_ksps != 30:
            raise ValueError("Internal speed trigger requires a 30 ksps sample rate")
        if self._internal_speed_trigger_config is None:
            raise RuntimeError("Internal speed trigger has not been configured")

        try:
            self._drain_rearm_serial()
            self.serial.reset_input_buffer()
            self.serial.write(b"GC")
            self.serial.flush()
            time.sleep(0.15)
            self._restore_internal_speed_trigger_settings()
            self.serial.reset_input_buffer()
        except serial.SerialTimeoutException as error:
            self._hardware_trigger_recovery_required = True
            logger.warning(
                "[OPS] Internal trigger re-arm timed out; capture retained and "
                "re-arm will be retried: %s",
                error,
            )
            return False

        time.sleep((4096 / (sample_rate_ksps * 1000)) + 0.05)
        logger.info("[OPS] Internal speed trigger re-armed (GC)")
        return True

    def configure_for_rolling_buffer(
        self, pre_trigger_segments: int = 16, sample_rate_ksps: int = 30
    ):
        """
        Configure radar optimally for rolling buffer mode.

        This is the high-level API for entering rolling buffer mode.
        Internally calls enter_rolling_buffer_mode() which uses the
        verified working sequence.

        Settings:
        - Units: MPH
        - Transmit power: Level 3 (reduced to avoid ADC clipping)
        - Sample rate: 30ksps (max ~208 mph, required for golf)
        - Rolling buffer enabled (GC command)
        - Trigger split: Configurable pre-trigger segments

        Args:
            pre_trigger_segments: Number of pre-trigger segments (0-32).
                Each segment = 128 samples = ~4.27ms at 30ksps.
                Default 12 gives ~51ms pre-trigger, ~85ms post-trigger.
        """
        # Set units to MPH first
        self.set_units(SpeedUnit.MPH)
        logger.info("[OPS] Units: MPH")

        # Reduced transmit power to avoid ADC saturation on close targets.
        # Level 0=max, 7=min. Rolling buffer captures raw I/Q at short range,
        # so max power clips the 12-bit ADC.
        self.set_transmit_power(3)
        logger.info("[OPS] Transmit power: level 3 (reduced to avoid clipping)")

        # Enter rolling buffer mode using the single source of truth
        self.enter_rolling_buffer_mode(
            pre_trigger_segments=pre_trigger_segments, sample_rate_ksps=sample_rate_ksps
        )

        logger.info("[OPS] Rolling buffer mode configured")

    def configure_for_swing_speed_training(self, min_speed_mph: float = 20, num_reports: int = 1):
        """
        Configure the radar for standalone swing-speed training.

        Training streams raw CW speed reports and never captures a rolling
        buffer. Per AN-010-AD (API Commands, p21) GS selects "CW operation
        only" -- the documented way out of rolling-buffer mode and the
        OPS243-A default -- so this path sends GS and never GC/S#n/S!.

        Nothing here writes flash (no A!). The HOST_INT workaround needs the
        board to *boot* into rolling-buffer mode from persistent memory, so a
        stray A! in this path would clobber launch mode until the setup script
        was re-run. Use restore_rolling_buffer_mode() to hand the radar back.

        Deliberately kept separate from configure_for_speed_trigger(): that
        path tunes the radar to *detect* a swing and immediately hand off to
        the rolling buffer, so retuning it for the launch monitor must not
        silently change training measurements. The two configurations are
        expected to diverge.

        Args:
            min_speed_mph: Ignore reports below this speed, filtering leg
                movement and backswing (R>n).
            num_reports: Speed reports per sample cycle (On). >1 surfaces
                several candidate reflections so the caller can pick the
                club head.
        """
        if not self.serial or not self.serial.is_open:
            raise ConnectionError("Not connected to radar")

        logger.info("[OPS] Configuring for swing speed training (CW speed reporting)...")

        # CW mode, then idle while we reconfigure.
        self._send_command("GS")
        time.sleep(0.1)
        logger.info("[OPS] GS: CW mode (standard radar signal processing)")

        self._send_command("PI")
        time.sleep(0.1)

        self.set_units(SpeedUnit.MPH)
        logger.info("[OPS] Units: MPH")

        # Max transmit power: the club head is a smaller, less reflective
        # target than a ball, and there is no rolling-buffer capture to clip.
        self.set_transmit_power(0)
        logger.info("[OPS] Transmit power: max (P0)")

        # 30ksps -> 208mph ceiling, above any club head speed (AN-027 Table 1).
        self.set_sample_rate(30000)
        time.sleep(0.1)
        logger.info("[OPS] Sample rate: 30ksps")

        self.set_buffer_size(128)
        time.sleep(0.1)
        logger.info("[OPS] Buffer size: 128")

        # X=2 (256-point FFT) trades speed resolution for report rate, keeping
        # enough reports across the ~100ms of a swing to find its peak.
        self.set_fft_size(2)
        time.sleep(0.1)
        logger.info("[OPS] FFT size: 256 (X=2)")

        # Outbound only: the downswing moves away from a radar placed behind
        # the player, so R- drops the backswing.
        self._send_command("R-")
        time.sleep(0.05)
        logger.info("[OPS] Direction filter: outbound only (R-)")

        self.set_min_speed_filter(min_speed_mph)
        time.sleep(0.05)
        logger.info("[OPS] Min speed filter: %s mph", min_speed_mph)

        self.set_num_reports(num_reports)
        time.sleep(0.05)
        logger.info("[OPS] Reports per cycle: %d", num_reports)

        self.enable_json_output(True)
        self.enable_magnitude_report(True)

        # No inter-report delay.
        self._send_command("W0")
        time.sleep(0.05)

        self._send_command("PA")
        time.sleep(0.1)
        logger.info("[OPS] Swing speed training mode ready (PA)")

    def configure_for_speed_trigger(self):
        """
        Configure radar for fast speed detection to trigger rolling buffer capture.

        Per OmniPreSense manufacturer recommendation for golf:
        - 30ksps sample rate
        - 128 buffer size
        - 256 FFT size (X=2) for ~150-200Hz report rate (5-6ms between reports)
        - R>20 = minimum 20mph filter (eliminates leg movement, backswing noise)
        - R- = outbound only (ball/club going away from radar)

        This mode is used to detect the initial club swing, then we switch
        to rolling buffer mode (GC) to capture high-resolution ball data.

        Expected timing:
        - Club detected in speed mode
        - ~5-6ms to switch to rolling buffer
        - Club to ball impact is 20-40ms, so we capture the ball
        """
        logger.info("[OPS] Configuring for fast speed trigger mode...")

        # Start from clean state
        self._send_command("GS")  # Ensure CW mode (not rolling buffer)
        time.sleep(0.1)

        self._send_command("PI")  # Idle mode
        time.sleep(0.1)

        # Set units to MPH
        self.set_units(SpeedUnit.MPH)
        logger.info("[OPS] Units: MPH")

        # Max transmit power for best detection range
        self.set_transmit_power(0)
        logger.info("[OPS] Transmit power: max (P0)")

        # 30ksps sample rate
        self.set_sample_rate(30000)
        time.sleep(0.1)
        logger.info("[OPS] Sample rate: 30ksps")

        # 128 buffer size for fast report rate
        self.set_buffer_size(128)
        time.sleep(0.1)
        logger.info("[OPS] Buffer size: 128")

        # 256 FFT size (X=2) for ~150-200Hz report rate
        # Report rate = 30000 / 128 / 2 ≈ 117 Hz per spec, but empirically faster
        self.set_fft_size(2)
        time.sleep(0.1)
        logger.info("[OPS] FFT size: 256 (X=2) for fast reports")

        # Outbound only - ignore backswing (R-)
        self._send_command("R-")
        time.sleep(0.05)
        logger.info("[OPS] Direction filter: outbound only (R-)")

        # Minimum speed 20mph - ignore leg movement and slow movements
        self._send_command("R>20")
        time.sleep(0.05)
        logger.info("[OPS] Min speed filter: 20 mph (R>20)")

        # Enable JSON output for parsing
        self.enable_json_output(True)

        # Enable magnitude reporting
        self.enable_magnitude_report(True)

        # No inter-report delay
        self._send_command("W0")
        time.sleep(0.05)

        # Activate
        self._send_command("PA")
        time.sleep(0.1)
        logger.info("[OPS] Speed trigger mode ready (PA)")

        # Verify settings
        response = self._send_command("S?")
        logger.info("[OPS] Settings: %s", response)

    def switch_to_rolling_buffer(self):
        """
        Quickly switch from speed detection mode to rolling buffer capture.

        Called immediately when a speed trigger is detected. Uses S#0 to
        capture only new data (no pre-trigger history) since we want the
        ball impact which happens AFTER the club detection.

        Per manufacturer: "have it report all the immediate data captured
        with no history (S#0 API command)"
        """
        # Switch to rolling buffer mode - radar goes active immediately
        self._send_command("GC")
        time.sleep(0.02)  # Brief delay for mode switch

        # S#0 = no pre-trigger history, only capture new samples
        self._send_command("S#0")
        time.sleep(0.02)

        # 30ksps sample rate (GC may reset to default)
        self.set_sample_rate(30000)
        time.sleep(0.02)

    def read_speed_nonblocking(self) -> Optional[SpeedReading]:
        """
        Non-blocking speed read for trigger detection.

        Returns immediately with a reading if one is available,
        or None if no complete line in buffer.
        """
        if not self.serial or not self.serial.is_open:
            return None

        if self.serial.in_waiting == 0:
            return None

        try:
            # Read available data and preserve partial JSON lines for the next poll.
            raw_bytes = self.serial.read(self.serial.in_waiting)
            self._speed_read_buffer += raw_bytes.decode("ascii", errors="ignore")

            if "\n" not in self._speed_read_buffer and "\r" not in self._speed_read_buffer:
                return None

            lines = self._speed_read_buffer.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            self._speed_read_buffer = lines.pop()

            # May have multiple complete lines - take the last valid one.
            for candidate in reversed(lines):
                candidate = candidate.strip()
                if not candidate:
                    continue
                reading = self._parse_reading(candidate)
                if reading:
                    return reading

            return None
        except Exception:
            return None

    def read_speed_candidates_nonblocking(self) -> List[SpeedReading]:
        """Non-blocking speed read returning every candidate from complete lines."""
        if not self.serial or not self.serial.is_open:
            return []

        if self.serial.in_waiting == 0:
            return []

        try:
            raw_bytes = self.serial.read(self.serial.in_waiting)
            self._speed_read_buffer += raw_bytes.decode("ascii", errors="ignore")

            if "\n" not in self._speed_read_buffer and "\r" not in self._speed_read_buffer:
                return []

            lines = self._speed_read_buffer.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            self._speed_read_buffer = lines.pop()

            readings: List[SpeedReading] = []
            for candidate in lines:
                candidate = candidate.strip()
                if candidate:
                    readings.extend(self._parse_readings(candidate))

            return readings
        except Exception:
            return []

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()
        return False
