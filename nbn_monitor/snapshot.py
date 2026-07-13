"""Typed snapshot model.

Owns the dataclasses that make up the state snapshot
(``StatusRecord``, ``ErrorRecord``, ``Period``, ``AddressEntry``,
``PollSummary``, ``Snapshot``), the ``StateLoadResult`` envelope,
``CorruptSnapshotError``, and the tiny ISO/timestamp helpers used by
the API → state translation lane.

Persistence (Azure Blob / local file backends) lives in
``nbn_monitor.persistence``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

StateLoadStatus = Literal["loaded", "missing", "failed", "corrupt"]

SCHEMA_VERSION = 2


class CorruptSnapshotError(ValueError):
    """Raised when a stored state payload is not a valid snapshot."""


@dataclass
class StatusRecord:
    display_outage: str
    label: str
    colour: str
    checked_at: str
    nbn_valid_at: int | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> StatusRecord:
        valid_at = raw.get("nbn_valid_at")
        return cls(
            display_outage=str(raw.get("display_outage", "")),
            label=str(raw.get("label", "")),
            colour=str(raw.get("colour", "grey")),
            checked_at=str(raw.get("checked_at", "")),
            nbn_valid_at=valid_at if isinstance(valid_at, int) else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "display_outage": self.display_outage,
            "label": self.label,
            "colour": self.colour,
            "checked_at": self.checked_at,
            "nbn_valid_at": self.nbn_valid_at,
        }


@dataclass
class ErrorRecord:
    checked_at: str
    category: str
    message: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ErrorRecord:
        return cls(
            checked_at=str(raw.get("checked_at", "")),
            category=str(raw.get("category", "")),
            message=str(raw.get("message", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked_at": self.checked_at,
            "category": self.category,
            "message": self.message,
        }


PeriodSource = Literal["nbn", "observed"]


@dataclass
class Period:
    display_outage: str
    started_at: str
    started_at_source: PeriodSource

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Period:
        source: PeriodSource = "nbn" if str(raw.get("started_at_source")) == "nbn" else "observed"
        return cls(
            display_outage=str(raw.get("display_outage", "")),
            started_at=str(raw.get("started_at", "")),
            started_at_source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "display_outage": self.display_outage,
            "started_at": self.started_at,
            "started_at_source": self.started_at_source,
        }


@dataclass(frozen=True)
class PlannedMaintenance:
    event_key: str
    maintenance_date: str
    starts_at: str
    maintenance_ends_at: str | None
    duration_minutes: int | None
    time_zone: str
    planned_power_outage: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PlannedMaintenance:
        event_key = _required_string(raw, "event_key")
        maintenance_date = _required_string(raw, "maintenance_date")
        try:
            date.fromisoformat(maintenance_date)
        except ValueError as error:
            raise CorruptSnapshotError("planned maintenance date is invalid") from error

        starts_at = _aware_iso_string(raw, "starts_at")
        time_zone = _required_string(raw, "time_zone")
        try:
            ZoneInfo(time_zone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise CorruptSnapshotError("planned maintenance time zone is invalid") from error

        duration_raw = raw.get("duration_minutes")
        if duration_raw is None:
            duration = None
        elif isinstance(duration_raw, int) and not isinstance(duration_raw, bool):
            if duration_raw < 0:
                raise CorruptSnapshotError("planned maintenance duration is negative")
            duration = duration_raw
        else:
            raise CorruptSnapshotError("planned maintenance duration is invalid")

        if "maintenance_ends_at" in raw and raw["maintenance_ends_at"] is not None:
            ends_at = _aware_iso_string(raw, "maintenance_ends_at")
        else:
            ends_at = None
        power_raw = raw.get("planned_power_outage", False)
        if not isinstance(power_raw, bool):
            raise CorruptSnapshotError("planned power flag is invalid")
        return cls(
            event_key=event_key,
            maintenance_date=maintenance_date,
            starts_at=starts_at,
            maintenance_ends_at=ends_at,
            duration_minutes=duration,
            time_zone=time_zone,
            planned_power_outage=power_raw,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_key": self.event_key,
            "maintenance_date": self.maintenance_date,
            "starts_at": self.starts_at,
            "maintenance_ends_at": self.maintenance_ends_at,
            "duration_minutes": self.duration_minutes,
            "time_zone": self.time_zone,
            "planned_power_outage": self.planned_power_outage,
        }


@dataclass
class PlannedNotificationState:
    announced_schedule: list[PlannedMaintenance] = field(default_factory=list)
    pending_schedule: list[PlannedMaintenance] | None = None
    day_before_sent: list[str] = field(default_factory=list)
    hour_before_sent: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PlannedNotificationState:
        required = {
            "announced_schedule",
            "pending_schedule",
            "day_before_sent",
            "hour_before_sent",
        }
        if not required.issubset(raw):
            raise CorruptSnapshotError("planned_notifications is incomplete")
        pending_raw = raw["pending_schedule"]
        if pending_raw is None:
            pending_schedule = None
        elif isinstance(pending_raw, list) and all(isinstance(item, dict) for item in pending_raw):
            pending_schedule = [PlannedMaintenance.from_dict(item) for item in pending_raw]
        else:
            raise CorruptSnapshotError("pending planned schedule is invalid")
        return cls(
            announced_schedule=_planned_list(raw, "announced_schedule"),
            pending_schedule=pending_schedule,
            day_before_sent=_strict_string_list(raw, "day_before_sent"),
            hour_before_sent=_strict_string_list(raw, "hour_before_sent"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "announced_schedule": [event.to_dict() for event in self.announced_schedule],
            "pending_schedule": (
                [event.to_dict() for event in self.pending_schedule]
                if self.pending_schedule is not None
                else None
            ),
            "day_before_sent": self.day_before_sent,
            "hour_before_sent": self.hour_before_sent,
        }


@dataclass(frozen=True)
class ServiceResolution:
    issue: Period
    resolved_at: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ServiceResolution:
        issue_raw = raw.get("issue")
        if not isinstance(issue_raw, dict):
            raise CorruptSnapshotError("service resolution issue is invalid")
        return cls(
            issue=_service_period(issue_raw, "service resolution issue"),
            resolved_at=_aware_iso_string(raw, "resolved_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue": self.issue.to_dict(),
            "resolved_at": self.resolved_at,
        }


@dataclass
class ServiceNotificationState:
    announced_issue: Period | None = None
    pending_starts: list[Period] = field(default_factory=list)
    pending_resolutions: list[ServiceResolution] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ServiceNotificationState:
        required = {
            "announced_issue",
            "pending_starts",
            "pending_resolutions",
        }
        if not required.issubset(raw):
            raise CorruptSnapshotError("service_notifications is incomplete")

        announced_raw = raw["announced_issue"]
        if announced_raw is None:
            announced = None
        elif isinstance(announced_raw, dict):
            announced = _service_period(announced_raw, "announced service issue")
        else:
            raise CorruptSnapshotError("announced service issue is invalid")

        starts_raw = raw["pending_starts"]
        if not isinstance(starts_raw, list) or any(
            not isinstance(item, dict) for item in starts_raw
        ):
            raise CorruptSnapshotError("pending service starts are invalid")

        resolutions_raw = raw["pending_resolutions"]
        if not isinstance(resolutions_raw, list) or any(
            not isinstance(item, dict) for item in resolutions_raw
        ):
            raise CorruptSnapshotError("pending service resolutions are invalid")

        return cls(
            announced_issue=announced,
            pending_starts=[_service_period(item, "pending service start") for item in starts_raw],
            pending_resolutions=[ServiceResolution.from_dict(item) for item in resolutions_raw],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "announced_issue": (self.announced_issue.to_dict() if self.announced_issue else None),
            "pending_starts": [issue.to_dict() for issue in self.pending_starts],
            "pending_resolutions": [
                resolution.to_dict() for resolution in self.pending_resolutions
            ],
        }


@dataclass
class AddressEntry:
    label: str = ""
    time_zone: str = ""
    notification_baseline_pending: bool = False
    last_success: StatusRecord | None = None
    last_error: ErrorRecord | None = None
    consecutive_error_count: int = 0
    current_period: Period | None = None
    service_issue: Period | None = None
    service_notifications: ServiceNotificationState = field(
        default_factory=ServiceNotificationState
    )
    planned_maintenance: list[PlannedMaintenance] = field(default_factory=list)
    planned_notifications: PlannedNotificationState = field(
        default_factory=PlannedNotificationState
    )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AddressEntry:
        if ("service_issue" in raw) != ("service_notifications" in raw):
            raise CorruptSnapshotError("service state is incomplete")
        if ("planned_maintenance" in raw) != ("planned_notifications" in raw):
            raise CorruptSnapshotError("planned maintenance state is incomplete")

        last_success_raw = raw.get("last_success")
        last_error_raw = raw.get("last_error")
        period_raw = raw.get("current_period")
        time_zone = _address_time_zone(raw)
        baseline_pending = raw.get("notification_baseline_pending", False)
        if not isinstance(baseline_pending, bool):
            raise CorruptSnapshotError("notification baseline flag is invalid")
        last_success = (
            StatusRecord.from_dict(last_success_raw) if isinstance(last_success_raw, dict) else None
        )
        current_period = Period.from_dict(period_raw) if isinstance(period_raw, dict) else None
        service_issue = _service_issue_state(raw, "service_issue")
        if "service_notifications" in raw:
            notifications_raw = raw["service_notifications"]
            if not isinstance(notifications_raw, dict):
                raise CorruptSnapshotError("service_notifications is not an object")
            service_notifications = ServiceNotificationState.from_dict(notifications_raw)
        else:
            legacy_issue = _legacy_service_issue(last_success, current_period)
            if service_issue is None:
                service_issue = legacy_issue
            announced_issue = service_issue or legacy_issue
            service_notifications = ServiceNotificationState(
                announced_issue=(
                    Period.from_dict(announced_issue.to_dict()) if announced_issue else None
                )
            )
        try:
            consecutive = int(raw.get("consecutive_error_count", 0) or 0)
        except (TypeError, ValueError):
            consecutive = 0
        return cls(
            label=str(raw.get("label", "")),
            time_zone=time_zone,
            notification_baseline_pending=baseline_pending,
            last_success=last_success,
            last_error=(
                ErrorRecord.from_dict(last_error_raw) if isinstance(last_error_raw, dict) else None
            ),
            consecutive_error_count=consecutive,
            current_period=current_period,
            service_issue=service_issue,
            service_notifications=service_notifications,
            planned_maintenance=_planned_list(raw, "planned_maintenance"),
            planned_notifications=_planned_notification_state(raw),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "time_zone": self.time_zone,
            "notification_baseline_pending": self.notification_baseline_pending,
            "last_success": self.last_success.to_dict() if self.last_success else None,
            "last_error": self.last_error.to_dict() if self.last_error else None,
            "consecutive_error_count": self.consecutive_error_count,
            "current_period": self.current_period.to_dict() if self.current_period else None,
            "service_issue": self.service_issue.to_dict() if self.service_issue else None,
            "service_notifications": self.service_notifications.to_dict(),
            "planned_maintenance": [event.to_dict() for event in self.planned_maintenance],
            "planned_notifications": self.planned_notifications.to_dict(),
        }

    def set_service_notification_baseline(self) -> None:
        self.service_notifications = ServiceNotificationState(
            announced_issue=(
                Period.from_dict(self.service_issue.to_dict()) if self.service_issue else None
            )
        )

    def seed_notification_baseline(self) -> None:
        self.notification_baseline_pending = False
        self.set_service_notification_baseline()
        self.planned_notifications = PlannedNotificationState(
            announced_schedule=list(self.planned_maintenance)
        )

    def defer_notification_baseline(self) -> None:
        self.notification_baseline_pending = True
        self.service_notifications = ServiceNotificationState()
        self.planned_notifications = PlannedNotificationState()

    @property
    def display_outage(self) -> str:
        return self.last_success.display_outage if self.last_success else ""

    @property
    def since(self) -> str:
        return self.current_period.started_at if self.current_period else ""


@dataclass
class PollSummary:
    started_at: str
    completed_at: str
    success_count: int
    error_count: int

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PollSummary:
        try:
            success = int(raw.get("success_count", 0) or 0)
        except (TypeError, ValueError):
            success = 0
        try:
            error = int(raw.get("error_count", 0) or 0)
        except (TypeError, ValueError):
            error = 0
        return cls(
            started_at=str(raw.get("started_at", "")),
            completed_at=str(raw.get("completed_at", "")),
            success_count=success,
            error_count=error,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "success_count": self.success_count,
            "error_count": self.error_count,
        }


@dataclass
class Snapshot:
    generated_at: str = ""
    poll: PollSummary | None = None
    addresses: dict[str, AddressEntry] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def empty(cls) -> Snapshot:
        return cls()

    @classmethod
    def from_dict(cls, raw: Any) -> Snapshot:
        """Parse a stored payload into a Snapshot or raise CorruptSnapshotError.

        Shape validation is the only gate: a payload must be a dict and must
        carry an ``addresses`` object. ``schema_version`` is enforced when
        present (must equal the current ``SCHEMA_VERSION``); payloads without
        a ``schema_version`` field are accepted as the current version for
        back-compat with state written before the field was introduced.
        Anything else (including legacy v1 blobs that stored a flat
        ``{LOC: state}`` mapping with no ``addresses`` key) is treated as
        corrupt so notification decisions and saves are blocked rather than
        silently overwriting state.
        """
        if not isinstance(raw, dict):
            raise CorruptSnapshotError("state payload is not a JSON object")
        if "addresses" not in raw:
            raise CorruptSnapshotError("state payload is missing the 'addresses' field")
        version_raw = raw.get("schema_version", SCHEMA_VERSION)
        if not isinstance(version_raw, int) or version_raw != SCHEMA_VERSION:
            raise CorruptSnapshotError(
                f"unsupported schema_version (expected {SCHEMA_VERSION}, got {version_raw!r})"
            )
        addresses_raw = raw["addresses"]
        if not isinstance(addresses_raw, dict):
            raise CorruptSnapshotError("addresses field is not an object")
        addresses: dict[str, AddressEntry] = {}
        for loc_id, entry_raw in addresses_raw.items():
            if not isinstance(loc_id, str) or not isinstance(entry_raw, dict):
                raise CorruptSnapshotError("address entry is invalid")
            addresses[loc_id] = AddressEntry.from_dict(entry_raw)
        poll_raw = raw.get("poll")
        poll = PollSummary.from_dict(poll_raw) if isinstance(poll_raw, dict) and poll_raw else None
        return cls(
            generated_at=str(raw.get("generated_at", "")),
            poll=poll,
            addresses=addresses,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": self.generated_at,
            "poll": self.poll.to_dict() if self.poll else {},
            "addresses": {loc_id: entry.to_dict() for loc_id, entry in self.addresses.items()},
        }

    def entry(self, loc_id: str) -> AddressEntry | None:
        return self.addresses.get(loc_id)


@dataclass
class StateLoadResult:
    status: StateLoadStatus
    snapshot: Snapshot
    source: str
    error: str | None = None

    @property
    def can_make_notification_decisions(self) -> bool:
        return self.status == "loaded"


def _iso_from_timestamp(value: float) -> str:
    timestamp = value if value else time.time()
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


def _timestamp_from_iso(value: Any) -> float:
    if not isinstance(value, str) or not value:
        return time.time()
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return time.time()


def _required_string(raw: dict[str, Any], field_name: str) -> str:
    value = raw.get(field_name)
    if not isinstance(value, str) or not value:
        raise CorruptSnapshotError(f"planned maintenance {field_name} is invalid")
    return value


def _aware_iso_string(raw: dict[str, Any], field_name: str) -> str:
    value = _required_string(raw, field_name)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise CorruptSnapshotError(f"planned maintenance {field_name} is invalid") from error
    if parsed.tzinfo is None:
        raise CorruptSnapshotError(f"planned maintenance {field_name} has no time zone")
    return value


def _planned_list(raw: dict[str, Any], field_name: str) -> list[PlannedMaintenance]:
    if field_name not in raw:
        return []
    value = raw[field_name]
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise CorruptSnapshotError(f"{field_name} is not a planned maintenance list")
    return [PlannedMaintenance.from_dict(item) for item in value]


def _strict_string_list(raw: dict[str, Any], field_name: str) -> list[str]:
    if field_name not in raw:
        return []
    value = raw[field_name]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CorruptSnapshotError(f"{field_name} is not a string list")
    return list(value)


def _planned_notification_state(raw: dict[str, Any]) -> PlannedNotificationState:
    if "planned_notifications" not in raw:
        return PlannedNotificationState()
    value = raw["planned_notifications"]
    if not isinstance(value, dict):
        raise CorruptSnapshotError("planned_notifications is not an object")
    return PlannedNotificationState.from_dict(value)


def _service_issue_state(raw: dict[str, Any], field_name: str) -> Period | None:
    if field_name not in raw or raw[field_name] is None:
        return None
    value = raw[field_name]
    if not isinstance(value, dict):
        raise CorruptSnapshotError(f"{field_name} is not an object")
    return _service_period(value, field_name)


def _address_time_zone(raw: dict[str, Any]) -> str:
    if "time_zone" not in raw:
        return ""
    value = raw["time_zone"]
    if not isinstance(value, str):
        raise CorruptSnapshotError("address time zone is invalid")
    if not value:
        return ""
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise CorruptSnapshotError("address time zone is invalid") from error
    return value


def _service_period(raw: dict[str, Any], field_name: str) -> Period:
    period = Period.from_dict(raw)
    if not period.display_outage.startswith(("UNPLANNED", "DEGRADATION")):
        raise CorruptSnapshotError(f"{field_name} status is invalid")
    try:
        started_at = datetime.fromisoformat(period.started_at)
    except ValueError as error:
        raise CorruptSnapshotError(f"{field_name} start is invalid") from error
    if started_at.tzinfo is None:
        raise CorruptSnapshotError(f"{field_name} start has no time zone")
    return period


def _legacy_service_issue(
    last_success: StatusRecord | None,
    current_period: Period | None,
) -> Period | None:
    if last_success is None or not last_success.display_outage.startswith(
        ("UNPLANNED", "DEGRADATION")
    ):
        return None
    started_at = current_period.started_at if current_period else last_success.checked_at
    try:
        parsed = datetime.fromisoformat(started_at)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return Period(
        display_outage=last_success.display_outage,
        started_at=started_at,
        started_at_source=(current_period.started_at_source if current_period else "observed"),
    )
