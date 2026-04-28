# NBN Status Monitor

Monitors [NBN](https://www.nbnco.com.au/) network outage status for configured addresses and sends push notifications via [ntfy](https://ntfy.sh) when outages start or resolve.

Includes a dark-themed traffic-light status page for quick visual checks on mobile and desktop.

## Features

- Polls the NBN maintenance API for multiple addresses in parallel
- Sends [ntfy](https://ntfy.sh) notifications on outage start and resolution
- Tracks outage duration and includes it in resolution notifications
- Detects whether an outage is localised or area-wide (via neighbour comparison)
- Traffic-light status page with iOS PWA support
- Deployed as an Azure Function App on the Consumption Plan (~$0/month)

## Setup

```bash
# Install dependencies
uv sync

# Copy and configure environment
cp .env.example .env
# Edit .env with your addresses and ntfy topic

# Run locally
uv run python nbn_monitor.py              # Poll and print status
uv run python nbn_monitor.py --notify     # Poll with ntfy notifications
uv run python nbn_monitor.py --serve      # Status page on localhost:8000
```

### Finding your LOC ID

Look up NBN location IDs for your addresses:

```
https://places.nbnco.net.au/places/v1/autocomplete?query=YOUR+ADDRESS+HERE
```

## Quality

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy nbn_monitor.py
uv run pytest tests/
```

## Deployment

Deployed as an Azure Function App (Consumption Plan, Python 3.12).

- **Timer trigger**: polls every 5 minutes, sends ntfy on successful NBN status changes
- **HTTP trigger**: serves the status page at the root URL from the stored Blob snapshot
- **State**: Azure Blob Storage is authoritative in Azure; local `state.json` is for development only
- **Auth**: Azure Entra ID (Easy Auth) with user assignment required
- **CI/CD**: GitHub Actions validates on push, deploys on merge to `main`
- **Deploy auth**: OIDC federated credentials (no stored secrets)
- **Dependencies**: managed by [Renovate](https://docs.renovatebot.com/) with auto-merge

See `deploy.sh` for initial Azure resource setup.

### Operations notes

The status page shows the last known good NBN status from Blob Storage. Transient poll errors are logged and stored as error metadata, but they do not start, resolve, or reset outages.

Application Insights is the primary live log source. To inspect the state blob directly, the operator identity needs Blob data-plane access, such as Storage Blob Data Reader, on the storage account or container.
