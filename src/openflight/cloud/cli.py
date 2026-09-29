"""``openflight-cloud`` command-line entry point.

openflight-cloud link              # one-time device pairing
openflight-cloud push [--dry-run]  # filter + upload anything unpushed
openflight-cloud status            # linked? queued? parked? last error?
openflight-cloud raw on|off        # opt in/out of raw radar + L3 dump uploads
"""

import argparse
from pathlib import Path
from typing import List, Optional

from ..session_logger import SessionLogger
from . import commands
from .client import CloudClient
from .config import CONFIG_PATH, CloudConfig, load_config, save_config

DEFAULT_LOG_DIR = SessionLogger.DEFAULT_LOG_DIR


def _build_parser() -> argparse.ArgumentParser:
    # Shared options usable either before or after the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help=f"Path to cloud config (default: {CONFIG_PATH}).",
    )
    common.add_argument(
        "--log-dir",
        type=Path,
        default=DEFAULT_LOG_DIR,
        help=f"Session log directory (default: {DEFAULT_LOG_DIR}).",
    )

    parser = argparse.ArgumentParser(
        prog="openflight-cloud",
        description="Push filtered OpenFlight session logs to the FlightWeb cloud.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command")

    link = sub.add_parser(
        "link", parents=[common], help="Pair this device with a FlightWeb account."
    )
    link.add_argument("--device-name", default=None, help="Device label (default: hostname).")

    push = sub.add_parser("push", parents=[common], help="Upload any unpushed sessions.")
    push.add_argument(
        "--dry-run",
        action="store_true",
        help="Show exactly which entries would upload; send nothing.",
    )
    push.add_argument(
        "--retry",
        nargs="?",
        const="__ALL__",
        default=None,
        metavar="SESSION",
        help=(
            "Re-upload sessions that were parked or failed. Give a session "
            "filename (or substring) to force re-upload a specific one even if "
            "it was already sent."
        ),
    )

    sub.add_parser("status", parents=[common], help="Show link state, queue, and parked sessions.")

    raw = sub.add_parser(
        "raw",
        parents=[common],
        help="Opt in/out of uploading raw radar data and IWR6843 L3 dumps (for testing).",
    )
    raw.add_argument("state", choices=["on", "off"])
    return parser


def _client(config: CloudConfig) -> CloudClient:
    """Build a CloudClient from persisted config."""
    return CloudClient(config.endpoint, token=config.device_token or None)


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    config = load_config(args.config) or CloudConfig()

    if args.command == "link":
        ok = commands.cmd_link(config, args.config, _client(config), device_name=args.device_name)
        return 0 if ok else 1

    if args.command == "push":
        retry = args.retry is not None
        session = None if args.retry == "__ALL__" else args.retry
        summary = commands.cmd_push(
            config,
            args.log_dir,
            _client(config),
            dry_run=args.dry_run,
            retry=retry,
            session=session,
        )
        return 1 if summary.get("needs_relink") else 0

    if args.command == "status":
        commands.cmd_status(config, args.log_dir, _client(config))
        return 0

    if args.command == "raw":
        config.upload_raw = args.state == "on"
        save_config(config, args.config)
        if config.upload_raw:
            print(
                "Raw uploads ON: all unpushed sessions upload unfiltered (raw radar data included) "
                "and each shot's IWR6843 L3 dump uploads too. The OpenFlight server saves "
                "L3 dumps to disk while this is on (restart it to apply)."
            )
        else:
            print("Raw uploads OFF: sessions upload filtered shot summaries only.")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
