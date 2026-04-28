"""NBN outage monitor with ntfy notifications and traffic-light status page."""

from __future__ import annotations

__version__ = "0.1.0"

import argparse
import html
import json
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Literal

import niquests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NBN_BASE = "https://places.nbnco.net.au/places"
NBN_HEADERS = {
    "Referer": "https://www.nbnco.com.au/support/network-status",
    "X-NBN-Recaptcha-Token": "nbn-status-monitor",
}

STATE_FILE = Path(os.environ.get("NBN_STATE_FILE", "state.json"))
_BLOB_CONTAINER = "nbn-state"
_BLOB_NAME = "state.json"
_STATE_SCHEMA_VERSION = 2

NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
STATUS_PAGE_URL = os.environ.get("STATUS_PAGE_URL", "")

StateLoadStatus = Literal["loaded", "missing", "failed", "corrupt"]

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


@dataclass
class StateLoadResult:
    status: StateLoadStatus
    state: dict[str, Any]
    source: str
    error: str | None = None

    @property
    def can_make_notification_decisions(self) -> bool:
        return self.status == "loaded"


def _safe_error_message(error: BaseException) -> str:
    message = str(error) or error.__class__.__name__
    message = _URL_RE.sub("[url]", message)
    message = _SECRET_FIELD_RE.sub(lambda match: f"{match.group(1)}=[redacted]", message)
    return _LOCATION_ID_RE.sub("[location]", message)


def load_addresses() -> list[Address]:
    """Load addresses from NBN_ADDRESSES env var (JSON string)."""
    raw = os.environ.get("NBN_ADDRESSES", "")
    if not raw:
        print("ERROR: NBN_ADDRESSES env var not set", file=sys.stderr)
        sys.exit(1)
    entries: list[dict[str, Any]] = json.loads(raw)
    return [Address(**entry) for entry in entries]


# ---------------------------------------------------------------------------
# NBN API
# ---------------------------------------------------------------------------

OUTAGE_LABELS: dict[str, str] = {
    "NO_OUTAGE": "No outage",
    "UNPLANNED_INPROGRESS": "Unplanned",
    "UNPLANNED_ECRQ_INPROGRESS": "Unplanned (eCRQ)",
    "UNPLANNED_POWER_INPROGRESS": "Unplanned (power)",
    "DEGRADATION_INPROGRESS": "Degradation",
    "PLANNED_INPROGRESS": "Planned maintenance",
    "PLANNED_NEARTERM": "Planned upcoming",
    "PLANNED_NOTACTIVE": "Planned today",
    "PLANNED_POWER_INPROGRESS": "Planned power work",
    "PLANNED_POWER_NEARTERM": "Planned power upcoming",
    "PLANNED_POWER_NOTACTIVE": "Planned power today",
}


@dataclass
class OutageStatus:
    loc_id: str
    display_outage: str  # e.g. "NO_OUTAGE", "UNPLANNED_INPROGRESS"
    label: str  # human-readable
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    checked_at: float = 0.0

    @property
    def is_outage(self) -> bool:
        return self.display_outage not in ("NO_OUTAGE", "")

    @property
    def colour(self) -> str:
        if self.error:
            return "grey"
        if self.display_outage == "NO_OUTAGE":
            return "green"
        if "UNPLANNED" in self.display_outage:
            return "red"
        if any(w in self.display_outage for w in ("PLANNED", "DEGRADATION")):
            return "amber"
        return "grey"


