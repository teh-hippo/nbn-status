"""HTML status page rendering.

Renders the traffic-light status page from a poll result list (live mode)
or directly from the stored snapshot (cold mode, used by both the local
HTTP server and the Azure HTTP route).

The favicon is kept as a base64 string in this module so the package
ships as pure Python with no package-data plumbing on the deployment side.
"""

from __future__ import annotations

import html
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .api import (
    OUTAGE_LABELS,
    display_outage_colour,
    display_outage_is_outage,
    display_outage_is_service_issue,
)
from .planned import (
    event_local_start,
    event_start,
    format_estimated_duration,
    format_event_date,
    format_event_end,
    format_event_time,
    upcoming_events,
    visible_events,
)
from .snapshot import StateLoadResult, _timestamp_from_iso

if TYPE_CHECKING:
    from .config import Address
    from .snapshot import AddressEntry, PlannedMaintenance, Snapshot


_FAVICON_B64 = (  # noqa: E501
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAADKklEQVR4nLWXz2scZRjHP88zM8luZoxvoxJKA178gdYWRS/eRfAagoKNCv7osQf9V3qxIGKt4CHoSdG7JxHR4sVbwWBbgsna7CbZnZnn8TCbLNptnMxmvpddntmd73fe5/O8zCuAAA4QQnhHVS+7+0UgHV87sRQYmHFubo5XQ+BOnvv3OzuDRORmpHpte3v7s/FPRQBdWVmZHwwG10VkDcDdm/hW5iLsYzzT6fLlk0/xeKeDAJ/cvctHt27RUcXcN9I0fXtzc3OogPf7/RuquubuhbtbY3dAzDn4e8iHy2d5Il1gO8/ZLUs+WF7m5Syz3aIoYtW1fr9/A/A4hHBJVVfNLAeSWcwxhzSh+8I5zugC+ahAVBiZ4VHEw3GsJaib5aq6GkK4pMAVKgaimcwjwfcKum8+zeKnr/D162dJBiVLErHc6fDLYMAP9+6RRRFWeTlwJQYuuLvQELj/ysMc6c6Iby52eff9R3nt+h/cvj3g6tYdds3oimCgVJxdiEWk09hNGHMM6PizcFyF7sj59sWULz7+k/z3LdKH5g/NJ38X6cSNzQFKxw+K6vucTkIBLrC474BSJAkugk2ZruYBzJE0IX7+MSQSrD+i+HnrX40sFUqgdH9gf5sFiATv5yxcfo7ue8/i/RzM2XnjO3xok7bUkDYKMJYszuEHRRUgEiRNqlE8Ac71V2AacKVXNZVqqOzkO2j9AMcAN4vqBagBXHsBThG4aaoN4WkAN03TV6Al4OoHaAm4egFaBO7/A7QM3DRNhbAt4GoHaAu4+gFa6nf9AO09cM0AkVTLXo6TqFQtaVo7RvePoYPvFUiWHK2E7+X4sKhgHN+4Ts1Lh/3i2P1WQgj7R++FAhSOnJknPr+EqGC9EcWvW8hSh/j8I4hygtqQ4re/Hji+7n4gIYQfVfUlr45DehjiaCdUQRbi5rXu1M3WRETM7CcJIayr6ufuPjmYCJPeHY7hLLX7lYtIYmZvybgNG1EUrZpZQdWxmV7VjpEBpqpxWZZf9Xq9NQUky7J1M9sQkVhE2jJHRFREYjPbyLJsnTEdp348nyIHBiJy08yu9Xq9o+P5Pz2rxc0q3wMCAAAAAElFTkSuQmCC"
)


_STATUS_COLOURS = {"green", "red", "amber", "grey"}


