# NBN Status Monitor

Monitors [NBN](https://www.nbnco.com.au/) network outage status for configured addresses and sends push notifications via [ntfy](https://ntfy.sh) when outages start or resolve.

Includes a dark-themed traffic-light status page for quick visual checks on mobile and desktop.

## Features

- Polls the NBN maintenance API for multiple addresses in parallel
- Sends [ntfy](https://ntfy.sh) notifications on outage start and resolution
- Sends planned-maintenance alerts when work is added or materially changed, at 9:00 am
  the day before, and one hour before the expected start
- Tracks outage duration and includes it in resolution notifications
- Detects whether an outage is localised or area-wide (via neighbour comparison)
- Shows current and upcoming daily maintenance for every address on the traffic-light
  status page
- Responsive status page with native expandable schedules and iOS PWA support
- Deployed as an Azure Function App on the Flex Consumption Plan (~$0/month at this poll cadence)

## Setup

```bash
# Install dependencies
uv sync

# Copy and configure environment
cp .env.example .env
# Edit .env with your addresses and ntfy topic

# Run locally
uv run python -m nbn_monitor              # Poll and print status
uv run python -m nbn_monitor --notify     # Poll with ntfy notifications
uv run python -m nbn_monitor --serve      # Status page on localhost:8000
```

### Finding your LOC ID

Look up NBN location IDs for your addresses:

```
https://places.nbnco.net.au/places/v1/autocomplete?query=YOUR+ADDRESS+HERE
```

### Address notification scope

Every configured address is polled and its planned schedule is stored for the status page.
Only addresses with `"notify": true` send outage and planned-maintenance notifications.
Comparison and nearby addresses remain visible without generating planned alerts.

Planned reminders use the NBN response's local time zone. The day-before reminder is due at
9:00 am on the previous calendar day, and the final reminder is due one hour before the
expected start. The five-minute poll sends any missed reminder on the next successful poll
while the event is still upcoming.

## Quality

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy nbn_monitor function_app.py
uv run pytest tests/
```

## Deployment

Deployed as an Azure Function App (Flex Consumption Plan, Python 3.13).

- **Timer trigger**: polls every 5 minutes, sends ntfy on successful NBN status changes
- **HTTP trigger**: serves the status page at the root URL from the stored Blob snapshot
- **State**: Azure Blob Storage is authoritative in Azure; local `state.json` is for development only
- **Auth**: Azure Entra ID (Easy Auth) with user assignment required
- **CI/CD**: GitHub Actions validates on push, deploys on merge to `main` via Azure Functions Core Tools (`func azure functionapp publish --build remote`)
- **Deploy auth**: OIDC federated credentials (no stored secrets)
- **Dependencies**: managed by [Renovate](https://docs.renovatebot.com/) with auto-merge inside bounded version ranges (`azure-functions ~2`, Python `~3.13`)
- **Infrastructure**: Terraform module under `infra/`

### Operations notes

The status page shows the last known good NBN status and planned schedule from Blob Storage.
Transient poll or schedule-parse errors are logged, but they do not start, resolve, reset, or
cancel service and maintenance state.

Service transitions and planned notification delivery markers are persisted with the
snapshot. Pending outage starts, restorations, schedule alerts, and reminders are cleared
only after ntfy accepts the message, so failed sends remain eligible on the next poll.

Notification delivery is intentionally at-least-once. ntfy does not provide a client
idempotency key, so the rare case where ntfy accepts a message and the following Blob save
fails can produce a duplicate rather than silently losing the alert.

Application Insights is the primary live log source. To inspect the state blob directly, the operator identity needs Blob data-plane access, such as Storage Blob Data Reader, on the storage account or container.