def check_outage(loc_id: str, session: niquests.Session | None = None) -> OutageStatus:
    """Query the NBN maintenance API for a single location."""
    uid = f"{int(time.time() * 1000)}-{random.randint(100000, 999999)}"
    url = f"{NBN_BASE}/v1/maintenance?locationId={loc_id}&uniqueid={uid}"

    do_close = False
    if session is None:
        session = niquests.Session()
        do_close = True

    try:
        attempt = 0
        while True:
            try:
                started = time.monotonic()
                resp = session.get(url, headers=NBN_HEADERS, timeout=10)
                latency_ms = int((time.monotonic() - started) * 1000)
                if resp.status_code == 404:
                    return OutageStatus(
                        loc_id=loc_id,
                        display_outage="",
                        label="Not connected",
                        error="Not connected to NBN",
                        checked_at=time.time(),
                    )
                resp.raise_for_status()
                data: dict[str, Any] = resp.json()
                display = data.get("displayOutage", "UNKNOWN")
                print(f"nbn result status={display} latency_ms={latency_ms}")
                return OutageStatus(
                    loc_id=loc_id,
                    display_outage=display,
                    label=OUTAGE_LABELS.get(display, display),
                    raw=data,
                    checked_at=time.time(),
                )
            except niquests.RequestException:
                attempt += 1
                if attempt >= 2:
                    raise
                time.sleep(0.5)
    except niquests.RequestException as e:
        message = _safe_error_message(e)
        print(f"nbn error category=request message={message}", file=sys.stderr)
        return OutageStatus(
            loc_id=loc_id,
            display_outage="",
            label="Error",
            error=message,
            checked_at=time.time(),
        )
    finally:
        if do_close:
            session.close()


def check_all(addresses: list[Address]) -> list[tuple[Address, OutageStatus]]:
    """Check outage status for all addresses in parallel."""

    def _check(addr: Address) -> tuple[Address, OutageStatus]:
        return addr, check_outage(addr.loc_id)

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(_check, addresses))
    return results


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------


def _blob_state_configured() -> bool:
    conn_str = os.environ.get("AzureWebJobsStorage", "")  # noqa: SIM112
    return bool(conn_str and not conn_str.startswith("UseDevelopment"))


def _get_blob_client() -> Any | None:
    """Get a BlobClient for the state blob, or None if not configured."""
    conn_str = os.environ.get("AzureWebJobsStorage", "")  # noqa: SIM112
    if not _blob_state_configured():
        return None

    from azure.storage.blob import BlobServiceClient

    service = BlobServiceClient.from_connection_string(conn_str)
    container = service.get_container_client(_BLOB_CONTAINER)
    if not container.exists():
        container.create_container()
    return container.get_blob_client(_BLOB_NAME)


def _empty_snapshot() -> dict[str, Any]:
    return {
        "schema_version": _STATE_SCHEMA_VERSION,
        "generated_at": "",
        "poll": {},
        "addresses": {},
    }


def _is_snapshot(state: dict[str, Any]) -> bool:
    return state.get("schema_version") == _STATE_SCHEMA_VERSION and isinstance(
        state.get("addresses"), dict
    )


def _address_labels(addresses: list[Address] | None) -> dict[str, str]:
    if addresses is None:
        return {}
    return {addr.loc_id: addr.label for addr in addresses}


def _iso_from_timestamp(value: float) -> str:
    timestamp = value if value else time.time()
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


def _timestamp_from_iso(value: Any) -> float:
    if not isinstance(value, str) or not value:
        return time.time()
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return time.time()


def _status_colour(display_outage: str) -> str:
    return OutageStatus(
        loc_id="",
        display_outage=display_outage,
        label=OUTAGE_LABELS.get(display_outage, display_outage),
    ).colour


def _success_record(status: OutageStatus) -> dict[str, Any]:
    return {
        "display_outage": status.display_outage,
        "label": status.label,
        "colour": status.colour,
        "checked_at": _iso_from_timestamp(status.checked_at),
        "nbn_valid_at": status.raw.get("validAt"),
    }


