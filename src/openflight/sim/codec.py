"""SimConnector: pairs a codec with a TCP transport, and the codec registry.

A connector is the unit the server fans shots out to. Adding a new simulator
means writing a codec and registering it here — nothing else in the server or
transport changes.
"""

import logging
from typing import Callable, List, Optional

from openflight.sim.config import ConnectorConfig
from openflight.sim.transport import DEFAULT_BACKOFF, Codec, TcpSimClient
from openflight.sim.types import InboundEvent, ResolvedShot, StatusEvent

logger = logging.getLogger(__name__)


class SimConnector:
    """One simulator endpoint: codec + transport + per-target callback routing.

    Callbacks are invoked as ``on_status(target, StatusEvent)`` and
    ``on_inbound(target, InboundEvent)`` so the server can multiplex several
    connectors through a single pair of handlers.
    """

    def __init__(
        self,
        codec: Codec,
        host: str,
        port: int,
        heartbeat_interval_s: float = 5.0,
        on_status: Optional[Callable[[str, StatusEvent], None]] = None,
        on_inbound: Optional[Callable[[str, InboundEvent], None]] = None,
        backoff_seconds=DEFAULT_BACKOFF,
    ):
        self.codec = codec
        self.name = codec.name
        self.host = host
        self.port = port
        self._on_status_user = on_status
        self._on_inbound_user = on_inbound
        self._client = TcpSimClient(
            host=host,
            port=port,
            codec=codec,
            heartbeat_interval_s=heartbeat_interval_s,
            name=codec.name,
            on_inbound=self._handle_inbound,
            on_status=self._handle_status,
            backoff_seconds=backoff_seconds,
        )

    def _handle_status(self, event: StatusEvent) -> None:
        if self._on_status_user is not None:
            self._on_status_user(self.name, event)

    def _handle_inbound(self, event: InboundEvent) -> None:
        if self._on_inbound_user is not None:
            self._on_inbound_user(self.name, event)

    def start(self) -> None:
        """Start the background simulator transport client."""
        self._client.start()

    def stop(self) -> None:
        """Stop the background simulator transport client."""
        self._client.stop()

    def is_connected(self) -> bool:
        """Return True if the underlying simulator connection is connected."""
        return self._client.is_connected()

    @property
    def state(self):
        """Current connection state of the simulator client."""
        return self._client.state

    def send_shot(self, resolved: ResolvedShot) -> None:
        """Serialize and send a resolved shot. Raises OSError if the socket fails."""
        self._client.send_raw(self.codec.build_shot(resolved))


def _codec_for(cfg: "ConnectorConfig") -> Codec:
    """Instantiate the codec for a connector type. Import is local to avoid an
    import cycle (the codec imports sim.types/resolver).

    The OpenConnect V1 codec is shared: GSPro uses it on 921; OpenGolfSim uses it
    on its Developer API (3111, which speaks OpenConnect), named "opengolfsim" so
    the UI/logs/config say OpenGolfSim rather than GSPro.
    """
    if cfg.type not in ("gspro", "opengolfsim"):
        raise ValueError(f"unknown simulator connector type: {cfg.type!r}")
    from openflight.gspro.codec import GSProCodec  # pylint: disable=import-outside-toplevel

    return GSProCodec(device_id=cfg.device_id, units=cfg.units, name=cfg.type)


def build_connector(
    cfg: "ConnectorConfig",
    on_status: Optional[Callable[[str, StatusEvent], None]] = None,
    on_inbound: Optional[Callable[[str, InboundEvent], None]] = None,
    backoff_seconds=DEFAULT_BACKOFF,
) -> SimConnector:
    """Build a single connector from a resolved ConnectorConfig."""
    codec = _codec_for(cfg)
    return SimConnector(
        codec=codec,
        host=cfg.host,
        port=cfg.port,
        heartbeat_interval_s=cfg.heartbeat_interval_s,
        on_status=on_status,
        on_inbound=on_inbound,
        backoff_seconds=backoff_seconds,
    )


def build_connectors(
    cfgs: List["ConnectorConfig"],
    on_status: Optional[Callable[[str, StatusEvent], None]] = None,
    on_inbound: Optional[Callable[[str, InboundEvent], None]] = None,
    backoff_seconds=DEFAULT_BACKOFF,
) -> List[SimConnector]:
    """Build every connector in a resolved config list."""
    return [
        build_connector(
            cfg, on_status=on_status, on_inbound=on_inbound, backoff_seconds=backoff_seconds
        )
        for cfg in cfgs
    ]
