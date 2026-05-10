"""Tests for ``nbn_monitor.api``.

Covers the NBN HTTP client (``check_outage`` / ``check_all``) and the free
functions that classify an NBN display outage into a traffic-light colour
and into a boolean "is this an outage" verdict.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import niquests
import pytest

import nbn_monitor

from .conftest import MAINTENANCE_OK, MAINTENANCE_OUTAGE, MAINTENANCE_PLANNED


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

    def test_404_not_connected(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        session = MagicMock()
        session.get.return_value = mock_resp

        result = nbn_monitor.check_outage("LOC000000000001", session=session)
        assert result.error == "Not connected to NBN"

    def test_request_exception(self) -> None:
        session = MagicMock()
        session.get.side_effect = niquests.RequestException("timeout")

        result = nbn_monitor.check_outage("LOC000000000001", session=session)
        assert result.error is not None
        assert "timeout" in result.error

    def test_request_exception_scrubs_url_and_loc(self) -> None:
        session = MagicMock()
        session.get.side_effect = nbn_monitor.api.niquests.RequestException(
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

        with patch.object(nbn_monitor.api.niquests, "Session") as mock_session_cls:
            instance = MagicMock()
            instance.get.return_value = mock_resp
            mock_session_cls.return_value = instance

            result = nbn_monitor.check_outage("LOC000000000001")
            assert result.display_outage == "NO_OUTAGE"
            instance.close.assert_called_once()

    def test_missing_display_outage_returns_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"someOtherField": "value"}
        mock_resp.raise_for_status = MagicMock()

        session = MagicMock()
        session.get.return_value = mock_resp

        result = nbn_monitor.check_outage("LOC000000000001", session=session)
        assert result.error == "NBN response missing displayOutage"
        assert result.display_outage == ""

    def test_non_string_display_outage_returns_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"displayOutage": None}
        mock_resp.raise_for_status = MagicMock()

        session = MagicMock()
        session.get.return_value = mock_resp

        result = nbn_monitor.check_outage("LOC000000000001", session=session)
        assert result.error == "NBN response missing displayOutage"


class TestCheckAll:
    def test_checks_all_addresses(self, addresses: list[nbn_monitor.Address]) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MAINTENANCE_OK
        mock_resp.raise_for_status = MagicMock()

        with patch.object(nbn_monitor.api.niquests, "Session") as mock_cls:
            instance = MagicMock()
            instance.get.return_value = mock_resp
            instance.__enter__ = MagicMock(return_value=instance)
            instance.__exit__ = MagicMock(return_value=False)
            mock_cls.return_value = instance

            results = nbn_monitor.check_all(addresses)
            assert len(results) == 3
            assert instance.get.call_count == 3


class TestDisplayOutageColour:
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
        assert nbn_monitor.display_outage_colour(display) == expected_colour

    def test_error_is_always_grey(self) -> None:
        assert nbn_monitor.display_outage_colour("NO_OUTAGE", error=True) == "grey"

    @pytest.mark.parametrize(
        ("display", "expected"),
        [
            ("NO_OUTAGE", False),
            ("", False),
            ("UNPLANNED_INPROGRESS", True),
            ("PLANNED_INPROGRESS", True),
            ("DEGRADATION_INPROGRESS", True),
        ],
    )
    def test_is_outage(self, display: str, expected: bool) -> None:
        assert nbn_monitor.display_outage_is_outage(display) is expected
