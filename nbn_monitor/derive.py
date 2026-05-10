"""Pure transform: turn a poll batch plus the prior snapshot into a new snapshot.

``derive_snapshot`` is the single state-derivation function. It is a pure
transform: no I/O, no clock reads, no notification side-effects. The caller
(``orchestrator.run_poll_cycle``) supplies honest ``started_at`` and
``completed_at`` ISO timestamps captured around the actual poll, and the
function folds the results into a fresh ``Snapshot``.

The previous snapshot is read but not mutated; address entries are copied via
``dataclasses.replace`` before being modified.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from .snapshot import (
    AddressEntry,
    ErrorRecord,
    Period,
    PollSummary,
    Snapshot,
    StatusRecord,
    _iso_from_timestamp,
)

if TYPE_CHECKING:
    from .api import OutageStatus
    from .config import Address


def _success_record(status: OutageStatus) -> StatusRecord:
    from .api import display_outage_colour  # local import keeps derive→api edge one-way

    valid_at = status.raw.get("validAt")
    return StatusRecord(
        display_outage=status.display_outage,
        label=status.label,
        colour=display_outage_colour(status.display_outage),
        checked_at=_iso_from_timestamp(status.checked_at),
        nbn_valid_at=valid_at if isinstance(valid_at, int) else None,
    )


def _status_timing(raw: dict[str, Any]) -> dict[str, str]:
    """Extract useful timing fields from known NBN payload shapes."""
    planned = raw.get("plannedOutages")
    if isinstance(planned, dict):
        primary = planned.get("primary")
        if isinstance(primary, dict):
            started_at = primary.get("maintenanceStartTime") or primary.get("interruptionStartTime")
            ended_at = primary.get("maintenanceEndTime")
            result: dict[str, str] = {}
            if isinstance(started_at, str) and started_at:
                result["started_at"] = started_at
            if isinstance(ended_at, str) and ended_at:
                result["ended_at"] = ended_at
            if result:
                return result
    return {}


def derive_snapshot(
    results: list[tuple[Address, OutageStatus]],
    previous: Snapshot,
    *,
    started_at: str,
    completed_at: str,
) -> Snapshot:
    """Build a new ``Snapshot`` from results plus the prior snapshot.

    ``started_at`` and ``completed_at`` are ISO timestamps captured by the
    caller around the actual poll. The function performs no clock reads itself.
    """
    new_addresses: dict[str, AddressEntry] = {
        loc_id: replace(entry) for loc_id, entry in previous.addresses.items()
    }

    for addr, status in results:
        existing = new_addresses.get(addr.loc_id)
        entry = replace(existing) if existing is not None else AddressEntry(label=addr.label)
        old_period = entry.current_period
        old_status = entry.display_outage

        entry.label = addr.label

        if status.error:
            entry.last_error = ErrorRecord(
                checked_at=_iso_from_timestamp(status.checked_at),
                category="request",
                message=status.error,
            )
            entry.consecutive_error_count += 1
            new_addresses[addr.loc_id] = entry
            print(f"poll outcome short_id={addr.short_id} result=error")
            continue

        checked_at = _iso_from_timestamp(status.checked_at)
        entry.last_success = _success_record(status)
        entry.last_error = None
        entry.consecutive_error_count = 0

        old_period_status = old_period.display_outage if old_period else ""
        if old_status != status.display_outage or old_period_status != status.display_outage:
            timing = _status_timing(status.raw)
            timing_started = timing.get("started_at")
            entry.current_period = Period(
                display_outage=status.display_outage,
                started_at=timing_started if timing_started else checked_at,
                started_at_source="nbn" if timing_started else "observed",
            )
        elif entry.current_period is None:
            entry.current_period = Period(
                display_outage=status.display_outage,
                started_at=checked_at,
                started_at_source="observed",
            )

        new_addresses[addr.loc_id] = entry
        print(
            f"poll outcome short_id={addr.short_id} result=success status={status.display_outage}"
        )

    return Snapshot(
        generated_at=completed_at,
        poll=PollSummary(
            started_at=started_at,
            completed_at=completed_at,
            success_count=sum(1 for _, status in results if not status.error),
            error_count=sum(1 for _, status in results if status.error),
        ),
        addresses=new_addresses,
    )
