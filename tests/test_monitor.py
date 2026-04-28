"""Tests for nbn_monitor."""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

import nbn_monitor

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


@pytest.fixture()
def addresses() -> list[nbn_monitor.Address]:
    with patch.dict(os.environ, {"NBN_ADDRESSES": SAMPLE_ADDRESSES_JSON}):
        return nbn_monitor.load_addresses()


@pytest.fixture()
def state_file(tmp_path: Path) -> Path:
    return tmp_path / "state.json"


# ---------------------------------------------------------------------------
# load_addresses
# ---------------------------------------------------------------------------


class TestLoadAddresses:
    def test_loads_from_env(self) -> None:
        with patch.dict(os.environ, {"NBN_ADDRESSES": SAMPLE_ADDRESSES_JSON}):
            addrs = nbn_monitor.load_addresses()
        assert len(addrs) == 3
        assert addrs[0].label == "Home"
        assert addrs[0].poll is True
        assert addrs[0].notify is True
        assert addrs[1].compare is True
        assert addrs[2].poll is False

    def test_missing_env_exits(self) -> None:
        with patch.dict(os.environ, {}, clear=True), pytest.raises(SystemExit):
            nbn_monitor.load_addresses()


# ---------------------------------------------------------------------------
# check_outage
# ---------------------------------------------------------------------------


