"""GC2 monitor: connects to a Foresight GC2 over Bluetooth (Linux) and emits
OpenFlight Shot objects through the same callback API the radar monitors use."""

import logging
import re
import select
import socket
import sys
import threading
import time
from datetime import datetime
from typing import List

from ..clubs import ClubType
from ..launch_monitor import Shot, summarize_shots
from .gc2_protocol import GC2ShotData, GC2StreamParser

logger = logging.getLogger(__name__)

GC2_DEFAULT_MAC = "00:06:66:46:F7:13"
GC2_RFCOMM_CHANNEL = 1
GC2_CONNECT_TIMEOUT_SECONDS = 10
GC2_RETRY_DELAY_MIN_SECONDS = 2.0
GC2_RETRY_DELAY_MAX_SECONDS = 10.0
GC2_READ_TIMEOUT_SECONDS = 0.05
GC2_MAC_PATTERN = re.compile(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}")


class GC2Monitor:
    """
    Reads GC2 shots over a Bluetooth RFCOMM socket, merges the follow-up
    messages that arrive within a second of the first, and emits one OpenFlight
    Shot per swing. Reconnects automatically if the link drops.
    """

    def __init__(self, mac=GC2_DEFAULT_MAC):
        self.mac = (mac or GC2_DEFAULT_MAC).upper()
        if not GC2_MAC_PATTERN.fullmatch(self.mac):
            raise ValueError(f"Invalid Bluetooth MAC address: {self.mac}")

        self._thread = None
        self._running = False
        self._connected = False

        self._shot_callback = None
        self._shots: List[Shot] = []
        self._current_club = ClubType.DRIVER

    # ------------------------------------------------------------------
    # OpenFlight monitor API
    # ------------------------------------------------------------------

    def connect(self):
        """Validate the environment and prepare the monitor."""
        if not sys.platform.startswith("linux"):
            raise RuntimeError("GC2 Bluetooth connection is supported on Linux only.")
        logger.info("GC2 monitor ready (Bluetooth %s)", self.mac)
        return True

    def disconnect(self):
        """Stop the monitor and clean up resources."""
        self.stop()

    def start(
        self,
        shot_callback=None,
        live_callback=None,  # pylint: disable=unused-argument
        diagnostic_callback=None,  # pylint: disable=unused-argument
        processing_callback=None,  # pylint: disable=unused-argument
    ):
        """Start the monitor and begin reading shots."""
        self._shot_callback = shot_callback
        self._running = True

        self._thread = threading.Thread(target=self._run, name="gc2-monitor", daemon=True)
        self._thread.start()

        logger.info("GC2 monitor started")

    def stop(self):
        """Stop the monitor thread and wait for it to finish."""
        self._running = False
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=GC2_CONNECT_TIMEOUT_SECONDS + 1)
        self._thread = None
        logger.info("GC2 monitor stopped")

    def get_shots(self):
        """Return a copy of the list of shots recorded in the current session."""
        return self._shots.copy()

    def clear_session(self):
        """Clear all shots recorded in the current session."""
        self._shots.clear()

    def get_session_stats(self):
        """Return summarized statistics for the current session."""
        return summarize_shots(self._shots, mode="gc2")

    def get_radar_info(self):
        """Return information about the radar device."""
        return {"Version": "GC2", "Connected": self._connected}

    def set_club(self, club):
        """Set the current club type for the monitor."""
        self._current_club = club

    # ------------------------------------------------------------------
    # Bluetooth connection
    # ------------------------------------------------------------------

    def _open_socket(self):
        """Open a Bluetooth socket to the GC2 device."""
        logger.info("Connecting to GC2 [%s] on RFCOMM channel %d", self.mac, GC2_RFCOMM_CHANNEL)
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        try:
            sock.settimeout(GC2_CONNECT_TIMEOUT_SECONDS)
            sock.connect((self.mac, GC2_RFCOMM_CHANNEL))
        except OSError:
            sock.close()
            raise
        sock.setblocking(False)
        return sock

    def _run(self):
        """Main loop for the monitor thread, handling connection and reconnection."""
        retry_delay = GC2_RETRY_DELAY_MIN_SECONDS
        while self._running:
            try:
                sock = self._open_socket()
            except OSError as exc:
                logger.warning("GC2 connect failed: %s; retrying in %.0fs", exc, retry_delay)
                self._sleep(retry_delay)
                retry_delay = min(GC2_RETRY_DELAY_MAX_SECONDS, retry_delay + 2.0)
                continue

            retry_delay = GC2_RETRY_DELAY_MIN_SECONDS
            self._connected = True
            logger.info("Connected to GC2")
            try:
                self._read_loop(sock)
            except OSError as exc:
                logger.warning("GC2 connection lost: %s", exc)
            finally:
                self._connected = False
                sock.close()
            self._sleep(GC2_RETRY_DELAY_MIN_SECONDS)

    def _read_loop(self, sock):
        """Read data from the GC2 device and feed it to the parser."""
        # A fresh parser per connection drops the stale last-shot report the
        # GC2 sends on connect.
        parser = GC2StreamParser(self._handle_shot_data)
        while self._running:
            readable, _, _ = select.select([sock], [], [], GC2_READ_TIMEOUT_SECONDS)
            if readable:
                data = sock.recv(1024)
                if not data:
                    raise OSError("Bluetooth connection closed")
                parser.feed(data)
            parser.poll()

    def _sleep(self, seconds):
        """Sleep for the specified number of seconds, but wake early if the monitor 
        stops running."""
        deadline = time.monotonic() + seconds
        while self._running and time.monotonic() < deadline:
            time.sleep(0.1)

    # ------------------------------------------------------------------
    # Shot handling
    # ------------------------------------------------------------------

    def _handle_shot_data(self, data: GC2ShotData):
        """Handle incoming shot data from the GC2 device."""
        shot = self._build_shot(data)
        self._shots.append(shot)

        if self._shot_callback:
            try:
                self._shot_callback(shot)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("GC2 shot callback failed")

    def _build_shot(self, data: GC2ShotData):
        """Convert merged GC2 data into an OpenFlight Shot."""
        has_club = data.club_speed > 0.001
        shot = Shot(
            ball_speed_mph=data.speed,
            club_speed_mph=data.club_speed if has_club else None,
            timestamp=datetime.now(),
            impact_timestamp=data.impact_time,
            club=self._current_club,
            launch_angle_vertical=data.vla,
            launch_angle_horizontal=data.hla,
            launch_angle_vertical_source="measured",
            launch_angle_horizontal_source="measured",
            angle_source="measured",
            launch_angle_vertical_confidence=1.0,
            launch_angle_horizontal_confidence=1.0,
            launch_angle_confidence=1.0,
            spin_rpm=data.total_spin,
            spin_axis_deg=data.spin_axis,
            spin_source="measured",
            spin_method="gc2",
            spin_confidence=1.0,
            mode="gc2",
        )
        if has_club and data.weco is not None:
            shot.club_angle_deg = data.weco["angle_of_attack"]
            shot.club_path_deg = data.weco["path"]
        return shot
