"""Configuration constants, the Address model, and shared redaction helpers.

This module is the lowest-level building block in the package. It owns the
environment-driven knobs (state location, ntfy endpoint, status page URL),
the URL/secret/LOC scrubbers used by every error-emitting path, and the
``Address`` value object that the rest of the codebase passes around.

It must not depend on any other ``nbn_monitor`` submodule so the import
graph stays acyclic.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

NBN_BASE = "https://places.nbnco.net.au/places"
NBN_HEADERS = {
    "Referer": "https://www.nbnco.com.au/support/network-status",
    "X-NBN-Recaptcha-Token": "nbn-status-monitor",
}

STATE_FILE = Path(os.environ.get("NBN_STATE_FILE", "state.json"))
_BLOB_CONTAINER = "nbn-state"
_BLOB_NAME = "state.json"

_URL_RE = re.compile(r"https?://\S+")
_LOCATION_ID_RE = re.compile(r"\bLOC[A-Z0-9]+\b")
_SECRET_FIELD_RE = re.compile(
    r"\b(AccountName|AccountKey|SharedAccessKey|SharedAccessSignature)=([^;\s]+)",
    re.IGNORECASE,
)


@dataclass
class Address:
    label: str
    loc_id: str
    poll: bool = True
    notify: bool = False
    compare: bool = False
    short_id: str = ""


@dataclass(frozen=True)
class NtfyConfig:
    """Ntfy delivery endpoint and optional status-page link."""

    server: str
    topic: str
    status_page_url: str

    @classmethod
    def from_env(cls) -> NtfyConfig:
        return cls(
            server=os.environ.get("NTFY_SERVER", "https://ntfy.sh"),
            topic=os.environ.get("NTFY_TOPIC", ""),
            status_page_url=os.environ.get("STATUS_PAGE_URL", ""),
        )


def _safe_error_message(error: BaseException) -> str:
    message = str(error) or error.__class__.__name__
    message = _URL_RE.sub("[url]", message)
    message = _SECRET_FIELD_RE.sub(lambda match: f"{match.group(1)}=[redacted]", message)
    return _LOCATION_ID_RE.sub("[location]", message)


def load_addresses() -> list[Address]:
    """Load addresses from the ``NBN_ADDRESSES`` env var (JSON string).

    Each address is assigned a stable opaque ``short_id`` based on its
    position in the configured list (``addr_0``, ``addr_1``, ...). Logs
    use this identifier so neither the user-facing label nor the LOC id
    is ever emitted to operator-visible streams.
    """
    raw = os.environ.get("NBN_ADDRESSES", "")
    if not raw:
        print("ERROR: NBN_ADDRESSES env var not set", file=sys.stderr)
        sys.exit(1)
    entries: list[dict[str, Any]] = json.loads(raw)
    addresses: list[Address] = []
    for index, entry in enumerate(entries):
        if "short_id" not in entry:
            entry["short_id"] = f"addr_{index}"
        addresses.append(Address(**entry))
    return addresses
