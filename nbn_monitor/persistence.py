"""Persistence backends for the state snapshot.

Owns the ``StateBackend`` protocol, the ``FileStateBackend`` /
``BlobStateBackend`` implementations, and the ``load_state_result`` /
``save_state`` entry points the orchestrator and the HTTP route use.

The snapshot dataclasses live in ``nbn_monitor.snapshot``.

``STATE_FILE`` and the ``AzureWebJobsStorage`` environment check remain
as module globals for now. A later DI pass will move them into config
objects so callers can stop using ``patch.object`` to swap them in tests.
"""

from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING, Any, Protocol

from . import config
from .config import _BLOB_CONTAINER, _BLOB_NAME, _safe_error_message
from .snapshot import CorruptSnapshotError, Snapshot, StateLoadResult

if TYPE_CHECKING:
    from pathlib import Path


# Re-export of the configured state-file path so the file backend's lookup
# happens inside this module. Tests patch ``nbn_monitor.persistence.STATE_FILE``;
# ``get_state_backend`` reads the name from this module's namespace at call
# time, so rebinding it through ``patch.object`` takes effect immediately.
STATE_FILE = config.STATE_FILE


def _blob_state_configured() -> bool:
    conn_str = os.environ.get("AzureWebJobsStorage", "")  # noqa: SIM112
    return bool(conn_str and not conn_str.startswith("UseDevelopment"))


def _get_blob_client() -> Any | None:
    """Get a BlobClient for the state blob, or ``None`` if not configured."""
    conn_str = os.environ.get("AzureWebJobsStorage", "")  # noqa: SIM112
    if not _blob_state_configured():
        return None

    from azure.storage.blob import BlobServiceClient

    service = BlobServiceClient.from_connection_string(conn_str)
    container = service.get_container_client(_BLOB_CONTAINER)
    if not container.exists():
        container.create_container()
    return container.get_blob_client(_BLOB_NAME)


class StateBackend(Protocol):
    """Persistence boundary for the state snapshot."""

    name: str

    def load(self) -> StateLoadResult: ...
    def save(self, snapshot: Snapshot) -> bool: ...


class FileStateBackend:
    """Local-file backend used in development and CLI runs."""

    name = "file"

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> StateLoadResult:
        if not self._path.exists():
            return StateLoadResult("missing", Snapshot.empty(), self.name)
        try:
            raw = json.loads(self._path.read_text())
            return StateLoadResult("loaded", Snapshot.from_dict(raw), self.name)
        except (json.JSONDecodeError, CorruptSnapshotError) as e:
            return StateLoadResult("corrupt", Snapshot.empty(), self.name, _safe_error_message(e))
        except OSError as e:
            return StateLoadResult("failed", Snapshot.empty(), self.name, _safe_error_message(e))

    def save(self, snapshot: Snapshot) -> bool:
        try:
            self._path.write_text(json.dumps(snapshot.to_dict(), indent=2))
            return True
        except OSError as e:
            print(f"state save failed: {_safe_error_message(e)}", file=sys.stderr)
            return False


class BlobStateBackend:
    """Azure Blob Storage backend used in production.

    The Azure SDK is imported lazily so that local development and tests do
    not pay the cost when the Blob backend is not selected.
    """

    name = "blob"

    def load(self) -> StateLoadResult:
        try:
            from azure.core.exceptions import AzureError, ResourceNotFoundError

            blob = _get_blob_client()
            if blob is None:
                return StateLoadResult("failed", Snapshot.empty(), self.name, "Blob not configured")
            data = blob.download_blob().readall()
            raw = json.loads(data)
            return StateLoadResult("loaded", Snapshot.from_dict(raw), self.name)
        except ResourceNotFoundError:
            return StateLoadResult("missing", Snapshot.empty(), self.name)
        except (json.JSONDecodeError, CorruptSnapshotError) as e:
            return StateLoadResult("corrupt", Snapshot.empty(), self.name, _safe_error_message(e))
        except (AzureError, OSError, ValueError) as e:
            return StateLoadResult("failed", Snapshot.empty(), self.name, _safe_error_message(e))

    def save(self, snapshot: Snapshot) -> bool:
        data = json.dumps(snapshot.to_dict(), indent=2)
        try:
            from azure.core.exceptions import AzureError

            blob = _get_blob_client()
            if blob is None:
                print("state save failed: Blob state is unavailable", file=sys.stderr)
                return False
            blob.upload_blob(data, overwrite=True)
            return True
        except (AzureError, OSError, ValueError) as e:
            print(f"state save failed: {_safe_error_message(e)}", file=sys.stderr)
            return False


def get_state_backend() -> StateBackend:
    """Pick the Blob backend in Azure, else the local-file backend."""
    if _blob_state_configured():
        return BlobStateBackend()
    return FileStateBackend(STATE_FILE)


def load_state_result() -> StateLoadResult:
    """Load previous outage state with explicit failure semantics.

    Uses Azure Blob Storage when running in Azure, falls back to local file.
    Any payload that does not match the current snapshot shape is reported
    as ``corrupt`` so notification decisions and saves are blocked rather
    than silently overwriting authoritative state.
    """
    return get_state_backend().load()


def save_state(snapshot: Snapshot) -> bool:
    """Save the current snapshot and report whether persistence succeeded."""
    return get_state_backend().save(snapshot)
