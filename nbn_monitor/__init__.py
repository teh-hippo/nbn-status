"""NBN outage monitor with ntfy notifications and a traffic-light status page.

This package exposes the public API expected by ``function_app.py`` and
the CLI entry point. Internal helpers live in their respective submodules
(``config``, ``api``, ``snapshot``, ``persistence``, ``derive``, ``notify``,
``render``, ``server``, ``orchestrator``, ``cli``); they are not re-exported here.
"""

from __future__ import annotations

from .api import (
    OUTAGE_LABELS,
    OutageStatus,
    check_all,
    check_outage,
    display_outage_colour,
    display_outage_is_outage,
)
from .cli import main
from .config import Address, NtfyConfig, load_addresses
from .derive import derive_snapshot
from .notify import notify_changes, send_ntfy
from .orchestrator import poll, run_poll_cycle
from .persistence import load_state_result, save_state
from .render import generate_html, generate_snapshot_html
from .server import make_handler, serve
from .snapshot import AddressEntry, Period, Snapshot, StatusRecord

__version__ = "0.1.0"

__all__ = [
    "OUTAGE_LABELS",
    "Address",
    "AddressEntry",
    "NtfyConfig",
    "OutageStatus",
    "Period",
    "Snapshot",
    "StatusRecord",
    "check_all",
    "check_outage",
    "derive_snapshot",
    "display_outage_colour",
    "display_outage_is_outage",
    "generate_html",
    "generate_snapshot_html",
    "load_addresses",
    "load_state_result",
    "main",
    "make_handler",
    "notify_changes",
    "poll",
    "run_poll_cycle",
    "save_state",
    "send_ntfy",
    "serve",
]
