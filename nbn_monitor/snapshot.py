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
from datetime import UTC, datetime
from typing import Any, Literal

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


@dataclass
class AddressEntry:
    label: str = ""
    last_success: StatusRecord | None = None
    last_error: ErrorRecord | None = None
    consecutive_error_count: int = 0
    current_period: Period | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AddressEntry:
        last_success_raw = raw.get("last_success")
        last_error_raw = raw.get("last_error")
        period_raw = raw.get("current_period")
        try:
            consecutive = int(raw.get("consecutive_error_count", 0) or 0)
        except (TypeError, ValueError):
            consecutive = 0
        return cls(
            label=str(raw.get("label", "")),
            last_success=(
                StatusRecord.from_dict(last_success_raw)
                if isinstance(last_success_raw, dict)
                else None
            ),
            last_error=(
                ErrorRecord.from_dict(last_error_raw) if isinstance(last_error_raw, dict) else None
            ),
            consecutive_error_count=consecutive,
            current_period=(Period.from_dict(period_raw) if isinstance(period_raw, dict) else None),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "last_success": self.last_success.to_dict() if self.last_success else None,
            "last_error": self.last_error.to_dict() if self.last_error else None,
            "consecutive_error_count": self.consecutive_error_count,
            "current_period": self.current_period.to_dict() if self.current_period else None,
        }

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
                continue
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
