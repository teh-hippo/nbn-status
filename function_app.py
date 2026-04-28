"""Azure Functions entry point for NBN Status Monitor.

Timer trigger polls every 5 minutes and sends ntfy on changes.
HTTP trigger serves the status page.
"""

from __future__ import annotations

import azure.functions as func

import nbn_monitor

app = func.FunctionApp()


@app.timer_trigger(schedule="0 */5 * * * *", arg_name="timer", run_on_startup=False)
def poll_nbn(timer: func.TimerRequest) -> None:
    """Poll all addresses and notify on status changes."""
    addresses = nbn_monitor.load_addresses()
    results = nbn_monitor.check_all(addresses)
    state_result = nbn_monitor.load_state_result(addresses)

    if state_result.status in ("failed", "corrupt"):
        print(f"State load {state_result.status}: {state_result.error}; skipping save")
    else:
        new_state = nbn_monitor.notify_changes(
            results,
            state_result.state,
            previous_loaded=state_result.can_make_notification_decisions,
        )
        nbn_monitor.save_state(new_state)

    for addr, status in results:
        symbol = {"green": "✅", "red": "🔴", "amber": "🟡", "grey": "⚪"}.get(status.colour, "?")
        print(f"  {symbol} {addr.label}: {status.label}")


@app.route(route="/", auth_level=func.AuthLevel.ANONYMOUS)
def status_page(req: func.HttpRequest) -> func.HttpResponse:
    """Serve the traffic-light status page."""
    addresses = nbn_monitor.load_addresses()
    state_result = nbn_monitor.load_state_result(addresses)
    html = nbn_monitor.generate_snapshot_html(addresses, state_result)
    return func.HttpResponse(html, mimetype="text/html", status_code=200)
