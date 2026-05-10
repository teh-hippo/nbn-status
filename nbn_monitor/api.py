"""NBN maintenance API client.

Exposes the ``OutageStatus`` value type, the human-readable label table,
``check_outage`` for a single LOC id (with transport-only retry), and
``check_all`` for fanning out across many addresses in parallel.
"""

from __future__ import annotations

import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import niquests

from .config import NBN_BASE, NBN_HEADERS, Address, _safe_error_message

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


def display_outage_colour(display_outage: str, *, error: bool = False) -> str:
    """Map an NBN ``displayOutage`` value to its traffic-light colour."""
    if error:
        return "grey"
    if display_outage == "NO_OUTAGE":
        return "green"
    if "UNPLANNED" in display_outage:
        return "red"
    if any(w in display_outage for w in ("PLANNED", "DEGRADATION")):
        return "amber"
    return "grey"


def display_outage_is_outage(display_outage: str) -> bool:
    """True if a ``displayOutage`` value indicates an active outage."""
    return display_outage not in ("NO_OUTAGE", "")


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
                display = data.get("displayOutage")
                if not isinstance(display, str) or not display:
                    print(
                        f"nbn result status=missing latency_ms={latency_ms}",
                        file=sys.stderr,
                    )
                    return OutageStatus(
                        loc_id=loc_id,
                        display_outage="",
                        label="Unknown",
                        error="NBN response missing displayOutage",
                        checked_at=time.time(),
                    )
                print(f"nbn result status={display} latency_ms={latency_ms}")
                return OutageStatus(
                    loc_id=loc_id,
                    display_outage=display,
                    label=OUTAGE_LABELS.get(display, display),
                    raw=data,
                    checked_at=time.time(),
                )
            except (
                niquests.ConnectionError,
                niquests.Timeout,
                niquests.exceptions.ChunkedEncodingError,
            ):
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
