"""Simulator-connector configuration: config/sim.json.

A single file lists every connector; the server streams to all that are
``enabled`` — but only when the sim feature is turned on at launch (``--sim``).

A connector's ``type`` is the *product*: gspro (OpenConnect V1 on 921),
opengolfsim (reached via its Developer API on 3111, which speaks OpenConnect),
or partee (the PAR-TEE phone app, which listens for OpenConnect on 921).
All ride the shared OpenConnect codec; they differ only in name + default port.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config/sim.json")

KNOWN_TYPES: Tuple[str, ...] = ("gspro", "opengolfsim", "partee")

# Per-type defaults applied when a field is absent from the file.
_DEFAULTS: Dict[str, dict] = {
    "gspro": {
        "port": 921,
        "units": "Yards",
        "device_id": "OpenFlight",
        "heartbeat_interval_s": 5.0,
    },
    "opengolfsim": {
        "port": 3111,
        "units": "Yards",
        "device_id": "OpenFlight",
        "heartbeat_interval_s": 5.0,
    },
    "partee": {
        "port": 921,
        "units": "Yards",
        "device_id": "OpenFlight",
        "heartbeat_interval_s": 5.0,
    },
}


@dataclass
class ConnectorConfig:
    """One resolved simulator endpoint."""

    type: str
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 0
    units: str = "Yards"
    device_id: str = "OpenFlight"
    heartbeat_interval_s: float = 5.0


def _with_defaults(connector_type: str, data: dict) -> ConnectorConfig:
    base = dict(_DEFAULTS[connector_type])
    base.update(data)
    return ConnectorConfig(
        type=connector_type,
        enabled=bool(base.get("enabled", False)),
        host=str(base.get("host", "127.0.0.1")),
        port=int(base["port"]),
        units=str(base.get("units", "Yards")),
        device_id=str(base.get("device_id", "OpenFlight")),
        heartbeat_interval_s=float(base.get("heartbeat_interval_s", 5.0)),
    )


def load_sim_config(config_path: Path = DEFAULT_CONFIG_PATH) -> List[ConnectorConfig]:
    """Resolve the enabled connector configs from the file (only enabled ones).

    Gating the whole feature on/off is the caller's job (the ``--sim`` flag);
    this just reads which connectors the file enables.

    Sim is opt-in and the core shot pipeline doesn't depend on it, so an
    unreadable/syntactically-broken file degrades to "no connectors" with a
    warning rather than crashing startup, and a single malformed connector entry
    is skipped so it can't take the others down with it. An *unknown connector
    type* still raises — that's a real misconfiguration worth surfacing loudly.
    """
    if not config_path.exists():
        return []
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("[sim] ignoring unreadable %s: %s", config_path, e)
        return []
    if not isinstance(data, dict):
        logger.warning(
            "[sim] ignoring %s: expected a JSON object, got %s",
            config_path,
            type(data).__name__,
        )
        return []
    cfgs: List[ConnectorConfig] = []
    for entry in data.get("connectors", []):
        if not isinstance(entry, dict):
            logger.warning(
                "[sim] skipping non-object connector entry in %s: %r", config_path, entry
            )
            continue
        ctype = entry.get("type")
        if ctype not in KNOWN_TYPES:
            raise ValueError(f"unknown simulator type in {config_path}: {ctype!r}")
        try:
            cfg = _with_defaults(ctype, entry)
        except (ValueError, TypeError, KeyError) as e:
            logger.warning("[sim] skipping malformed %s connector in %s: %s", ctype, config_path, e)
            continue
        if cfg.enabled:
            cfgs.append(cfg)
    return cfgs
