"""Command-line entry point for the monitor.

Wires ``argparse`` to the orchestrator (``poll``) and the local HTTP
server (``serve``). The bare names are looked up via this module's
namespace at call time so tests can swap them with ``patch.object``.
"""

from __future__ import annotations

import argparse

from .config import load_addresses
from .orchestrator import poll
from .server import serve


def main() -> None:
    parser = argparse.ArgumentParser(description="NBN outage monitor")
    parser.add_argument("--notify", action="store_true", help="Send ntfy on status changes")
    parser.add_argument("--serve", action="store_true", help="Serve status page on localhost")
    parser.add_argument("--port", type=int, default=8000, help="Port for status page server")
    args = parser.parse_args()

    addresses = load_addresses()

    if args.serve:
        serve(addresses, port=args.port)
    else:
        poll(addresses, notify=args.notify)
