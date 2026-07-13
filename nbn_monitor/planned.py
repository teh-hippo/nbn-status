"""Normalise NBN planned maintenance and calculate schedule notifications."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .snapshot import PlannedMaintenance, PlannedNotificationState


@dataclass(frozen=True)
class PlannedParseResult:
    events: tuple[PlannedMaintenance, ...]
    skipped_count: int = 0


@dataclass(frozen=True)
class PlannedChange:
    previous: PlannedMaintenance
    current: PlannedMaintenance


@dataclass(frozen=True)
class PlannedScheduleDiff:
    added: tuple[PlannedMaintenance, ...]
    changed: tuple[PlannedChange, ...]
    removed: tuple[PlannedMaintenance, ...]
    previous_count: int
    current_count: int

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.changed or self.removed)

    @property
    def is_initial(self) -> bool:
        return self.previous_count == 0 and self.current_count > 0

    @property
    def is_cancellation(self) -> bool:
        return self.current_count == 0 and bool(self.removed)


@dataclass(frozen=True)
class PlannedReminders:
    day_before: tuple[PlannedMaintenance, ...]
    hour_before: tuple[PlannedMaintenance, ...]

    @property
    def has_due(self) -> bool:
        return bool(self.day_before or self.hour_before)


@dataclass(frozen=True)
class _Candidate:
    maintenance_date: date
    starts_at: datetime
    maintenance_ends_at: datetime | None
    duration_minutes: int | None
    time_zone: str
    planned_power_outage: bool


def parse_planned_maintenance(raw: dict[str, Any]) -> PlannedParseResult:
    """Parse the API's daily maintenance list into stable material events."""
    planned = raw.get("plannedOutages")
    if not isinstance(planned, dict):
        return PlannedParseResult((), 1)
    if "maintenanceList" not in planned and "primary" not in planned:
        return PlannedParseResult((), 1)

    raw_candidates, shape_errors = _raw_candidates(planned)
    time_zone = raw.get("timeZoneId")
    zone = _load_zone(time_zone)
    if zone is None:
        return PlannedParseResult(
            (),
            max(1, len(raw_candidates) + shape_errors),
        )
    if not raw_candidates:
        return PlannedParseResult((), shape_errors)

    parsed, skipped = _parse_candidates(raw_candidates, zone, str(time_zone))

    if not parsed and raw_candidates:
        primary = planned.get("primary")
        if isinstance(primary, dict) and all(primary is not item[0] for item in raw_candidates):
            fallback, fallback_skipped = _parse_candidates([(primary, None)], zone, str(time_zone))
            parsed = fallback
            skipped += fallback_skipped

    parsed.sort(
        key=lambda item: (
            item.maintenance_date,
            item.starts_at,
            item.maintenance_ends_at or datetime.max.replace(tzinfo=UTC),
            item.duration_minutes if item.duration_minutes is not None else -1,
            item.planned_power_outage,
        )
    )

    events: list[PlannedMaintenance] = []
    for item in parsed:
        date_text = item.maintenance_date.isoformat()
        starts_at = item.starts_at.astimezone(UTC).isoformat()
        events.append(
            PlannedMaintenance(
                event_key=date_text,
                maintenance_date=date_text,
                starts_at=starts_at,
                maintenance_ends_at=(
                    item.maintenance_ends_at.astimezone(UTC).isoformat()
                    if item.maintenance_ends_at
                    else None
                ),
                duration_minutes=item.duration_minutes,
                time_zone=item.time_zone,
                planned_power_outage=item.planned_power_outage,
            )
        )

    return PlannedParseResult(tuple(events), skipped + shape_errors)