def _normalise_state(
    raw: dict[str, Any],
    addresses: list[Address] | None = None,
) -> dict[str, Any]:
    """Return a v2 snapshot, migrating legacy state in memory."""
    labels = _address_labels(addresses)
    if _is_snapshot(raw):
        snapshot = dict(raw)
        snapshot["addresses"] = dict(raw.get("addresses", {}))
        for loc_id, label in labels.items():
            existing_entry = snapshot["addresses"].get(loc_id)
            if isinstance(existing_entry, dict):
                existing_entry.setdefault("label", label)
        return snapshot

    snapshot = _empty_snapshot()
    migrated: dict[str, Any] = {}
    for loc_id, value in raw.items():
        if not isinstance(loc_id, str):
            continue
        if isinstance(value, str):
            status = value
            since = ""
            last_checked = ""
        elif isinstance(value, dict):
            status = str(value.get("status", ""))
            since = str(value.get("since", "") or "")
            last_checked = str(value.get("last_checked", "") or "")
        else:
            continue

        entry: dict[str, Any] = {
            "label": labels.get(loc_id, loc_id),
            "last_error": None,
            "consecutive_error_count": 0,
        }
        if status:
            checked_at = last_checked or datetime.now(tz=UTC).isoformat()
            label = OUTAGE_LABELS.get(status, status)
            entry["last_success"] = {
                "display_outage": status,
                "label": label,
                "colour": _status_colour(status),
                "checked_at": checked_at,
                "nbn_valid_at": None,
            }
            entry["status"] = status
            entry["last_checked"] = checked_at
            entry["current_period"] = {
                "display_outage": status,
                "started_at": since or checked_at,
                "started_at_source": "observed",
            }
            if _was_outage(status):
                entry["since"] = since or checked_at
        migrated[loc_id] = entry
    snapshot["addresses"] = migrated
    return snapshot


def load_state_result(addresses: list[Address] | None = None) -> StateLoadResult:
    """Load previous outage state with explicit failure semantics.

    Uses Azure Blob Storage when running in Azure, falls back to local file.
    Handles legacy format (plain string values) by migrating to the new
    versioned snapshot structure.
    """
    if _blob_state_configured():
        try:
            from azure.core.exceptions import AzureError, ResourceNotFoundError

            blob = _get_blob_client()
            if blob is None:
                return StateLoadResult("failed", _empty_snapshot(), "blob", "Blob not configured")
            data = blob.download_blob().readall()
            raw: dict[str, Any] = json.loads(data)
            return StateLoadResult("loaded", _normalise_state(raw, addresses), "blob")
        except ResourceNotFoundError:
            return StateLoadResult("missing", _empty_snapshot(), "blob")
        except json.JSONDecodeError as e:
            return StateLoadResult("corrupt", _empty_snapshot(), "blob", _safe_error_message(e))
        except (AzureError, OSError, ValueError) as e:
            return StateLoadResult("failed", _empty_snapshot(), "blob", _safe_error_message(e))

    if not STATE_FILE.exists():
        return StateLoadResult("missing", _empty_snapshot(), "file")
    try:
        raw = json.loads(STATE_FILE.read_text())
        return StateLoadResult("loaded", _normalise_state(raw, addresses), "file")
    except json.JSONDecodeError as e:
        return StateLoadResult("corrupt", _empty_snapshot(), "file", _safe_error_message(e))
    except OSError as e:
        return StateLoadResult("failed", _empty_snapshot(), "file", _safe_error_message(e))


def load_state(addresses: list[Address] | None = None) -> dict[str, Any]:
    """Load previous outage state as a normalised snapshot."""
    return load_state_result(addresses).state


