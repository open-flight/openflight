"""Protocol-agnostic TCP transport for simulator connectors.

Owns the socket, the connect/reconnect state machine, JSON framing, and an
optional heartbeat thread. All wire-format knowledge is delegated to a codec;
this module never imports a specific protocol.
"""

import logging
import os
import socket
import threading
import time
from typing import Callable, List, Optional, Protocol, Tuple

from openflight.sim.types import (
    ConnectionState,
    InboundEvent,
    StatusEvent,
)

logger = logging.getLogger(__name__)

DEFAULT_BACKOFF: Tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)

# Diagnostic: set OPENFLIGHT_SIM_LOG_RAW=1 to log every inbound frame verbatim
# (useful for capturing a simulator's exact wire format).
_LOG_RAW_FRAMES = bool(os.environ.get("OPENFLIGHT_SIM_LOG_RAW"))

# Backstop for the inbound framer: real frames are a few hundred bytes, so if the
# undrained buffer ever passes this with no complete frame, the peer is misframing
# (truncation / protocol bug) and we drop it rather than grow memory without limit.
_MAX_FRAME_BYTES = 64 * 1024


class Codec(Protocol):
    """Wire format for one simulator. See gspro.codec (the shared OpenConnect V1
    codec used by both GSPro and OpenGolfSim)."""

    name: str

    def build_shot(self, resolved) -> bytes:
        """Encode a resolved shot into wire-format payload bytes."""

    def parse_inbound(self, frame: bytes) -> List[InboundEvent]:
        """Parse raw frame bytes into zero or more inbound simulator events."""

    def heartbeat_bytes(self) -> Optional[bytes]:
        """Return heartbeat payload bytes if this protocol requires periodic keepalive."""

    def on_connect_bytes(self) -> Optional[bytes]:
        """Return greeting or handshake payload bytes to send immediately on connect."""

    def fields_for_target(self) -> List[str]:
        """Return the target parameter fields supported by this simulator."""


def find_json_end(buf: bytes) -> Optional[int]:
    """Index past the first complete top-level JSON object in buf, or None.

    Neither OpenConnectV1 nor OpenGolfSim length-prefixes or delimits frames,
    so we frame on balanced top-level braces (string-aware so quoted braces
    don't count).
    """
    depth = 0
    in_str = False
    escape = False
    started = False
    for i, b in enumerate(buf):
        ch = chr(b) if b < 128 else ""
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
                if started and depth == 0:
                    return i + 1
    return None


