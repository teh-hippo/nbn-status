"""Tests for ``nbn_monitor.orchestrator``.

Covers ``poll`` (the CLI helper) and ``run_poll_cycle`` (the single source
of truth for the load → derive → notify → save sequence the timer trigger
and the ``--notify`` CLI both delegate to).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

import nbn_monitor
from nbn_monitor.snapshot import StateLoadResult

from .conftest import MAINTENANCE_OK, MULTI_WINDOW_MAINTENANCE

if TYPE_CHECKING:
    from pathlib import Path


class TestPoll:
    def test_poll_checks_all_addresses(self, addresses: list[nbn_monitor.Address]) -> None:
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

            results = nbn_monitor.poll(addresses)
            assert len(results) == 3

    def test_poll_with_notify(self, addresses: list[nbn_monitor.Address], state_file: Path) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MAINTENANCE_OK
        mock_resp.raise_for_status = MagicMock()

        with (
            patch.object(nbn_monitor.api.niquests, "Session") as mock_cls,
            patch.object(nbn_monitor.persistence, "STATE_FILE", state_file),
        ):
            instance = MagicMock()
            instance.get.return_value = mock_resp
            instance.__enter__ = MagicMock(return_value=instance)
            instance.__exit__ = MagicMock(return_value=False)
            mock_cls.return_value = instance

            nbn_monitor.poll(addresses, notify=True)
            assert state_file.exists()

    def test_poll_cycle_applies_successful_planned_delivery_before_save(self) -> None:
        addr = nbn_monitor.Address(
            label="Home",
            loc_id="LOC000000000001",
            notify=True,
        )
        status = nbn_monitor.OutageStatus(
            loc_id=addr.loc_id,
            display_outage="NO_OUTAGE",
            label="No outage",
        )
        event = nbn_monitor.parse_planned_maintenance(MULTI_WINDOW_MAINTENANCE).events[0]
        new_snapshot = nbn_monitor.Snapshot(
            addresses={
                addr.loc_id: nbn_monitor.AddressEntry(
                    label=addr.label,
                    planned_maintenance=[event],
                )
            }
        )
        delivery = nbn_monitor.PlannedDelivery(
            loc_id=addr.loc_id,
            announced_schedule=(event,),
            day_before_revisions=(nbn_monitor.material_revision(event),),
            hour_before_revisions=(),
        )

        with (
            patch.object(nbn_monitor.orchestrator, "check_all", return_value=[(addr, status)]),
            patch.object(
                nbn_monitor.orchestrator,
                "load_state_result",
                return_value=StateLoadResult(
                    "loaded",
                    nbn_monitor.Snapshot.empty(),
                    "file",
                ),
            ),
            patch.object(
                nbn_monitor.orchestrator,
                "derive_snapshot",
                return_value=new_snapshot,
            ),
            patch.object(nbn_monitor.orchestrator, "notify_changes", return_value=()),
            patch.object(
                nbn_monitor.orchestrator,
                "notify_planned_maintenance",
                return_value=(delivery,),
            ),
            patch.object(nbn_monitor.orchestrator, "save_state", return_value=True) as save,
        ):
            nbn_monitor.run_poll_cycle([addr])

        saved = save.call_args.args[0]
        entry = saved.entry(addr.loc_id)
        assert entry is not None
        assert entry.planned_notifications.announced_schedule == [event]
        assert entry.planned_notifications.day_before_sent == [nbn_monitor.material_revision(event)]

    def test_poll_cycle_surfaces_state_save_failure(self) -> None:
        addr = nbn_monitor.Address(label="Home", loc_id="LOC000000000001")
        status = nbn_monitor.OutageStatus(
            loc_id=addr.loc_id,
            display_outage="NO_OUTAGE",
            label="No outage",
        )

        with (
            patch.object(nbn_monitor.orchestrator, "check_all", return_value=[(addr, status)]),
            patch.object(
                nbn_monitor.orchestrator,
                "load_state_result",
                return_value=StateLoadResult(
                    "loaded",
                    nbn_monitor.Snapshot.empty(),
                    "file",
                ),
            ),
            patch.object(nbn_monitor.orchestrator, "notify_changes", return_value=()),
            patch.object(
                nbn_monitor.orchestrator,
                "notify_planned_maintenance",
                return_value=(),
            ),
            patch.object(nbn_monitor.orchestrator, "save_state", return_value=False),
            pytest.raises(RuntimeError, match="state save failed"),
        ):
            nbn_monitor.run_poll_cycle([addr])

    def test_missing_state_seeds_notification_baselines(self) -> None:
        addr = nbn_monitor.Address(
            label="Home",
            loc_id="LOC000000000001",
            notify=True,
        )
        status = nbn_monitor.OutageStatus(
            loc_id=addr.loc_id,
            display_outage="UNPLANNED_INPROGRESS",
            label="Unplanned",
            raw=MULTI_WINDOW_MAINTENANCE,
            checked_at=1_783_908_979,
        )

        with (
            patch.object(
                nbn_monitor.orchestrator,
                "check_all",
                return_value=[(addr, status)],
            ),
            patch.object(
                nbn_monitor.orchestrator,
                "load_state_result",
                return_value=StateLoadResult(
                    "missing",
                    nbn_monitor.Snapshot.empty(),
                    "file",
                ),
            ),
            patch.object(
                nbn_monitor.orchestrator,
                "save_state",
                return_value=True,
            ) as save,
        ):
            nbn_monitor.run_poll_cycle([addr])

        saved = save.call_args.args[0]
        entry = saved.entry(addr.loc_id)
        assert entry is not None
        assert entry.service_issue is not None
        assert entry.service_notifications.pending_starts == []
        assert entry.service_notifications.announced_issue == entry.service_issue
        assert entry.planned_notifications.announced_schedule == entry.planned_maintenance

    def test_missing_state_waits_for_first_successful_address_baseline(self) -> None:
        addr = nbn_monitor.Address(
            label="Home",
            loc_id="LOC000000000001",
            notify=True,
        )
        failed_status = nbn_monitor.OutageStatus(
            loc_id=addr.loc_id,
            display_outage="",
            label="Error",
            error="timeout",
            checked_at=1_783_908_979,
        )

        with (
            patch.object(
                nbn_monitor.orchestrator,
                "check_all",
                return_value=[(addr, failed_status)],
            ),
            patch.object(
                nbn_monitor.orchestrator,
                "load_state_result",
                return_value=StateLoadResult(
                    "missing",
                    nbn_monitor.Snapshot.empty(),
                    "file",
                ),
            ),
            patch.object(
                nbn_monitor.orchestrator,
                "save_state",
                return_value=True,
            ) as first_save,
        ):
            nbn_monitor.run_poll_cycle([addr])

        first_snapshot = first_save.call_args.args[0]
        first_entry = first_snapshot.entry(addr.loc_id)
        assert first_entry is not None
        assert first_entry.notification_baseline_pending

        success_status = nbn_monitor.OutageStatus(
            loc_id=addr.loc_id,
            display_outage="UNPLANNED_INPROGRESS",
            label="Unplanned",
            raw=MULTI_WINDOW_MAINTENANCE,
            checked_at=1_783_909_279,
        )
        with (
            patch.object(
                nbn_monitor.orchestrator,
                "check_all",
                return_value=[(addr, success_status)],
            ),
            patch.object(
                nbn_monitor.orchestrator,
                "load_state_result",
                return_value=StateLoadResult(
                    "loaded",
                    first_snapshot,
                    "file",
                ),
            ),
            patch.object(
                nbn_monitor.orchestrator,
                "save_state",
                return_value=True,
            ) as second_save,
            patch.object(nbn_monitor.notify, "send_ntfy") as send_ntfy,
        ):
            nbn_monitor.run_poll_cycle([addr])

        titles = [call.kwargs["title"] for call in send_ntfy.call_args_list]
        assert "NBN Outage Alert" not in titles
        assert "NBN Planned Maintenance Added" not in titles
        second_snapshot = second_save.call_args.args[0]
        second_entry = second_snapshot.entry(addr.loc_id)
        assert second_entry is not None
        assert not second_entry.notification_baseline_pending
        assert second_entry.service_notifications.pending_starts == []
        assert second_entry.service_notifications.announced_issue == second_entry.service_issue
        assert (
            second_entry.planned_notifications.announced_schedule
            == second_entry.planned_maintenance
        )