def save_state(state: dict[str, Any]) -> bool:
    """Save current outage state and report whether persistence succeeded.

    Uses Azure Blob Storage when running in Azure, falls back to local file.
    """
    data = json.dumps(state, indent=2)
    if _blob_state_configured():
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

    try:
        STATE_FILE.write_text(data)
        return True
    except OSError as e:
        print(f"state save failed: {_safe_error_message(e)}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# ntfy notifications
# ---------------------------------------------------------------------------


def send_ntfy(
    title: str,
    message: str,
    *,
    priority: str = "default",
    tags: str = "white_check_mark",
) -> bool:
    """Send a notification to the configured ntfy topic."""
    if not NTFY_TOPIC:
        return False

    url = f"{NTFY_SERVER}/{NTFY_TOPIC}"
    headers: dict[str, str] = {
        "Title": title,
        "Priority": priority,
        "Tags": tags,
    }
    if STATUS_PAGE_URL:
        headers["Actions"] = f"view, View Status Page, {STATUS_PAGE_URL}"

    try:
        resp = niquests.post(url, data=message.encode(), headers=headers, timeout=10)
        resp.raise_for_status()
        return True
    except niquests.RequestException as e:
        print(f"ntfy error: {_safe_error_message(e)}", file=sys.stderr)
        return False


def _update_state(
    results: list[tuple[Address, OutageStatus]],
    previous: dict[str, Any],
) -> dict[str, Any]:
    """Build new state with timestamps from results and previous state."""
    now = datetime.now(tz=UTC).isoformat()
    snapshot = _normalise_state(previous, [addr for addr, _ in results])
    snapshot["schema_version"] = _STATE_SCHEMA_VERSION
    snapshot["generated_at"] = now
    snapshot["poll"] = {
        "started_at": now,
        "completed_at": now,
        "success_count": sum(1 for _, status in results if not status.error),
        "error_count": sum(1 for _, status in results if status.error),
    }
    addresses_state: dict[str, Any] = snapshot.setdefault("addresses", {})

    for addr, status in results:
        existing = addresses_state.get(addr.loc_id)
        entry = existing if isinstance(existing, dict) else {}
        old_status = _entry_status(entry)
        old_period = entry.get("current_period")
        if not isinstance(old_period, dict):
            old_period = {}

        entry["label"] = addr.label

        if status.error:
            entry["last_error"] = {
                "checked_at": _iso_from_timestamp(status.checked_at),
                "category": "request",
                "message": status.error,
            }
            entry["consecutive_error_count"] = int(entry.get("consecutive_error_count", 0)) + 1
            addresses_state[addr.loc_id] = entry
            print(f"poll outcome label={addr.label} result=error")
            continue

        checked_at = _iso_from_timestamp(status.checked_at)
        entry["last_success"] = _success_record(status)
        entry["last_checked"] = checked_at
        entry["status"] = status.display_outage
        entry["last_error"] = None
        entry["consecutive_error_count"] = 0

        old_period_status = str(old_period.get("display_outage", ""))
        if old_status != status.display_outage or old_period_status != status.display_outage:
            entry["current_period"] = {
                "display_outage": status.display_outage,
                "started_at": _status_started_at(status, checked_at),
                "started_at_source": _status_started_at_source(status),
            }
        elif "current_period" not in entry:
            entry["current_period"] = {
                "display_outage": status.display_outage,
                "started_at": checked_at,
                "started_at_source": "observed",
            }

        if status.is_outage:
            period = entry.get("current_period")
            if isinstance(period, dict):
                entry["since"] = period.get("started_at", checked_at)
        else:
            entry.pop("since", None)

        addresses_state[addr.loc_id] = entry
        print(f"poll outcome label={addr.label} result=success status={status.display_outage}")

    return snapshot


def notify_changes(
    results: list[tuple[Address, OutageStatus]],
    previous: dict[str, Any],
    *,
    previous_loaded: bool = True,
) -> dict[str, Any]:
    """Compare results with previous state, notify on changes, return new state."""
    previous_snapshot = _normalise_state(previous, [addr for addr, _ in results])
    new_state = _update_state(results, previous)

    if not previous_loaded:
        print("notification decisions skipped: previous state was not loaded")
        return new_state

    # Determine transitions for notify=True addresses
    started: list[tuple[Address, OutageStatus]] = []
    resolved: list[tuple[Address, str]] = []

    for addr, status in results:
        if not addr.notify:
            continue
        if status.error:
            print(f"notification skipped label={addr.label} reason=poll_error")
            continue

        old_entry = _snapshot_entry(previous_snapshot, addr.loc_id)
        old_status = _entry_status(old_entry)

        if status.is_outage and not _was_outage(old_status):
            started.append((addr, status))
        elif not status.is_outage and _was_outage(old_status):
            old_since = _entry_since(old_entry)
            duration_str = ""
            if old_since:
                try:
                    since_dt = datetime.fromisoformat(old_since)
                    secs = (datetime.now(tz=UTC) - since_dt).total_seconds()
                    duration_str = _format_duration(secs)
                except (ValueError, TypeError):
                    pass
            resolved.append((addr, duration_str))

    # Send at most ONE outage alert per poll cycle
    if started:
        all_affected = [(a, s) for a, s in results if s.is_outage]
        total = len(results)
        compare_down = any(a.compare and s.is_outage for a, s in results)
        notify_down_count = sum(1 for a, s in results if a.notify and s.is_outage)
        other_down_count = len(all_affected) - notify_down_count

        lines = [f"{a.label}: {s.label}" for a, s in started]
        msg = "\n".join(lines)

        if compare_down:
            msg += "\n(area-wide, neighbour also affected)"
        elif other_down_count > 0:
            msg += f"\n(widespread, {len(all_affected)} of {total} addresses affected)"
        else:
            msg += "\n(may be localised)"

        send_ntfy(
            title="NBN Outage Alert",
            message=msg,
            priority="high",
            tags="rotating_light",
        )

    # Send at most ONE resolution alert per poll cycle
    if resolved:
        lines = []
        for addr, dur in resolved:
            line = f"{addr.label}: service restored"
            if dur:
                line += f" after {dur}"
            lines.append(line)
        send_ntfy(
            title="NBN Outage Resolved",
            message="\n".join(lines),
            priority="default",
            tags="white_check_mark",
        )

    return new_state


def _snapshot_entry(snapshot: dict[str, Any], loc_id: str) -> dict[str, Any]:
    if _is_snapshot(snapshot):
        addresses = snapshot.get("addresses", {})
        if isinstance(addresses, dict):
            entry = addresses.get(loc_id, {})
            return entry if isinstance(entry, dict) else {}
        return {}
    entry = snapshot.get(loc_id, {})
    if isinstance(entry, str):
        return {"status": entry}
    return entry if isinstance(entry, dict) else {}


def _entry_status(entry: dict[str, Any]) -> str:
    last_success = entry.get("last_success")
    if isinstance(last_success, dict):
        return str(last_success.get("display_outage", ""))
    return str(entry.get("status", ""))


def _entry_since(entry: dict[str, Any]) -> str:
    period = entry.get("current_period")
    if isinstance(period, dict):
        return str(period.get("started_at", "") or "")
    return str(entry.get("since", "") or "")


def _status_started_at(status: OutageStatus, fallback: str) -> str:
    timing = _status_timing(status.raw)
    started_at = timing.get("started_at")
    return started_at if started_at else fallback


def _status_started_at_source(status: OutageStatus) -> str:
    timing = _status_timing(status.raw)
    return "nbn" if timing.get("started_at") else "observed"


def _status_timing(raw: dict[str, Any]) -> dict[str, str]:
    """Extract useful timing fields from known NBN payload shapes."""
    planned = raw.get("plannedOutages")
    if isinstance(planned, dict):
        primary = planned.get("primary")
        if isinstance(primary, dict):
            started_at = primary.get("maintenanceStartTime") or primary.get("interruptionStartTime")
            ended_at = primary.get("maintenanceEndTime")
            result: dict[str, str] = {}
            if isinstance(started_at, str) and started_at:
                result["started_at"] = started_at
            if isinstance(ended_at, str) and ended_at:
                result["ended_at"] = ended_at
            if result:
                return result
    return {}


def _was_outage(display_outage: str) -> bool:
    return display_outage not in ("NO_OUTAGE", "")


def _format_duration(seconds: float) -> str:
    """Format a duration in seconds as a human-readable string, e.g. '2h 15m'."""
    total_minutes = int(seconds) // 60
    hours = total_minutes // 60
    minutes = total_minutes % 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


# ---------------------------------------------------------------------------
# HTML status page
# ---------------------------------------------------------------------------

_FAVICON_B64 = (  # noqa: E501
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAADKklEQVR4nLWXz2scZRjHP88zM8luZoxvoxJKA178gdYWRS/eRfAagoKNCv7osQf9V3qxIGKt4CHoSdG7JxHR4sVbwWBbgsna7CbZnZnn8TCbLNptnMxmvpddntmd73fe5/O8zCuAAA4QQnhHVS+7+0UgHV87sRQYmHFubo5XQ+BOnvv3OzuDRORmpHpte3v7s/FPRQBdWVmZHwwG10VkDcDdm/hW5iLsYzzT6fLlk0/xeKeDAJ/cvctHt27RUcXcN9I0fXtzc3OogPf7/RuquubuhbtbY3dAzDn4e8iHy2d5Il1gO8/ZLUs+WF7m5Syz3aIoYtW1fr9/A/A4hHBJVVfNLAeSWcwxhzSh+8I5zugC+ahAVBiZ4VHEw3GsJaib5aq6GkK4pMAVKgaimcwjwfcKum8+zeKnr/D162dJBiVLErHc6fDLYMAP9+6RRRFWeTlwJQYuuLvQELj/ysMc6c6Iby52eff9R3nt+h/cvj3g6tYdds3oimCgVJxdiEWk09hNGHMM6PizcFyF7sj59sWULz7+k/z3LdKH5g/NJ38X6cSNzQFKxw+K6vucTkIBLrC474BSJAkugk2ZruYBzJE0IX7+MSQSrD+i+HnrX40sFUqgdH9gf5sFiATv5yxcfo7ue8/i/RzM2XnjO3xok7bUkDYKMJYszuEHRRUgEiRNqlE8Ac71V2AacKVXNZVqqOzkO2j9AMcAN4vqBagBXHsBThG4aaoN4WkAN03TV6Al4OoHaAm4egFaBO7/A7QM3DRNhbAt4GoHaAu4+gFa6nf9AO09cM0AkVTLXo6TqFQtaVo7RvePoYPvFUiWHK2E7+X4sKhgHN+4Ts1Lh/3i2P1WQgj7R++FAhSOnJknPr+EqGC9EcWvW8hSh/j8I4hygtqQ4re/Hji+7n4gIYQfVfUlr45DehjiaCdUQRbi5rXu1M3WRETM7CcJIayr6ufuPjmYCJPeHY7hLLX7lYtIYmZvybgNG1EUrZpZQdWxmV7VjpEBpqpxWZZf9Xq9NQUky7J1M9sQkVhE2jJHRFREYjPbyLJsnTEdp348nyIHBiJy08yu9Xq9o+P5Pz2rxc0q3wMCAAAAAElFTkSuQmCC"
)

_APPLE_ICON_B64 = (  # noqa: E501
    "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"
)

_COLOURS: dict[str, dict[str, str]] = {
    "green": {"light": "#22c55e"},
    "red": {
        "light": "#ef4444",
        "tag_text": "#fca5a5",
        "tag_bg": "#991b1b33",
        "tag_border": "#991b1b",
    },
    "amber": {
        "light": "#f59e0b",
        "tag_text": "#fcd34d",
        "tag_bg": "#92400e33",
        "tag_border": "#92400e",
    },
    "grey": {
        "light": "#9ca3af",
        "tag_text": "#9ca3af",
        "tag_bg": "#37415133",
        "tag_border": "#374151",
    },
}


def generate_html(
    results: list[tuple[Address, OutageStatus]],
    state: dict[str, Any] | None = None,
    *,
    warning: str = "",
) -> str:
    """Generate a self-contained HTML status page."""
    cards = ""
    for addr, status in results:
        c = _COLOURS.get(status.colour, _COLOURS["grey"])
        tag = ""
        if status.is_outage or status.error:
            label = html.escape(status.error if status.error else status.label)
            since_text = ""
            if status.is_outage and not status.error and state:
                since_value = _entry_since(_snapshot_entry(state, addr.loc_id))
                try:
                    since_dt = datetime.fromisoformat(since_value).astimezone()
                    since_text = " (since " + since_dt.strftime("%-I:%M%p").lower() + ")"
                except (ValueError, TypeError):
                    pass
            tag = (
                f'<div class="tag" style="background:{c["tag_bg"]};'
                f'color:{c["tag_text"]};border:1px solid {c["tag_border"]}">'
                f"{label}{since_text}</div>"
            )
        escaped_label = html.escape(addr.label)
        cards += f"""
        <div class="card">
            <div class="light" style="background:{c["light"]};color:{c["light"]}"></div>
            <div class="label">{escaped_label}</div>
            {tag}
        </div>"""

    timestamp_ms = int(max((s.checked_at for _, s in results), default=time.time()) * 1000)
    warning_html = f'<div class="warning">{html.escape(warning)}</div>' if warning else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#111111">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="icon" href="data:image/png;base64,{_FAVICON_B64}">
<link rel="apple-touch-icon" href="data:image/png;base64,{_FAVICON_B64}">
<title>NBN Status</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box }}
body {{ font-family:-apple-system,system-ui,sans-serif; background:#111; color:#e5e5e5;
       display:flex; flex-direction:column; align-items:center;
       padding:2rem 1rem; padding-top:max(2rem, env(safe-area-inset-top)) }}
