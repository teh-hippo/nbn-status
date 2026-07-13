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

import sys
from dataclasses import replace
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .api import display_outage_is_service_issue
from .planned import event_start, parse_planned_maintenance
from .snapshot import (
    AddressEntry,
    ErrorRecord,
    Period,
    PlannedMaintenance,
    PollSummary,
    ServiceNotificationState,
    ServiceResolution,
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


def _planned_status_start(
    display_outage: str,
    planned_maintenance: list[PlannedMaintenance],
) -> str | None:
    if not display_outage.startswith("PLANNED") or not planned_maintenance:
        return None
    starts_at = event_start(planned_maintenance[0])
    return starts_at.isoformat() if starts_at else None


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
        old_service_issue = entry.service_issue
        baseline_pending = entry.notification_baseline_pending
        if old_service_issue is None and display_outage_is_service_issue(old_status):
            entry.service_issue = (
                replace(old_period)
                if old_period is not None
                else Period(
                    display_outage=old_status,
                    started_at=(entry.last_success.checked_at if entry.last_success else ""),
                    started_at_source="observed",
                )
            )
            old_service_issue = entry.service_issue

        entry.label = addr.label
        if not addr.notify:
            entry.set_service_notification_baseline()

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
        if "timeZoneId" in status.raw:
            time_zone = status.raw["timeZoneId"]
            if isinstance(time_zone, str):
                try:
                    ZoneInfo(time_zone)
                except (ZoneInfoNotFoundError, ValueError):
                    print(
                        f"timezone parse short_id={addr.short_id} result=invalid",
                        file=sys.stderr,
                    )
                else:
                    entry.time_zone = time_zone
            else:
                print(
                    f"timezone parse short_id={addr.short_id} result=invalid",
                    file=sys.stderr,
                )
        old_service_issue = _normalise_issue(old_service_issue, checked_at)
        service_notifications = entry.service_notifications
        announced_issue = _normalise_issue(
            service_notifications.announced_issue,
            checked_at,
        )
        normalised_starts = [
            _normalise_issue(issue, checked_at) for issue in service_notifications.pending_starts
        ]
        pending_starts: list[Period] = [issue for issue in normalised_starts if issue is not None]
        pending_resolutions = [
            ServiceResolution(
                issue=_normalise_issue(resolution.issue, checked_at) or resolution.issue,
                resolved_at=resolution.resolved_at or checked_at,
            )
            for resolution in service_notifications.pending_resolutions
        ]
        parsed_planned = parse_planned_maintenance(status.raw)
        if parsed_planned.skipped_count:
            print(
                f"planned parse short_id={addr.short_id} skipped={parsed_planned.skipped_count}",
                file=sys.stderr,
            )
        else:
            entry.planned_maintenance = list(parsed_planned.events)

        old_period_status = old_period.display_outage if old_period else ""
        if old_status != status.display_outage or old_period_status != status.display_outage:
            timing_started = _planned_status_start(
                status.display_outage,
                entry.planned_maintenance,
            )
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

        if display_outage_is_service_issue(status.display_outage):
            current_issue = Period(
                display_outage=status.display_outage,
                started_at=(old_service_issue.started_at if old_service_issue else checked_at),
                started_at_source=(
                    old_service_issue.started_at_source if old_service_issue else "observed"
                ),
            )
            entry.service_issue = current_issue
            if (
                old_service_issue is None
                and not _same_issue(announced_issue, current_issue)
                and not any(_same_issue(issue, current_issue) for issue in pending_starts)
            ):
                pending_starts.append(current_issue)
        elif status.display_outage == "NO_OUTAGE":
            if old_service_issue is not None and not any(
                _same_issue(resolution.issue, old_service_issue)
                for resolution in pending_resolutions
            ):
                pending_resolutions.append(
                    ServiceResolution(
                        issue=old_service_issue,
                        resolved_at=checked_at,
                    )
                )
            entry.service_issue = None

        if addr.notify:
            entry.service_notifications = ServiceNotificationState(
                announced_issue=announced_issue,
                pending_starts=pending_starts,
                pending_resolutions=pending_resolutions,
            )
        else:
            entry.set_service_notification_baseline()
        if baseline_pending:
            entry.seed_notification_baseline()
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


def _normalise_issue(issue: Period | None, fallback_start: str) -> Period | None:
    if issue is None:
        return None
    if issue.started_at:
        return replace(issue)
    return Period(
        display_outage=issue.display_outage,
        started_at=fallback_start,
        started_at_source="observed",
    )


def _same_issue(first: Period | None, second: Period | None) -> bool:
    return bool(first is not None and second is not None and first.started_at == second.started_at)