def material_revision(event: PlannedMaintenance) -> str:
    payload = json.dumps(
        {
            "event_key": event.event_key,
            "maintenance_date": event.maintenance_date,
            "starts_at": event.starts_at,
            "maintenance_ends_at": event.maintenance_ends_at,
            "duration_minutes": event.duration_minutes,
            "time_zone": event.time_zone,
            "planned_power_outage": event.planned_power_outage,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def event_start(event: PlannedMaintenance) -> datetime | None:
    return _stored_datetime(event.starts_at)


def event_local_start(event: PlannedMaintenance) -> datetime | None:
    starts_at = event_start(event)
    zone = _load_zone(event.time_zone)
    if starts_at is None or zone is None:
        return None
    return starts_at.astimezone(zone)


def event_local_end(event: PlannedMaintenance) -> datetime | None:
    if not event.maintenance_ends_at:
        return None
    ends_at = _stored_datetime(event.maintenance_ends_at)
    zone = _load_zone(event.time_zone)
    if ends_at is None or zone is None:
        return None
    return ends_at.astimezone(zone)


def visible_events(
    events: list[PlannedMaintenance] | tuple[PlannedMaintenance, ...],
    now: datetime,
) -> list[PlannedMaintenance]:
    now_utc = _as_utc(now)
    visible: list[PlannedMaintenance] = []
    for event in events:
        local_start = event_local_start(event)
        if local_start is None:
            continue
        if local_start.date() >= now_utc.astimezone(local_start.tzinfo).date():
            visible.append(event)
    return sorted(visible, key=_event_sort_key)


def upcoming_events(
    events: list[PlannedMaintenance] | tuple[PlannedMaintenance, ...],
    now: datetime,
) -> list[PlannedMaintenance]:
    now_utc = _as_utc(now)
    return sorted(
        [
            event
            for event in events
            if (starts_at := event_start(event)) is not None and starts_at > now_utc
        ],
        key=_event_sort_key,
    )


def diff_planned_schedule(
    current: list[PlannedMaintenance] | tuple[PlannedMaintenance, ...],
    announced: list[PlannedMaintenance] | tuple[PlannedMaintenance, ...],
    now: datetime,
) -> PlannedScheduleDiff:
    return _diff_event_sets(
        _all_events(current),
        _all_events(announced),
        now=_as_utc(now),
    )


def diff_complete_planned_schedule(
    current: list[PlannedMaintenance] | tuple[PlannedMaintenance, ...],
    announced: list[PlannedMaintenance] | tuple[PlannedMaintenance, ...],
) -> PlannedScheduleDiff:
    """Compare complete schedules without expiring started events."""
    return _diff_event_sets(
        _all_events(current),
        _all_events(announced),
        now=None,
    )


def apply_planned_schedule_diff(
    base: list[PlannedMaintenance] | tuple[PlannedMaintenance, ...],
    diff: PlannedScheduleDiff,
) -> list[PlannedMaintenance]:
    result = list(base)
    for event in diff.removed:
        _remove_material_event(result, event)
    for change in diff.changed:
        _remove_material_event(result, change.previous)
        result.append(change.current)
    result.extend(diff.added)
    return sorted(result, key=_event_sort_key)


def planned_diff_event_keys(diff: PlannedScheduleDiff) -> set[str]:
    return {
        *(event.event_key for event in diff.added),
        *(event.event_key for event in diff.removed),
        *(change.previous.event_key for change in diff.changed),
        *(change.current.event_key for change in diff.changed),
    }


def _diff_event_sets(
    current_events: list[PlannedMaintenance],
    announced_events: list[PlannedMaintenance],
    *,
    now: datetime | None,
) -> PlannedScheduleDiff:
    current_groups = _group_by_event_key(current_events)
    announced_groups = _group_by_event_key(announced_events)
    added: list[PlannedMaintenance] = []
    changed: list[PlannedChange] = []
    removed: list[PlannedMaintenance] = []

    for key in sorted(current_groups.keys() | announced_groups.keys()):
        current_group = list(current_groups.get(key, ()))
        announced_group = list(announced_groups.get(key, ()))
        unmatched_current: list[PlannedMaintenance] = []

        for event in current_group:
            revision = material_revision(event)
            match = next(
                (
                    index
                    for index, previous in enumerate(announced_group)
                    if material_revision(previous) == revision
                ),
                None,
            )
            if match is None:
                unmatched_current.append(event)
            else:
                announced_group.pop(match)

        remaining_current: list[PlannedMaintenance] = []
        for event in unmatched_current:
            starts_at = event_start(event)
            match = next(
                (
                    index
                    for index, previous in enumerate(announced_group)
                    if event_start(previous) == starts_at
                ),
                None,
            )
            if match is None:
                remaining_current.append(event)
            else:
                changed.append(PlannedChange(announced_group.pop(match), event))
        unmatched_current = remaining_current

        if now is not None:
            announced_group = [
                event
                for event in announced_group
                if (starts_at := event_start(event)) is not None and starts_at > now
            ]
        paired = min(len(unmatched_current), len(announced_group))
        changed.extend(
            PlannedChange(announced_group[index], unmatched_current[index])
            for index in range(paired)
        )
        added.extend(
            event
            for event in unmatched_current[paired:]
            if now is None or ((starts_at := event_start(event)) is not None and starts_at > now)
        )
        removed.extend(announced_group[paired:])

    current_count = (
        len(current_events)
        if now is None
        else sum(
            1
            for event in current_events
            if (starts_at := event_start(event)) is not None and starts_at > now
        )
    )
    previous_count = (
        len(announced_events)
        if now is None
        else sum(
            1
            for event in announced_events
            if (starts_at := event_start(event)) is not None and starts_at > now
        )
    )
    return PlannedScheduleDiff(
        added=tuple(sorted(added, key=_event_sort_key)),
        changed=tuple(sorted(changed, key=lambda item: _event_sort_key(item.current))),
        removed=tuple(sorted(removed, key=_event_sort_key)),
        previous_count=previous_count,
        current_count=current_count,
    )


def due_planned_reminders(
    events: list[PlannedMaintenance] | tuple[PlannedMaintenance, ...],
    state: PlannedNotificationState,
    now: datetime,
) -> PlannedReminders:
    now_utc = _as_utc(now)
    sent_day = set(state.day_before_sent)
    sent_hour = set(state.hour_before_sent)
    day_before: list[PlannedMaintenance] = []
    hour_before: list[PlannedMaintenance] = []

    for event in upcoming_events(events, now_utc):
        starts_at = event_start(event)
        local_start = event_local_start(event)
        if starts_at is None or local_start is None:
            continue

        revision = material_revision(event)
        day_due_local = datetime.combine(
            local_start.date() - timedelta(days=1),
            time(hour=9),
            tzinfo=local_start.tzinfo,
        )
        if now_utc >= day_due_local.astimezone(UTC) and revision not in sent_day:
            day_before.append(event)
        if now_utc >= starts_at - timedelta(hours=1) and revision not in sent_hour:
            hour_before.append(event)

    return PlannedReminders(tuple(day_before), tuple(hour_before))


def format_event_date(event: PlannedMaintenance) -> str:
    local_start = event_local_start(event)
    return local_start.strftime("%a %-d %b") if local_start else event.maintenance_date


def format_event_time(event: PlannedMaintenance) -> str:
    local_start = event_local_start(event)
    if local_start is None:
        return "unknown time"
    if local_start.hour == 0 and local_start.minute == 0:
        return "midnight"
    if local_start.hour == 12 and local_start.minute == 0:
        return "midday"
    pattern = "%-I%p" if local_start.minute == 0 else "%-I:%M%p"
    return local_start.strftime(pattern).lower()


def format_event_end(event: PlannedMaintenance) -> str:
    local_end = event_local_end(event)
    if local_end is None:
        return ""
    pattern = "%-I%p" if local_end.minute == 0 else "%-I:%M%p"
    return f"{local_end.strftime('%a %-d %b')}, {local_end.strftime(pattern).lower()}"


def format_estimated_duration(minutes: int | None) -> str:
    if minutes is None:
        return "duration not provided"
    hours, remaining = divmod(minutes, 60)
    if hours and remaining:
        return f"{hours}h {remaining}m"
    if hours:
        return f"{hours}h"
    return f"{remaining}m"


def describe_event(event: PlannedMaintenance) -> str:
    description = (
        f"{format_event_date(event)} from {format_event_time(event)}, "
        f"estimated interruption {format_estimated_duration(event.duration_minutes)}"
    )
    end_text = format_event_end(event)
    if end_text:
        description += f", work scheduled through {end_text}"
    if event.planned_power_outage:
        description += ", planned power work"
    return description


def _raw_candidates(
    planned: dict[str, Any],
) -> tuple[list[tuple[dict[str, Any], Any]], int]:
    maintenance_list = planned.get("maintenanceList")
    candidates: list[tuple[dict[str, Any], Any]] = []
    shape_errors = 0

    if "maintenanceList" in planned:
        if isinstance(maintenance_list, dict):
            for bucket, items in maintenance_list.items():
                if isinstance(items, list):
                    if not items:
                        shape_errors += 1
                        continue
                    for item in items:
                        if isinstance(item, dict):
                            candidates.append((item, bucket))
                        else:
                            shape_errors += 1
                elif isinstance(items, dict):
                    candidates.append((items, bucket))
                else:
                    shape_errors += 1
            if maintenance_list:
                if not candidates and shape_errors == 0:
                    shape_errors = 1
                return candidates, shape_errors
        else:
            shape_errors += 1

    if "primary" in planned:
        primary = planned["primary"]
        if isinstance(primary, dict):
            if not _is_empty_primary(primary):
                candidates.append((primary, None))
        else:
            shape_errors += 1
    return candidates, shape_errors


def _parse_candidates(
    raw_candidates: list[tuple[dict[str, Any], Any]],
    zone: ZoneInfo,
    time_zone: str,
) -> tuple[list[_Candidate], int]:
    parsed: list[_Candidate] = []
    skipped = 0
    for raw, bucket in raw_candidates:
        start_value = raw.get("interruptionStartTime")
        if start_value is None:
            start_value = raw.get("maintenanceStartTime")
        starts_at = _parse_timestamp(
            start_value,
            zone,
        )
        if starts_at is None:
            skipped += 1
            continue

        date_value = raw.get("maintenanceDate", bucket)
        if date_value is not None:
            maintenance_date_at = _parse_timestamp(date_value, zone)
            if maintenance_date_at is None:
                skipped += 1
                continue
            maintenance_date = maintenance_date_at.astimezone(zone).date()
        else:
            maintenance_date = starts_at.astimezone(zone).date()

        if "maintenanceEndTime" in raw and raw["maintenanceEndTime"] is not None:
            ends_at = _parse_timestamp(raw["maintenanceEndTime"], zone)
            if ends_at is None:
                skipped += 1
                continue
        else:
            ends_at = None

        duration_raw = raw.get("duration")
        if duration_raw is None:
            duration = None
        elif isinstance(duration_raw, int) and not isinstance(duration_raw, bool):
            if duration_raw < 0:
                skipped += 1
                continue
            duration = duration_raw
        else:
            skipped += 1
            continue

        power_raw = raw.get("plannedPowerOutage", False)
        if not isinstance(power_raw, bool):
            skipped += 1
            continue
        parsed.append(
            _Candidate(
                maintenance_date=maintenance_date,
                starts_at=starts_at,
                maintenance_ends_at=ends_at,
                duration_minutes=duration,
                time_zone=time_zone,
                planned_power_outage=power_raw,
            )
        )
    return parsed, skipped


def _parse_timestamp(value: Any, zone: ZoneInfo) -> datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
        if abs(seconds) > 10_000_000_000:
            seconds /= 1000
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None

    if not isinstance(value, str) or not value:
        return None
    if value.isdigit():
        return _parse_timestamp(int(value), zone)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(UTC)


def _stored_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _is_empty_primary(primary: dict[str, Any]) -> bool:
    return (
        primary.get("state") == "UNDEFINED"
        and not primary.get("maintenanceStartTime")
        and not primary.get("interruptionStartTime")
    )


def _load_zone(value: Any) -> ZoneInfo | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("reference time must be timezone-aware")
    return value.astimezone(UTC)


def _group_by_event_key(
    events: list[PlannedMaintenance],
) -> dict[str, tuple[PlannedMaintenance, ...]]:
    groups: dict[str, list[PlannedMaintenance]] = {}
    for event in events:
        groups.setdefault(event.event_key, []).append(event)
    return {key: tuple(sorted(group, key=material_revision)) for key, group in groups.items()}


def _all_events(
    events: list[PlannedMaintenance] | tuple[PlannedMaintenance, ...],
) -> list[PlannedMaintenance]:
    return sorted(
        [event for event in events if event_start(event) is not None],
        key=_event_sort_key,
    )


def _remove_material_event(
    events: list[PlannedMaintenance],
    target: PlannedMaintenance,
) -> None:
    revision = material_revision(target)
    match = next(
        (index for index, event in enumerate(events) if material_revision(event) == revision),
        None,
    )
    if match is not None:
        events.pop(match)


def _event_sort_key(event: PlannedMaintenance) -> tuple[datetime, str]:
    starts_at = event_start(event)
    return starts_at or datetime.max.replace(tzinfo=UTC), event.event_key
