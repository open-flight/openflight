#!/usr/bin/env python3
"""Idle the OPS243-A (PI) so it does not sample or transmit until OpenFlight starts.

The radar powers up active in its persisted rolling-buffer mode. Run once at
boot by openflight-ops-idle.service (scripts/setup/install_ops_idle_service.sh);
OpenFlight's own startup wakes the radar again, and its shutdown idles it.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Callable, Optional, Sequence

from openflight.ops243 import OPS243Radar

PROC_ROOT = Path("/proc")


def is_server_command(argv: Sequence[str]) -> bool:
    """Whether a process's argv is an OpenFlight server, not just a mention of one.

    Matches ``python -m openflight.server`` and the ``openflight-server`` entry
    point (run directly or through its python shebang).
    """
    if not argv:
        return False
    if Path(argv[0]).name == "openflight-server":
        return True
    if not Path(argv[0]).name.startswith("python") or len(argv) < 2:
        return False
    if argv[1] == "-m":
        return len(argv) > 2 and argv[2] == "openflight.server"
    return Path(argv[1]).name == "openflight-server"


def openflight_running(proc_root: Path = PROC_ROOT) -> bool:
    """Whether an OpenFlight server process is running."""
    for cmdline in proc_root.glob("[0-9]*/cmdline"):
        try:
            raw = cmdline.read_bytes()
        except OSError:
            continue
        argv = [part.decode(errors="replace") for part in raw.split(b"\0") if part]
        if is_server_command(argv):
            return True
    return False


def main(
    argv: Optional[Sequence[str]] = None,
    radar_factory: Callable[..., OPS243Radar] = OPS243Radar,
    is_openflight_running: Callable[[], bool] = openflight_running,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--port",
        help="OPS243 serial port, e.g. /dev/ttyAMA0 for the GPIO UART (default: USB auto-detect)",
    )
    parser.add_argument("--attempts", type=int, default=5, help="Connection attempts (default: 5)")
    parser.add_argument(
        "--retry-delay", type=float, default=2.0, help="Seconds between attempts (default: 2)"
    )
    args = parser.parse_args(argv)

    if is_openflight_running():
        print("[ops-idle] OpenFlight is running; leaving the radar alone.")
        return 0

    last_error: Optional[Exception] = None
    for attempt in range(1, args.attempts + 1):
        radar = radar_factory(port=args.port)
        try:
            radar.connect()
            radar.idle()
            print(f"[ops-idle] Radar on {radar.port} is idle until OpenFlight starts.")
            return 0
        except Exception as error:  # noqa: BLE001 - retry any connection or serial failure
            last_error = error
            print(f"[ops-idle] Attempt {attempt}/{args.attempts} failed: {error}")
        finally:
            radar.disconnect()
        if attempt < args.attempts:
            sleep(args.retry_delay)

    print(f"[ops-idle] Could not idle the radar: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
