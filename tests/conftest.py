"""Reusable mock simulator TCP server for connector transport tests.

Protocol-agnostic: records bytes received and can send scripted JSON replies or
drop the connection. Used by both GSPro and OpenGolfSim connector tests.
"""
import json
import socket
import sys
import threading
from pathlib import Path
from typing import List, Optional

import pytest

# Add tests directory to sys.path for importing test utilities like spin_synth
sys.path.insert(0, str(Path(__file__).parent))


class MockSimServer:
    """Tiny TCP server that records bytes and can send scripted replies."""

    def __init__(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.host, self.port = self._sock.getsockname()
        self._client_sock: Optional[socket.socket] = None
        self.received: List[bytes] = []
        self.scripted_replies: List[bytes] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self._sock.settimeout(0.5)
            while not self._stop.is_set():
                try:
                    self._client_sock, _ = self._sock.accept()
                except socket.timeout:
                    continue
                self._client_sock.settimeout(0.2)
                # Send any scripted replies that were queued before connect
                for reply in list(self.scripted_replies):
                    try:
                        self._client_sock.sendall(reply)
                    except OSError:
                        break
                self.scripted_replies.clear()
                while not self._stop.is_set():
                    try:
                        chunk = self._client_sock.recv(4096)
                    except socket.timeout:
                        # Send any newly-queued replies
                        for reply in list(self.scripted_replies):
                            try:
                                self._client_sock.sendall(reply)
                            except OSError:
                                break
                        self.scripted_replies.clear()
                        continue
                    except OSError:
                        break
                    if not chunk:
                        break
                    self.received.append(chunk)
                conn = self._client_sock
                self._client_sock = None
                if conn is not None:
                    try:
                        conn.close()
                    except OSError:
                        pass
        finally:
            try:
                self._sock.close()
            except OSError:
                pass

    def queue_reply(self, obj: dict) -> None:
        self.scripted_replies.append(json.dumps(obj).encode("utf-8"))

    def queue_raw(self, data: bytes) -> None:
        self.scripted_replies.append(data)

    def disconnect_client(self) -> None:
        # Capture locally; the accept loop may null _client_sock concurrently.
        conn = self._client_sock
        self._client_sock = None
        if conn is not None:
            try:
                conn.shutdown(socket.SHUT_RDWR)
                conn.close()
            except OSError:
                pass

    def stop(self) -> None:
        self._stop.set()
        self.disconnect_client()
        self._thread.join(timeout=2.0)


@pytest.fixture
def mock_sim():
    server = MockSimServer()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def _isolate_profile_store(tmp_path_factory, monkeypatch):
    """Keep ProfileStore() off ~/.config/openflight/profiles.json during tests."""
    roster = tmp_path_factory.mktemp("profiles") / "profiles.json"
    monkeypatch.setenv("OPENFLIGHT_PROFILES_PATH", str(roster))
