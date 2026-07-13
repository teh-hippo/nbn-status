"""Tests for ``nbn_monitor.snapshot``.

Covers the shape-validation contract that classifies a stored payload as
loadable or corrupt. The persistence backends only catch
``CorruptSnapshotError`` and ``json.JSONDecodeError``; the shape semantics
live in ``Snapshot.from_dict``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

import nbn_monitor

from .conftest import MULTI_WINDOW_MAINTENANCE

if TYPE_CHECKING:
    from pathlib import Path


class TestSnapshotShape:
    def test_v1_state_rejected_as_corrupt(self, state_file: Path) -> None:
        """Legacy flat state (no 'addresses' field) is treated as corrupt."""
        old_state = {"LOC000000000001": "NO_OUTAGE", "LOC000000000002": "UNPLANNED_INPROGRESS"}
        state_file.write_text(json.dumps(old_state))
        with patch.object(nbn_monitor.persistence, "STATE_FILE", state_file):
            result = nbn_monitor.load_state_result()
            assert result.status == "corrupt"
            assert result.error is not None
            assert result.snapshot.addresses == {}

    def test_corrupt_state_is_explicit(self, state_file: Path) -> None:
        state_file.write_text("{not json")
        with patch.object(nbn_monitor.persistence, "STATE_FILE", state_file):
            result = nbn_monitor.load_state_result()
            assert result.status == "corrupt"
            assert result.error is not None

    def test_to_dict_emits_schema_version(self) -> None:
        from nbn_monitor.snapshot import SCHEMA_VERSION

        snapshot = nbn_monitor.Snapshot.empty()
        assert snapshot.to_dict()["schema_version"] == SCHEMA_VERSION

    def test_state_without_schema_version_loads(self, state_file: Path) -> None:
        """Existing production blobs were written before the field; accept them."""
        state_file.write_text(json.dumps({"addresses": {}, "generated_at": ""}))
        with patch.object(nbn_monitor.persistence, "STATE_FILE", state_file):
            result = nbn_monitor.load_state_result()
            assert result.status == "loaded"

    def test_existing_v2_address_gets_empty_planned_defaults(self) -> None:
        state_file = {
            "schema_version": 2,
            "addresses": {
                "LOC000000000001": {
                    "label": "Home",
                    "last_success": None,
                    "last_error": None,
                    "consecutive_error_count": 0,
                    "current_period": None,
                }
            },
        }

        snapshot = nbn_monitor.Snapshot.from_dict(state_file)
        entry = snapshot.entry("LOC000000000001")

        assert entry is not None
        assert entry.planned_maintenance == []
        assert entry.planned_notifications == nbn_monitor.PlannedNotificationState()

    def test_existing_v2_service_issue_is_migrated_as_announced(self) -> None:
        payload = {
            "schema_version": 2,
            "addresses": {
                "LOC000000000001": {
                    "label": "Home",
                    "last_success": {
                        "display_outage": "UNPLANNED_INPROGRESS",
                        "label": "Unplanned",
                        "colour": "red",
                        "checked_at": "2026-07-13T00:00:00+00:00",
                    },
                    "current_period": {
                        "display_outage": "UNPLANNED_INPROGRESS",
                        "started_at": "2026-07-13T00:00:00+00:00",
                        "started_at_source": "observed",
                    },
                }
            },
        }

        snapshot = nbn_monitor.Snapshot.from_dict(payload)
        entry = snapshot.entry("LOC000000000001")

        assert entry is not None
        assert entry.service_issue is not None
        assert entry.service_notifications.announced_issue == entry.service_issue

    def test_planned_state_round_trips(self) -> None:
        event = nbn_monitor.parse_planned_maintenance(MULTI_WINDOW_MAINTENANCE).events[0]
        revision = nbn_monitor.material_revision(event)
        snapshot = nbn_monitor.Snapshot(
            addresses={
                "LOC000000000001": nbn_monitor.AddressEntry(
                    label="Home",
                    service_issue=nbn_monitor.Period(
                        display_outage="UNPLANNED_INPROGRESS",
                        started_at="2026-07-13T00:00:00+00:00",
                        started_at_source="observed",
                    ),
                    service_notifications=nbn_monitor.ServiceNotificationState(
                        announced_issue=nbn_monitor.Period(
                            display_outage="UNPLANNED_INPROGRESS",
                            started_at="2026-07-13T00:00:00+00:00",
                            started_at_source="observed",
                        )
                    ),
                    planned_maintenance=[event],
                    planned_notifications=nbn_monitor.PlannedNotificationState(
                        announced_schedule=[event],
                        pending_schedule=[event],
                        day_before_sent=[revision],
                        hour_before_sent=[revision],
                    ),
                )
            }
        )

        restored = nbn_monitor.Snapshot.from_dict(snapshot.to_dict())

        assert restored == snapshot

    def test_present_invalid_planned_fields_are_corrupt(self) -> None:
        invalid_values = [
            {"planned_maintenance": "invalid"},
            {"planned_notifications": "invalid"},
            {
                "planned_maintenance": [
                    {
                        "event_key": "",
                        "maintenance_date": "invalid",
                        "starts_at": "invalid",
                        "time_zone": "/etc/passwd",
                    }
                ]
            },
            {
                "planned_notifications": {
                    "announced_schedule": "invalid",
                    "day_before_sent": [],
                    "hour_before_sent": [],
                }
            },
            {"planned_notifications": {"announced_schedule": []}},
            {
                "planned_notifications": {
                    "announced_schedule": [],
                    "day_before_sent": "invalid",
                    "hour_before_sent": [],
                }
            },
            {"service_issue": "invalid"},
            {"service_notifications": {"announced_issue": None}},
            {"time_zone": "/etc/passwd"},
            {"notification_baseline_pending": "yes"},
        ]

        for invalid in invalid_values:
            payload = {
                "schema_version": 2,
                "addresses": {
                    "LOC000000000001": {
                        "label": "Home",
                        **invalid,
                    }
                },
            }
            with pytest.raises(nbn_monitor.snapshot.CorruptSnapshotError):
                nbn_monitor.Snapshot.from_dict(payload)

    def test_malformed_address_entry_is_corrupt(self) -> None:
        payload = {
            "schema_version": 2,
            "addresses": {
                "LOC000000000001": "invalid",
            },
        }

        with pytest.raises(nbn_monitor.snapshot.CorruptSnapshotError):
            nbn_monitor.Snapshot.from_dict(payload)

    def test_unsupported_schema_version_is_corrupt(self, state_file: Path) -> None:
        """A future schema version is treated as corrupt rather than misread."""
        state_file.write_text(json.dumps({"schema_version": 99, "addresses": {}}))
        with patch.object(nbn_monitor.persistence, "STATE_FILE", state_file):
            result = nbn_monitor.load_state_result()
            assert result.status == "corrupt"
            assert result.error is not None
            assert "schema_version" in result.error
