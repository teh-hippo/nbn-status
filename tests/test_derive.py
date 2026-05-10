"""Tests for ``nbn_monitor.derive``.

Covers ``derive_snapshot``, the pure transform that builds the next
snapshot from poll results plus the previous snapshot.
"""

from __future__ import annotations

import time

import nbn_monitor


def _success(loc_id: str, display: str) -> tuple[nbn_monitor.Address, nbn_monitor.OutageStatus]:
    addr = nbn_monitor.Address(label="Home", loc_id=loc_id, poll=True, notify=True)
    status = nbn_monitor.OutageStatus(
        loc_id=loc_id,
        display_outage=display,
        label=nbn_monitor.OUTAGE_LABELS.get(display, display),
        checked_at=time.time(),
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

    def test_poll_error_preserves_existing_period(self) -> None:
        """A transient poll failure must not restart or clear the current period."""
        from .conftest import v2_snapshot

        previous = v2_snapshot(
            ("LOC000000000001", "UNPLANNED_INPROGRESS", "2025-01-01T00:00:00+00:00")
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
