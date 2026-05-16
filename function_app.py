"""Azure Functions entry point for NBN Status Monitor.

Timer trigger polls every 5 minutes and sends ntfy on changes.
HTTP trigger serves the status page.

The HTTP route runs behind Azure Entra ID Easy Auth. Easy Auth is configured
on the Function App resource (`Microsoft.Web/sites`), not in code, so this
module enforces a request-time check rather than a startup guard. When
``REQUIRE_EASY_AUTH=true`` is set on the hosting Function App, the page
returns HTTP 500 unless the request carries an Easy Auth-injected
``X-MS-Client-Principal-Name`` header. The header is set by the platform
only after a successful Easy Auth flow, so an accidentally unprotected
deployment fails closed regardless of which hosting plan provides the
runtime. ``REQUIRE_EASY_AUTH`` is a config-driven assertion that the
deployment intends to run behind Easy Auth.
"""

from __future__ import annotations

import os

import azure.functions as func

import nbn_monitor

app = func.FunctionApp()


@app.timer_trigger(schedule="0 */5 * * * *", arg_name="timer", run_on_startup=False)
def poll_nbn(timer: func.TimerRequest) -> None:
    """Poll all addresses and notify on status changes."""
    del timer
    addresses = nbn_monitor.load_addresses()
    nbn_monitor.run_poll_cycle(addresses)


@app.route(route="/", auth_level=func.AuthLevel.ANONYMOUS)
def status_page(req: func.HttpRequest) -> func.HttpResponse:
    """Serve the traffic-light status page."""
    if os.environ.get("REQUIRE_EASY_AUTH") == "true" and not req.headers.get(
        "X-MS-Client-Principal-Name"
    ):
        return func.HttpResponse(
            "Easy Auth is not enabled on this Function App; refusing to serve the status page.",
            status_code=500,
        )
    addresses = nbn_monitor.load_addresses()
    state_result = nbn_monitor.load_state_result()
    html = nbn_monitor.generate_snapshot_html(addresses, state_result)
    return func.HttpResponse(html, mimetype="text/html", status_code=200)