def generate_html(
    addresses: list[Address],
    snapshot: Snapshot,
    *,
    warning: str = "",
    now: datetime | None = None,
) -> str:
    """Generate a self-contained HTML status page from the snapshot.

    ``now`` overrides the reference time used to describe how long ago an
    outage started; it defaults to the current time and exists so callers
    (and tests) can render against a fixed clock.
    """
    reference = now or datetime.now(tz=UTC)
    cards: list[str] = []
    checked_ats: list[float] = []
    for addr in addresses:
        entry = snapshot.entry(addr.loc_id)
        last_success = entry.last_success if entry else None
        if last_success is None:
            display_outage = ""
            error: str | None = "No status snapshot yet"
            tag_label = ""
            checked_ats.append(time.time())
        else:
            display_outage = last_success.display_outage
            error = None
            tag_label = last_success.label or OUTAGE_LABELS.get(display_outage, display_outage)
            checked_ats.append(_timestamp_from_iso(last_success.checked_at))

        colour = display_outage_colour(display_outage, error=bool(error))
        if colour not in _STATUS_COLOURS:
            colour = "grey"

        is_outage = display_outage_is_outage(display_outage)
        tag = ""
        if is_outage or error:
            text = html.escape(error if error else tag_label)
            since_text = _format_since(entry, display_outage, reference)
            tag = f'<div class="status-tag">{text}{since_text}</div>'

        escaped_label = html.escape(addr.label)
        status_text = html.escape(error if error else (tag_label or "No outage"))
        maintenance = _maintenance_html(
            entry.planned_maintenance if entry else [],
            reference,
        )
        cards.append(
            f"""
        <section class="card status-{colour}">
          <div class="status-row">
            <div class="light" aria-hidden="true"></div>
            <div class="label">{escaped_label}</div>
            <span class="sr-only">Current status: {status_text}</span>
            {tag}
          </div>
          {maintenance}
        </section>"""
        )

    timestamp_ms = int(max(checked_ats, default=time.time()) * 1000)
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
* {{ box-sizing:border-box }}
:root {{ color-scheme:dark }}
body {{ margin:0; min-height:100vh; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",
       sans-serif; background:#101010; color:#ededed; padding:2rem 1rem;
       padding-top:max(2rem, env(safe-area-inset-top)) }}
