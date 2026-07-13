"""Tests for ``nbn_monitor.derive``.

Covers ``derive_snapshot``, the pure transform that builds the next
snapshot from poll results plus the previous snapshot.
"""

from __future__ import annotations

import time
from copy import deepcopy
from datetime import UTC, datetime

import nbn_monitor

from .conftest import MAINTENANCE_OK, MULTI_WINDOW_MAINTENANCE, v2_snapshot


def _success(
    loc_id: str,
    display: str,
    *,
    raw: dict[str, object] | None = None,
    checked_at: float | None = None,
) -> tuple[nbn_monitor.Address, nbn_monitor.OutageStatus]:
    addr = nbn_monitor.Address(label="Home", loc_id=loc_id, poll=True, notify=True)
    status = nbn_monitor.OutageStatus(
        loc_id=loc_id,
        display_outage=display,
        label=nbn_monitor.OUTAGE_LABELS.get(display, display),
        raw=raw or {},
        checked_at=checked_at if checked_at is not None else time.time(),
    )
    return addr, status


class TestDeriveSnapshot:
    def test_builds_new_state_for_first_outage(self) -> None:
        results = [_success("LOC000000000001", "UNPLANNED_INPROGRESS")]

        new_state = nbn_monitor.derive_snapshot(
            results,
            nbn_monitor.Snapshot.empty(),
            started_at="2025-01-01T00:00:00+00:00",
            completed_at="2025-01-01T00:00:01+00:00",
        )
        entry = new_state.entry("LOC000000000001")
        assert entry is not None
        assert entry.last_success is not None
        assert entry.last_success.display_outage == "UNPLANNED_INPROGRESS"
        assert entry.current_period is not None
        assert entry.current_period.display_outage == "UNPLANNED_INPROGRESS"
        assert entry.service_issue is not None
        assert entry.service_issue.display_outage == "UNPLANNED_INPROGRESS"

    def test_service_issue_survives_planned_status_until_no_outage(self) -> None:
        previous = v2_snapshot(
            ("LOC000000000001", "UNPLANNED_INPROGRESS", "2026-07-13T00:00:00+00:00")
        )
        planned = nbn_monitor.derive_snapshot(
            [
                _success(
                    "LOC000000000001",
                    "PLANNED_INPROGRESS",
                    raw=MULTI_WINDOW_MAINTENANCE,
                )
            ],
            previous,
            started_at="2026-07-13T02:00:00+00:00",
            completed_at="2026-07-13T02:00:01+00:00",
        )
        planned_entry = planned.entry("LOC000000000001")
        assert planned_entry is not None
        assert planned_entry.service_issue is not None
        assert planned_entry.service_issue.started_at == "2026-07-13T00:00:00+00:00"

        healthy = nbn_monitor.derive_snapshot(
            [_success("LOC000000000001", "NO_OUTAGE", raw=MAINTENANCE_OK)],
            planned,
            started_at="2026-07-13T03:00:00+00:00",
            completed_at="2026-07-13T03:00:01+00:00",
        )
        healthy_entry = healthy.entry("LOC000000000001")
        assert healthy_entry is not None
        assert healthy_entry.service_issue is None

    def test_successful_poll_stores_all_planned_events(self) -> None:
        results = [
            _success(
                "LOC000000000001",
                "UNPLANNED_INPROGRESS",
                raw=MULTI_WINDOW_MAINTENANCE,
            )
        ]

        new_state = nbn_monitor.derive_snapshot(
            results,
            nbn_monitor.Snapshot.empty(),
            started_at="2026-07-13T02:00:00+00:00",
            completed_at="2026-07-13T02:00:01+00:00",
        )

        entry = new_state.entry("LOC000000000001")
        assert entry is not None
        assert len(entry.planned_maintenance) == 8

    def test_invalid_material_schedule_field_preserves_last_known_schedule(self) -> None:
        previous_events = list(
            nbn_monitor.parse_planned_maintenance(MULTI_WINDOW_MAINTENANCE).events
        )
        previous = nbn_monitor.Snapshot(
            addresses={
                "LOC000000000001": nbn_monitor.AddressEntry(
                    label="Home",
                    planned_maintenance=previous_events,
                )
            }
        )
        malformed = deepcopy(MULTI_WINDOW_MAINTENANCE)
        malformed["plannedOutages"]["maintenanceList"]["1783951200000"][0]["duration"] = "invalid"

        new_state = nbn_monitor.derive_snapshot(
            [
                _success(
                    "LOC000000000001",
                    "NO_OUTAGE",
                    raw=malformed,
                )
            ],
            previous,
            started_at="2026-07-13T02:00:00+00:00",
            completed_at="2026-07-13T02:00:01+00:00",
        )

        entry = new_state.entry("LOC000000000001")
        assert entry is not None
        assert entry.planned_maintenance == previous_events
        assert entry.planned_maintenance[0].maintenance_date == "2026-07-13"

    def test_non_notify_address_still_stores_planned_events(self) -> None:
        addr = nbn_monitor.Address(
            label="Neighbour",
            loc_id="LOC000000000002",
            notify=False,
            compare=True,
        )
        status = nbn_monitor.OutageStatus(
            loc_id=addr.loc_id,
            display_outage="NO_OUTAGE",
            label="No outage",
            raw=MULTI_WINDOW_MAINTENANCE,
            checked_at=time.time(),
        )

        new_state = nbn_monitor.derive_snapshot(
            [(addr, status)],
            nbn_monitor.Snapshot.empty(),
            started_at="2026-07-13T02:00:00+00:00",
            completed_at="2026-07-13T02:00:01+00:00",
        )

        entry = new_state.entry(addr.loc_id)
        assert entry is not None
        assert len(entry.planned_maintenance) == 8
        assert entry.time_zone == "Australia/Sydney"
        assert entry.planned_notifications == nbn_monitor.PlannedNotificationState()

    def test_non_notify_address_does_not_accumulate_service_deliveries(self) -> None:
        addr = nbn_monitor.Address(
            label="Neighbour",
            loc_id="LOC000000000002",
            notify=False,
            compare=True,
        )
        outage = nbn_monitor.OutageStatus(
            loc_id=addr.loc_id,
            display_outage="UNPLANNED_INPROGRESS",
            label="Unplanned",
            checked_at=datetime(2026, 7, 13, 0, 0, tzinfo=UTC).timestamp(),
        )
        active = nbn_monitor.derive_snapshot(
            [(addr, outage)],
            nbn_monitor.Snapshot.empty(),
            started_at="2026-07-13T00:00:00+00:00",
            completed_at="2026-07-13T00:00:01+00:00",
        )
        healthy = nbn_monitor.derive_snapshot(
            [
                (
                    addr,
                    nbn_monitor.OutageStatus(
                        loc_id=addr.loc_id,
                        display_outage="NO_OUTAGE",
                        label="No outage",
                        checked_at=datetime(
                            2026,
                            7,
                            13,
                            1,
                            0,
                            tzinfo=UTC,
                        ).timestamp(),
                    ),
                )
            ],
            active,
            started_at="2026-07-13T01:00:00+00:00",
            completed_at="2026-07-13T01:00:01+00:00",
        )

        entry = healthy.entry(addr.loc_id)
        assert entry is not None
        assert entry.service_notifications.pending_starts == []
        assert entry.service_notifications.pending_resolutions == []
        assert entry.service_notifications.announced_issue is None

    def test_malformed_schedule_preserves_last_known_schedule(self) -> None:
        previous = nbn_monitor.Snapshot(
            addresses={
                "LOC000000000001": nbn_monitor.AddressEntry(
                    label="Home",
                    planned_maintenance=list(
                        nbn_monitor.parse_planned_maintenance(MULTI_WINDOW_MAINTENANCE).events
                    ),
                )
            }
        )
        malformed = dict(MULTI_WINDOW_MAINTENANCE)
        malformed.pop("timeZoneId")
        results = [
            _success(
                "LOC000000000001",
                "NO_OUTAGE",
                raw=malformed,
            )
        ]

        new_state = nbn_monitor.derive_snapshot(
            results,
            previous,
            started_at="2026-07-13T02:00:00+00:00",
            completed_at="2026-07-13T02:00:01+00:00",
        )

        entry = new_state.entry("LOC000000000001")
        assert entry is not None
        assert len(entry.planned_maintenance) == 8

    def test_missing_schedule_shape_preserves_last_known_schedule(self) -> None:
        previous = nbn_monitor.Snapshot(
            addresses={
                "LOC000000000001": nbn_monitor.AddressEntry(
                    label="Home",
                    planned_maintenance=list(
                        nbn_monitor.parse_planned_maintenance(MULTI_WINDOW_MAINTENANCE).events
                    ),
                )
            }
        )
        results = [
            _success(
                "LOC000000000001",
                "NO_OUTAGE",
                raw={
                    "displayOutage": "NO_OUTAGE",
                    "timeZoneId": "Australia/Sydney",
                },
            )
        ]

        new_state = nbn_monitor.derive_snapshot(
            results,
            previous,
            started_at="2026-07-13T02:00:00+00:00",
            completed_at="2026-07-13T02:00:01+00:00",
        )

        entry = new_state.entry("LOC000000000001")
        assert entry is not None
        assert len(entry.planned_maintenance) == 8

    def test_unplanned_period_does_not_use_planned_start(self) -> None:
        checked_at = datetime(2026, 7, 13, 2, 0, tzinfo=UTC).timestamp()
        results = [
            _success(
                "LOC000000000001",
                "UNPLANNED_INPROGRESS",
                raw=MULTI_WINDOW_MAINTENANCE,
                checked_at=checked_at,
            )
        ]

        new_state = nbn_monitor.derive_snapshot(
            results,
            nbn_monitor.Snapshot.empty(),
            started_at="2026-07-13T02:00:00+00:00",
            completed_at="2026-07-13T02:00:01+00:00",
        )

        entry = new_state.entry("LOC000000000001")
        assert entry is not None
        assert entry.current_period is not None
        assert entry.current_period.started_at == "2026-07-13T02:00:00+00:00"

    def test_planned_period_uses_nbn_event_start(self) -> None:
        results = [
            _success(
                "LOC000000000001",
                "PLANNED_INPROGRESS",
                raw=MULTI_WINDOW_MAINTENANCE,
            )
        ]

        new_state = nbn_monitor.derive_snapshot(
            results,
            nbn_monitor.Snapshot.empty(),
            started_at="2026-07-13T02:00:00+00:00",
            completed_at="2026-07-13T02:00:01+00:00",
        )

        entry = new_state.entry("LOC000000000001")
        assert entry is not None
        assert entry.current_period is not None
        assert entry.current_period.started_at == "2026-07-12T14:00:00+00:00"

    def test_poll_error_preserves_existing_period(self) -> None:
        """A transient poll failure must not restart or clear the current period."""
        previous = v2_snapshot(
            ("LOC000000000001", "UNPLANNED_INPROGRESS", "2025-01-01T00:00:00+00:00")
        )
        existing = previous.entry("LOC000000000001")
        assert existing is not None
        existing.planned_maintenance = list(
            nbn_monitor.parse_planned_maintenance(MULTI_WINDOW_MAINTENANCE).events
        )
        addr = nbn_monitor.Address(label="Home", loc_id="LOC000000000001", poll=True, notify=True)
        status = nbn_monitor.OutageStatus(
            loc_id="LOC000000000001",
            display_outage="",
            label="Error",
            error="timeout",
            checked_at=time.time(),
        )

        new_state = nbn_monitor.derive_snapshot(
            [(addr, status)],
            previous,
            started_at="2025-01-01T00:00:00+00:00",
            completed_at="2025-01-01T00:00:01+00:00",
        )
        entry = new_state.entry("LOC000000000001")
        assert entry is not None
        assert entry.last_success is not None
        assert entry.last_success.display_outage == "UNPLANNED_INPROGRESS"
        assert entry.current_period is not None
        assert entry.current_period.started_at == "2025-01-01T00:00:00+00:00"
        assert entry.last_error is not None
        assert entry.last_error.message == "timeout"
        assert len(entry.planned_maintenance) == 8
