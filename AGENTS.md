# AGENTS.md

Guidance for contributors and coding agents working in this repository.

## Project shape

- This is a Python 3.12 Azure Functions app for monitoring NBN outage status and sending ntfy notifications.
- Core monitor logic lives in `nbn_monitor.py`.
- Azure Functions wiring lives in `function_app.py`.
- Unit and regression tests live in `tests/test_monitor.py`.
- `host.json` sets an empty route prefix, so the HTTP status page is served from `/`, not `/api/status`.

## Architecture rules

- Keep the low-cost Azure Functions plus Blob Storage architecture unless a task explicitly asks for a broader redesign.
- The `poll_nbn` timer trigger is the only production writer of monitor state.
- Azure Blob Storage is authoritative in Azure. The state blob is `nbn-state/state.json`.
- Local `state.json` is for development only and must not be used as a fallback when Azure storage is configured.
- The HTTP status page must render the stored Blob snapshot. It must not poll NBN during normal page rendering.
- Do not debounce successful NBN statuses. If NBN returns a successful status transition, treat it as the source of truth.
- NBN poll errors are operational errors, not service states. They may update error metadata, but must not start, resolve, or reset outages.
- Failed or corrupt Blob loads must block notification decisions and must not overwrite the authoritative snapshot.
- Do not log raw LOC IDs, street addresses, ntfy topics, Azure connection strings, or raw state snapshots.

## State and notifications

- The current snapshot schema is version 2 with `schema_version`, `generated_at`, `poll`, and `addresses`.
- Per-address state separates `last_success`, `last_error`, `consecutive_error_count`, and `current_period`.
- `last_success`, `status`, `since`, and `current_period` are only updated from successful NBN responses.
- Notifications are sent only for successful NBN status transitions and only when the previous state loaded safely.

## Local tooling

- Use `uv` for Python dependency management.
- Run the validation commands before committing code changes:

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy nbn_monitor.py
NBN_ADDRESSES='[{"label":"test","loc_id":"LOC000000000001","poll":true,"notify":false}]' uv run pytest tests/ -v
```

## Deployment and production

- GitHub Actions validates on push and pull request, and deploys after changes land on `main`.
- Production runs as Function App `nbn-status` in resource group `nbn-status-rg`.
- Production HTTP access is protected by Azure Entra ID Easy Auth.
- Application Insights is the primary source for live proof. Prefer aggregate queries that avoid printing location identifiers or user-specific labels.
- The public NBN network status page should remain reachable at `https://www.nbnco.com.au/support/network-status`.
