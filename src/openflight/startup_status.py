"""Structured startup progress for the optional kiosk splash page."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_STARTUP_LOG_PATH = str(Path.home() / "openflight_sessions" / "terminal_logs")


@dataclass(frozen=True)
class StartupComponent:
    """A configured component that should be visible during startup."""

    component_id: str
    label: str


def configured_startup_components(
    *,
    mock: bool,
    camera: bool,
    iwr6843: bool,
    inclinometer: bool,
    kld7: bool,
    kld7_horizontal: bool,
    battery: bool,
    simulators: bool,
) -> list[StartupComponent]:
    """Return only the components enabled for this OpenFlight process."""
    components = [StartupComponent("server", "OpenFlight server")]
    components.append(
        StartupComponent("monitor", "Shot simulator")
        if mock
        else StartupComponent("ops", "OPS radar")
    )
    if iwr6843:
        components.append(StartupComponent("ti", "TI radar"))
    if camera:
        components.append(StartupComponent("camera", "Camera"))
    if inclinometer:
        components.append(StartupComponent("inclinometer", "Inclinometer"))
    if kld7:
        components.append(StartupComponent("kld7_vertical", "K-LD7 launch radar"))
    if kld7_horizontal:
        components.append(StartupComponent("kld7_horizontal", "K-LD7 path radar"))
    if battery:
        components.append(StartupComponent("battery", "Power monitor"))
    if simulators:
        components.append(StartupComponent("simulators", "Simulator connections"))
    return components


def initialize_startup_status(
    path: Path | str,
    *,
    mock: bool,
    camera: bool,
    iwr6843: bool,
    inclinometer: bool,
    kld7: bool,
    kld7_horizontal: bool,
    battery: bool,
    simulators: bool,
) -> None:
    """Publish the final component layout before the splash browser opens."""
    reporter = StartupStatusReporter(
        path,
        configured_startup_components(
            mock=mock,
            camera=camera,
            iwr6843=iwr6843,
            inclinometer=inclinometer,
            kld7=kld7,
            kld7_horizontal=kld7_horizontal,
            battery=battery,
            simulators=simulators,
        ),
    )
    reporter.start("server", "Preparing OpenFlight server")


def _atomic_write_payload(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            json.dump(payload, temporary, separators=(",", ":"))
            temporary.write("\n")
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def complete_startup_status(path: Path | str) -> None:
    """Mark the server ready after the launcher confirms its HTTP endpoint responds."""
    status_path = Path(path)
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("components"), list):
        raise ValueError("Unsupported startup status document")

    server = next(
        (component for component in payload["components"] if component.get("id") == "server"),
        None,
    )
    if server is None:
        raise ValueError("Startup status document has no server component")
    server["state"] = "ready"
    payload["overall"] = "ready"
    payload["message"] = "OpenFlight is ready"
    _atomic_write_payload(status_path, payload)


def fail_startup_status(
    path: Path | str,
    *,
    component_id: str | None,
    message: str,
    recovery: str,
    log_path: str,
    preserve_existing: bool = False,
) -> bool:
    """Publish a concise startup failure, optionally preserving a more specific one."""
    status_path = Path(path)
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("components"), list):
        raise ValueError("Unsupported startup status document")
    if preserve_existing and payload.get("overall") == "error":
        return False

    if component_id is not None:
        component = next(
            (item for item in payload["components"] if item.get("id") == component_id),
            None,
        )
        if component is None:
            raise ValueError(f"Startup status document has no {component_id!r} component")
        component["state"] = "error"

    payload["overall"] = "error"
    payload["message"] = message
    payload["error"] = {"recovery": recovery, "log_path": log_path}
    _atomic_write_payload(status_path, payload)
    return True


class StartupStatusReporter:
    """Publish versioned startup state to a JSON file using atomic replacement."""

    def __init__(self, path: Path | str | None, components: Iterable[StartupComponent]):
        self._path = Path(path) if path is not None else None
        self._components = {
            component.component_id: {
                "id": component.component_id,
                "label": component.label,
                "state": "waiting",
            }
            for component in components
        }
        self._overall = "starting"
        self._message = "Preparing OpenFlight"
        self._error = None
        self._write()

    def start(self, component_id: str, message: str | None = None) -> None:
        """Mark a configured component as actively initializing."""
        self._set_component(component_id, "starting", message)

    def ready(self, component_id: str, message: str | None = None) -> None:
        """Mark a configured component as ready."""
        self._set_component(component_id, "ready", message)

    def skip(self, component_id: str, message: str | None = None) -> None:
        """Mark an optional configured component unavailable without failing startup."""
        self._set_component(component_id, "skipped", message)

    def error(
        self,
        component_id: str,
        message: str,
        recovery: str = "Check the connected hardware, then relaunch OpenFlight.",
        log_path: str = DEFAULT_STARTUP_LOG_PATH,
    ) -> None:
        """Record an initialization failure for a configured component."""
        self._overall = "error"
        self._error = {"recovery": recovery, "log_path": log_path}
        self._set_component(component_id, "error", message)

    def finish(self, message: str = "OpenFlight is ready") -> None:
        """Mark the full startup sequence ready for browser handoff."""
        self._overall = "ready"
        self._message = message
        self._write()

    def _set_component(self, component_id: str, state: str, message: str | None) -> None:
        if component_id not in self._components:
            raise KeyError(f"Unknown startup component: {component_id}")
        self._components[component_id]["state"] = state
        if message is not None:
            self._message = message
        self._write()

    def _write(self) -> None:
        if self._path is None:
            return
        payload = {
            "version": 1,
            "overall": self._overall,
            "message": self._message,
            "components": list(self._components.values()),
        }
        if self._error is not None:
            payload["error"] = self._error
        _atomic_write_payload(self._path, payload)


def main() -> None:
    """Complete a startup status document from the kiosk launch script."""
    import argparse  # pylint: disable=import-outside-toplevel

    parser = argparse.ArgumentParser(description="Update an OpenFlight startup status")
    subparsers = parser.add_subparsers(dest="command", required=True)
    ready_parser = subparsers.add_parser("ready")
    ready_parser.add_argument("status_file")
    fail_parser = subparsers.add_parser("fail")
    fail_parser.add_argument("status_file")
    fail_parser.add_argument("--component", default=None)
    fail_parser.add_argument("--message", required=True)
    fail_parser.add_argument("--recovery", required=True)
    fail_parser.add_argument("--log-path", required=True)
    fail_parser.add_argument("--preserve-existing", action="store_true")
    initialize_parser = subparsers.add_parser("initialize")
    initialize_parser.add_argument("status_file")
    initialize_parser.add_argument("--mock", action="store_true")
    initialize_parser.add_argument("--camera", action="store_true")
    initialize_parser.add_argument("--iwr6843", action="store_true")
    initialize_parser.add_argument("--inclinometer", action="store_true")
    initialize_parser.add_argument("--kld7", action="store_true")
    initialize_parser.add_argument("--kld7-horizontal", action="store_true")
    initialize_parser.add_argument("--battery", action="store_true")
    initialize_parser.add_argument("--simulators", action="store_true")
    args = parser.parse_args()
    if args.command == "ready":
        complete_startup_status(args.status_file)
    elif args.command == "fail":
        fail_startup_status(
            args.status_file,
            component_id=args.component,
            message=args.message,
            recovery=args.recovery,
            log_path=args.log_path,
            preserve_existing=args.preserve_existing,
        )
    else:
        initialize_startup_status(
            args.status_file,
            mock=args.mock,
            camera=args.camera,
            iwr6843=args.iwr6843,
            inclinometer=args.inclinometer,
            kld7=args.kld7,
            kld7_horizontal=args.kld7_horizontal,
            battery=args.battery,
            simulators=args.simulators,
        )


if __name__ == "__main__":
    main()
