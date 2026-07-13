"""Notification side-effects.

Owns ``send_ntfy`` (the ntfy push transport) and ``notify_changes`` (the
decision lane that compares new poll results against the previous snapshot
to decide whether to push alerts). Snapshot derivation lives in
``nbn_monitor.derive``; this module is concerned only with side-effects.

The ntfy endpoint is injected as a ``NtfyConfig`` value object; this module
does not read environment variables directly.

Service notifications are categorised by the worst NBN ``displayOutage``
value in the cycle:

- ``UNPLANNED_*`` → "Outage Alert", high priority, rotating-light tag.
- ``DEGRADATION_*`` → "Degradation", default priority.

Planned maintenance uses the normalised schedule and persistent delivery
markers rather than ``displayOutage`` transitions.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import niquests

from .api import OUTAGE_LABELS
from .config import Address, _safe_error_message
from .planned import (
    PlannedChange,
    PlannedReminders,
    PlannedScheduleDiff,
    apply_planned_schedule_diff,
    describe_event,
    diff_complete_planned_schedule,
    diff_planned_schedule,
    due_planned_reminders,
    event_local_start,
    event_start,
    material_revision,
    planned_diff_event_keys,
)
from .snapshot import (
    Period,
    PlannedMaintenance,
    PlannedNotificationState,
    ServiceNotificationState,
    ServiceResolution,
    Snapshot,
)

if TYPE_CHECKING:
    from .api import OutageStatus
    from .config import NtfyConfig


@dataclass(frozen=True)
class ServiceDelivery:
    loc_id: str
    started_issue_ids: tuple[str, ...] = ()
    resolved_issue_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlannedDelivery:
    loc_id: str
    announced_schedule: tuple[PlannedMaintenance, ...] | None
    day_before_revisions: tuple[str, ...]
    hour_before_revisions: tuple[str, ...]


@dataclass(frozen=True)
class _PlannedAlert:
    address: Address
    schedule: tuple[PlannedMaintenance, ...]
    diff: PlannedScheduleDiff
    reminders: PlannedReminders


def send_ntfy(
    ntfy: NtfyConfig,
    title: str,
    message: str,
    *,
    priority: str = "default",
    tags: str = "white_check_mark",
) -> bool:
    """Send a notification to the configured ntfy topic."""
    if not ntfy.topic:
        return False

    url = f"{ntfy.server}/{ntfy.topic}"
    headers: dict[str, str] = {
        "Title": title,
        "Priority": priority,
        "Tags": tags,
    }
    if ntfy.status_page_url:
        headers["Actions"] = f"view, View Status Page, {ntfy.status_page_url}"

    try:
        resp = niquests.post(url, data=message.encode(), headers=headers, timeout=10)
        resp.raise_for_status()
        return True
    except niquests.RequestException as e:
        print(f"ntfy error: {_safe_error_message(e)}", file=sys.stderr)
        return False


def _is_real_outage(display_outage: str) -> bool:
    """``UNPLANNED_*`` family — what NBN themselves call an outage."""
    return display_outage.startswith("UNPLANNED")


def _format_duration(seconds: float) -> str:
    """Format a duration in seconds as a human-readable string, e.g. '2h 15m'."""
    total_minutes = int(seconds) // 60
    days = total_minutes // (60 * 24)
    hours_in_day = (total_minutes // 60) % 24
    minutes = total_minutes % 60
    if days:
        return f"{days}d {hours_in_day}h" if hours_in_day else f"{days}d"
    hours = total_minutes // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def notify_changes(
    results: list[tuple[Address, OutageStatus]],
    current: Snapshot,
    *,
    previous_loaded: bool = True,
    ntfy: NtfyConfig,
) -> tuple[ServiceDelivery, ...]:
    """Deliver pending service notifications from the derived snapshot.

    If ``previous_loaded`` is ``False`` (the prior state failed to load), all
    notification decisions are skipped to avoid emitting alerts from an
    unreliable baseline.
    """
    if not previous_loaded:
        print("notification decisions skipped: previous state was not loaded")
        return ()

    started: list[tuple[Address, Period]] = []
    resolved: list[tuple[Address, ServiceResolution]] = []

    for addr, _status in results:
        if not addr.notify:
            continue
        current_entry = current.entry(addr.loc_id)
        if current_entry is None:
            continue
        started.extend(
            (addr, issue) for issue in current_entry.service_notifications.pending_starts
        )
        resolved.extend(
            (addr, resolution)
            for resolution in current_entry.service_notifications.pending_resolutions
        )

    deliveries: list[ServiceDelivery] = []
    delivered_start_ids: set[tuple[str, str]] = set()

    if started:
        all_affected = [
            (addr, entry.service_issue)
            for addr, status in results
            if not status.error
            if (entry := current.entry(addr.loc_id)) is not None and entry.service_issue is not None
        ]
        total = len(results)
        any_compare_address = any(a.compare for a in (a for a, _ in results))
        compare_down = any(addr.compare for addr, _ in all_affected)
        notify_down_count = sum(1 for addr, _ in all_affected if addr.notify)
        other_down_count = len(all_affected) - notify_down_count

        lines = [
            f"{addr.label}: {OUTAGE_LABELS.get(issue.display_outage, issue.display_outage)}"
            for addr, issue in started
        ]
        msg = "\n".join(lines)

        context_reliable = all(
            (entry := current.entry(addr.loc_id)) is not None
            and entry.service_issue is not None
            and entry.service_issue.started_at == issue.started_at
            for addr, issue in started
        )
        context_polls_reliable = all(not status.error for _, status in results)
        if context_reliable and context_polls_reliable:
            if compare_down:
                msg += "\n(area-wide, neighbour also affected)"
            elif other_down_count > 0:
                msg += f"\n(widespread, {len(all_affected)} of {total} addresses affected)"
            elif any_compare_address:
                msg += "\n(localised, neighbour unaffected)"

        any_real_outage = any(_is_real_outage(issue.display_outage) for _, issue in started)
        if any_real_outage:
            title = "NBN Outage Alert"
            priority = "high"
            tags = "rotating_light"
        else:
            title = "NBN Degradation"
            priority = "default"
            tags = "warning"

        if send_ntfy(
            ntfy,
            title=title,
            message=msg,
            priority=priority,
            tags=tags,
        ):
            delivered_start_ids.update((addr.loc_id, issue.started_at) for addr, issue in started)
            deliveries.extend(
                ServiceDelivery(
                    loc_id=addr.loc_id,
                    started_issue_ids=(issue.started_at,),
                )
                for addr, issue in started
            )

    pending_start_ids = {(addr.loc_id, issue.started_at) for addr, issue in started}
    resolved = [
        (addr, resolution)
        for addr, resolution in resolved
        if (addr.loc_id, resolution.issue.started_at) not in pending_start_ids
        or (addr.loc_id, resolution.issue.started_at) in delivered_start_ids
    ]
    if resolved:
        lines = []
        for addr, resolution in resolved:
            line = f"{addr.label}: service restored"
            try:
                since_dt = datetime.fromisoformat(resolution.issue.started_at)
                resolved_at = datetime.fromisoformat(resolution.resolved_at)
                duration = _format_duration((resolved_at - since_dt).total_seconds())
            except (ValueError, TypeError):
                duration = ""
            if duration:
                line += f" after {duration}"
            lines.append(line)
        if send_ntfy(
            ntfy,
            title="NBN Outage Resolved",
            message="\n".join(lines),
            priority="default",
            tags="white_check_mark",
        ):
            deliveries.extend(
                ServiceDelivery(
                    loc_id=addr.loc_id,
                    resolved_issue_ids=(resolution.issue.started_at,),
                )
                for addr, resolution in resolved
            )

    return tuple(deliveries)


def apply_service_deliveries(
    snapshot: Snapshot,
    deliveries: tuple[ServiceDelivery, ...],
) -> None:
    """Record successful service notifications before the snapshot is saved."""
    for delivery in deliveries:
        entry = snapshot.entry(delivery.loc_id)
        if entry is None:
            continue
        state = entry.service_notifications
        pending_starts = [
            issue
            for issue in state.pending_starts
            if issue.started_at not in delivery.started_issue_ids
        ]
        pending_resolutions = [
            resolution
            for resolution in state.pending_resolutions
            if resolution.issue.started_at not in delivery.resolved_issue_ids
        ]
        announced = state.announced_issue
        for issue_id in delivery.started_issue_ids:
            if entry.service_issue is not None and entry.service_issue.started_at == issue_id:
                announced = replace(entry.service_issue)
        if announced is not None and announced.started_at in delivery.resolved_issue_ids:
            announced = None
        entry.service_notifications = ServiceNotificationState(
            announced_issue=announced,
            pending_starts=pending_starts,
            pending_resolutions=pending_resolutions,
        )


def seed_notification_baselines(
    snapshot: Snapshot,
    results: list[tuple[Address, OutageStatus]],
) -> None:
    """Treat a newly created snapshot as the notification baseline."""
    for addr, status in results:
        entry = snapshot.entry(addr.loc_id)
        if entry is None:
            continue
        if status.error:
            entry.defer_notification_baseline()
            continue
        entry.seed_notification_baseline()


def notify_planned_maintenance(
    results: list[tuple[Address, OutageStatus]],
    snapshot: Snapshot,
    *,
    previous_loaded: bool = True,
    ntfy: NtfyConfig,
    now: datetime | None = None,
) -> tuple[PlannedDelivery, ...]:
    """Send one consolidated planned-maintenance notification for the cycle."""
    if not previous_loaded:
        return ()

    reference = (now or datetime.now(tz=UTC)).astimezone(UTC)
    alerts: list[_PlannedAlert] = []

    for addr, status in results:
        if not addr.notify or status.error:
            continue
        entry = snapshot.entry(addr.loc_id)
        if entry is None:
            continue

        schedule = tuple(entry.planned_maintenance)
        state = entry.planned_notifications
        observed_diff = diff_planned_schedule(
            schedule,
            state.announced_schedule,
            reference,
        )
        pending_schedule = state.pending_schedule
        pending_diff: PlannedScheduleDiff | None = None
        if pending_schedule is not None:
            pending_diff = _reconcile_pending_diff(
                diff_complete_planned_schedule(
                    pending_schedule,
                    state.announced_schedule,
                ),
                schedule,
                tuple(state.announced_schedule),
                reference,
            )
            pending_schedule = apply_planned_schedule_diff(
                state.announced_schedule,
                pending_diff,
            )
        if observed_diff.has_changes:
            latest_target = apply_planned_schedule_diff(
                state.announced_schedule,
                observed_diff,
            )
            if pending_diff is not None:
                carry_diff = _pending_carry_diff(
                    pending_diff,
                    observed_diff,
                    reference,
                )
                latest_target = apply_planned_schedule_diff(
                    latest_target,
                    carry_diff,
                )
            pending_schedule = latest_target
        elif pending_diff is not None and not pending_diff.has_changes:
            pending_schedule = None
        entry.planned_notifications = PlannedNotificationState(
            announced_schedule=list(state.announced_schedule),
            pending_schedule=pending_schedule,
            day_before_sent=list(state.day_before_sent),
            hour_before_sent=list(state.hour_before_sent),
        )
        target_schedule = tuple(pending_schedule) if pending_schedule is not None else schedule
        diff = (
            diff_complete_planned_schedule(
                target_schedule,
                state.announced_schedule,
            )
            if pending_schedule is not None
            else observed_diff
        )
        reminders = due_planned_reminders(
            schedule,
            entry.planned_notifications,
            reference,
        )
        if diff.has_changes or reminders.has_due:
            alerts.append(_PlannedAlert(addr, target_schedule, diff, reminders))

    if not alerts:
        return ()

    title = _planned_title(alerts, reference)
    message = "\n\n".join(_format_planned_alert(alert, reference) for alert in alerts)
    if not send_ntfy(
        ntfy,
        title=title,
        message=message,
        priority="default",
        tags="construction",
    ):
        return ()

    return tuple(
        PlannedDelivery(
            loc_id=alert.address.loc_id,
            announced_schedule=alert.schedule if alert.diff.has_changes else None,
            day_before_revisions=tuple(
                material_revision(event) for event in alert.reminders.day_before
            ),
            hour_before_revisions=tuple(
                material_revision(event) for event in alert.reminders.hour_before
            ),
        )
        for alert in alerts
    )


def apply_planned_deliveries(
    snapshot: Snapshot,
    deliveries: tuple[PlannedDelivery, ...],
) -> None:
    """Record successful planned notifications in the snapshot before saving."""
    for delivery in deliveries:
        entry = snapshot.entry(delivery.loc_id)
        if entry is None:
            continue

        current_revisions = {material_revision(event) for event in entry.planned_maintenance}
        state = entry.planned_notifications
        day_before_sent = {
            revision for revision in state.day_before_sent if revision in current_revisions
        } | set(delivery.day_before_revisions)
        hour_before_sent = {
            revision for revision in state.hour_before_sent if revision in current_revisions
        } | set(delivery.hour_before_revisions)
        entry.planned_notifications = PlannedNotificationState(
            announced_schedule=(
                list(delivery.announced_schedule)
                if delivery.announced_schedule is not None
                else list(state.announced_schedule)
            ),
            pending_schedule=(
                None if delivery.announced_schedule is not None else state.pending_schedule
            ),
            day_before_sent=sorted(day_before_sent),
            hour_before_sent=sorted(hour_before_sent),
        )


def _planned_title(alerts: list[_PlannedAlert], now: datetime) -> str:
    if any(alert.reminders.hour_before for alert in alerts):
        return "NBN Maintenance Starting Soon"
    if any(alert.diff.has_changes for alert in alerts):
        if all(alert.diff.is_cancellation for alert in alerts):
            return "NBN Planned Maintenance Cancelled"
        if all(alert.diff.is_initial for alert in alerts):
            return "NBN Planned Maintenance Added"
        return "NBN Planned Maintenance Updated"
    day_events = [event for alert in alerts for event in alert.reminders.day_before]
    relation = _day_reminder_relation(day_events, now)
    if relation == "tomorrow":
        return "NBN Maintenance Tomorrow"
    if relation == "today":
        return "NBN Maintenance Later Today"
    return "NBN Maintenance Reminder"


def _format_planned_alert(alert: _PlannedAlert, now: datetime) -> str:
    lines = [alert.address.label]
    diff = alert.diff
    if diff.has_changes:
        if diff.is_initial:
            lines.append("Planned maintenance added:")
        else:
            lines.append("Schedule changed:")
        lines.extend(f"- Added: {describe_event(event)}" for event in diff.added)
        lines.extend(
            f"- Changed: {describe_event(change.previous)} -> {describe_event(change.current)}"
            for change in diff.changed
        )
        lines.extend(f"- Cancelled: {describe_event(event)}" for event in diff.removed)

    hour_revisions = {material_revision(event) for event in alert.reminders.hour_before}
    day_only = [
        event
        for event in alert.reminders.day_before
        if material_revision(event) not in hour_revisions
    ]
    if day_only:
        relation = _day_reminder_relation(day_only, now)
        if relation == "tomorrow":
            lines.append("Reminder for tomorrow:")
        elif relation == "today":
            lines.append("Reminder for today:")
        else:
            lines.append("Upcoming maintenance reminder:")
        lines.extend(f"- {describe_event(event)}" for event in day_only)
    if alert.reminders.hour_before:
        lines.append("Expected to begin soon:")
        lines.extend(f"- {describe_event(event)}" for event in alert.reminders.hour_before)

    return "\n".join(lines)


def _day_reminder_relation(
    events: list[PlannedMaintenance],
    now: datetime,
) -> str:
    relations: set[str] = set()
    for event in events:
        local_start = event_local_start(event)
        if local_start is None:
            relations.add("upcoming")
            continue
        days = (local_start.date() - now.astimezone(local_start.tzinfo).date()).days
        if days == 0:
            relations.add("today")
        elif days == 1:
            relations.add("tomorrow")
        else:
            relations.add("upcoming")
    return relations.pop() if len(relations) == 1 else "upcoming"


def _reconcile_pending_diff(
    diff: PlannedScheduleDiff,
    current: tuple[PlannedMaintenance, ...],
    announced: tuple[PlannedMaintenance, ...],
    now: datetime,
) -> PlannedScheduleDiff:
    current_groups: dict[str, list[PlannedMaintenance]] = {}
    for event in current:
        current_groups.setdefault(event.event_key, []).append(event)
    current_revisions = {material_revision(event) for event in current}
    for event in announced:
        group = current_groups.get(event.event_key, [])
        revision = material_revision(event)
        match = next(
            (
                index
                for index, current_event in enumerate(group)
                if material_revision(current_event) == revision
            ),
            None,
        )
        if match is not None:
            group.pop(match)

    added: list[PlannedMaintenance] = []
    for event in diff.added:
        matched = _take_current_occurrence(current_groups, event)
        if matched is not None:
            added.append(matched)
        elif _event_started(event, now):
            added.append(event)

    changed: list[PlannedChange] = []
    for change in diff.changed:
        matched = _take_current_occurrence(current_groups, change.current)
        if matched is not None:
            changed.append(PlannedChange(change.previous, matched))
        elif _event_started(change.current, now):
            changed.append(change)

    removed = [event for event in diff.removed if material_revision(event) not in current_revisions]
    return PlannedScheduleDiff(
        added=tuple(added),
        changed=tuple(changed),
        removed=tuple(removed),
        previous_count=diff.previous_count,
        current_count=diff.current_count,
    )


def _pending_carry_diff(
    diff: PlannedScheduleDiff,
    observed: PlannedScheduleDiff,
    now: datetime,
) -> PlannedScheduleDiff:
    observed_keys = planned_diff_event_keys(observed)
    observed_targets = {
        *(material_revision(event) for event in observed.added),
        *(material_revision(change.current) for change in observed.changed),
    }
    observed_removed = {
        *(material_revision(event) for event in observed.removed),
        *(material_revision(change.previous) for change in observed.changed),
    }
    added = tuple(
        event
        for event in diff.added
        if event.event_key not in observed_keys
        or (_event_started(event, now) and material_revision(event) not in observed_targets)
    )
    changed = tuple(
        change
        for change in diff.changed
        if change.current.event_key not in observed_keys
        or (
            _event_started(change.current, now)
            and material_revision(change.current) not in observed_targets
        )
    )
    removed = tuple(
        event
        for event in diff.removed
        if event.event_key not in observed_keys
        or (_event_started(event, now) and material_revision(event) not in observed_removed)
    )
    return PlannedScheduleDiff(
        added=added,
        changed=changed,
        removed=removed,
        previous_count=diff.previous_count,
        current_count=diff.current_count,
    )


def _take_current_occurrence(
    groups: dict[str, list[PlannedMaintenance]],
    target: PlannedMaintenance,
) -> PlannedMaintenance | None:
    group = groups.get(target.event_key, [])
    revision = material_revision(target)
    match = next(
        (index for index, event in enumerate(group) if material_revision(event) == revision),
        None,
    )
    if match is None:
        target_start = event_start(target)
        match = next(
            (index for index, event in enumerate(group) if event_start(event) == target_start),
            None,
        )
    return group.pop(match) if match is not None else None


def _event_started(event: PlannedMaintenance, now: datetime) -> bool:
    starts_at = event_start(event)
    return starts_at is not None and starts_at <= now
