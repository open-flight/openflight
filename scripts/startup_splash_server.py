#!/usr/bin/env python3
"""Serve the local startup splash and accept its dismissal action."""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class SplashRequestHandler(SimpleHTTPRequestHandler):
    """Static-file handler with one loopback-only dismissal endpoint."""

    def __init__(self, *args, dismiss_file: Path, **kwargs):
        self._dismiss_file = dismiss_file
        super().__init__(*args, **kwargs)

    def do_POST(self) -> None:  # pylint: disable=invalid-name
        if self.path != "/dismiss":
            self.send_error(404)
            return
        self._dismiss_file.write_text("dismissed\n", encoding="utf-8")
        self.send_response(204)
        self.end_headers()

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def create_server(
    directory: Path | str,
    dismiss_file: Path | str,
    *,
    host: str = "127.0.0.1",
    port: int,
) -> ThreadingHTTPServer:
    """Create the splash HTTP server without starting its request loop."""
    handler = partial(
        SplashRequestHandler,
        directory=str(Path(directory)),
        dismiss_file=Path(dismiss_file),
    )
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the OpenFlight startup splash")
    parser.add_argument("--directory", required=True)
    parser.add_argument("--dismiss-file", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args()

    server = create_server(
        args.directory,
        args.dismiss_file,
        host=args.bind,
        port=args.port,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
