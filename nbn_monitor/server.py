"""Local HTTP server that serves the cached status snapshot.

Used by ``python -m nbn_monitor --serve`` for quick local checks; the
production HTTP route lives directly in ``function_app.py`` and goes
through the same ``generate_snapshot_html`` rendering path.
"""

from __future__ import annotations

import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, Any

from .persistence import load_state_result
from .render import generate_snapshot_html

if TYPE_CHECKING:
    from .config import Address


def make_handler(addresses: list[Address]) -> type[BaseHTTPRequestHandler]:
    """Create a request handler that serves the stored status snapshot."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state_result = load_state_result()
            html = generate_snapshot_html(addresses, state_result)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html.encode())
            except BrokenPipeError:
                pass

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"  {args[0]}", file=sys.stderr)

    return Handler


def serve(addresses: list[Address], port: int = 8000) -> None:
    """Start a local HTTP server serving the status page."""
    handler = make_handler(addresses)
    server = HTTPServer(("", port), handler)
    print(f"Status page: http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
