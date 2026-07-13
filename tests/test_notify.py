"""Tests for ``nbn_monitor.notify``.

Covers the ntfy transport (``send_ntfy``), the change-detection lane
(``notify_changes``) that decides when to emit alerts, and the
``_format_duration`` helper used in resolved-outage messages.
"""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import niquests
import pytest

import nbn_monitor

from .conftest import MULTI_WINDOW_MAINTENANCE, TEST_NTFY, v2_snapshot


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
        raw: dict[str, object] | None = None,
        checked_at: float | None = None,
    ) -> tuple[nbn_monitor.Address, nbn_monitor.OutageStatus]:
        addr = nbn_monitor.Address(
            label=label, loc_id=loc_id, poll=True, notify=notify, compare=compare
        )
        status = nbn_monitor.OutageStatus(
            loc_id=loc_id,
            display_outage=display,
            label=nbn_monitor.OUTAGE_LABELS.get(display, display),
            raw=raw or {},
            checked_at=checked_at if checked_at is not None else time.time(),
        )
        return addr, status

    def _notify(
        self,
        results: list[tuple[nbn_monitor.Address, nbn_monitor.OutageStatus]],
        previous: nbn_monitor.Snapshot,
        *,
        previous_loaded: bool = True,
    ) -> tuple[nbn_monitor.ServiceDelivery, ...]:
        current = nbn_monitor.derive_snapshot(
            results,
            previous,
            started_at="2026-07-13T00:00:00+00:00",
            completed_at="2026-07-13T00:00:01+00:00",
        )
        return nbn_monitor.notify_changes(
            results,
            current,
            previous_loaded=previous_loaded,
            ntfy=TEST_NTFY,
        )

    def test_no_change_no_notification(self) -> None:
        results = [self._make_result("Home", "LOC000000000001", "NO_OUTAGE")]
        previous = v2_snapshot(("LOC000000000001", "NO_OUTAGE", ""))

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            self._notify(results, previous)
            mock_ntfy.assert_not_called()

    def test_outage_start_sends_alert(self) -> None:
        results = [self._make_result("Home", "LOC000000000001", "UNPLANNED_INPROGRESS")]
        previous = v2_snapshot(("LOC000000000001", "NO_OUTAGE", ""))

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            self._notify(results, previous)
            mock_ntfy.assert_called_once()
            assert mock_ntfy.call_args.kwargs["priority"] == "high"

    def test_outage_resolved_sends_resolved(self) -> None:
        results = [self._make_result("Home", "LOC000000000001", "NO_OUTAGE")]
        previous = v2_snapshot(("LOC000000000001", "UNPLANNED_INPROGRESS", ""))

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            self._notify(results, previous)
            mock_ntfy.assert_called_once()
            call_kw = mock_ntfy.call_args
            msg = call_kw.kwargs.get("message") or call_kw.args[1]
            assert "restored" in msg

    def test_first_run_outage_triggers_alert(self) -> None:
        results = [self._make_result("Home", "LOC000000000001", "UNPLANNED_INPROGRESS")]
        previous = nbn_monitor.Snapshot.empty()

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            self._notify(results, previous)
            mock_ntfy.assert_called_once()

    def test_non_notify_address_skipped(self) -> None:
        results = [
            self._make_result("Neighbour", "LOC000000000002", "UNPLANNED_INPROGRESS", notify=False)
        ]
        previous = v2_snapshot(("LOC000000000002", "NO_OUTAGE", ""))

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            self._notify(results, previous)
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
            self._notify(results, previous)
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
            self._notify(results, previous)
            call_kw = mock_ntfy.call_args
            msg = call_kw.kwargs.get("message") or call_kw.args[1]
            assert "localised" in msg
            assert "neighbour unaffected" in msg

    def test_compare_poll_error_suppresses_localisation_context(self) -> None:
        results = [
            self._make_result("Home", "LOC000000000001", "UNPLANNED_INPROGRESS"),
            (
                nbn_monitor.Address(
                    label="Neighbour",
                    loc_id="LOC000000000002",
                    notify=False,
                    compare=True,
                ),
                nbn_monitor.OutageStatus(
                    loc_id="LOC000000000002",
                    display_outage="",
                    label="Error",
                    error="timeout",
                    checked_at=time.time(),
                ),
            ),
        ]
        previous = v2_snapshot(
            ("LOC000000000001", "NO_OUTAGE", ""),
            ("LOC000000000002", "NO_OUTAGE", ""),
        )
        current = nbn_monitor.derive_snapshot(
            results,
            previous,
            started_at="2026-07-13T00:00:00+00:00",
            completed_at="2026-07-13T00:00:01+00:00",
        )

        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=True) as mock_ntfy:
            nbn_monitor.notify_changes(
                results,
                current,
                ntfy=TEST_NTFY,
            )

        message = mock_ntfy.call_args.kwargs["message"]
        assert "localised" not in message
        assert "area-wide" not in message
        assert "widespread" not in message

    def test_errored_nearby_address_suppresses_widespread_context(self) -> None:
        results = [
            self._make_result("Home", "LOC000000000001", "UNPLANNED_INPROGRESS"),
            self._make_result(
                "Neighbour",
                "LOC000000000002",
                "NO_OUTAGE",
                notify=False,
                compare=True,
            ),
            (
                nbn_monitor.Address(
                    label="Nearby",
                    loc_id="LOC000000000003",
                    notify=False,
                ),
                nbn_monitor.OutageStatus(
                    loc_id="LOC000000000003",
                    display_outage="",
                    label="Error",
                    error="timeout",
                    checked_at=time.time(),
                ),
            ),
        ]
        previous = v2_snapshot(
            ("LOC000000000001", "NO_OUTAGE", ""),
            ("LOC000000000002", "NO_OUTAGE", ""),
            (
                "LOC000000000003",
                "UNPLANNED_INPROGRESS",
                "2026-07-13T00:00:00+00:00",
            ),
        )
        current = nbn_monitor.derive_snapshot(
            results,
            previous,
            started_at="2026-07-13T01:00:00+00:00",
            completed_at="2026-07-13T01:00:01+00:00",
        )

        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=True) as mock_ntfy:
            nbn_monitor.notify_changes(
                results,
                current,
                ntfy=TEST_NTFY,
            )

        message = mock_ntfy.call_args.kwargs["message"]
        assert "widespread" not in message
        assert "localised" not in message
        assert "area-wide" not in message

    def test_no_localisation_hint_when_no_compare_address(self) -> None:
        """Without any compare address, we have no neighbour data — say nothing."""
        results = [self._make_result("Home", "LOC000000000001", "UNPLANNED_INPROGRESS")]
        previous = nbn_monitor.Snapshot.empty()

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            self._notify(results, previous)
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
            self._notify(results, previous)
            mock_ntfy.assert_called_once()
            call_kw = mock_ntfy.call_args
            assert call_kw.kwargs["priority"] == "default"
            assert "Degradation" in call_kw.kwargs["title"]
            assert call_kw.kwargs["tags"] == "warning"

    def test_planned_status_does_not_use_generic_transition_alert(self) -> None:
        results = [self._make_result("Home", "LOC000000000001", "PLANNED_INPROGRESS")]
        previous = v2_snapshot(("LOC000000000001", "NO_OUTAGE", ""))

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            self._notify(results, previous)
            mock_ntfy.assert_not_called()

    def test_planned_status_does_not_falsely_resolve_service_issue(self) -> None:
        results = [self._make_result("Home", "LOC000000000001", "PLANNED_INPROGRESS")]
        previous = v2_snapshot(
            ("LOC000000000001", "UNPLANNED_INPROGRESS", "2026-07-13T00:00:00+00:00")
        )

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            self._notify(results, previous)
            mock_ntfy.assert_not_called()

    def test_planned_masked_issue_resolves_on_confirmed_no_outage(self) -> None:
        previous = v2_snapshot(
            ("LOC000000000001", "PLANNED_INPROGRESS", "2026-07-13T01:00:00+00:00")
        )
        entry = previous.entry("LOC000000000001")
        assert entry is not None
        entry.service_issue = nbn_monitor.Period(
            display_outage="UNPLANNED_INPROGRESS",
            started_at="2026-07-13T00:00:00+00:00",
            started_at_source="observed",
        )
        entry.service_notifications = nbn_monitor.ServiceNotificationState(
            announced_issue=nbn_monitor.Period(
                display_outage="UNPLANNED_INPROGRESS",
                started_at="2026-07-13T00:00:00+00:00",
                started_at_source="observed",
            )
        )
        results = [self._make_result("Home", "LOC000000000001", "NO_OUTAGE")]

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            self._notify(results, previous)

        mock_ntfy.assert_called_once()
        assert "restored" in mock_ntfy.call_args.kwargs["message"]

    def test_planned_masked_issue_does_not_restart_when_unplanned_returns(self) -> None:
        previous = v2_snapshot(
            ("LOC000000000001", "PLANNED_INPROGRESS", "2026-07-13T01:00:00+00:00")
        )
        entry = previous.entry("LOC000000000001")
        assert entry is not None
        entry.service_issue = nbn_monitor.Period(
            display_outage="UNPLANNED_INPROGRESS",
            started_at="2026-07-13T00:00:00+00:00",
            started_at_source="observed",
        )
        entry.service_notifications = nbn_monitor.ServiceNotificationState(
            announced_issue=nbn_monitor.Period(
                display_outage="UNPLANNED_INPROGRESS",
                started_at="2026-07-13T00:00:00+00:00",
                started_at_source="observed",
            )
        )
        results = [self._make_result("Home", "LOC000000000001", "UNPLANNED_INPROGRESS")]

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            self._notify(results, previous)

        mock_ntfy.assert_not_called()

    def test_unplanned_outage_keeps_high_priority(self) -> None:
        results = [self._make_result("Home", "LOC000000000001", "UNPLANNED_INPROGRESS")]
        previous = v2_snapshot(("LOC000000000001", "NO_OUTAGE", ""))

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            self._notify(results, previous)
            call_kw = mock_ntfy.call_args
            assert call_kw.kwargs["priority"] == "high"
            assert call_kw.kwargs["tags"] == "rotating_light"
            assert "Outage" in call_kw.kwargs["title"]

    def test_outage_resolved_includes_duration(self) -> None:
        """When an outage resolves, the message includes 'after Xh Ym'."""
        two_hours_ago = (datetime.now(tz=UTC) - timedelta(hours=2)).isoformat()
        results = [self._make_result("Home", "LOC000000000001", "NO_OUTAGE")]
        previous = v2_snapshot(("LOC000000000001", "UNPLANNED_INPROGRESS", two_hours_ago))

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            self._notify(results, previous)
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
            self._notify([(addr, status)], previous)

        mock_ntfy.assert_not_called()

    def test_missing_previous_state_skips_notification_decisions(self) -> None:
        results = [self._make_result("Home", "LOC000000000001", "UNPLANNED_INPROGRESS")]

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes(
                results, nbn_monitor.Snapshot.empty(), previous_loaded=False, ntfy=TEST_NTFY
            )

        mock_ntfy.assert_not_called()

    def test_failed_service_start_retries_until_delivery_is_recorded(self) -> None:
        results = [self._make_result("Home", "LOC000000000001", "UNPLANNED_INPROGRESS")]
        previous = v2_snapshot(("LOC000000000001", "NO_OUTAGE", ""))
        current = nbn_monitor.derive_snapshot(
            results,
            previous,
            started_at="2026-07-13T00:00:00+00:00",
            completed_at="2026-07-13T00:00:01+00:00",
        )

        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=False):
            failed = nbn_monitor.notify_changes(
                results,
                current,
                ntfy=TEST_NTFY,
            )
        assert failed == ()

        next_snapshot = nbn_monitor.derive_snapshot(
            results,
            current,
            started_at="2026-07-13T00:05:00+00:00",
            completed_at="2026-07-13T00:05:01+00:00",
        )
        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=True):
            delivered = nbn_monitor.notify_changes(
                results,
                next_snapshot,
                ntfy=TEST_NTFY,
            )

        assert delivered
        nbn_monitor.apply_service_deliveries(next_snapshot, delivered)
        entry = next_snapshot.entry("LOC000000000001")
        assert entry is not None
        assert entry.service_notifications.announced_issue is not None
        assert entry.service_notifications.pending_starts == []

    def test_failed_service_resolution_retries(self) -> None:
        results = [self._make_result("Home", "LOC000000000001", "NO_OUTAGE")]
        previous = v2_snapshot(
            ("LOC000000000001", "UNPLANNED_INPROGRESS", "2026-07-13T00:00:00+00:00")
        )
        current = nbn_monitor.derive_snapshot(
            results,
            previous,
            started_at="2026-07-13T01:00:00+00:00",
            completed_at="2026-07-13T01:00:01+00:00",
        )

        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=False):
            failed = nbn_monitor.notify_changes(
                results,
                current,
                ntfy=TEST_NTFY,
            )
        assert failed == ()
        current_entry = current.entry("LOC000000000001")
        assert current_entry is not None
        assert current_entry.service_issue is None
        assert current_entry.service_notifications.announced_issue is not None
        assert current_entry.service_notifications.pending_resolutions

        next_snapshot = nbn_monitor.derive_snapshot(
            results,
            current,
            started_at="2026-07-13T01:05:00+00:00",
            completed_at="2026-07-13T01:05:01+00:00",
        )
        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=True):
            delivered = nbn_monitor.notify_changes(
                results,
                next_snapshot,
                ntfy=TEST_NTFY,
            )

        assert delivered
        nbn_monitor.apply_service_deliveries(next_snapshot, delivered)
        entry = next_snapshot.entry("LOC000000000001")
        assert entry is not None
        assert entry.service_notifications.announced_issue is None
        assert entry.service_notifications.pending_resolutions == []

    def test_failed_start_and_resolution_remain_independently_pending(self) -> None:
        started_results = [
            self._make_result(
                "Home",
                "LOC000000000001",
                "UNPLANNED_INPROGRESS",
                checked_at=datetime(2026, 7, 13, 0, 0, tzinfo=UTC).timestamp(),
            )
        ]
        previous = v2_snapshot(("LOC000000000001", "NO_OUTAGE", ""))
        started = nbn_monitor.derive_snapshot(
            started_results,
            previous,
            started_at="2026-07-13T00:00:00+00:00",
            completed_at="2026-07-13T00:00:01+00:00",
        )
        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=False):
            assert (
                nbn_monitor.notify_changes(
                    started_results,
                    started,
                    ntfy=TEST_NTFY,
                )
                == ()
            )

        healthy_results = [
            self._make_result(
                "Home",
                "LOC000000000001",
                "NO_OUTAGE",
                checked_at=datetime(2026, 7, 13, 0, 30, tzinfo=UTC).timestamp(),
            )
        ]
        resolved = nbn_monitor.derive_snapshot(
            healthy_results,
            started,
            started_at="2026-07-13T00:30:00+00:00",
            completed_at="2026-07-13T00:30:01+00:00",
        )
        resolved_entry = resolved.entry("LOC000000000001")
        assert resolved_entry is not None
        assert resolved_entry.service_notifications.pending_starts
        assert resolved_entry.service_notifications.pending_resolutions

        with patch.object(
            nbn_monitor.notify,
            "send_ntfy",
            side_effect=[False, True],
        ) as blocked_resolution:
            blocked = nbn_monitor.notify_changes(
                healthy_results,
                resolved,
                ntfy=TEST_NTFY,
            )
        assert blocked == ()
        assert blocked_resolution.call_count == 1

        with patch.object(
            nbn_monitor.notify,
            "send_ntfy",
            return_value=True,
        ) as mock_ntfy:
            deliveries = nbn_monitor.notify_changes(
                healthy_results,
                resolved,
                ntfy=TEST_NTFY,
            )

        assert [call.kwargs["title"] for call in mock_ntfy.call_args_list] == [
            "NBN Outage Alert",
            "NBN Outage Resolved",
        ]
        nbn_monitor.apply_service_deliveries(resolved, deliveries)
        final_entry = resolved.entry("LOC000000000001")
        assert final_entry is not None
        assert final_entry.service_notifications.pending_starts == []
        assert final_entry.service_notifications.pending_resolutions == []

    def test_failed_resolution_survives_a_new_service_issue(self) -> None:
        previous = v2_snapshot(
            ("LOC000000000001", "UNPLANNED_INPROGRESS", "2026-07-13T00:00:00+00:00")
        )
        healthy_results = [
            self._make_result(
                "Home",
                "LOC000000000001",
                "NO_OUTAGE",
                checked_at=datetime(2026, 7, 13, 1, 0, tzinfo=UTC).timestamp(),
            )
        ]
        healthy = nbn_monitor.derive_snapshot(
            healthy_results,
            previous,
            started_at="2026-07-13T01:00:00+00:00",
            completed_at="2026-07-13T01:00:01+00:00",
        )
        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=False):
            assert (
                nbn_monitor.notify_changes(
                    healthy_results,
                    healthy,
                    ntfy=TEST_NTFY,
                )
                == ()
            )

        new_results = [
            self._make_result(
                "Home",
                "LOC000000000001",
                "UNPLANNED_INPROGRESS",
                checked_at=datetime(2026, 7, 13, 2, 0, tzinfo=UTC).timestamp(),
            )
        ]
        new_issue = nbn_monitor.derive_snapshot(
            new_results,
            healthy,
            started_at="2026-07-13T02:00:00+00:00",
            completed_at="2026-07-13T02:00:01+00:00",
        )
        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=True):
            deliveries = nbn_monitor.notify_changes(
                new_results,
                new_issue,
                ntfy=TEST_NTFY,
            )

        nbn_monitor.apply_service_deliveries(new_issue, deliveries)
        entry = new_issue.entry("LOC000000000001")
        assert entry is not None
        assert entry.service_notifications.pending_starts == []
        assert entry.service_notifications.pending_resolutions == []
        assert entry.service_notifications.announced_issue is not None
        assert entry.service_notifications.announced_issue.started_at == (
            "2026-07-13T02:00:00+00:00"
        )

    def test_pending_unplanned_start_keeps_its_label_while_planned_masks_it(self) -> None:
        previous = v2_snapshot(("LOC000000000001", "NO_OUTAGE", ""))
        outage_results = [
            self._make_result(
                "Home",
                "LOC000000000001",
                "UNPLANNED_INPROGRESS",
                checked_at=datetime(2026, 7, 13, 0, 0, tzinfo=UTC).timestamp(),
            )
        ]
        outage = nbn_monitor.derive_snapshot(
            outage_results,
            previous,
            started_at="2026-07-13T00:00:00+00:00",
            completed_at="2026-07-13T00:00:01+00:00",
        )
        planned_results = [
            self._make_result(
                "Home",
                "LOC000000000001",
                "PLANNED_INPROGRESS",
                raw=MULTI_WINDOW_MAINTENANCE,
                checked_at=datetime(2026, 7, 13, 0, 30, tzinfo=UTC).timestamp(),
            )
        ]
        masked = nbn_monitor.derive_snapshot(
            planned_results,
            outage,
            started_at="2026-07-13T00:30:00+00:00",
            completed_at="2026-07-13T00:30:01+00:00",
        )

        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=True) as mock_ntfy:
            nbn_monitor.notify_changes(
                planned_results,
                masked,
                ntfy=TEST_NTFY,
            )

        assert mock_ntfy.call_args.kwargs["title"] == "NBN Outage Alert"
        assert "Home: Unplanned" in mock_ntfy.call_args.kwargs["message"]
        assert "Planned maintenance" not in mock_ntfy.call_args.kwargs["message"]

    def test_resolution_retry_keeps_first_observed_duration(self) -> None:
        previous = v2_snapshot(
            ("LOC000000000001", "UNPLANNED_INPROGRESS", "2026-07-13T00:00:00+00:00")
        )
        results = [
            self._make_result(
                "Home",
                "LOC000000000001",
                "NO_OUTAGE",
                checked_at=datetime(2026, 7, 13, 1, 0, tzinfo=UTC).timestamp(),
            )
        ]
        resolved = nbn_monitor.derive_snapshot(
            results,
            previous,
            started_at="2026-07-13T01:00:00+00:00",
            completed_at="2026-07-13T01:00:01+00:00",
        )
        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=False):
            nbn_monitor.notify_changes(
                results,
                resolved,
                ntfy=TEST_NTFY,
            )

        retry = nbn_monitor.derive_snapshot(
            results,
            resolved,
            started_at="2026-07-13T03:00:00+00:00",
            completed_at="2026-07-13T03:00:01+00:00",
        )
        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=True) as mock_ntfy:
            nbn_monitor.notify_changes(
                results,
                retry,
                ntfy=TEST_NTFY,
            )

        assert "after 1h" in mock_ntfy.call_args.kwargs["message"]

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
            self._notify(results, previous)
            # Only one outage alert per cycle, even with multiple affected addresses
            mock_ntfy.assert_called_once()
            call_kw = mock_ntfy.call_args
            msg = call_kw.kwargs.get("message") or call_kw.args[1]
            assert "area-wide" in msg


