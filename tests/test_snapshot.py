"""Tests for ``nbn_monitor.snapshot``.

Covers the shape-validation contract that classifies a stored payload as
loadable or corrupt. The persistence backends only catch
``CorruptSnapshotError`` and ``json.JSONDecodeError``; the shape semantics
live in ``Snapshot.from_dict``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import nbn_monitor

if TYPE_CHECKING:
    from pathlib import Path


class TestSnapshotShape:
    def test_v1_state_rejected_as_corrupt(self, state_file: Path) -> None:
        """Legacy flat state (no 'addresses' field) is treated as corrupt."""
        old_state = {"LOC000000000001": "NO_OUTAGE", "LOC000000000002": "UNPLANNED_INPROGRESS"}
        state_file.write_text(json.dumps(old_state))
        with patch.object(nbn_monitor.persistence, "STATE_FILE", state_file):
            result = nbn_monitor.load_state_result()
            assert result.status == "corrupt"
            assert result.error is not None
            assert result.snapshot.addresses == {}

    def test_corrupt_state_is_explicit(self, state_file: Path) -> None:
        state_file.write_text("{not json")
        with patch.object(nbn_monitor.persistence, "STATE_FILE", state_file):
            result = nbn_monitor.load_state_result()
            assert result.status == "corrupt"
            assert result.error is not None
