"""Tests for ``nbn_monitor.orchestrator``.

Covers ``poll`` (the CLI helper) and ``run_poll_cycle`` (the single source
of truth for the load → derive → notify → save sequence the timer trigger
and the ``--notify`` CLI both delegate to).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import nbn_monitor

from .conftest import MAINTENANCE_OK

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
