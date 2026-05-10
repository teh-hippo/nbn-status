"""Tests for ``nbn_monitor.persistence``.

Covers the file backend round-trip and the failure-mode semantics for the
Azure Blob backend (load failures, save failures, secret redaction).
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING
from unittest.mock import patch

import nbn_monitor

from .conftest import v2_snapshot

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class TestFileBackend:
    def test_load_empty(self, state_file: Path) -> None:
        with patch.object(nbn_monitor.persistence, "STATE_FILE", state_file):
            result = nbn_monitor.load_state_result()
            assert result.status == "missing"
            assert isinstance(result.snapshot, nbn_monitor.Snapshot)
            assert result.snapshot.addresses == {}
            assert result.snapshot.poll is None

    def test_save_and_load(self, state_file: Path) -> None:
        snapshot = v2_snapshot(
            ("LOC000000000001", "NO_OUTAGE", ""),
            ("LOC000000000002", "UNPLANNED_INPROGRESS", "2025-01-01T00:00:00+00:00"),
        )
        with patch.object(nbn_monitor.persistence, "STATE_FILE", state_file):
            assert nbn_monitor.save_state(snapshot) is True
            loaded = nbn_monitor.load_state_result().snapshot
            entry_one = loaded.entry("LOC000000000001")
            assert entry_one is not None
            assert entry_one.last_success is not None
            assert entry_one.last_success.display_outage == "NO_OUTAGE"
            entry_two = loaded.entry("LOC000000000002")
            assert entry_two is not None
            assert entry_two.last_success is not None
            assert entry_two.last_success.display_outage == "UNPLANNED_INPROGRESS"
            assert entry_two.current_period is not None
            assert entry_two.current_period.started_at == "2025-01-01T00:00:00+00:00"


class TestAzureBlobBackend:
    def test_azure_storage_failure_does_not_fall_back_to_local(self, state_file: Path) -> None:
        state_file.write_text(json.dumps({"LOC000000000001": "NO_OUTAGE"}))
        with (
            patch.dict(
                os.environ,
                {"AzureWebJobsStorage": "DefaultEndpointsProtocol=https;AccountName=test"},
            ),
            patch.object(nbn_monitor.persistence, "STATE_FILE", state_file),
            patch.object(
                nbn_monitor.persistence, "_get_blob_client", side_effect=OSError("blob down")
            ),
        ):
            result = nbn_monitor.load_state_result()

        assert result.status == "failed"
        assert result.snapshot.addresses == {}

    def test_malformed_azure_storage_is_failed_load(self, state_file: Path) -> None:
        state_file.write_text(json.dumps({"LOC000000000001": "NO_OUTAGE"}))
        with (
            patch.dict(
                os.environ,
                {"AzureWebJobsStorage": "DefaultEndpointsProtocol=https;AccountName=test"},
            ),
            patch.object(nbn_monitor.persistence, "STATE_FILE", state_file),
            patch.object(
                nbn_monitor.persistence,
                "_get_blob_client",
                side_effect=ValueError(
                    "malformed DefaultEndpointsProtocol=https;AccountName=test;AccountKey=secret"
                ),
            ),
        ):
            result = nbn_monitor.load_state_result()

        assert result.status == "failed"
        assert result.snapshot.addresses == {}
        assert result.error is not None
        assert "secret" not in result.error
        assert "AccountKey=[redacted]" in result.error
        assert "AccountName=[redacted]" in result.error

    def test_azure_save_failure_does_not_fall_back_to_local(
        self, state_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        snapshot = nbn_monitor.Snapshot.empty()
        with (
            patch.dict(
                os.environ,
                {"AzureWebJobsStorage": "DefaultEndpointsProtocol=https;AccountName=test"},
            ),
            patch.object(nbn_monitor.persistence, "STATE_FILE", state_file),
            patch.object(
                nbn_monitor.persistence,
                "_get_blob_client",
                side_effect=OSError(
                    "blob down DefaultEndpointsProtocol=https;AccountName=test;AccountKey=secret"
                ),
            ),
        ):
            saved = nbn_monitor.save_state(snapshot)

        assert saved is False
        assert not state_file.exists()
        stderr = capsys.readouterr().err
        assert "secret" not in stderr
        assert "AccountKey=[redacted]" in stderr