main {{ width:100%; display:flex; flex-direction:column; align-items:center }}
h1 {{ margin:0 0 1.5rem; font-size:1.1rem; font-weight:500; color:#b3b3b3 }}
.cards {{ width:100%; display:flex; flex-direction:column; align-items:center; gap:0.75rem }}
.card {{ --status:#a3aab5; --tag-text:#cbd5e1; --tag-bg:#1f293780;
         --tag-border:#475569; width:min(100%,560px); padding:1rem 1.1rem;
         border:1px solid #252525; border-radius:14px; background:#1a1a1a }}
.status-green {{ --status:#22c55e }}
.status-red {{ --status:#ff4d55; --tag-text:#fda4af; --tag-bg:#7f1d1d40;
               --tag-border:#b91c1c }}
.status-amber {{ --status:#f59e0b; --tag-text:#fcd34d; --tag-bg:#78350f40;
                 --tag-border:#a16207 }}
.status-grey {{ --status:#a3aab5; --tag-text:#cbd5e1; --tag-bg:#1f293780;
                --tag-border:#475569 }}
.status-row {{ display:grid; grid-template-columns:28px minmax(0,1fr) auto;
               align-items:center; gap:0.75rem }}
.light {{ width:28px; height:28px; border-radius:50%; background:var(--status);
          box-shadow:0 0 12px var(--status) }}
.label {{ min-width:0; font-size:1rem; font-weight:650; line-height:1.3;
          overflow-wrap:anywhere }}
.status-tag {{ max-width:100%; padding:3px 10px; border:1px solid var(--tag-border);
               border-radius:999px; background:var(--tag-bg); color:var(--tag-text);
               font-size:0.72rem; font-weight:650; line-height:1.2 }}
.maintenance {{ margin:0.9rem 0 0 calc(28px + 0.75rem); border:1px solid #303030;
                border-radius:10px; background:#151515 }}
.maintenance summary {{ padding:0.75rem 0.85rem; cursor:pointer; color:#d4d4d4 }}
.maintenance summary:focus-visible {{ outline:2px solid var(--status); outline-offset:3px;
                                     border-radius:8px }}
.maintenance-summary {{ display:grid; grid-template-columns:minmax(0,1fr) auto;
                        gap:0.35rem 0.75rem; align-items:center }}
.maintenance-title {{ font-size:0.76rem; font-weight:700; color:#d4d4d4;
                      text-transform:uppercase; letter-spacing:0.04em }}
.maintenance-count {{ grid-row:1 / span 2; grid-column:2; color:#a3a3a3;
                      font-size:0.72rem; white-space:nowrap }}
.maintenance-next {{ font-size:0.84rem; line-height:1.35; color:#f5f5f5 }}
.maintenance-list {{ border-top:1px solid #303030 }}
.maintenance-window {{ padding:0.55rem 0.85rem; background:#181818; color:#a3a3a3;
                       font-size:0.72rem; font-weight:650 }}
.maintenance-row {{ display:grid; grid-template-columns:minmax(8rem,0.8fr) minmax(0,1.4fr);
                    gap:0.25rem 0.75rem; padding:0.75rem 0.85rem }}
.maintenance-row + .maintenance-row,
.maintenance-window + .maintenance-row {{ border-top:1px solid #282828 }}
.maintenance-when {{ font-size:0.8rem; font-weight:650; color:#e5e5e5 }}
.maintenance-duration {{ font-size:0.8rem; color:#d4d4d4 }}
.maintenance-meta {{ grid-column:1 / -1; font-size:0.72rem; color:#a3a3a3 }}
.warning {{ width:min(100%,560px); margin-bottom:1rem; padding:0.75rem 1rem;
            border:1px solid #92400e; border-radius:12px; background:#451a0333;
            color:#fcd34d; font-size:0.85rem }}
.sr-only {{ position:absolute; width:1px; height:1px; padding:0; margin:-1px;
            overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0 }}
#footer {{ margin-top:1.5rem; color:#737373; font-size:0.75rem }}
@media (max-width:520px) {{
  body {{ padding:1.5rem 0.75rem }}
  .card {{ padding:0.9rem }}
  .status-row {{ grid-template-columns:28px minmax(0,1fr); gap:0.55rem 0.65rem }}
  .status-tag {{ grid-column:2; justify-self:start }}
  .maintenance {{ margin-left:calc(28px + 0.65rem) }}
  .maintenance-row {{ grid-template-columns:1fr }}
  .maintenance-meta {{ grid-column:auto }}
}}
</style>
</head>
<body>
<main>
  <h1>NBN Status Monitor</h1>
  {warning_html}
  <div class="cards">{"".join(cards)}</div>
  <div id="footer"></div>
</main>
<script>
(function(){{
  var snapshotAt=new Date({timestamp_ms}),refreshAt=Date.now()+60000,
      el=document.getElementById('footer');
  function t(){{
    var r=Math.max(0,Math.ceil((refreshAt-Date.now())/1000));
    el.textContent=r
      ? 'Last updated '+snapshotAt.toLocaleTimeString()+', refreshing in '+r+'s'
      : 'Refreshing...';
  }}
  t();setInterval(t,1000);setTimeout(function(){{location.reload()}},60000)
}})()
</script>
</body>
</html>"""


def _format_since(
    entry: AddressEntry | None,
    display_outage: str,
    now: datetime,
) -> str:
    if entry is None or display_outage.startswith("PLANNED"):
        return ""
    since_value = (
        entry.service_issue.started_at
        if display_outage_is_service_issue(display_outage) and entry.service_issue is not None
        else entry.since
    )
    try:
        since_dt = datetime.fromisoformat(since_value)
    except (ValueError, TypeError):
        return ""
    if since_dt.tzinfo is None:
        return ""

    zone = _entry_zone(entry)
    if zone is not None:
        since_dt = since_dt.astimezone(zone)
        reference = now.astimezone(zone)
    else:
        reference = now.astimezone(since_dt.tzinfo)
    delta_days = (reference.date() - since_dt.date()).days
    time_str = since_dt.strftime("%-I:%M%p").lower()
    if delta_days == 0:
        return f" (since {time_str})"
    if delta_days == 1:
        return f" (since yesterday {time_str})"
    if delta_days < 7:
        return f" (since {since_dt.strftime('%a')} {time_str})"
    return f" (since {since_dt.strftime('%-d %b')}, {delta_days} days ago)"


def _entry_zone(entry: AddressEntry) -> ZoneInfo | None:
    time_zone = entry.time_zone
    if not time_zone and entry.planned_maintenance:
        time_zone = entry.planned_maintenance[0].time_zone
    if not time_zone:
        return None
    try:
        return ZoneInfo(time_zone)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _maintenance_html(events: list[PlannedMaintenance], now: datetime) -> str:
    current_and_future = visible_events(events, now)
    if not current_and_future:
        return ""

    upcoming = upcoming_events(current_and_future, now)
    today_events = [
        event
        for event in current_and_future
        if (local := event_local_start(event)) is not None
        and local.date() == now.astimezone(local.tzinfo).date()
    ]
    next_event = today_events[0] if today_events else upcoming[0]
    local_start = event_local_start(next_event)
    is_today = bool(local_start and local_start.date() == now.astimezone(local_start.tzinfo).date())
    next_date = "Today" if is_today else format_event_date(next_event)
    next_text = (
        f"{next_date} from {format_event_time(next_event)}, "
        f"estimated interruption {format_estimated_duration(next_event.duration_minutes)}"
    )
    count_text = (
        "1 interruption"
        if len(current_and_future) == 1
        else f"{len(current_and_future)} interruptions"
    )
    rows = _maintenance_rows_html(current_and_future)
    return f"""
    <details class="maintenance">
      <summary>
        <span class="maintenance-summary">
          <span class="maintenance-title">Planned maintenance</span>
          <span class="maintenance-next">{html.escape(next_text)}</span>
          <span class="maintenance-count">{count_text}</span>
        </span>
      </summary>
      <div class="maintenance-list">{rows}</div>
    </details>"""


def _maintenance_rows_html(events: list[PlannedMaintenance]) -> str:
    rows: list[str] = []
    previous_end = ""
    for event in events:
        end_text = format_event_end(event)
        if end_text and end_text != previous_end:
            rows.append(
                '<div class="maintenance-window">'
                f"Work scheduled through {html.escape(end_text)}"
                "</div>"
            )
        rows.append(_maintenance_row_html(event))
        previous_end = end_text
    return "".join(rows)


def _maintenance_row_html(event: PlannedMaintenance) -> str:
    starts_at = event_start(event)
    date_text = html.escape(format_event_date(event))
    time_text = html.escape(format_event_time(event))
    duration = html.escape(format_estimated_duration(event.duration_minutes))
    meta = (
        '<div class="maintenance-meta">Planned power work</div>'
        if event.planned_power_outage
        else ""
    )
    datetime_attr = html.escape(starts_at.isoformat() if starts_at else event.starts_at)
    return f"""
      <div class="maintenance-row">
        <time class="maintenance-when" datetime="{datetime_attr}">
          {date_text} from {time_text}
        </time>
        <div class="maintenance-duration">Estimated interruption {duration}</div>
        {meta}
      </div>"""


def generate_snapshot_html(addresses: list[Address], load_result: StateLoadResult) -> str:
    """Generate the status page from stored state without polling NBN."""
    warning = ""
    if load_result.status in ("failed", "corrupt"):
        warning = "Status snapshot is unavailable; showing degraded state."
    elif load_result.status == "missing":
        warning = "No status snapshot has been written yet."

    return generate_html(addresses, load_result.snapshot, warning=warning)
