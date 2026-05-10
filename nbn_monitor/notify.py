"""Notification side-effects.

Owns ``send_ntfy`` (the ntfy push transport) and ``notify_changes`` (the
decision lane that compares new poll results against the previous snapshot
to decide whether to push alerts). Snapshot derivation lives in
``nbn_monitor.derive``; this module is concerned only with side-effects.

The ntfy endpoint is injected as a ``NtfyConfig`` value object; this module
does not read environment variables directly.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import niquests

from .api import display_outage_is_outage
from .config import Address, _safe_error_message

if TYPE_CHECKING:
    from .api import OutageStatus
    from .config import NtfyConfig
    from .snapshot import Snapshot


def send_ntfy(
    ntfy: NtfyConfig,
    title: str,
    message: str,
    *,
    priority: str = "default",
    tags: str = "white_check_mark",
) -> bool:
    """Send a notification to the configured ntfy topic."""
    if not ntfy.topic:
        return False

    url = f"{ntfy.server}/{ntfy.topic}"
    headers: dict[str, str] = {
        "Title": title,
        "Priority": priority,
        "Tags": tags,
    }
    if ntfy.status_page_url:
        headers["Actions"] = f"view, View Status Page, {ntfy.status_page_url}"

    try:
        resp = niquests.post(url, data=message.encode(), headers=headers, timeout=10)
        resp.raise_for_status()
        return True
    except niquests.RequestException as e:
        print(f"ntfy error: {_safe_error_message(e)}", file=sys.stderr)
        return False


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


def notify_changes(
    results: list[tuple[Address, OutageStatus]],
    previous: Snapshot,
    *,
    previous_loaded: bool = True,
    ntfy: NtfyConfig,
) -> None:
    """Compare results against the prior snapshot and emit ntfy notifications.

    Pure side-effects: snapshot derivation lives in ``derive.derive_snapshot``.
    If ``previous_loaded`` is ``False`` (the prior state failed to load), all
    notification decisions are skipped to avoid emitting alerts from an
    unreliable baseline.
    """
    if not previous_loaded:
        print("notification decisions skipped: previous state was not loaded")
        return

    started: list[tuple[Address, OutageStatus]] = []
    resolved: list[tuple[Address, str]] = []

    for addr, status in results:
        if not addr.notify:
            continue
        if status.error:
            print(f"notification skipped short_id={addr.short_id} reason=poll_error")
            continue

        old_entry = previous.entry(addr.loc_id)
        old_status = old_entry.display_outage if old_entry else ""

        if display_outage_is_outage(status.display_outage) and not _was_outage(old_status):
            started.append((addr, status))
        elif not display_outage_is_outage(status.display_outage) and _was_outage(old_status):
            old_since = old_entry.since if old_entry else ""
            duration_str = ""
            if old_since:
                try:
                    since_dt = datetime.fromisoformat(old_since)
                    secs = (datetime.now(tz=UTC) - since_dt).total_seconds()
                    duration_str = _format_duration(secs)
                except (ValueError, TypeError):
                    pass
            resolved.append((addr, duration_str))

    if started:
        all_affected = [(a, s) for a, s in results if display_outage_is_outage(s.display_outage)]
        total = len(results)
        compare_down = any(
            a.compare and display_outage_is_outage(s.display_outage) for a, s in results
        )
        notify_down_count = sum(
            1 for a, s in results if a.notify and display_outage_is_outage(s.display_outage)
        )
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
            ntfy,
            title="NBN Outage Alert",
            message=msg,
            priority="high",
            tags="rotating_light",
        )

    if resolved:
        lines = []
        for addr, dur in resolved:
            line = f"{addr.label}: service restored"
            if dur:
                line += f" after {dur}"
            lines.append(line)
        send_ntfy(
            ntfy,
            title="NBN Outage Resolved",
            message="\n".join(lines),
            priority="default",
            tags="white_check_mark",
        )
