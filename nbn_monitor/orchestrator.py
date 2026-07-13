"""Single source of truth for a full poll-and-notify cycle.

Both the Azure timer trigger (``function_app.poll_nbn``) and the CLI's
``poll --notify`` path delegate to ``run_poll_cycle`` so that the
load → derive → notify → save ordering, the failed-load safeguard, and the
operator-facing status print all live in exactly one place.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .api import check_all, display_outage_colour
from .config import NtfyConfig
from .derive import derive_snapshot
from .notify import (
    apply_planned_deliveries,
    apply_service_deliveries,
    notify_changes,
    notify_planned_maintenance,
    seed_notification_baselines,
)
from .persistence import load_state_result, save_state

if TYPE_CHECKING:
    from .api import OutageStatus
    from .config import Address

_STATUS_SYMBOLS: dict[str, str] = {"green": "✅", "red": "🔴", "amber": "🟡", "grey": "⚪"}


def _print_status_line(addr: Address, status: OutageStatus) -> None:
    colour = display_outage_colour(status.display_outage, error=bool(status.error))
    symbol = _STATUS_SYMBOLS.get(colour, "?")
    print(f"  {symbol} {addr.short_id}: {status.label}")


def run_poll_cycle(addresses: list[Address]) -> list[tuple[Address, OutageStatus]]:
    """Poll all addresses, persist state, and send notifications."""
    started_at = datetime.now(tz=UTC).isoformat()
    results = check_all(addresses)
    completed_at = datetime.now(tz=UTC).isoformat()

    state_result = load_state_result()

    if state_result.status in ("failed", "corrupt"):
        print(
            f"state load {state_result.status}: {state_result.error}; skipping save",
            file=sys.stderr,
        )
    else:
        ntfy = NtfyConfig.from_env()
        new_snapshot = derive_snapshot(
            results,
            state_result.snapshot,
            started_at=started_at,
            completed_at=completed_at,
        )
        if state_result.status == "missing":
            seed_notification_baselines(new_snapshot, results)
        service_deliveries = notify_changes(
            results,
            new_snapshot,
            previous_loaded=state_result.can_make_notification_decisions,
            ntfy=ntfy,
        )
        apply_service_deliveries(new_snapshot, service_deliveries)
        planned_deliveries = notify_planned_maintenance(
            results,
            new_snapshot,
            previous_loaded=state_result.can_make_notification_decisions,
            ntfy=ntfy,
        )
        apply_planned_deliveries(new_snapshot, planned_deliveries)
        if not save_state(new_snapshot):
            raise RuntimeError("state save failed")

    for addr, status in results:
        _print_status_line(addr, status)
    return results


def poll(addresses: list[Address], *, notify: bool = False) -> list[tuple[Address, OutageStatus]]:
    """Poll addresses; with ``notify=True`` runs the full notification cycle."""
    if notify:
        return run_poll_cycle(addresses)
    results = check_all(addresses)
    for addr, status in results:
        _print_status_line(addr, status)
    return results
