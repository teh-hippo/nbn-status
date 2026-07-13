"""Tests for ``nbn_monitor.render``.

Covers the HTML status-page renderer that turns the authoritative snapshot
plus the configured address list into the user-facing traffic-light page.
"""

from __future__ import annotations

import nbn_monitor

from .conftest import MULTI_WINDOW_MAINTENANCE, v2_snapshot


class TestGenerateHtml:
    def test_generates_valid_html(self, addresses: list[nbn_monitor.Address]) -> None:
        snapshot = v2_snapshot(
            ("LOC000000000001", "NO_OUTAGE", ""),
            ("LOC000000000002", "UNPLANNED_INPROGRESS", "2025-01-01T00:00:00+00:00"),
        )
        html = nbn_monitor.generate_html(addresses, snapshot)
        assert "<!DOCTYPE html>" in html
        assert "Home" in html
        assert "Neighbour" in html
        assert "#22c55e" in html  # green
        assert "#ff4d55" in html  # red
        assert "refreshing in" in html
        assert 'class="status-tag"' in html
        assert 'class="card status-green"' in html
        assert 'style="background:' not in html

    def test_missing_snapshot_entry_renders_grey(self) -> None:
        """An address without a snapshot entry renders as grey with a no-data tag."""
        addr = nbn_monitor.Address(label="Broken", loc_id="LOC000000000099")
        html = nbn_monitor.generate_html([addr], nbn_monitor.Snapshot.empty())
        assert "No status snapshot yet" in html
        assert 'class="card status-grey"' in html

    def test_generate_html_with_snapshot(self) -> None:
        """generate_html shows 'since' time when an outage entry has a period start."""
        from datetime import UTC, datetime

        since_iso = datetime(2025, 3, 15, 10, 30, tzinfo=UTC).isoformat()
        addr = nbn_monitor.Address(label="Home", loc_id="LOC000000000001", poll=True, notify=True)
        snapshot = v2_snapshot(("LOC000000000001", "UNPLANNED_INPROGRESS", since_iso))
        html = nbn_monitor.generate_html([addr], snapshot)
        assert "since" in html

    def test_warning_banner_appears(self) -> None:
        addr = nbn_monitor.Address(label="Home", loc_id="LOC000000000001")
        html = nbn_monitor.generate_html(
            [addr], nbn_monitor.Snapshot.empty(), warning="degraded state"
        )
        assert "degraded state" in html
        assert 'class="warning"' in html

    def test_refresh_is_relative_to_page_load_and_uses_simple_reload(self) -> None:
        addr = nbn_monitor.Address(label="Home", loc_id="LOC000000000001")
        html = nbn_monitor.generate_html([addr], nbn_monitor.Snapshot.empty())
        assert "refreshAt=Date.now()+60000" in html
        assert "location.reload()" in html
        assert "fetch(location.href)" not in html
        assert "DOMParser" not in html

    def test_since_tag_includes_date_when_outage_predates_today(self) -> None:
        """A multi-day-old outage's tag must surface the date, not just the time."""
        from datetime import UTC, datetime, timedelta

        from .conftest import v2_snapshot

        now = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)
        long_ago = (now - timedelta(days=17)).isoformat()
        addr = nbn_monitor.Address(label="Home", loc_id="LOC000000000001", poll=True, notify=True)
        snapshot = v2_snapshot(("LOC000000000001", "UNPLANNED_INPROGRESS", long_ago))
        html = nbn_monitor.generate_html([addr], snapshot, now=now)
        # Should include "X days ago" rather than only a time-of-day.
        assert "days ago" in html

    def test_since_tag_says_yesterday_for_one_day_old(self) -> None:
        from datetime import UTC, datetime, timedelta

        from .conftest import v2_snapshot

        now = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)
        yesterday = (now - timedelta(days=1)).isoformat()
        addr = nbn_monitor.Address(label="Home", loc_id="LOC000000000001", poll=True, notify=True)
        snapshot = v2_snapshot(("LOC000000000001", "UNPLANNED_INPROGRESS", yesterday))
        html = nbn_monitor.generate_html([addr], snapshot, now=now)
        assert "yesterday" in html

    def test_since_tag_says_weekday_for_few_days_old(self) -> None:
        from datetime import UTC, datetime, timedelta

        from .conftest import v2_snapshot

        now = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)
        a_few_days_ago = (now - timedelta(days=3)).isoformat()
        addr = nbn_monitor.Address(label="Home", loc_id="LOC000000000001", poll=True, notify=True)
        snapshot = v2_snapshot(("LOC000000000001", "UNPLANNED_INPROGRESS", a_few_days_ago))
        html = nbn_monitor.generate_html([addr], snapshot, now=now)
        # 3 days ago — should render with a weekday (Mon/Tue/Wed/...) not bare time.
        weekday_present = any(
            d in html for d in ("Mon ", "Tue ", "Wed ", "Thu ", "Fri ", "Sat ", "Sun ")
        )
        assert weekday_present

    def test_renders_all_address_planned_maintenance_in_expandable_details(self) -> None:
        from datetime import UTC, datetime

        events = list(nbn_monitor.parse_planned_maintenance(MULTI_WINDOW_MAINTENANCE).events)
        addr = nbn_monitor.Address(
            label="Neighbour",
            loc_id="LOC000000000002",
            notify=False,
            compare=True,
        )
        snapshot = v2_snapshot(
            ("LOC000000000002", "UNPLANNED_INPROGRESS", "2026-07-13T01:00:00+00:00")
        )
        entry = snapshot.entry(addr.loc_id)
        assert entry is not None
        entry.planned_maintenance = events

        html = nbn_monitor.generate_html(
            [addr],
            snapshot,
            now=datetime(2026, 7, 13, 2, 0, tzinfo=UTC),
        )

        assert "<details" in html
        assert "Planned maintenance" in html
        assert "8 interruptions" in html
        assert "Today from midnight" in html
        assert "Wed 22 Jul from 7am" in html
        assert "Estimated interruption 6h" in html
        assert "Work scheduled through Fri 24 Jul, 7pm" in html
        assert html.count("Work scheduled through") == 2
        assert "Unplanned" in html

    def test_planned_status_uses_schedule_instead_of_observed_since(self) -> None:
        from datetime import UTC, datetime

        events = list(nbn_monitor.parse_planned_maintenance(MULTI_WINDOW_MAINTENANCE).events)
        addr = nbn_monitor.Address(label="Home", loc_id="LOC000000000001")
        snapshot = v2_snapshot(("LOC000000000001", "PLANNED_NEARTERM", "2026-07-12T00:00:00+00:00"))
        entry = snapshot.entry(addr.loc_id)
        assert entry is not None
        entry.planned_maintenance = events

        html = nbn_monitor.generate_html(
            [addr],
            snapshot,
            now=datetime(2026, 7, 12, 1, 0, tzinfo=UTC),
        )

        assert "Planned upcoming" in html
        assert "Planned upcoming (since" not in html

    def test_css_wraps_labels_and_uses_wider_desktop_cards(self) -> None:
        addr = nbn_monitor.Address(
            label="A deliberately long address label that should remain visible",
            loc_id="LOC000000000001",
        )
        html = nbn_monitor.generate_html([addr], nbn_monitor.Snapshot.empty())

        assert "width:min(100%,560px)" in html
        assert "overflow-wrap:anywhere" in html
        assert "text-overflow:ellipsis" not in html

    def test_since_time_uses_address_timezone_not_process_timezone(self) -> None:
        from datetime import UTC, datetime

        addr = nbn_monitor.Address(label="Home", loc_id="LOC000000000001")
        snapshot = v2_snapshot(
            ("LOC000000000001", "UNPLANNED_INPROGRESS", "2026-07-13T00:00:00+00:00")
        )
        entry = snapshot.entry(addr.loc_id)
        assert entry is not None
        entry.time_zone = "Australia/Sydney"

        html = nbn_monitor.generate_html(
            [addr],
            snapshot,
            now=datetime(2026, 7, 13, 1, 0, tzinfo=UTC),
        )

        assert "since 10:00am" in html

    def test_service_issue_start_survives_display_status_masking(self) -> None:
        from datetime import UTC, datetime

        addr = nbn_monitor.Address(label="Home", loc_id="LOC000000000001")
        snapshot = v2_snapshot(
            ("LOC000000000001", "UNPLANNED_INPROGRESS", "2026-07-13T01:00:00+00:00")
        )
        entry = snapshot.entry(addr.loc_id)
        assert entry is not None
        entry.time_zone = "Australia/Sydney"
        entry.service_issue = nbn_monitor.Period(
            display_outage="UNPLANNED_INPROGRESS",
            started_at="2026-07-13T00:00:00+00:00",
            started_at_source="observed",
        )

        html = nbn_monitor.generate_html(
            [addr],
            snapshot,
            now=datetime(2026, 7, 13, 2, 0, tzinfo=UTC),
        )

        assert "since 10:00am" in html
        assert "since 11:00am" not in html
