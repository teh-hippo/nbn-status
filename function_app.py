"""Azure Functions entry point for NBN Status Monitor.

Timer trigger polls every 5 minutes and sends ntfy on changes.
HTTP trigger serves the status page.
"""

from __future__ import annotations

import azure.functions as func

import nbn_monitor

app = func.FunctionApp()


@app.timer_trigger(schedule="0 */5 * * * *", arg_name="timer", run_on_startup=True)
def poll_nbn(timer: func.TimerRequest) -> None:
    """Poll all addresses and notify on status changes."""
    addresses = nbn_monitor.load_addresses()
    results = nbn_monitor.check_all(addresses)
    previous = nbn_monitor.load_state()

    # On first run (no previous state), send a summary notification
    if not previous:
        outages = [a for a, s in results if s.is_outage]
        if outages:
            msg = "Startup: " + ", ".join(a.label for a in outages) + " in outage"
        else:
            msg = "Startup: all addresses clear"
        nbn_monitor.send_ntfy(
            title="NBN Monitor Online",
            message=msg,
            priority="default",
            tags="satellite",
        )

    new_state = nbn_monitor.notify_changes(results, previous)
    nbn_monitor.save_state(new_state)

    for addr, status in results:
        symbol = {"green": "✅", "red": "🔴", "amber": "🟡", "grey": "⚪"}.get(status.colour, "?")
        print(f"  {symbol} {addr.label}: {status.label}")


@app.route(route="status", auth_level=func.AuthLevel.FUNCTION)
def status_page(req: func.HttpRequest) -> func.HttpResponse:
    """Serve the traffic-light status page."""
    addresses = nbn_monitor.load_addresses()
    results = nbn_monitor.check_all(addresses)
    state = nbn_monitor.load_state()
    state = nbn_monitor._update_state(results, state)
    nbn_monitor.save_state(state)
    html = nbn_monitor.generate_html(results, state=state)
    return func.HttpResponse(html, mimetype="text/html", status_code=200)