h1 {{ margin-bottom:1.5rem; font-weight:400; color:#a3a3a3; font-size:1.1rem }}
.card {{ display:flex; align-items:center; gap:0.75rem; background:#1a1a1a;
         border-radius:12px; padding:1rem 1.25rem; margin-bottom:0.75rem;
         width:100%; max-width:420px; flex-wrap:wrap }}
.light {{ width:28px; height:28px; border-radius:50%; flex-shrink:0;
          box-shadow:0 0 12px currentColor }}
.label {{ font-weight:600; font-size:1rem; flex:1 1 0; min-width:0;
          overflow:hidden; text-overflow:ellipsis; white-space:nowrap }}
.tag {{ font-size:0.7rem; font-weight:600; padding:3px 10px; border-radius:999px;
         white-space:nowrap; flex-shrink:0; overflow:hidden; text-overflow:ellipsis }}
.warning {{ width:100%; max-width:420px; margin-bottom:1rem; padding:0.75rem 1rem;
            border-radius:12px; background:#451a0333; color:#fcd34d;
            border:1px solid #92400e; font-size:0.85rem }}
@media (max-width:420px) {{
  .card {{ gap:0.5rem }}
  .tag {{ flex-basis:100%; margin-left:calc(28px + 0.5rem);
          max-width:calc(100% - 28px - 0.5rem) }}
}}
#footer {{ margin-top:1.5rem; font-size:0.75rem; color:#525252 }}
</style>
</head>
<body>
<h1>NBN Status Monitor</h1>
{warning_html}
{cards}
<div id="footer"></div>
<script>
(function(){{
  var u=new Date({timestamp_ms}),el=document.getElementById('footer');
  var refreshing=false;
  function t(){{
    if(refreshing) return;
    var r=Math.max(0,60-Math.floor((Date.now()-u)/1e3));
    el.textContent='Last updated '+u.toLocaleTimeString()+', refreshing in '+r+'s';
    if(!r){{
      refreshing=true;
      el.textContent='Refreshing\u2026';
      fetch(location.href).then(function(r){{return r.text()}}).then(function(h){{
        var d=new DOMParser().parseFromString(h,'text/html');
        document.body.innerHTML=d.body.innerHTML;
        var s=d.querySelectorAll('script');
        s.forEach(function(x){{
          var n=document.createElement('script');
          n.textContent=x.textContent;
          document.body.appendChild(n)
        }})
      }}).catch(function(){{location.reload()}})
    }}
  }}
  t();setInterval(t,1000)
}})()
</script>
</body>
</html>"""


def results_from_state(
    addresses: list[Address],
    state: dict[str, Any],
) -> list[tuple[Address, OutageStatus]]:
    """Build display results from the authoritative state snapshot."""
    snapshot = _normalise_state(state, addresses)
    results: list[tuple[Address, OutageStatus]] = []
    for addr in addresses:
        entry = _snapshot_entry(snapshot, addr.loc_id)
        last_success = entry.get("last_success")
        if isinstance(last_success, dict):
            display = str(last_success.get("display_outage", ""))
            label = str(last_success.get("label", OUTAGE_LABELS.get(display, display)))
            checked_at = _timestamp_from_iso(last_success.get("checked_at"))
            results.append(
                (
                    addr,
                    OutageStatus(
                        loc_id=addr.loc_id,
                        display_outage=display,
                        label=label,
                        checked_at=checked_at,
                    ),
                )
            )
            continue

        results.append(
            (
                addr,
                OutageStatus(
                    loc_id=addr.loc_id,
                    display_outage="",
                    label="No data",
                    error="No status snapshot yet",
                    checked_at=time.time(),
                ),
            )
        )
    return results


def generate_snapshot_html(addresses: list[Address], load_result: StateLoadResult) -> str:
    """Generate the status page from stored state without polling NBN."""
    warning = ""
    if load_result.status in ("failed", "corrupt"):
        warning = "Status snapshot is unavailable; showing degraded state."
    elif load_result.status == "missing":
        warning = "No status snapshot has been written yet."

    results = results_from_state(addresses, load_result.state)
    return generate_html(results, state=load_result.state, warning=warning)


# ---------------------------------------------------------------------------
# HTTP server (local)
# ---------------------------------------------------------------------------


def make_handler(addresses: list[Address]) -> type[BaseHTTPRequestHandler]:
    """Create a request handler that serves the stored status snapshot."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state_result = load_state_result(addresses)
            html = generate_snapshot_html(addresses, state_result)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html.encode())
            except BrokenPipeError:
                pass

        def log_message(self, fmt: str, *args: Any) -> None:
            # Quieter logging
            print(f"  {args[0]}", file=sys.stderr)

    return Handler


def serve(addresses: list[Address], port: int = 8000) -> None:
    """Start a local HTTP server serving the status page."""
    handler = make_handler(addresses)
    server = HTTPServer(("", port), handler)
    print(f"Status page: http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def poll(addresses: list[Address], *, notify: bool = False) -> list[tuple[Address, OutageStatus]]:
    """Poll addresses and optionally send notifications."""
    results = check_all(addresses)

    for addr, status in results:
        symbol = {"green": "✅", "red": "🔴", "amber": "🟡", "grey": "⚪"}.get(status.colour, "?")
        print(f"  {symbol} {addr.label}: {status.label}")

    if notify:
        state_result = load_state_result(addresses)
        if state_result.status in ("failed", "corrupt"):
            print(
                f"state load {state_result.status}: {state_result.error}; skipping save",
                file=sys.stderr,
            )
        else:
            new_state = notify_changes(
                results,
                state_result.state,
                previous_loaded=state_result.can_make_notification_decisions,
            )
            save_state(new_state)

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="NBN outage monitor")
    parser.add_argument("--notify", action="store_true", help="Send ntfy on status changes")
    parser.add_argument("--serve", action="store_true", help="Serve status page on localhost")
    parser.add_argument("--port", type=int, default=8000, help="Port for status page server")
    args = parser.parse_args()

    addresses = load_addresses()

    if args.serve:
        serve(addresses, port=args.port)
    else:
        poll(addresses, notify=args.notify)


if __name__ == "__main__":
    main()
