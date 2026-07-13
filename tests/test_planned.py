"""Tests for planned-maintenance parsing, diffing, and reminder timing."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime

import nbn_monitor

from .conftest import MAINTENANCE_OK, MAINTENANCE_PLANNED, MULTI_WINDOW_MAINTENANCE


class TestParsePlannedMaintenance:
    def test_parses_live_multi_day_schedule(self) -> None:
        result = nbn_monitor.parse_planned_maintenance(MULTI_WINDOW_MAINTENANCE)

        assert result.skipped_count == 0
        assert len(result.events) == 8
        assert [event.maintenance_date for event in result.events] == [
            "2026-07-13",
            "2026-07-14",
            "2026-07-15",
            "2026-07-16",
            "2026-07-17",
            "2026-07-20",
            "2026-07-21",
            "2026-07-22",
        ]
        assert result.events[0].event_key == "2026-07-13"
        assert result.events[0].starts_at == "2026-07-12T14:00:00+00:00"
        assert result.events[0].duration_minutes == 360
        assert result.events[3].starts_at == "2026-07-15T21:00:00+00:00"
        assert result.events[5].maintenance_ends_at == "2026-07-24T09:00:00+00:00"

    def test_maintenance_list_does_not_duplicate_primary(self) -> None:
        result = nbn_monitor.parse_planned_maintenance(MULTI_WINDOW_MAINTENANCE)
        assert len(result.events) == 8

    def test_supports_iso_primary_fallback(self) -> None:
        result = nbn_monitor.parse_planned_maintenance(MAINTENANCE_PLANNED)

        assert len(result.events) == 1
        assert result.events[0].starts_at == "2026-03-25T21:00:00+00:00"
        assert result.events[0].maintenance_ends_at == "2026-03-26T01:00:00+00:00"

    def test_missing_timezone_skips_schedule(self) -> None:
        payload = deepcopy(MULTI_WINDOW_MAINTENANCE)
        payload.pop("timeZoneId")

        result = nbn_monitor.parse_planned_maintenance(payload)

        assert result.events == ()
        assert result.skipped_count == 8

    def test_undefined_primary_is_a_valid_empty_schedule(self) -> None:
        result = nbn_monitor.parse_planned_maintenance(MAINTENANCE_OK)
        assert result.events == ()
        assert result.skipped_count == 0

    def test_empty_schedule_without_timezone_is_incomplete(self) -> None:
        payload = deepcopy(MAINTENANCE_OK)
        payload.pop("timeZoneId")

        result = nbn_monitor.parse_planned_maintenance(payload)

        assert result.events == ()
        assert result.skipped_count == 1

    def test_null_primary_is_incomplete(self) -> None:
        result = nbn_monitor.parse_planned_maintenance(
            {
                "plannedOutages": {"primary": None},
                "timeZoneId": "Australia/Sydney",
            }
        )
        assert result.events == ()
        assert result.skipped_count == 1

    def test_missing_planned_outages_is_malformed(self) -> None:
        result = nbn_monitor.parse_planned_maintenance(
            {"displayOutage": "NO_OUTAGE", "timeZoneId": "Australia/Sydney"}
        )
        assert result.events == ()
        assert result.skipped_count == 1

    def test_invalid_maintenance_list_container_is_malformed(self) -> None:
        payload = deepcopy(MULTI_WINDOW_MAINTENANCE)
        payload["plannedOutages"]["maintenanceList"] = "truncated"

        result = nbn_monitor.parse_planned_maintenance(payload)

        assert result.skipped_count == 1

    def test_non_empty_maintenance_list_with_only_empty_buckets_is_incomplete(self) -> None:
        payload = deepcopy(MULTI_WINDOW_MAINTENANCE)
        payload["plannedOutages"]["maintenanceList"] = {"1783864800000": []}

        result = nbn_monitor.parse_planned_maintenance(payload)

        assert result.events == ()
        assert result.skipped_count == 1

    def test_empty_bucket_alongside_valid_entries_is_incomplete(self) -> None:
        payload = deepcopy(MULTI_WINDOW_MAINTENANCE)
        payload["plannedOutages"]["maintenanceList"]["1783951200000"] = []

        result = nbn_monitor.parse_planned_maintenance(payload)

        assert len(result.events) == 7
        assert result.skipped_count == 1

    def test_invalid_material_field_marks_parse_incomplete(self) -> None:
        payload = deepcopy(MULTI_WINDOW_MAINTENANCE)
        payload["plannedOutages"]["maintenanceList"]["1783951200000"][0]["duration"] = "invalid"

        result = nbn_monitor.parse_planned_maintenance(payload)

        assert result.skipped_count == 1

    def test_lifecycle_only_changes_do_not_change_material_revision(self) -> None:
        changed = deepcopy(MULTI_WINDOW_MAINTENANCE)
        first = changed["plannedOutages"]["maintenanceList"]["1783864800000"][0]
        first["state"] = "Last_Day_ETI"
        first["overLap"] = True
        changed["validAt"] += 1

        original_events = nbn_monitor.parse_planned_maintenance(MULTI_WINDOW_MAINTENANCE).events
        changed_events = nbn_monitor.parse_planned_maintenance(changed).events

        assert [nbn_monitor.material_revision(event) for event in changed_events] == [
            nbn_monitor.material_revision(event) for event in original_events
        ]

    def test_multiple_entries_on_one_day_get_stable_occurrences(self) -> None:
        payload = {
            "timeZoneId": "Australia/Sydney",
            "plannedOutages": {
                "maintenanceList": {
                    "1783864800000": [
                        {
                            "maintenanceDate": 1783864800000,
                            "maintenanceStartTime": 1783864800000,
                            "duration": 60,
                        },
                        {
                            "maintenanceDate": 1783864800000,
                            "interruptionStartTime": 1783886400000,
                            "duration": 30,
                        },
                    ]
                }
            },
        }

        result = nbn_monitor.parse_planned_maintenance(payload)

        assert [event.event_key for event in result.events] == [
            "2026-07-13",
            "2026-07-13",
        ]

    def test_removing_same_day_event_does_not_rekey_survivor(self) -> None:
        payload = {
            "timeZoneId": "Australia/Sydney",
            "plannedOutages": {
                "maintenanceList": {
                    "1783864800000": [
                        {
                            "maintenanceDate": 1783864800000,
                            "maintenanceStartTime": 1783864800000,
                            "duration": 60,
                        },
                        {
                            "maintenanceDate": 1783864800000,
                            "interruptionStartTime": 1783886400000,
                            "duration": 30,
                        },
                    ]
                }
            },
        }
        announced = nbn_monitor.parse_planned_maintenance(payload).events
        changed = deepcopy(payload)
        changed["plannedOutages"]["maintenanceList"]["1783864800000"].pop(0)
        current = nbn_monitor.parse_planned_maintenance(changed).events

        assert current[0].event_key == announced[1].event_key
        diff = nbn_monitor.diff_planned_schedule(
            current,
            announced,
            datetime(2026, 7, 12, 0, 0, tzinfo=UTC),
        )
        assert len(diff.removed) == 1
        assert not diff.added
        assert not diff.changed

    def test_removing_same_start_event_preserves_exact_survivor(self) -> None:
        payload = {
            "timeZoneId": "Australia/Sydney",
            "plannedOutages": {
                "maintenanceList": {
                    "1783864800000": [
                        {
                            "maintenanceDate": 1783864800000,
                            "maintenanceStartTime": 1783864800000,
                            "duration": 30,
                        },
                        {
                            "maintenanceDate": 1783864800000,
                            "maintenanceStartTime": 1783864800000,
                            "duration": 60,
                        },
                    ]
                }
            },
        }
        announced = nbn_monitor.parse_planned_maintenance(payload).events
        changed = deepcopy(payload)
        changed["plannedOutages"]["maintenanceList"]["1783864800000"].pop(0)
        current = nbn_monitor.parse_planned_maintenance(changed).events

        assert current[0].event_key == announced[1].event_key
        assert nbn_monitor.material_revision(current[0]) == nbn_monitor.material_revision(
            announced[1]
        )
        diff = nbn_monitor.diff_planned_schedule(
            current,
            announced,
            datetime(2026, 7, 12, 0, 0, tzinfo=UTC),
        )
        assert len(diff.removed) == 1
        assert diff.removed[0].duration_minutes == 30
        assert not diff.added
        assert not diff.changed


class TestPlannedScheduleDiff:
    def setup_method(self) -> None:
        self.events = list(nbn_monitor.parse_planned_maintenance(MULTI_WINDOW_MAINTENANCE).events)
        self.before_schedule = datetime(2026, 7, 12, 0, 0, tzinfo=UTC)

    def test_initial_schedule_is_added(self) -> None:
        diff = nbn_monitor.diff_planned_schedule(self.events, [], self.before_schedule)
        assert diff.is_initial
        assert len(diff.added) == 8

    def test_unchanged_schedule_has_no_diff(self) -> None:
        diff = nbn_monitor.diff_planned_schedule(
            self.events,
            self.events,
            self.before_schedule,
        )
        assert not diff.has_changes

    def test_material_change_is_detected(self) -> None:
        changed = [replace(self.events[0], duration_minutes=361), *self.events[1:]]

        diff = nbn_monitor.diff_planned_schedule(
            changed,
            self.events,
            self.before_schedule,
        )

        assert len(diff.changed) == 1
        assert diff.changed[0].previous.duration_minutes == 360
        assert diff.changed[0].current.duration_minutes == 361

    def test_future_removal_is_cancellation(self) -> None:
        diff = nbn_monitor.diff_planned_schedule(
            self.events[1:],
            self.events,
            self.before_schedule,
        )
        assert len(diff.removed) == 1

    def test_started_event_expiry_is_not_cancellation(self) -> None:
        after_first_start = datetime(2026, 7, 12, 15, 0, tzinfo=UTC)
        diff = nbn_monitor.diff_planned_schedule([], [self.events[0]], after_first_start)
        assert not diff.has_changes

    def test_event_rescheduled_earlier_is_changed_not_cancelled(self) -> None:
        announced = self.events[1]
        current = replace(
            announced,
            starts_at="2026-07-12T23:00:00+00:00",
        )
        now = datetime(2026, 7, 13, 0, 0, tzinfo=UTC)

        diff = nbn_monitor.diff_planned_schedule([current], [announced], now)

        assert len(diff.changed) == 1
        assert not diff.added
        assert not diff.removed
        assert not diff.is_cancellation

    def test_past_exact_match_does_not_consume_same_day_future_event(self) -> None:
        morning = replace(
            self.events[0],
            event_key="2026-07-13",
            starts_at="2026-07-12T22:00:00+00:00",
            duration_minutes=30,
        )
        evening = replace(
            self.events[0],
            event_key="2026-07-13",
            starts_at="2026-07-13T10:00:00+00:00",
            duration_minutes=60,
        )

        diff = nbn_monitor.diff_planned_schedule(
            [morning],
            [morning, evening],
            datetime(2026, 7, 13, 2, 0, tzinfo=UTC),
        )

        assert diff.removed == (evening,)
        assert not diff.changed
        assert not diff.added

    def test_changed_started_row_does_not_consume_future_same_day_event(self) -> None:
        morning = replace(
            self.events[0],
            event_key="2026-07-13",
            starts_at="2026-07-13T00:00:00+00:00",
            duration_minutes=30,
        )
        changed_morning = replace(morning, duration_minutes=45)
        evening = replace(
            self.events[0],
            event_key="2026-07-13",
            starts_at="2026-07-13T08:00:00+00:00",
            duration_minutes=60,
        )

        diff = nbn_monitor.diff_planned_schedule(
            [changed_morning],
            [morning, evening],
            datetime(2026, 7, 13, 1, 0, tzinfo=UTC),
        )

        assert len(diff.changed) == 1
        assert diff.changed[0].previous == morning
        assert diff.changed[0].current == changed_morning
        assert diff.removed == (evening,)
        assert not diff.added


class TestPlannedReminders:
    def setup_method(self) -> None:
        self.event = nbn_monitor.parse_planned_maintenance(MULTI_WINDOW_MAINTENANCE).events[1]

    def test_day_before_is_due_at_9am_local(self) -> None:
        before = datetime(2026, 7, 12, 22, 59, tzinfo=UTC)
        due = datetime(2026, 7, 12, 23, 0, tzinfo=UTC)
        state = nbn_monitor.PlannedNotificationState()

        assert not nbn_monitor.due_planned_reminders([self.event], state, before).has_due
        reminders = nbn_monitor.due_planned_reminders([self.event], state, due)
        assert reminders.day_before == (self.event,)
        assert reminders.hour_before == ()

    def test_hour_before_catches_up_and_consolidates_day_marker(self) -> None:
        one_hour_before = datetime(2026, 7, 13, 13, 0, tzinfo=UTC)
        reminders = nbn_monitor.due_planned_reminders(
            [self.event],
            nbn_monitor.PlannedNotificationState(),
            one_hour_before,
        )

        assert reminders.day_before == (self.event,)
        assert reminders.hour_before == (self.event,)

    def test_sent_markers_prevent_repeats(self) -> None:
        revision = nbn_monitor.material_revision(self.event)
        state = nbn_monitor.PlannedNotificationState(
            day_before_sent=[revision],
            hour_before_sent=[revision],
        )

        reminders = nbn_monitor.due_planned_reminders(
            [self.event],
            state,
            datetime(2026, 7, 13, 13, 30, tzinfo=UTC),
        )

        assert not reminders.has_due

    def test_reminders_do_not_send_after_start(self) -> None:
        reminders = nbn_monitor.due_planned_reminders(
            [self.event],
            nbn_monitor.PlannedNotificationState(),
            datetime(2026, 7, 13, 14, 1, tzinfo=UTC),
        )
        assert not reminders.has_due

    def test_visible_schedule_keeps_today_and_future(self) -> None:
        events = nbn_monitor.parse_planned_maintenance(MULTI_WINDOW_MAINTENANCE).events
        visible = nbn_monitor.visible_events(
            events,
            datetime(2026, 7, 13, 16, 0, tzinfo=UTC),
        )

        assert visible[0].maintenance_date == "2026-07-14"
        assert len(visible) == 7