class TcpSimClient:
    """TCP client with reconnect, framing, and an optional heartbeat.

    Lifecycle:
      start() — spawn connection thread; DISABLED → CONNECTING → CONNECTED.
      stop()  — terminate threads; transitions to STOPPED.

    Callbacks:
      on_inbound(InboundEvent) — called per decoded inbound event
      on_status(StatusEvent)   — called on every state change

    The heartbeat thread is only spawned when ``codec.heartbeat_bytes()``
    returns bytes; codecs whose protocol has no keepalive return None.
    """

    def __init__(
        self,
        host: str,
        port: int,
        codec: Codec,
        heartbeat_interval_s: float = 5.0,
        name: str = "sim",
        on_inbound: Optional[Callable[[InboundEvent], None]] = None,
        on_status: Optional[Callable[[StatusEvent], None]] = None,
        backoff_seconds: Tuple[float, ...] = DEFAULT_BACKOFF,
    ):
        self._host = host
        self._port = port
        self._codec = codec
        self._hb_interval = heartbeat_interval_s
        self._name = name
        self.on_inbound = on_inbound
        self.on_status = on_status
        self._backoff = backoff_seconds
        self._state = ConnectionState.DISABLED
        self._state_lock = threading.Lock()
        self._sock: Optional[socket.socket] = None
        self._sock_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._conn_thread: Optional[threading.Thread] = None
        self._hb_thread: Optional[threading.Thread] = None
        self._last_send_time = 0.0
        self._send_time_lock = threading.Lock()

    # --- public state ---------------------------------------------------------

    @property
    def state(self) -> ConnectionState:
        """Current connection lifecycle state."""
        with self._state_lock:
            return self._state

    def is_connected(self) -> bool:
        """Return True if the underlying socket is currently connected."""
        return self.state == ConnectionState.CONNECTED

    # --- start / stop ---------------------------------------------------------

    def start(self) -> None:
        """Start the background connection worker and optional heartbeat thread."""
        if self._conn_thread is not None and self._conn_thread.is_alive():
            return
        self._stop_event.clear()
        self._conn_thread = threading.Thread(
            target=self._connection_loop,
            name=f"{self._name}-conn",
            daemon=True,
        )
        self._conn_thread.start()
        # Only run a heartbeat thread if the codec's protocol uses one.
        if self._codec.heartbeat_bytes() is not None:
            self._hb_thread = threading.Thread(
                target=self._heartbeat_loop,
                name=f"{self._name}-hb",
                daemon=True,
            )
            self._hb_thread.start()

    def stop(self) -> None:
        """Stop background worker threads and close the active socket."""
        self._stop_event.set()
        self._close_socket()
        for t in (self._conn_thread, self._hb_thread):
            if t is not None:
                t.join(timeout=3.0)
        self._conn_thread = None
        self._hb_thread = None
        self._set_state(ConnectionState.STOPPED)

    # --- send -----------------------------------------------------------------

    def send_raw(self, data: bytes) -> None:
        """Send raw bytes across the connected socket."""
        with self._sock_lock:
            if self._sock is None:
                # ConnectionError (an OSError subclass) rather than RuntimeError so
                # the shot pipeline's `except OSError` guard catches a send that
                # races a disconnect, instead of it propagating into on_shot_detected.
                raise ConnectionError("send_raw called while not connected")
            self._sock.sendall(data)
        with self._send_time_lock:
            self._last_send_time = time.time()

    # --- internals ------------------------------------------------------------

    def _send_heartbeat(self) -> None:
        # Separate path that does NOT update _last_send_time, otherwise
        # heartbeats would keep themselves alive even when no real traffic.
        beat = self._codec.heartbeat_bytes()
        if beat is None:
            return
        with self._sock_lock:
            if self._sock is None:
                return
            try:
                self._sock.sendall(beat)
            except OSError as e:
                logger.info("[%s] heartbeat send failed: %s", self._name, e)

    def _heartbeat_loop(self) -> None:
        interval = max(self._hb_interval, 0.05)
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=interval)
            if self._stop_event.is_set():
                return
            if self.state != ConnectionState.CONNECTED:
                continue
            with self._send_time_lock:
                idle_for = time.time() - self._last_send_time
            if idle_for < interval:
                continue
            self._send_heartbeat()

    def _set_state(self, new_state: ConnectionState, **status_kwargs) -> None:
        with self._state_lock:
            if self._state == new_state and not status_kwargs:
                return
            self._state = new_state
        if self.on_status is not None:
            self.on_status(
                StatusEvent(
                    state=new_state,
                    target=self._name,
                    host=self._host,
                    port=self._port,
                    **status_kwargs,
                )
            )

    def _close_socket(self) -> None:
        with self._sock_lock:
            if self._sock is None:
                return
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _try_connect(self) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5.0)
        try:
            s.connect((self._host, self._port))
        except OSError as e:
            try:
                s.close()
            except OSError:
                pass
            logger.info("[%s] connect failed: %s", self._name, e)
            return False
        with self._sock_lock:
            self._sock = s
        # Some protocols (e.g. OpenGolfSim) want a hello/device-ready frame.
        hello = self._codec.on_connect_bytes()
        if hello is not None:
            try:
                self.send_raw(hello)
            except OSError as e:
                logger.info("[%s] on-connect send failed: %s", self._name, e)
        return True

    def _backoff_for_attempt(self, attempt: int) -> float:
        idx = min(attempt, len(self._backoff) - 1)
        return self._backoff[idx]

    def _connection_loop(self) -> None:
        attempt = 0
        ever_connected = False
        while not self._stop_event.is_set():
            self._set_state(ConnectionState.CONNECTING)
            if self._try_connect():
                attempt = 0
                ever_connected = True
                self._set_state(ConnectionState.CONNECTED)
                self._recv_loop()
                self._close_socket()
                if self._stop_event.is_set():
                    break
                # Connection dropped — fall through to reconnect
            wait = self._backoff_for_attempt(attempt)
            # Until the first successful connection, report CONNECTING during the
            # retry backoff; RECONNECT_BACKOFF ("reconnecting") is reserved for a
            # connection that was once established and then lost, so we never
            # imply a connection that never happened.
            backoff_state = (
                ConnectionState.RECONNECT_BACKOFF
                if ever_connected
                else ConnectionState.CONNECTING
            )
            self._set_state(
                backoff_state,
                attempt=attempt + 1,
                next_retry_in_s=wait,
            )
            attempt += 1
            self._stop_event.wait(timeout=wait)

    def _dispatch(self, event: InboundEvent) -> None:
        if self.on_inbound is None:
            return
        try:
            self.on_inbound(event)
        except Exception:  # pylint: disable=broad-except
            logger.exception("[%s] on_inbound raised", self._name)

    def _recv_loop(self) -> None:
        buffer = bytearray()
        while not self._stop_event.is_set():
            with self._sock_lock:
                sock = self._sock
            if sock is None:
                return
            sock.settimeout(0.2)
            try:
                data = sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if not data:
                return
            buffer.extend(data)
            # Drain every complete JSON object currently in the buffer.
            while True:
                end = find_json_end(bytes(buffer))
                if end is None:
                    break
                chunk = bytes(buffer[:end])
                del buffer[:end]
                if _LOG_RAW_FRAMES:
                    logger.info("[%s] raw ← %s", self._name, chunk.decode("utf-8", "replace"))
                try:
                    events = self._codec.parse_inbound(chunk)
                except ValueError as e:
                    logger.warning("[%s] dropping malformed frame: %s", self._name, e)
                    continue
                for event in events:
                    self._dispatch(event)
            # Drained all complete frames; if the incomplete remainder has grown
            # past the cap, the peer never closed a frame — drop it and resync.
            if len(buffer) > _MAX_FRAME_BYTES:
                logger.warning(
                    "[%s] inbound buffer exceeded %d bytes with no complete frame; resetting",
                    self._name,
                    _MAX_FRAME_BYTES,
                )
                buffer.clear()
