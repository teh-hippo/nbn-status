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
from datetime import datetime
from typing import TYPE_CHECKING

from .api import OUTAGE_LABELS, display_outage_colour, display_outage_is_outage
from .snapshot import StateLoadResult, _timestamp_from_iso

if TYPE_CHECKING:
    from .config import Address
    from .snapshot import Snapshot


_FAVICON_B64 = (  # noqa: E501
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAADKklEQVR4nLWXz2scZRjHP88zM8luZoxvoxJKA178gdYWRS/eRfAagoKNCv7osQf9V3qxIGKt4CHoSdG7JxHR4sVbwWBbgsna7CbZnZnn8TCbLNptnMxmvpddntmd73fe5/O8zCuAAA4QQnhHVS+7+0UgHV87sRQYmHFubo5XQ+BOnvv3OzuDRORmpHpte3v7s/FPRQBdWVmZHwwG10VkDcDdm/hW5iLsYzzT6fLlk0/xeKeDAJ/cvctHt27RUcXcN9I0fXtzc3OogPf7/RuquubuhbtbY3dAzDn4e8iHy2d5Il1gO8/ZLUs+WF7m5Syz3aIoYtW1fr9/A/A4hHBJVVfNLAeSWcwxhzSh+8I5zugC+ahAVBiZ4VHEw3GsJaib5aq6GkK4pMAVKgaimcwjwfcKum8+zeKnr/D162dJBiVLErHc6fDLYMAP9+6RRRFWeTlwJQYuuLvQELj/ysMc6c6Iby52eff9R3nt+h/cvj3g6tYdds3oimCgVJxdiEWk09hNGHMM6PizcFyF7sj59sWULz7+k/z3LdKH5g/NJ38X6cSNzQFKxw+K6vucTkIBLrC474BSJAkugk2ZruYBzJE0IX7+MSQSrD+i+HnrX40sFUqgdH9gf5sFiATv5yxcfo7ue8/i/RzM2XnjO3xok7bUkDYKMJYszuEHRRUgEiRNqlE8Ac71V2AacKVXNZVqqOzkO2j9AMcAN4vqBagBXHsBThG4aaoN4WkAN03TV6Al4OoHaAm4egFaBO7/A7QM3DRNhbAt4GoHaAu4+gFa6nf9AO09cM0AkVTLXo6TqFQtaVo7RvePoYPvFUiWHK2E7+X4sKhgHN+4Ts1Lh/3i2P1WQgj7R++FAhSOnJknPr+EqGC9EcWvW8hSh/j8I4hygtqQ4re/Hji+7n4gIYQfVfUlr45DehjiaCdUQRbi5rXu1M3WRETM7CcJIayr6ufuPjmYCJPeHY7hLLX7lYtIYmZvybgNG1EUrZpZQdWxmV7VjpEBpqpxWZZf9Xq9NQUky7J1M9sQkVhE2jJHRFREYjPbyLJsnTEdp348nyIHBiJy08yu9Xq9o+P5Pz2rxc0q3wMCAAAAAElFTkSuQmCC"
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
    cards = ""
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
        c = _COLOURS.get(colour, _COLOURS["grey"])

        is_outage = display_outage_is_outage(display_outage)
        tag = ""
        if is_outage or error:
            text = html.escape(error if error else tag_label)
            since_text = ""
            if is_outage and not error and entry is not None:
                since_value = entry.since
                try:
                    since_dt = datetime.fromisoformat(since_value).astimezone()
                    reference = (
                        now.astimezone(since_dt.tzinfo)
                        if now is not None
                        else datetime.now(tz=since_dt.tzinfo)
                    )
                    delta_days = (reference.date() - since_dt.date()).days
                    time_str = since_dt.strftime("%-I:%M%p").lower()
                    if delta_days == 0:
                        since_text = f" (since {time_str})"
                    elif delta_days == 1:
                        since_text = f" (since yesterday {time_str})"
                    elif delta_days < 7:
                        weekday = since_dt.strftime("%a")
                        since_text = f" (since {weekday} {time_str})"
                    else:
                        date_str = since_dt.strftime("%-d %b")
                        since_text = f" (since {date_str}, {delta_days} days ago)"
                except (ValueError, TypeError):
                    pass
            tag = (
                f'<div class="tag" style="background:{c["tag_bg"]};'
                f'color:{c["tag_text"]};border:1px solid {c["tag_border"]}">'
                f"{text}{since_text}</div>"
            )
        escaped_label = html.escape(addr.label)
        cards += f"""
        <div class="card">
            <div class="light" style="background:{c["light"]};color:{c["light"]}"></div>
            <div class="label">{escaped_label}</div>
            {tag}
        </div>"""

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
  var snapshotAt=new Date({timestamp_ms}),
      pageLoadedAt=Date.now(),
      el=document.getElementById('footer'),
      refreshing=false,
      REFRESH_AFTER_MS=60000;
  function t(){{
    if(refreshing) return;
    var elapsed=Date.now()-pageLoadedAt;
    var r=Math.max(0,Math.floor((REFRESH_AFTER_MS-elapsed)/1000));
    el.textContent='Last updated '+snapshotAt.toLocaleTimeString()+', refreshing in '+r+'s';
    if(elapsed>=REFRESH_AFTER_MS){{
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


def generate_snapshot_html(addresses: list[Address], load_result: StateLoadResult) -> str:
    """Generate the status page from stored state without polling NBN."""
    warning = ""
    if load_result.status in ("failed", "corrupt"):
        warning = "Status snapshot is unavailable; showing degraded state."
    elif load_result.status == "missing":
        warning = "No status snapshot has been written yet."

    return generate_html(addresses, load_result.snapshot, warning=warning)
