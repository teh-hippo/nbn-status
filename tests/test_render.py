"""Tests for ``nbn_monitor.render``.

Covers the HTML status-page renderer that turns the authoritative snapshot
plus the configured address list into the user-facing traffic-light page.
"""

from __future__ import annotations

import nbn_monitor

from .conftest import v2_snapshot


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
        assert "#ef4444" in html  # red
        assert "refreshing in" in html
        assert 'class="tag"' in html

    def test_missing_snapshot_entry_renders_grey(self) -> None:
        """An address without a snapshot entry renders as grey with a no-data tag."""
        addr = nbn_monitor.Address(label="Broken", loc_id="LOC000000000099")
        html = nbn_monitor.generate_html([addr], nbn_monitor.Snapshot.empty())
        assert "No status snapshot yet" in html
        assert "#9ca3af" in html  # grey light

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
