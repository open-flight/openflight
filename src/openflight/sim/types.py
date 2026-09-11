"""Protocol-neutral types shared by every simulator connector.

A *codec* (e.g. ``gspro.codec.GSProCodec``) owns the wire format for one
simulator. The transport, resolver, and these types know nothing about any
specific protocol — that is what makes a third simulator a single new codec.
"""

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Union

from openflight.launch_monitor import ClubType

# GSPro parses ShotNumber as a signed 32-bit int; a larger value overflows it and
# the shot is rejected with 501 "Bad format". Keep every ShotNumber at or below
# this. (OpenConnect spec: https://gsprogolf.com/GSProConnectV1.html)
SHOT_NUMBER_MAX = 2_147_483_647


def initial_shot_counter() -> int:
    """Seed for the shared shot counter: epoch *seconds*.

    Seeding from the clock keeps ShotNumber strictly increasing across server
    restarts — some sims (e.g. OpenGolfSim's Developer API) reject any
    ShotNumber <= the highest they have seen, so a per-run reset to 1 would get
    every shot dropped. Seconds, not milliseconds: epoch millis (~1.78e12)
    overflow GSPro's 32-bit ShotNumber and trigger 501 "Bad format"; epoch
    seconds (~1.78e9) stay within range until 2038.
    """
    return int(time.time())


class ConnectionState(Enum):
    """Lifecycle of a single connector's TCP socket."""

    DISABLED = "disabled"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECT_BACKOFF = "reconnecting"
    STOPPED = "stopped"


class IncompleteShotError(Exception):
    """Shot lacks the minimum fields required to send to a simulator (ball speed)."""


@dataclass
class StatusEvent:
    """Emitted on every connection-state change. ``target`` is the codec name."""

    state: ConnectionState
    target: str = ""
    host: str = ""
    port: int = 0
    attempt: int = 0
    next_retry_in_s: float = 0.0
    message: str = ""


# --- inbound events (simulator → OpenFlight), normalized across protocols ----


@dataclass
class PlayerUpdate:
    """Player/club change pushed by the sim. Fields are None when not present."""

    handed: Optional[str] = None
    club: Optional[ClubType] = None


@dataclass
class ShotAck:
    """Sim acknowledged a shot. ``ok`` False means the sim rejected it."""

    shot_number: Optional[int] = None
    ok: bool = True
    message: str = ""


@dataclass
class SimError:
    """Sim reported an error but the connection stays up."""

    message: str = ""


InboundEvent = Union[PlayerUpdate, ShotAck, SimError]


# --- resolved shot (post-fallback, pre-serialization) ------------------------


@dataclass
class ResolvedShot:
    """A Shot with every simulator-relevant field filled by the resolver.

    Field names are *logical* (protocol-neutral); each codec maps them to its
    own wire names. ``provenance`` tags each logical field "measured" or
    "estimated" so the UI can render per-field badges identically for any sim.
    """

    shot_number: int
    ball_speed_mph: float
    vla: float
    hla: float
    total_spin_rpm: float
    spin_axis_deg: float
    back_spin_rpm: float
    side_spin_rpm: float
    carry_yards: float
    club_path_deg: float
    club: ClubType
    club_speed_mph: Optional[float] = None  # None => no club-speed data
    provenance: Dict[str, str] = field(default_factory=dict)

    def as_values(self) -> Dict[str, Optional[float]]:
        """Logical field -> value map (keys match ``provenance``) for the UI."""
        return {
            "ball_speed": self.ball_speed_mph,
            "vla": self.vla,
            "hla": self.hla,
            "total_spin": self.total_spin_rpm,
            "spin_axis": self.spin_axis_deg,
            "back_spin": self.back_spin_rpm,
            "side_spin": self.side_spin_rpm,
            "carry": self.carry_yards,
            "club_speed": self.club_speed_mph,
            "club_path": self.club_path_deg,
        }


# --- player state (shared across connectors) ---------------------------------


@dataclass
class PlayerState:
    """Mutable player-level state, shared by all connectors and kept across shots.

    Every connector runs on its own thread, so the two mutators are guarded by a
    lock. CPython's GIL makes ``shot_counter += 1`` incidentally safe today, but
    that's an implementation detail — the lock makes the invariants (a unique
    number per call; an all-or-nothing field update) explicit and correct under
    free-threaded builds where the GIL no longer serializes the read-modify-write.
    """

    handed: str = "RH"
    club: ClubType = ClubType.DRIVER
    shot_counter: int = 0
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False
    )

    def next_shot_number(self) -> int:
        """Increment and return the next sequential shot counter atomically."""
        with self._lock:
            self.shot_counter += 1
            return self.shot_counter

    def apply(self, update: PlayerUpdate) -> None:
        """Apply a normalized PlayerUpdate from any simulator."""
        with self._lock:
            if update.handed is not None:
                self.handed = update.handed
            if update.club is not None:
                self.club = update.club