class TestNotifyPlannedMaintenance:
    def setup_method(self) -> None:
        self.events = list(nbn_monitor.parse_planned_maintenance(MULTI_WINDOW_MAINTENANCE).events)
        self.home = nbn_monitor.Address(
            label="Home",
            loc_id="LOC000000000001",
            poll=True,
            notify=True,
        )
        self.result = (
            self.home,
            nbn_monitor.OutageStatus(
                loc_id=self.home.loc_id,
                display_outage="UNPLANNED_INPROGRESS",
                label="Unplanned",
                raw=MULTI_WINDOW_MAINTENANCE,
                checked_at=time.time(),
            ),
        )

    def _snapshot(
        self,
        events: list[nbn_monitor.PlannedMaintenance],
        *,
        announced: list[nbn_monitor.PlannedMaintenance] | None = None,
        day_before_sent: list[str] | None = None,
        hour_before_sent: list[str] | None = None,
    ) -> nbn_monitor.Snapshot:
        return nbn_monitor.Snapshot(
            addresses={
                self.home.loc_id: nbn_monitor.AddressEntry(
                    label=self.home.label,
                    planned_maintenance=events,
                    planned_notifications=nbn_monitor.PlannedNotificationState(
                        announced_schedule=announced or [],
                        day_before_sent=day_before_sent or [],
                        hour_before_sent=hour_before_sent or [],
                    ),
                )
            }
        )

    def test_initial_schedule_sends_once_and_records_announcement(self) -> None:
        snapshot = self._snapshot(self.events)
        now = datetime(2026, 7, 12, 0, 0, tzinfo=UTC)

        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=True) as mock_ntfy:
            deliveries = nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=now,
            )

        assert mock_ntfy.call_args.kwargs["title"] == "NBN Planned Maintenance Added"
        assert "Home" in mock_ntfy.call_args.kwargs["message"]
        assert "Mon 13 Jul" in mock_ntfy.call_args.kwargs["message"]
        assert len(deliveries) == 1

        nbn_monitor.apply_planned_deliveries(snapshot, deliveries)
        entry = snapshot.entry(self.home.loc_id)
        assert entry is not None
        assert entry.planned_notifications.announced_schedule == self.events

        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_repeat:
            repeated = nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=now,
            )
        assert repeated == ()
        mock_repeat.assert_not_called()

    def test_material_change_sends_updated_schedule(self) -> None:
        changed = [replace(self.events[0], duration_minutes=361), *self.events[1:]]
        snapshot = self._snapshot(changed, announced=self.events)

        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=True) as mock_ntfy:
            deliveries = nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=datetime(2026, 7, 12, 0, 0, tzinfo=UTC),
            )

        assert deliveries
        assert mock_ntfy.call_args.kwargs["title"] == "NBN Planned Maintenance Updated"
        assert "Changed:" in mock_ntfy.call_args.kwargs["message"]

    def test_future_removal_sends_cancellation(self) -> None:
        snapshot = self._snapshot([], announced=[self.events[0]])

        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=True) as mock_ntfy:
            deliveries = nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=datetime(2026, 7, 12, 0, 0, tzinfo=UTC),
            )

        assert deliveries
        assert mock_ntfy.call_args.kwargs["title"] == "NBN Planned Maintenance Cancelled"
        assert "Cancelled:" in mock_ntfy.call_args.kwargs["message"]

    def test_day_before_reminder_is_recorded_and_not_repeated(self) -> None:
        event = self.events[1]
        snapshot = self._snapshot([event], announced=[event])
        now = datetime(2026, 7, 12, 23, 0, tzinfo=UTC)

        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=True) as mock_ntfy:
            deliveries = nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=now,
            )

        assert mock_ntfy.call_args.kwargs["title"] == "NBN Maintenance Tomorrow"
        assert deliveries[0].day_before_revisions
        assert not deliveries[0].hour_before_revisions

        nbn_monitor.apply_planned_deliveries(snapshot, deliveries)
        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_repeat:
            repeated = nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=now + timedelta(minutes=5),
            )
        assert repeated == ()
        mock_repeat.assert_not_called()

    def test_hour_before_consolidates_overdue_day_reminder(self) -> None:
        event = self.events[1]
        snapshot = self._snapshot([event], announced=[event])

        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=True) as mock_ntfy:
            deliveries = nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=datetime(2026, 7, 13, 13, 0, tzinfo=UTC),
            )

        assert mock_ntfy.call_args.kwargs["title"] == "NBN Maintenance Starting Soon"
        assert "Expected to begin soon:" in mock_ntfy.call_args.kwargs["message"]
        assert "Reminder for tomorrow:" not in mock_ntfy.call_args.kwargs["message"]
        assert deliveries[0].day_before_revisions
        assert deliveries[0].hour_before_revisions

    def test_day_before_catch_up_on_event_day_says_today(self) -> None:
        event = self.events[3]
        snapshot = self._snapshot([event], announced=[event])

        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=True) as mock_ntfy:
            nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=datetime(2026, 7, 15, 16, 0, tzinfo=UTC),
            )

        assert mock_ntfy.call_args.kwargs["title"] == "NBN Maintenance Later Today"
        assert "Reminder for today:" in mock_ntfy.call_args.kwargs["message"]

    def test_end_time_only_change_describes_old_and_new_windows(self) -> None:
        original = self.events[0]
        changed = replace(
            original,
            maintenance_ends_at="2026-07-18T09:00:00+00:00",
        )
        snapshot = self._snapshot([changed], announced=[original])

        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=True) as mock_ntfy:
            nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=datetime(2026, 7, 12, 0, 0, tzinfo=UTC),
            )

        message = mock_ntfy.call_args.kwargs["message"]
        assert "work scheduled through Fri 17 Jul, 7pm" in message
        assert "work scheduled through Sat 18 Jul, 7pm" in message

    def test_failed_send_retries_without_advancing_state(self) -> None:
        snapshot = self._snapshot(self.events)
        now = datetime(2026, 7, 12, 0, 0, tzinfo=UTC)

        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=False):
            failed = nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=now,
            )
        assert failed == ()

        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=True) as retry:
            succeeded = nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=now,
            )
        assert succeeded
        retry.assert_called_once()

    def test_failed_schedule_change_retries_after_original_start(self) -> None:
        announced = self.events[1]
        changed = replace(
            announced,
            starts_at="2026-07-13T13:00:00+00:00",
        )
        snapshot = self._snapshot([changed], announced=[announced])

        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=False):
            failed = nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=datetime(2026, 7, 13, 13, 30, tzinfo=UTC),
            )
        assert failed == ()
        entry = snapshot.entry(self.home.loc_id)
        assert entry is not None
        assert entry.planned_notifications.pending_schedule == [changed]

        refreshed = replace(changed, duration_minutes=90)
        entry.planned_maintenance = [refreshed]
        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=False):
            nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=datetime(2026, 7, 13, 13, 40, tzinfo=UTC),
            )
        assert entry.planned_notifications.pending_schedule == [refreshed]

        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=True) as retry:
            delivered = nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=datetime(2026, 7, 13, 14, 1, tzinfo=UTC),
            )

        assert delivered
        assert retry.call_args.kwargs["title"] == "NBN Planned Maintenance Updated"
        nbn_monitor.apply_planned_deliveries(snapshot, delivered)
        assert entry.planned_notifications.pending_schedule is None
        assert entry.planned_notifications.announced_schedule == [refreshed]

    def test_pending_target_preserves_expired_announced_events(self) -> None:
        expired = self.events[0]
        announced_future = self.events[1]
        changed_future = replace(announced_future, duration_minutes=91)
        snapshot = self._snapshot(
            [changed_future],
            announced=[expired, announced_future],
        )

        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=True) as mock_ntfy:
            delivered = nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=datetime(2026, 7, 12, 15, 0, tzinfo=UTC),
            )

        message = mock_ntfy.call_args.kwargs["message"]
        assert "Changed:" in message
        assert "Cancelled:" not in message
        assert delivered
        assert delivered[0].announced_schedule == (expired, changed_future)

    def test_started_pending_reschedule_survives_api_pruning(self) -> None:
        announced = self.events[1]
        changed = replace(
            announced,
            starts_at="2026-07-13T13:00:00+00:00",
        )
        snapshot = self._snapshot([changed], announced=[announced])

        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=False):
            nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=datetime(2026, 7, 13, 13, 30, tzinfo=UTC),
            )
        entry = snapshot.entry(self.home.loc_id)
        assert entry is not None
        assert entry.planned_notifications.pending_schedule == [changed]

        entry.planned_maintenance = []
        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=False):
            nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=datetime(2026, 7, 13, 13, 40, tzinfo=UTC),
            )
        assert entry.planned_notifications.pending_schedule == [changed]

        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=True) as retry:
            delivered = nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=datetime(2026, 7, 13, 14, 1, tzinfo=UTC),
            )

        assert delivered
        assert "Changed:" in retry.call_args.kwargs["message"]
        assert "Cancelled:" not in retry.call_args.kwargs["message"]

    def test_started_pending_addition_survives_api_pruning(self) -> None:
        added = replace(
            self.events[1],
            starts_at="2026-07-13T13:00:00+00:00",
        )
        snapshot = self._snapshot([added], announced=[])

        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=False):
            nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=datetime(2026, 7, 13, 12, 30, tzinfo=UTC),
            )
        entry = snapshot.entry(self.home.loc_id)
        assert entry is not None
        assert entry.planned_notifications.pending_schedule == [added]

        entry.planned_maintenance = []
        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=False):
            nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=datetime(2026, 7, 13, 13, 30, tzinfo=UTC),
            )

        assert entry.planned_notifications.pending_schedule == [added]

    def test_started_pending_addition_survives_same_day_sibling(self) -> None:
        announced = replace(
            self.events[0],
            event_key="2026-07-13",
            starts_at="2026-07-13T08:00:00+00:00",
            duration_minutes=60,
        )
        added = replace(
            self.events[0],
            event_key="2026-07-13",
            starts_at="2026-07-13T00:00:00+00:00",
            duration_minutes=30,
        )
        snapshot = self._snapshot(
            [added, announced],
            announced=[announced],
        )

        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=False):
            nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=datetime(2026, 7, 12, 23, 0, tzinfo=UTC),
            )
        entry = snapshot.entry(self.home.loc_id)
        assert entry is not None

        entry.planned_maintenance = [announced]
        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=True) as retry:
            delivered = nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=datetime(2026, 7, 13, 1, 0, tzinfo=UTC),
            )

        assert delivered
        assert "Added:" in retry.call_args.kwargs["message"]
        assert "from 10am" in retry.call_args.kwargs["message"]

    def test_withdrawn_same_start_addition_does_not_reuse_announced_sibling(
        self,
    ) -> None:
        announced = replace(
            self.events[1],
            duration_minutes=60,
        )
        addition = replace(
            announced,
            duration_minutes=30,
        )
        snapshot = self._snapshot(
            [announced, addition],
            announced=[announced],
        )

        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=False):
            nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=datetime(2026, 7, 12, 0, 0, tzinfo=UTC),
            )
        entry = snapshot.entry(self.home.loc_id)
        assert entry is not None
        assert entry.planned_notifications.pending_schedule is not None

        entry.planned_maintenance = [announced]
        with patch.object(nbn_monitor.notify, "send_ntfy") as retry:
            delivered = nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=datetime(2026, 7, 12, 0, 5, tzinfo=UTC),
            )

        assert delivered == ()
        retry.assert_not_called()
        assert entry.planned_notifications.pending_schedule is None

    def test_withdrawn_future_addition_is_not_carried_with_unrelated_change(self) -> None:
        announced = self.events[1]
        addition = self.events[2]
        snapshot = self._snapshot(
            [announced, addition],
            announced=[announced],
        )

        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=False):
            nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=datetime(2026, 7, 12, 0, 0, tzinfo=UTC),
            )
        entry = snapshot.entry(self.home.loc_id)
        assert entry is not None

        changed = replace(announced, duration_minutes=91)
        entry.planned_maintenance = [changed]
        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=True) as retry:
            delivered = nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                ntfy=TEST_NTFY,
                now=datetime(2026, 7, 12, 0, 5, tzinfo=UTC),
            )

        assert delivered
        message = retry.call_args.kwargs["message"]
        assert "Changed:" in message
        assert "Wed 15 Jul" not in message

    def test_non_notify_addresses_are_visible_but_do_not_alert(self) -> None:
        neighbour = nbn_monitor.Address(
            label="Neighbour",
            loc_id="LOC000000000002",
            poll=True,
            notify=False,
            compare=True,
        )
        neighbour_result = (
            neighbour,
            nbn_monitor.OutageStatus(
                loc_id=neighbour.loc_id,
                display_outage="NO_OUTAGE",
                label="No outage",
                checked_at=time.time(),
            ),
        )
        snapshot = self._snapshot(self.events)
        snapshot.addresses[neighbour.loc_id] = nbn_monitor.AddressEntry(
            label=neighbour.label,
            planned_maintenance=self.events,
        )

        with patch.object(nbn_monitor.notify, "send_ntfy", return_value=True) as mock_ntfy:
            deliveries = nbn_monitor.notify_planned_maintenance(
                [self.result, neighbour_result],
                snapshot,
                ntfy=TEST_NTFY,
                now=datetime(2026, 7, 12, 0, 0, tzinfo=UTC),
            )

        assert len(deliveries) == 1
        message = mock_ntfy.call_args.kwargs["message"]
        assert "Home" in message
        assert "Neighbour" not in message

    def test_failed_or_missing_previous_state_skips_decisions(self) -> None:
        snapshot = self._snapshot(self.events)
        with patch.object(nbn_monitor.notify, "send_ntfy") as mock_ntfy:
            deliveries = nbn_monitor.notify_planned_maintenance(
                [self.result],
                snapshot,
                previous_loaded=False,
                ntfy=TEST_NTFY,
                now=datetime(2026, 7, 12, 0, 0, tzinfo=UTC),
            )
        assert deliveries == ()
        mock_ntfy.assert_not_called()


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
