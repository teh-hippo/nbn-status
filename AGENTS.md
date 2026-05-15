# AGENTS.md

Guidance for contributors and coding agents working in this repository.

## Project shape

- This is a Python 3.12 Azure Functions app for monitoring NBN outage status and sending ntfy notifications.
- Core monitor logic lives in the `nbn_monitor/` package, split into focused modules:
  - `config.py` — environment variables, `Address` dataclass, `load_addresses`.
  - `api.py` — NBN API client (`OutageStatus`, `check_outage`, `check_all`).
  - `state.py` — typed `Snapshot` model and `StateBackend` protocol with `BlobStateBackend` and `FileStateBackend`.
  - `notify.py` — ntfy delivery and `notify_changes` transition logic.
  - `render.py` — HTML rendering of the status page.
  - `server.py` — local development HTTP server.
  - `orchestrator.py` — `run_poll_cycle`, the single source of truth for polling, persisting, and notifying.
  - `cli.py` — argparse `main` entry point (`python -m nbn_monitor`).
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

## Runtime constraints

- Production runs on the Azure Functions Linux Consumption plan, which is in feature freeze and retires on 30 September 2028. It does not serve Python 3.13 or 3.14 worker images, even though `az functionapp list-runtimes` lists them (the CLI does not filter by plan type). Setting `linuxFxVersion` to `Python|3.13` or `Python|3.14` produces an indefinite HTTP 503 with no Application Insights traces.
- Therefore the entire stack is pinned to Python 3.12: `.python-version`, `pyproject.toml`, both GitHub Actions workflows, `deploy.sh --runtime-version`, and the build environment that produces wheels in the deploy step.
- `azure-functions` is pinned to `>=1.24,<2`. The 2.x release line bumped `requires-python` to `>=3.13`, so it cannot install on Python 3.12. The 1.x line is still actively maintained and is API-compatible with 2.x for the decorators this app uses.
- Renovate has explicit `packageRules` to block both bumps. Do not relax those constraints without first migrating the Function App to Flex Consumption (a separate, larger task).
- CI builds wheels on the GitHub runner with the same Python version Azure runs (3.12), so the binary wheels (e.g. `_cffi_backend.cpython-312-*.so`) match the runtime ABI. Mismatching these produces `ModuleNotFoundError: No module named '_cffi_backend'` at invocation time.

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
uv run mypy nbn_monitor function_app.py
NBN_ADDRESSES='[{"label":"test","loc_id":"LOC000000000001","poll":true,"notify":false}]' uv run pytest tests/ -v
```

## Deployment and production

- GitHub Actions validates on push and pull request, and deploys after changes land on `main`.
- Production runs as Function App `nbn-status` in resource group `nbn-status-rg`.
- Production HTTP access is protected by Azure Entra ID Easy Auth.
- Application Insights is the primary source for live proof. Prefer aggregate queries that avoid printing location identifiers or user-specific labels.
- The public NBN network status page should remain reachable at `https://www.nbnco.com.au/support/network-status`.