class TestCheckOutage:
    def test_no_outage(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MAINTENANCE_OK
        mock_resp.raise_for_status = MagicMock()

        session = MagicMock()
        session.get.return_value = mock_resp

        result = nbn_monitor.check_outage("LOC000000000001", session=session)
        assert result.display_outage == "NO_OUTAGE"
        assert result.colour == "green"
        assert result.is_outage is False
        assert result.error is None

    def test_unplanned_outage(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MAINTENANCE_OUTAGE
        mock_resp.raise_for_status = MagicMock()

        session = MagicMock()
        session.get.return_value = mock_resp

        result = nbn_monitor.check_outage("LOC000000000001", session=session)
        assert result.display_outage == "UNPLANNED_INPROGRESS"
        assert result.colour == "red"
        assert result.is_outage is True
        assert result.label == "Unplanned"

    def test_planned_maintenance(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MAINTENANCE_PLANNED
        mock_resp.raise_for_status = MagicMock()

        session = MagicMock()
        session.get.return_value = mock_resp

        result = nbn_monitor.check_outage("LOC000000000001", session=session)
        assert result.display_outage == "PLANNED_INPROGRESS"
        assert result.colour == "amber"
        assert result.is_outage is True

    def test_404_not_connected(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        session = MagicMock()
        session.get.return_value = mock_resp

        result = nbn_monitor.check_outage("LOC000000000001", session=session)
        assert result.error == "Not connected to NBN"
        assert result.colour == "grey"

    def test_request_exception(self) -> None:
        session = MagicMock()
        session.get.side_effect = nbn_monitor.niquests.RequestException("timeout")

        result = nbn_monitor.check_outage("LOC000000000001", session=session)
        assert result.error is not None
        assert "timeout" in result.error
        assert result.colour == "grey"

    def test_request_exception_scrubs_sensitive_url(self) -> None:
        session = MagicMock()
        session.get.side_effect = nbn_monitor.niquests.RequestException(
            "500 Server Error for url: "
            "https://places.nbnco.net.au/places/v1/maintenance?locationId=LOCSECRET123"
        )

        result = nbn_monitor.check_outage("LOC000000000001", session=session)
        assert result.error is not None
        assert "[url]" in result.error
        assert "https://" not in result.error
        assert "LOCSECRET123" not in result.error

    def test_creates_own_session_when_none(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MAINTENANCE_OK
        mock_resp.raise_for_status = MagicMock()

        with patch.object(nbn_monitor.niquests, "Session") as mock_session_cls:
            instance = MagicMock()
            instance.get.return_value = mock_resp
            mock_session_cls.return_value = instance

            result = nbn_monitor.check_outage("LOC000000000001")
            assert result.display_outage == "NO_OUTAGE"
            instance.close.assert_called_once()


# ---------------------------------------------------------------------------
# check_all
# ---------------------------------------------------------------------------


class TestCheckAll:
    def test_checks_all_addresses(self, addresses: list[nbn_monitor.Address]) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MAINTENANCE_OK
        mock_resp.raise_for_status = MagicMock()

        with patch.object(nbn_monitor.niquests, "Session") as mock_cls:
            instance = MagicMock()
            instance.get.return_value = mock_resp
            instance.__enter__ = MagicMock(return_value=instance)
            instance.__exit__ = MagicMock(return_value=False)
            mock_cls.return_value = instance

            results = nbn_monitor.check_all(addresses)
            assert len(results) == 3
            assert instance.get.call_count == 3


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------


class TestState:
    def test_load_empty(self, state_file: Path) -> None:
        with patch.object(nbn_monitor, "STATE_FILE", state_file):
            result = nbn_monitor.load_state_result()
            assert result.status == "missing"
            assert nbn_monitor.load_state() == {
                "schema_version": 2,
                "generated_at": "",
                "poll": {},
                "addresses": {},
            }

    def test_save_and_load(self, state_file: Path) -> None:
        state = {
            "LOC000000000001": {
                "status": "NO_OUTAGE",
                "since": "",
                "last_checked": "2025-01-01T00:00:00+00:00",
            },
            "LOC000000000002": {
                "status": "UNPLANNED_INPROGRESS",
                "since": "2025-01-01T00:00:00+00:00",
                "last_checked": "2025-01-01T00:00:00+00:00",
            },
        }
        with patch.object(nbn_monitor, "STATE_FILE", state_file):
            nbn_monitor.save_state(state)
            loaded = nbn_monitor.load_state()
            assert loaded["schema_version"] == 2
            assert (
                loaded["addresses"]["LOC000000000001"]["last_success"]["display_outage"]
                == "NO_OUTAGE"
            )
            assert (
                loaded["addresses"]["LOC000000000002"]["last_success"]["display_outage"]
                == "UNPLANNED_INPROGRESS"
            )
            assert (
                loaded["addresses"]["LOC000000000002"]["current_period"]["started_at"]
                == "2025-01-01T00:00:00+00:00"
            )

    def test_state_backward_compat(self, state_file: Path) -> None:
        """Old-format state (plain string values) is migrated on load."""
        old_state = {"LOC000000000001": "NO_OUTAGE", "LOC000000000002": "UNPLANNED_INPROGRESS"}
        state_file.write_text(json.dumps(old_state))
        with patch.object(nbn_monitor, "STATE_FILE", state_file):
            loaded = nbn_monitor.load_state()
            assert loaded["schema_version"] == 2
            assert (
                loaded["addresses"]["LOC000000000001"]["last_success"]["display_outage"]
                == "NO_OUTAGE"
            )
            assert (
                loaded["addresses"]["LOC000000000002"]["last_success"]["display_outage"]
                == "UNPLANNED_INPROGRESS"
            )

    def test_corrupt_state_is_explicit(self, state_file: Path) -> None:
        state_file.write_text("{not json")
        with patch.object(nbn_monitor, "STATE_FILE", state_file):
            result = nbn_monitor.load_state_result()
            assert result.status == "corrupt"
            assert result.error is not None

    def test_azure_storage_failure_does_not_fall_back_to_local(self, state_file: Path) -> None:
        state_file.write_text(json.dumps({"LOC000000000001": "NO_OUTAGE"}))
        with (
            patch.dict(
                os.environ,
                {"AzureWebJobsStorage": "DefaultEndpointsProtocol=https;AccountName=test"},
            ),
            patch.object(nbn_monitor, "STATE_FILE", state_file),
            patch.object(nbn_monitor, "_get_blob_client", side_effect=OSError("blob down")),
        ):
            result = nbn_monitor.load_state_result()

        assert result.status == "failed"
        assert result.state["addresses"] == {}

    def test_malformed_azure_storage_is_failed_load(self, state_file: Path) -> None:
        state_file.write_text(json.dumps({"LOC000000000001": "NO_OUTAGE"}))
        with (
            patch.dict(
                os.environ,
                {"AzureWebJobsStorage": "DefaultEndpointsProtocol=https;AccountName=test"},
            ),
            patch.object(nbn_monitor, "STATE_FILE", state_file),
            patch.object(
                nbn_monitor,
                "_get_blob_client",
                side_effect=ValueError(
                    "malformed DefaultEndpointsProtocol=https;AccountName=test;AccountKey=secret"
                ),
            ),
        ):
            result = nbn_monitor.load_state_result()

        assert result.status == "failed"
        assert result.state["addresses"] == {}
        assert result.error is not None
        assert "secret" not in result.error
        assert "AccountKey=[redacted]" in result.error
        assert "AccountName=[redacted]" in result.error

    def test_azure_save_failure_does_not_fall_back_to_local(
        self, state_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        state = {"schema_version": 2, "generated_at": "", "poll": {}, "addresses": {}}
        with (
            patch.dict(
                os.environ,
                {"AzureWebJobsStorage": "DefaultEndpointsProtocol=https;AccountName=test"},
            ),
            patch.object(nbn_monitor, "STATE_FILE", state_file),
            patch.object(
                nbn_monitor,
                "_get_blob_client",
                side_effect=OSError(
                    "blob down DefaultEndpointsProtocol=https;AccountName=test;AccountKey=secret"
                ),
            ),
        ):
            saved = nbn_monitor.save_state(state)

        assert saved is False
        assert not state_file.exists()
        stderr = capsys.readouterr().err
        assert "secret" not in stderr
        assert "AccountKey=[redacted]" in stderr


# ---------------------------------------------------------------------------
# ntfy
# ---------------------------------------------------------------------------


class TestSendNtfy:
    def test_no_topic_returns_false(self) -> None:
        with patch.object(nbn_monitor, "NTFY_TOPIC", ""):
            assert nbn_monitor.send_ntfy("title", "msg") is False

    def test_sends_notification(self) -> None:
        with (
            patch.object(nbn_monitor, "NTFY_TOPIC", "test-topic"),
            patch.object(nbn_monitor, "NTFY_SERVER", "https://ntfy.sh"),
            patch.object(nbn_monitor, "STATUS_PAGE_URL", "https://example.com/status"),
            patch.object(nbn_monitor.niquests, "post") as mock_post,
        ):
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp

            result = nbn_monitor.send_ntfy("title", "msg", priority="high", tags="warning")
            assert result is True

            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args
            assert call_kwargs.args[0] == "https://ntfy.sh/test-topic"
            headers = call_kwargs.kwargs["headers"]
            assert headers["Title"] == "title"
            assert headers["Priority"] == "high"
            assert "Actions" in headers

    def test_handles_request_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch.object(nbn_monitor, "NTFY_TOPIC", "test-topic"),
            patch.object(nbn_monitor.niquests, "post") as mock_post,
        ):
            mock_post.side_effect = nbn_monitor.niquests.RequestException("fail")
            assert nbn_monitor.send_ntfy("title", "msg") is False

        assert "fail" in capsys.readouterr().err

    def test_handles_request_error_scrubs_topic(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch.object(nbn_monitor, "NTFY_TOPIC", "secret-topic"),
            patch.object(nbn_monitor.niquests, "post") as mock_post,
        ):
            mock_post.side_effect = nbn_monitor.niquests.RequestException(
                "POST https://ntfy.sh/secret-topic failed"
            )
            assert nbn_monitor.send_ntfy("title", "msg") is False

        stderr = capsys.readouterr().err
        assert "[url]" in stderr
        assert "secret-topic" not in stderr


# ---------------------------------------------------------------------------
# notify_changes
# ---------------------------------------------------------------------------


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
        previous: dict[str, Any] = {
            "LOC000000000001": {"status": "NO_OUTAGE", "since": "", "last_checked": ""},
        }

        with patch.object(nbn_monitor, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes(results, previous)
            mock_ntfy.assert_not_called()

    def test_outage_start_sends_alert(self) -> None:
        results = [self._make_result("Home", "LOC000000000001", "UNPLANNED_INPROGRESS")]
        previous: dict[str, Any] = {
            "LOC000000000001": {"status": "NO_OUTAGE", "since": "", "last_checked": ""},
        }

        with patch.object(nbn_monitor, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes(results, previous)
            mock_ntfy.assert_called_once()
            assert mock_ntfy.call_args.kwargs["priority"] == "high"

    def test_outage_resolved_sends_resolved(self) -> None:
        results = [self._make_result("Home", "LOC000000000001", "NO_OUTAGE")]
        previous: dict[str, Any] = {
            "LOC000000000001": {
                "status": "UNPLANNED_INPROGRESS",
                "since": "",
                "last_checked": "",
            },
        }

        with patch.object(nbn_monitor, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes(results, previous)
            mock_ntfy.assert_called_once()
            call_kw = mock_ntfy.call_args
            msg = call_kw.kwargs.get("message") or call_kw.args[1]
            assert "restored" in msg

    def test_first_run_outage_triggers_alert(self) -> None:
        results = [self._make_result("Home", "LOC000000000001", "UNPLANNED_INPROGRESS")]
        previous: dict[str, Any] = {}

        with patch.object(nbn_monitor, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes(results, previous)
            mock_ntfy.assert_called_once()

    def test_non_notify_address_skipped(self) -> None:
        results = [
            self._make_result("Neighbour", "LOC000000000002", "UNPLANNED_INPROGRESS", notify=False)
        ]
        previous: dict[str, Any] = {
            "LOC000000000002": {"status": "NO_OUTAGE", "since": "", "last_checked": ""},
        }

        with patch.object(nbn_monitor, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes(results, previous)
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
        previous: dict[str, Any] = {}

        with patch.object(nbn_monitor, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes(results, previous)
            call_kw = mock_ntfy.call_args
            msg = call_kw.kwargs.get("message") or call_kw.args[1]
            assert "area-wide" in msg

    def test_compare_localised(self) -> None:
        results = [
            self._make_result("Home", "LOC000000000001", "UNPLANNED_INPROGRESS"),
            self._make_result(
                "Neighbour", "LOC000000000002", "NO_OUTAGE", notify=False, compare=True
            ),
        ]
        previous: dict[str, Any] = {}

        with patch.object(nbn_monitor, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes(results, previous)
            call_kw = mock_ntfy.call_args
            msg = call_kw.kwargs.get("message") or call_kw.args[1]
            assert "localised" in msg

    def test_returns_new_state(self) -> None:
        results = [self._make_result("Home", "LOC000000000001", "UNPLANNED_INPROGRESS")]
        previous: dict[str, Any] = {}

        with patch.object(nbn_monitor, "send_ntfy"):
            new_state = nbn_monitor.notify_changes(results, previous)
            assert "LOC000000000001" in new_state["addresses"]
            entry = new_state["addresses"]["LOC000000000001"]
            assert entry["last_success"]["display_outage"] == "UNPLANNED_INPROGRESS"
            assert entry["current_period"]["display_outage"] == "UNPLANNED_INPROGRESS"
            assert "last_checked" in entry

    def test_outage_resolved_includes_duration(self) -> None:
        """When an outage resolves, the message includes 'after Xh Ym'."""
        from datetime import UTC, datetime, timedelta

        two_hours_ago = (datetime.now(tz=UTC) - timedelta(hours=2)).isoformat()
        results = [self._make_result("Home", "LOC000000000001", "NO_OUTAGE")]
        previous: dict[str, Any] = {
            "LOC000000000001": {
                "status": "UNPLANNED_INPROGRESS",
                "since": two_hours_ago,
                "last_checked": two_hours_ago,
            },
        }

        with patch.object(nbn_monitor, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes(results, previous)
            mock_ntfy.assert_called_once()
            call_kw = mock_ntfy.call_args
            msg = call_kw.kwargs.get("message") or call_kw.args[1]
            assert "after 2h" in msg

    def test_poll_error_does_not_resolve_existing_outage(self) -> None:
        """A transient poll failure is not a successful service-restored sample."""
        previous: dict[str, Any] = {
            "LOC000000000001": {
                "status": "UNPLANNED_INPROGRESS",
                "since": "2025-01-01T00:00:00+00:00",
                "last_checked": "2025-01-01T00:05:00+00:00",
            },
        }
        addr = nbn_monitor.Address(label="Home", loc_id="LOC000000000001", poll=True, notify=True)
        status = nbn_monitor.OutageStatus(
            loc_id="LOC000000000001",
            display_outage="",
            label="Error",
            error="timeout",
            checked_at=time.time(),
        )

        with patch.object(nbn_monitor, "send_ntfy") as mock_ntfy:
            new_state = nbn_monitor.notify_changes([(addr, status)], previous)

        mock_ntfy.assert_not_called()
        entry = new_state["addresses"]["LOC000000000001"]
        assert entry["last_success"]["display_outage"] == "UNPLANNED_INPROGRESS"
        assert entry["current_period"]["started_at"] == "2025-01-01T00:00:00+00:00"
        assert entry["last_error"]["message"] == "timeout"

    def test_missing_previous_state_skips_notification_decisions(self) -> None:
        results = [self._make_result("Home", "LOC000000000001", "UNPLANNED_INPROGRESS")]

        with patch.object(nbn_monitor, "send_ntfy") as mock_ntfy:
            new_state = nbn_monitor.notify_changes(results, {}, previous_loaded=False)

        mock_ntfy.assert_not_called()
        entry = new_state["addresses"]["LOC000000000001"]
        assert entry["last_success"]["display_outage"] == "UNPLANNED_INPROGRESS"

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
        previous: dict[str, Any] = {}

        with patch.object(nbn_monitor, "send_ntfy") as mock_ntfy:
            nbn_monitor.notify_changes(results, previous)
            # Only one outage alert per cycle, even with multiple affected addresses
            mock_ntfy.assert_called_once()
            call_kw = mock_ntfy.call_args
            msg = call_kw.kwargs.get("message") or call_kw.args[1]
            assert "area-wide" in msg


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------


class TestGenerateHtml:
    def test_generates_valid_html(self, addresses: list[nbn_monitor.Address]) -> None:
        results = [
            (
                addresses[0],
                nbn_monitor.OutageStatus(
                    loc_id="LOC1",
                    display_outage="NO_OUTAGE",
                    label="No outage",
                    checked_at=time.time(),
                ),
            ),
            (
                addresses[1],
                nbn_monitor.OutageStatus(
                    loc_id="LOC2",
                    display_outage="UNPLANNED_INPROGRESS",
                    label="Unplanned outage",
                    checked_at=time.time(),
                ),
            ),
        ]
        html = nbn_monitor.generate_html(results)
        assert "<!DOCTYPE html>" in html
        assert "Home" in html
        assert "Neighbour" in html
        assert "#22c55e" in html  # green
        assert "#ef4444" in html  # red
        assert "refreshing in" in html
        # No outage label shown for green
        assert "No outage" not in html
        # Outage label shown as tag for red
        assert "Unplanned outage" in html
        assert 'class="tag"' in html

    def test_error_address(self) -> None:
        addr = nbn_monitor.Address(label="Broken", loc_id="LOC000000000099")
        status = nbn_monitor.OutageStatus(
            loc_id="LOC000000000099",
            display_outage="",
            label="Error",
            error="Connection refused",
            checked_at=time.time(),
        )
        html = nbn_monitor.generate_html([(addr, status)])
        assert "Connection refused" in html
        assert "#9ca3af" in html  # grey light

    def test_generate_html_with_state(self) -> None:
        """generate_html shows 'since' time when state is provided."""
        from datetime import UTC, datetime

        since_iso = datetime(2025, 3, 15, 10, 30, tzinfo=UTC).isoformat()
        addr = nbn_monitor.Address(label="Home", loc_id="LOC000000000001", poll=True, notify=True)
        status = nbn_monitor.OutageStatus(
            loc_id="LOC000000000001",
            display_outage="UNPLANNED_INPROGRESS",
            label="Unplanned outage",
            checked_at=time.time(),
        )
        state: dict[str, Any] = {
            "LOC000000000001": {
                "status": "UNPLANNED_INPROGRESS",
                "since": since_iso,
                "last_checked": since_iso,
            },
        }
        html = nbn_monitor.generate_html([(addr, status)], state=state)
        assert "since" in html


# ---------------------------------------------------------------------------
# OutageStatus properties
# ---------------------------------------------------------------------------


class TestOutageStatus:
    @pytest.mark.parametrize(
        ("display", "expected_colour"),
        [
            ("NO_OUTAGE", "green"),
            ("UNPLANNED_INPROGRESS", "red"),
            ("UNPLANNED_POWER_INPROGRESS", "red"),
            ("PLANNED_INPROGRESS", "amber"),
            ("PLANNED_NEARTERM", "amber"),
            ("DEGRADATION_INPROGRESS", "amber"),
            ("SOMETHING_UNKNOWN", "grey"),
        ],
    )
    def test_colour(self, display: str, expected_colour: str) -> None:
        s = nbn_monitor.OutageStatus(loc_id="X", display_outage=display, label="test")
        assert s.colour == expected_colour

    def test_error_is_always_grey(self) -> None:
        s = nbn_monitor.OutageStatus(
            loc_id="X", display_outage="NO_OUTAGE", label="test", error="fail"
        )
        assert s.colour == "grey"


# ---------------------------------------------------------------------------
# _format_duration
# ---------------------------------------------------------------------------


class TestFormatDuration:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (60, "1m"),
            (3600, "1h"),
            (8100, "2h 15m"),
            (45 * 60, "45m"),
            (0, "0m"),
        ],
    )
    def test_format_duration(self, seconds: float, expected: str) -> None:
        assert nbn_monitor._format_duration(seconds) == expected


# ---------------------------------------------------------------------------
# CLI (poll)
# ---------------------------------------------------------------------------


class TestPoll:
    def test_poll_checks_all_addresses(self, addresses: list[nbn_monitor.Address]) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MAINTENANCE_OK
        mock_resp.raise_for_status = MagicMock()

        with patch.object(nbn_monitor.niquests, "Session") as mock_cls:
            instance = MagicMock()
            instance.get.return_value = mock_resp
            instance.__enter__ = MagicMock(return_value=instance)
            instance.__exit__ = MagicMock(return_value=False)
            mock_cls.return_value = instance

            results = nbn_monitor.poll(addresses)
            # poll() now checks ALL addresses
            assert len(results) == 3

    def test_poll_with_notify(self, addresses: list[nbn_monitor.Address], state_file: Path) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MAINTENANCE_OK
        mock_resp.raise_for_status = MagicMock()

        with (
            patch.object(nbn_monitor.niquests, "Session") as mock_cls,
            patch.object(nbn_monitor, "STATE_FILE", state_file),
        ):
            instance = MagicMock()
            instance.get.return_value = mock_resp
            instance.__enter__ = MagicMock(return_value=instance)
            instance.__exit__ = MagicMock(return_value=False)
            mock_cls.return_value = instance

            nbn_monitor.poll(addresses, notify=True)
            assert state_file.exists()


# ---------------------------------------------------------------------------
# CLI (main)
# ---------------------------------------------------------------------------


class TestMain:
    def test_main_poll(self, addresses: list[nbn_monitor.Address]) -> None:
        with (
            patch("sys.argv", ["nbn_monitor.py"]),
            patch.dict(os.environ, {"NBN_ADDRESSES": SAMPLE_ADDRESSES_JSON}),
            patch.object(nbn_monitor, "poll") as mock_poll,
        ):
            mock_poll.return_value = []
            nbn_monitor.main()
            mock_poll.assert_called_once()
            assert mock_poll.call_args.kwargs.get("notify") is False

    def test_main_notify(self) -> None:
        with (
            patch("sys.argv", ["nbn_monitor.py", "--notify"]),
            patch.dict(os.environ, {"NBN_ADDRESSES": SAMPLE_ADDRESSES_JSON}),
            patch.object(nbn_monitor, "poll") as mock_poll,
        ):
            mock_poll.return_value = []
            nbn_monitor.main()
            assert mock_poll.call_args.kwargs.get("notify") is True

    def test_main_serve(self) -> None:
        with (
            patch("sys.argv", ["nbn_monitor.py", "--serve"]),
            patch.dict(os.environ, {"NBN_ADDRESSES": SAMPLE_ADDRESSES_JSON}),
            patch.object(nbn_monitor, "serve") as mock_serve,
        ):
            nbn_monitor.main()
            mock_serve.assert_called_once()


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class TestHandler:
    def test_do_get_reads_snapshot_without_polling(
        self, addresses: list[nbn_monitor.Address], state_file: Path
    ) -> None:
        state = nbn_monitor.notify_changes(
            [
                (
                    addresses[0],
                    nbn_monitor.OutageStatus(
                        loc_id=addresses[0].loc_id,
                        display_outage="NO_OUTAGE",
                        label="No outage",
                        checked_at=time.time(),
                    ),
                )
            ],
            {},
            previous_loaded=False,
        )
        state_file.write_text(json.dumps(state))

        with (
            patch.object(nbn_monitor, "STATE_FILE", state_file),
            patch.object(nbn_monitor, "check_all") as mock_check_all,
        ):
            handler_cls = nbn_monitor.make_handler(addresses)

            handler = MagicMock(spec=handler_cls)
            handler.wfile = MagicMock()
            handler_cls.do_GET(handler)

        mock_check_all.assert_not_called()
        handler.send_response.assert_called_with(200)
        handler.wfile.write.assert_called_once()
        html = handler.wfile.write.call_args.args[0].decode()
        assert "<!DOCTYPE html>" in html
        assert "Home" in html
        assert "No status snapshot yet" in html
