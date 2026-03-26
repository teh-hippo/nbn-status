# NBN Status Monitor

Monitors NBN network outage status for configured addresses and sends push notifications via [ntfy](https://ntfy.sh) when outages start or resolve.

Includes a dark-themed traffic-light status page for quick visual checks.

## Setup

```bash
# Install dependencies
uv sync

# Copy and configure environment
cp .env.example .env
# Edit .env with your addresses and ntfy topic

# Run locally
uv run python nbn_monitor.py              # Poll and print
uv run python nbn_monitor.py --notify     # Poll with ntfy
uv run python nbn_monitor.py --serve      # Status page on localhost:8000
```

## Quality

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy nbn_monitor.py
uv run pytest tests/
```

## Deployment

Deployed as an Azure Function App (Consumption Plan). See `deploy.sh` for initial setup.

The GitHub Actions workflow validates on push and deploys to Azure on merge to `main`.
