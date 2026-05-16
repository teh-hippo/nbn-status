"""Tests for ``nbn_monitor.notify``.

Covers the ntfy transport (``send_ntfy``), the change-detection lane
(``notify_changes``) that decides when to emit alerts, and the
``_format_duration`` helper used in resolved-outage messages.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import niquests
import pytest

import nbn_monitor

from .conftest import TEST_NTFY, v2_snapshot


class TestSendNtfy:
    def test_no_topic_returns_false(self) -> None:
        ntfy = nbn_monitor.NtfyConfig(server="https://ntfy.sh", topic="", status_page_url="")
        assert nbn_monitor.send_ntfy(ntfy, "title", "msg") is False

    def test_sends_notification(self) -> None:
        ntfy = nbn_monitor.NtfyConfig(
            server="https://ntfy.sh",
            topic="test-topic",
            status_page_url="https://example.com/status",
        )
        with patch.object(nbn_monitor.notify.niquests, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp

            result = nbn_monitor.send_ntfy(ntfy, "title", "msg", priority="high", tags="warning")
            assert result is True

            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args
            assert call_kwargs.args[0] == "https://ntfy.sh/test-topic"
            headers = call_kwargs.kwargs["headers"]
            assert headers["Title"] == "title"
            assert headers["Priority"] == "high"
            assert "Actions" in headers

    def test_handles_request_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        ntfy = nbn_monitor.NtfyConfig(
            server="https://ntfy.sh", topic="test-topic", status_page_url=""
        )
        with patch.object(nbn_monitor.notify.niquests, "post") as mock_post:
            mock_post.side_effect = niquests.RequestException("fail")
            assert nbn_monitor.send_ntfy(ntfy, "title", "msg") is False

        assert "fail" in capsys.readouterr().err

    def test_handles_request_error_scrubs_topic(self, capsys: pytest.CaptureFixture[str]) -> None:
        ntfy = nbn_monitor.NtfyConfig(
            server="https://ntfy.sh", topic="secret-topic", status_page_url=""
        )
        with patch.object(nbn_monitor.notify.niquests, "post") as mock_post:
            mock_post.side_effect = niquests.RequestException(
                "POST https://ntfy.sh/secret-topic failed"
            )
            assert nbn_monitor.send_ntfy(ntfy, "title", "msg") is False

        stderr = capsys.readouterr().err
        assert "[url]" in stderr
        assert "secret-topic" not in stderr


class TestNotifyChanges:
    def _make_result(
        self,
        label: str,
        loc_id: str,
        display: str,
        *,
        notify: bool = True,
        compare: bool = False,
    ) -> tuple[nbn_monitor.Address, nbn_monitor.OutageStatus]:
        addr = nbn_monitor.Address(
            label=label, loc_id=loc_id, poll=True, notify=notify, compare=compare
        )
        status = nbn_monitor.OutageStatus(
            loc_id=loc_id,
            display_outage=display,
            label=nbn_monitor.OUTAGE_LABELS.get(display, display),
            checked_at=time.time(),
        )
        return addr, status

    def test_no_change_no_notification(self) -> None:
        results = [self._make_result("Home", "LOC000000000001", "NO_OUTAGE")]
        previous = v2_snapshot(("LOC000000000001", "NO_OUTAGE", ""))

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes(results, previous, ntfy=TEST_NTFY)
            mock_ntfy.assert_not_called()

    def test_outage_start_sends_alert(self) -> None:
        results = [self._make_result("Home", "LOC000000000001", "UNPLANNED_INPROGRESS")]
        previous = v2_snapshot(("LOC000000000001", "NO_OUTAGE", ""))

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes(results, previous, ntfy=TEST_NTFY)
            mock_ntfy.assert_called_once()
            assert mock_ntfy.call_args.kwargs["priority"] == "high"

    def test_outage_resolved_sends_resolved(self) -> None:
        results = [self._make_result("Home", "LOC000000000001", "NO_OUTAGE")]
        previous = v2_snapshot(("LOC000000000001", "UNPLANNED_INPROGRESS", ""))

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes(results, previous, ntfy=TEST_NTFY)
            mock_ntfy.assert_called_once()
            call_kw = mock_ntfy.call_args
            msg = call_kw.kwargs.get("message") or call_kw.args[1]
            assert "restored" in msg

    def test_first_run_outage_triggers_alert(self) -> None:
        results = [self._make_result("Home", "LOC000000000001", "UNPLANNED_INPROGRESS")]
        previous = nbn_monitor.Snapshot.empty()

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes(results, previous, ntfy=TEST_NTFY)
            mock_ntfy.assert_called_once()

    def test_non_notify_address_skipped(self) -> None:
        results = [
            self._make_result("Neighbour", "LOC000000000002", "UNPLANNED_INPROGRESS", notify=False)
        ]
        previous = v2_snapshot(("LOC000000000002", "NO_OUTAGE", ""))

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes(results, previous, ntfy=TEST_NTFY)
            mock_ntfy.assert_not_called()

    def test_compare_address_provides_context(self) -> None:
        results = [
            self._make_result("Home", "LOC000000000001", "UNPLANNED_INPROGRESS"),
            self._make_result(
                "Neighbour",
                "LOC000000000002",
                "UNPLANNED_INPROGRESS",
                notify=False,
                compare=True,
            ),
        ]
        previous = nbn_monitor.Snapshot.empty()

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes(results, previous, ntfy=TEST_NTFY)
            call_kw = mock_ntfy.call_args
            msg = call_kw.kwargs.get("message") or call_kw.args[1]
            assert "area-wide" in msg

    def _localised_test(self) -> tuple[nbn_monitor.Address, nbn_monitor.OutageStatus]:
        return self._make_result("Home", "LOC000000000001", "UNPLANNED_INPROGRESS")

    def test_compare_localised(self) -> None:
        results = [
            self._make_result("Home", "LOC000000000001", "UNPLANNED_INPROGRESS"),
            self._make_result(
                "Neighbour", "LOC000000000002", "NO_OUTAGE", notify=False, compare=True
            ),
        ]
        previous = nbn_monitor.Snapshot.empty()

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes(results, previous, ntfy=TEST_NTFY)
            call_kw = mock_ntfy.call_args
            msg = call_kw.kwargs.get("message") or call_kw.args[1]
            assert "localised" in msg
            assert "neighbour unaffected" in msg

    def test_no_localisation_hint_when_no_compare_address(self) -> None:
        """Without any compare address, we have no neighbour data — say nothing."""
        results = [self._make_result("Home", "LOC000000000001", "UNPLANNED_INPROGRESS")]
        previous = nbn_monitor.Snapshot.empty()

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes(results, previous, ntfy=TEST_NTFY)
            call_kw = mock_ntfy.call_args
            msg = call_kw.kwargs.get("message") or call_kw.args[1]
            assert "localised" not in msg
            assert "may be" not in msg
            assert "area-wide" not in msg

    def test_degradation_uses_default_priority(self) -> None:
        """A new degradation event is not promoted to a high-priority outage alert."""
        results = [self._make_result("Home", "LOC000000000001", "DEGRADATION_INPROGRESS")]
        previous = v2_snapshot(("LOC000000000001", "NO_OUTAGE", ""))

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes(results, previous, ntfy=TEST_NTFY)
            mock_ntfy.assert_called_once()
            call_kw = mock_ntfy.call_args
            assert call_kw.kwargs["priority"] == "default"
            assert "Degradation" in call_kw.kwargs["title"]
            assert call_kw.kwargs["tags"] == "warning"

    def test_planned_uses_default_priority(self) -> None:
        """A new planned-maintenance event is not promoted to a high-priority outage alert."""
        results = [self._make_result("Home", "LOC000000000001", "PLANNED_INPROGRESS")]
        previous = v2_snapshot(("LOC000000000001", "NO_OUTAGE", ""))

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes(results, previous, ntfy=TEST_NTFY)
            mock_ntfy.assert_called_once()
            call_kw = mock_ntfy.call_args
            assert call_kw.kwargs["priority"] == "default"
            assert "Maintenance" in call_kw.kwargs["title"]
            assert call_kw.kwargs["tags"] == "construction"

    def test_unplanned_outage_keeps_high_priority(self) -> None:
        results = [self._make_result("Home", "LOC000000000001", "UNPLANNED_INPROGRESS")]
        previous = v2_snapshot(("LOC000000000001", "NO_OUTAGE", ""))

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes(results, previous, ntfy=TEST_NTFY)
            call_kw = mock_ntfy.call_args
            assert call_kw.kwargs["priority"] == "high"
            assert call_kw.kwargs["tags"] == "rotating_light"
            assert "Outage" in call_kw.kwargs["title"]

    def test_outage_resolved_includes_duration(self) -> None:
        """When an outage resolves, the message includes 'after Xh Ym'."""
        from datetime import UTC, datetime, timedelta

        two_hours_ago = (datetime.now(tz=UTC) - timedelta(hours=2)).isoformat()
        results = [self._make_result("Home", "LOC000000000001", "NO_OUTAGE")]
        previous = v2_snapshot(("LOC000000000001", "UNPLANNED_INPROGRESS", two_hours_ago))

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes(results, previous, ntfy=TEST_NTFY)
            mock_ntfy.assert_called_once()
            call_kw = mock_ntfy.call_args
            msg = call_kw.kwargs.get("message") or call_kw.args[1]
            assert "after 2h" in msg

    def test_poll_error_does_not_resolve_existing_outage(self) -> None:
        """A transient poll failure is not a successful service-restored sample."""
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

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes([(addr, status)], previous, ntfy=TEST_NTFY)

        mock_ntfy.assert_not_called()

    def test_missing_previous_state_skips_notification_decisions(self) -> None:
        results = [self._make_result("Home", "LOC000000000001", "UNPLANNED_INPROGRESS")]

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes(
                results, nbn_monitor.Snapshot.empty(), previous_loaded=False, ntfy=TEST_NTFY
            )

        mock_ntfy.assert_not_called()

    def test_notify_changes_area_wide(self) -> None:
        """Batch notification: ntfy called once per cycle with area-wide context."""
        results = [
            self._make_result("Home", "LOC000000000001", "UNPLANNED_INPROGRESS"),
            self._make_result(
                "Neighbour",
                "LOC000000000002",
                "UNPLANNED_INPROGRESS",
                notify=False,
                compare=True,
            ),
            self._make_result("Family", "LOC000000000003", "UNPLANNED_INPROGRESS", notify=False),
        ]
        previous = nbn_monitor.Snapshot.empty()

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes(results, previous, ntfy=TEST_NTFY)
            # Only one outage alert per cycle, even with multiple affected addresses
            mock_ntfy.assert_called_once()
            call_kw = mock_ntfy.call_args
            msg = call_kw.kwargs.get("message") or call_kw.args[1]
            assert "area-wide" in msg


class TestFormatDuration:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (60, "1m"),
            (3600, "1h"),
            (8100, "2h 15m"),
            (45 * 60, "45m"),
            (0, "0m"),
            (24 * 3600, "1d"),
            (24 * 3600 + 3600, "1d 1h"),
            (3 * 24 * 3600 + 7 * 3600, "3d 7h"),
        ],
    )
    def test_format_duration(self, seconds: float, expected: str) -> None:
        assert nbn_monitor.notify._format_duration(seconds) == expected
