"""Tests for ``nbn_monitor.server``.

Covers the local-development HTTP handler that renders the status page
from the stored snapshot without polling NBN.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import nbn_monitor

if TYPE_CHECKING:
    from pathlib import Path


class TestHandler:
    def test_do_get_reads_snapshot_without_polling(
        self, addresses: list[nbn_monitor.Address], state_file: Path
    ) -> None:
        snapshot = nbn_monitor.derive_snapshot(
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
            nbn_monitor.Snapshot.empty(),
            started_at="2025-01-01T00:00:00+00:00",
            completed_at="2025-01-01T00:00:01+00:00",
        )
        state_file.write_text(json.dumps(snapshot.to_dict()))

        with (
            patch.object(nbn_monitor.persistence, "STATE_FILE", state_file),
            patch.object(nbn_monitor.api, "check_all") as mock_check_all,
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
