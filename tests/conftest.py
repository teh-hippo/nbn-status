"""Shared fixtures and helpers for the test suite.

Every per-module test file imports from here rather than re-defining the
sample address list, the canned NBN responses, or the snapshot helper.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

import nbn_monitor

SAMPLE_ADDRESSES_JSON = json.dumps(
    [
        {"label": "Home", "loc_id": "LOC000000000001", "poll": True, "notify": True},
        {
            "label": "Neighbour",
            "loc_id": "LOC000000000002",
            "poll": True,
            "notify": False,
            "compare": True,
        },
        {"label": "Family", "loc_id": "LOC000000000003", "poll": False, "notify": False},
    ]
)

MAINTENANCE_OK: dict[str, Any] = {
    "plannedOutages": {"primary": {"state": "UNDEFINED", "overLap": False}},
    "displayOutage": "NO_OUTAGE",
    "validAt": 1700000000000,
    "timeZoneId": "Australia/Sydney",
}

MAINTENANCE_OUTAGE: dict[str, Any] = {
    "plannedOutages": {"primary": {"state": "UNDEFINED", "overLap": False}},
    "unplannedOutages": {
        "current": [
            {
                "status": "IN_PROGRESS",
                "isUnplannedNBNOutage": True,
                "networkDegradation": False,
                "unplannedPowerOutage": False,
                "ecrqRequest": False,
            }
        ]
    },
    "displayOutage": "UNPLANNED_INPROGRESS",
    "validAt": 1700000000000,
    "timeZoneId": "Australia/Sydney",
}

MAINTENANCE_PLANNED: dict[str, Any] = {
    "plannedOutages": {
        "primary": {
            "state": "IN_PROGRESS",
            "overLap": False,
            "maintenanceStartTime": "2026-03-26T08:00:00",
            "maintenanceEndTime": "2026-03-26T12:00:00",
        }
    },
    "displayOutage": "PLANNED_INPROGRESS",
    "validAt": 1700000000000,
    "timeZoneId": "Australia/Sydney",
}

TEST_NTFY = nbn_monitor.NtfyConfig(
    server="https://ntfy.sh",
    topic="test-topic",
    status_page_url="",
)

MULTI_WINDOW_MAINTENANCE: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "multi_window_maintenance.json").read_text()
)


def v2_snapshot(*entries: tuple[str, str, str]) -> nbn_monitor.Snapshot:
    """Build a Snapshot from (loc_id, display_outage, since) tuples.

    An empty ``display_outage`` skips last_success and current_period; the
    address still appears as a known key with no recorded status.
    """
    addresses: dict[str, nbn_monitor.AddressEntry] = {}
    for loc_id, display, since in entries:
        last_success: nbn_monitor.StatusRecord | None = None
        current_period: nbn_monitor.Period | None = None
        service_issue: nbn_monitor.Period | None = None
        service_notifications = nbn_monitor.ServiceNotificationState()
        if display:
            last_success = nbn_monitor.StatusRecord(
                display_outage=display,
                label=nbn_monitor.OUTAGE_LABELS.get(display, display),
                colour=nbn_monitor.display_outage_colour(display),
                checked_at=since,
            )
            current_period = nbn_monitor.Period(
                display_outage=display,
                started_at=since,
                started_at_source="observed",
            )
            if nbn_monitor.display_outage_is_service_issue(display):
                service_issue = nbn_monitor.Period(
                    display_outage=display,
                    started_at=since,
                    started_at_source="observed",
                )
                service_notifications = nbn_monitor.ServiceNotificationState(
                    announced_issue=nbn_monitor.Period(
                        display_outage=display,
                        started_at=since,
                        started_at_source="observed",
                    )
                )
        addresses[loc_id] = nbn_monitor.AddressEntry(
            label="",
            last_success=last_success,
            current_period=current_period,
            service_issue=service_issue,
            service_notifications=service_notifications,
        )
    return nbn_monitor.Snapshot(addresses=addresses)


@pytest.fixture()
def addresses() -> list[nbn_monitor.Address]:
    with patch.dict(os.environ, {"NBN_ADDRESSES": SAMPLE_ADDRESSES_JSON}):
        return nbn_monitor.load_addresses()


@pytest.fixture()
def state_file(tmp_path: Path) -> Path:
    return tmp_path / "state.json"
