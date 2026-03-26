"""NBN outage monitor with ntfy notifications and traffic-light status page."""

from __future__ import annotations

__version__ = "0.1.0"

import argparse
import html
import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

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

NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
STATUS_PAGE_URL = os.environ.get("STATUS_PAGE_URL", "")


@dataclass
class Address:
    label: str
    loc_id: str
    poll: bool = True
    notify: bool = False
    compare: bool = False


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
        resp = session.get(url, headers=NBN_HEADERS, timeout=10)
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
        return OutageStatus(
            loc_id=loc_id,
            display_outage=display,
            label=OUTAGE_LABELS.get(display, display),
            raw=data,
            checked_at=time.time(),
        )
    except niquests.RequestException as e:
        return OutageStatus(
            loc_id=loc_id,
            display_outage="",
            label="Error",
            error=str(e),
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


def _get_blob_client() -> Any | None:
    """Get a BlobClient for the state blob, or None if not configured."""
    conn_str = os.environ.get("AzureWebJobsStorage", "")  # noqa: SIM112
    if not conn_str or conn_str.startswith("UseDevelopment"):
        return None
    try:
        from azure.storage.blob import BlobServiceClient

        service = BlobServiceClient.from_connection_string(conn_str)
        container = service.get_container_client(_BLOB_CONTAINER)
        if not container.exists():
            container.create_container()
        return container.get_blob_client(_BLOB_NAME)
    except Exception:
        return None


def load_state() -> dict[str, Any]:
    """Load previous outage state.

    Uses Azure Blob Storage when running in Azure, falls back to local file.
    Handles legacy format (plain string values) by migrating to the new
    ``{"status": ..., "since": ..., "last_checked": ...}`` structure.
    """
    raw: dict[str, Any] = {}
    blob = _get_blob_client()
    if blob is not None:
        try:
            data = blob.download_blob().readall()
            raw = json.loads(data)
        except Exception:
            return {}
    elif STATE_FILE.exists():
        raw = json.loads(STATE_FILE.read_text())
    else:
        return {}

    for loc_id, value in raw.items():
        if isinstance(value, str):
            raw[loc_id] = {"status": value, "since": "", "last_checked": ""}
    return raw


def save_state(state: dict[str, Any]) -> None:
    """Save current outage state.

    Uses Azure Blob Storage when running in Azure, falls back to local file.
    """
    data = json.dumps(state, indent=2)
    blob = _get_blob_client()
    if blob is not None:
        blob.upload_blob(data, overwrite=True)
    else:
        STATE_FILE.write_text(data)


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
        print(f"ntfy error: {e}", file=sys.stderr)
        return False


def _update_state(
    results: list[tuple[Address, OutageStatus]],
    previous: dict[str, Any],
) -> dict[str, Any]:
    """Build new state with timestamps from results and previous state."""
    now = datetime.now(tz=UTC).isoformat()
    new_state: dict[str, Any] = {}

    for addr, status in results:
        old_entry = previous.get(addr.loc_id, {})
        if isinstance(old_entry, str):
            old_entry = {"status": old_entry}
        old_status = old_entry.get("status", "")
        old_since = old_entry.get("since", "")

        entry: dict[str, Any] = {
            "status": status.display_outage,
            "last_checked": now,
        }

        if status.is_outage:
            if _was_outage(old_status) and old_since:
                entry["since"] = old_since
            else:
                entry["since"] = now

        new_state[addr.loc_id] = entry

    return new_state


def notify_changes(
    results: list[tuple[Address, OutageStatus]],
    previous: dict[str, Any],
) -> dict[str, Any]:
    """Compare results with previous state, notify on changes, return new state."""
    new_state = _update_state(results, previous)

    # Determine transitions for notify=True addresses
    started: list[tuple[Address, OutageStatus]] = []
    resolved: list[tuple[Address, str]] = []

    for addr, status in results:
        if not addr.notify:
            continue
        old_entry = previous.get(addr.loc_id, {})
        if isinstance(old_entry, str):
            old_entry = {"status": old_entry}
        old_status = old_entry.get("status", "")

        if status.is_outage and not _was_outage(old_status):
            started.append((addr, status))
        elif not status.is_outage and _was_outage(old_status):
            old_since = old_entry.get("since")
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
                loc_entry = state.get(addr.loc_id)
                if isinstance(loc_entry, dict) and "since" in loc_entry:
                    try:
                        since_dt = datetime.fromisoformat(loc_entry["since"]).astimezone()
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
.card {{ display:flex; align-items:center; gap:1rem; background:#1a1a1a;
         border-radius:12px; padding:1rem 1.5rem; margin-bottom:0.75rem;
         width:100%; max-width:420px }}
.light {{ width:28px; height:28px; border-radius:50%; flex-shrink:0;
          box-shadow:0 0 12px currentColor }}
.label {{ flex:1; font-weight:600; font-size:1rem }}
.tag {{ font-size:0.7rem; font-weight:600; padding:3px 10px; border-radius:999px;
        white-space:nowrap; flex-shrink:0 }}
#footer {{ margin-top:1.5rem; font-size:0.75rem; color:#525252 }}
</style>
</head>
<body>
<h1>NBN Status Monitor</h1>
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


# ---------------------------------------------------------------------------
# HTTP server (local)
# ---------------------------------------------------------------------------


def make_handler(addresses: list[Address]) -> type[BaseHTTPRequestHandler]:
    """Create a request handler that polls and serves the status page."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            results = check_all(addresses)
            state = load_state()
            state = _update_state(results, state)
            save_state(state)
            html = generate_html(results, state=state)
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
        previous = load_state()
        new_state = notify_changes(results, previous)
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
