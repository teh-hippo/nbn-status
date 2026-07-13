# AGENTS.md

Guidance for contributors and coding agents working in this repository.

## Project shape

- This is a Python 3.13 Azure Functions app for monitoring NBN outage status and sending ntfy notifications.
- Core monitor logic lives in the `nbn_monitor/` package, split into focused modules:
  - `config.py` — environment variables, `Address` dataclass, `load_addresses`.
  - `api.py` — NBN API client (`OutageStatus`, `check_outage`, `check_all`).
  - `planned.py` — planned-maintenance parsing, canonical schedule diffs, reminder timing,
    and shared event formatting.
  - `snapshot.py` — typed snapshot and planned-maintenance state models.
  - `persistence.py` — `StateBackend` protocol with Blob and local-file implementations.
  - `notify.py` — ntfy delivery and `notify_changes` transition logic.
  - `render.py` — HTML rendering of the status page.
  - `server.py` — local development HTTP server.
  - `orchestrator.py` — `run_poll_cycle`, the single source of truth for polling, persisting, and notifying.
  - `cli.py` — argparse `main` entry point (`python -m nbn_monitor`).
- Azure Functions wiring lives in `function_app.py`.
- Unit and regression tests live in `tests/`; one file per module (`test_api.py`, `test_orchestrator.py`, etc.). Coverage threshold is 85% (enforced in `pyproject.toml`).
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

## Runtime invariants

- Production runs on the Azure Functions **Flex Consumption** plan (FC1, Linux) in `australiaeast`. The Function App name is randomised and the URL is the default `*.azurewebsites.net`; ambiguity comes from the random name, not from a custom domain.
- The entire stack is pinned to Python **3.13**: `.python-version`, `pyproject.toml`, both GitHub Actions workflows, and the Terraform module's `runtime_version`. The previous Linux Consumption + Python 3.12 + `azure-functions<2` constraints were retired when the app was migrated to Flex.
- `azure-functions` is pinned to `>=2.1,<3`. The 2.x line is what Flex Consumption + Python 3.13 expect; the 1.x line is no longer used.
- Renovate has bounded `packageRules` (`azure-functions <3.0.0`, Python `~3.13`) so the next major Python (3.14 preview) and the next major `azure-functions` (3.x) cannot land automatically — both would need a verification round on Flex first.
- The deploy workflow ships a source-only package and lets Azure run the remote build via Azure Functions Core Tools (`func azure functionapp publish --build remote`). Do not reintroduce a local `uv pip install --target` step; Flex Consumption installs dependencies server-side from `requirements.txt`.
- Easy Auth is configured via `auth_settings_v2` on the Function App resource (nested block, not a separate Terraform resource). The Entra ID app registration (client id supplied via Terraform variables, never committed) is shared and survives Function App recreates.
- The Function App's system-assigned MSI has `Storage Blob Data Contributor` on the `flex-deploy` container only — never on the storage account or on `nbn-state` / `tfstate`. Maintain the container scope when adding new permissions.
- `function_app.py` reads `REQUIRE_EASY_AUTH=true` as a fail-closed deployment-time assertion. The Terraform module sets this on production; do not remove it.

## Infrastructure

- All Azure resources are managed by the Terraform module under `infra/`. State lives in the `tfstate` container of the project storage account (azurerm backend, shared-key auth — the storage account's `allowSharedKeyAccess` must stay `true`).
- Sensitive runtime config (`MICROSOFT_PROVIDER_AUTHENTICATION_SECRET`, `NTFY_TOPIC`, `NBN_ADDRESSES`) is loaded into `TF_VAR_*` at apply time by `infra/load-secrets.sh`, which reads them from the live Function App.
- Imported resources (resource group, storage account, `nbn-state` container, Application Insights, Entra ID app reg + SP) carry `lifecycle { prevent_destroy = true }` where appropriate. Do not relax that without an explicit reason.
- See `infra/README.md` for the apply workflow and constraints.

## State and notifications

- The current snapshot schema is version 2 with `schema_version`, `generated_at`, `poll`, and `addresses`.
- Per-address state separates `last_success`, `last_error`, `consecutive_error_count`, and `current_period`.
- Per-address state also stores the latest normalised planned schedule. Existing version 2
  snapshots load with empty planned fields, so additions must remain backward-compatible.
- Service-incident state is independent of `displayOutage` so a temporary `PLANNED_*` status
  cannot lose an active unplanned/degradation incident. Pending starts and restorations remain
  queued until ntfy accepts them.
- Planned notification state stores the last successfully announced schedule and one-shot
  reminder markers. Advance those markers only after ntfy accepts the notification.
- `last_success`, `status`, `since`, and `current_period` are only updated from successful NBN responses.
- Successful but malformed planned schedules must preserve the previous normalised schedule
  rather than creating false changes or cancellations.
- Notifications are sent only for successful NBN status transitions and only when the previous state loaded safely.
- Planned schedules are retained and rendered for every address. Planned notifications are
  limited to addresses with `notify: true`.
- Notification delivery is at-least-once because ntfy has no client idempotency key. State
  save failures must surface rather than silently claiming success.

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
- Production runs as a Flex Consumption Function App with a randomised name (the previous Linux Consumption app was retired in the migration). Resource names and ids are supplied via gitignored Terraform vars, not committed.
- Production HTTP access is protected by Azure Entra ID Easy Auth.
- Application Insights is the primary source for live proof. Prefer aggregate queries that avoid printing location identifiers or user-specific labels.
- The public NBN network status page should remain reachable at `https://www.nbnco.com.au/support/network-status`.
